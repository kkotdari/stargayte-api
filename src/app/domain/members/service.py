from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.imaging import resize_image_bytes
from app.core.security import hash_password, verify_password
from app.domain.members.models import (
    Member,
    MemberRoleAssignment,
    ReplayAlias,
)
from app.domain.members.repository import MemberRepository
from app.domain.members.schemas import MemberUpdate
from app.storage.base import FileStorage
from app.storage.data_url import decode_data_url, guess_extension, is_data_url

_BLOCKED_STATUS_MESSAGES = {
    "pending": "관리자 승인 대기 중인 계정입니다.",
    "suspended": "이용이 정지된 계정입니다.",
    "withdrawn": "탈퇴한 계정입니다.",
}


def _dedupe_aliases(aliases: list[str]) -> list[str]:
    """인게임 아이디 목록을 다듬는다 — 앞뒤 공백 제거, 빈 값/중복 제외. 회원 생성(가입/관리자
    즉시생성)과 기존 회원의 목록 전체 교체(replace_replay_aliases)가 함께 쓴다."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in aliases:
        alias = raw.strip()
        if not alias or alias in seen:
            continue
        seen.add(alias)
        cleaned.append(alias)
    return cleaned


def ensure_member_usable(member: Member) -> None:
    """로그인 및 이미 발급된 토큰으로 API를 쓸 때 공통으로 거치는 상태 게이트.
    active 가 아니면 상태별 안내 메시지로 막는다."""
    message = _BLOCKED_STATUS_MESSAGES.get(member.status)
    if message:
        raise UnauthorizedError(message)


class MemberService:
    def __init__(self, session: AsyncSession, storage: FileStorage) -> None:
        self._session = session
        self._repo = MemberRepository(session)
        self._storage = storage

    async def list_members(self) -> list[Member]:
        return await self._repo.list_all()

    async def get_member(self, member_id: str) -> Member:
        member = await self._repo.get_by_login_id(member_id)
        if member is None:
            raise NotFoundError("회원을 찾을 수 없습니다.")
        return member

    async def update_status(self, member_id: str, new_status: str, *, actor: Member) -> Member:
        member = await self.get_member(member_id)

        if member.pk == actor.pk and new_status == "suspended":
            raise ValidationError("본인 계정은 정지할 수 없습니다.")

        if new_status == "suspended":
            await self._ensure_not_last_active_admin(member)

        member.status = new_status
        member.updated_by = actor.pk
        await self._repo.flush()
        return member

    async def withdraw(self, member_id: str, *, actor: Member) -> Member:
        """본인 탈퇴. 경기결과는 member_pk를 RESTRICT로 참조하므로 실제로 행을 지우지
        않고 상태만 withdrawn으로 바꿔 로그인/이용을 막는다 (과거 경기결과는 그대로 남는다)."""
        member = await self.get_member(member_id)
        if member.pk != actor.pk:
            raise ForbiddenError("본인 계정만 탈퇴할 수 있습니다.")

        await self._ensure_not_last_active_admin(member)

        member.status = "withdrawn"
        member.updated_by = actor.pk
        await self._repo.flush()
        return member

    async def _ensure_not_last_active_admin(self, member: Member) -> None:
        if not member.has_any_role("0202"):
            return
        others = await self._repo.list_all()
        remaining_admins = [
            m for m in others
            if m.has_any_role("0202") and m.status == "active" and m.pk != member.pk
        ]
        if not remaining_admins:
            raise ConflictError(
                "마지막 활성 관리자는 정지/탈퇴할 수 없습니다. 다른 관리자를 먼저 지정해주세요."
            )

    async def update_roles(self, member_id: str, new_roles: list[str], *, actor: Member) -> Member:
        """회원의 역할 집합을 통째로 교체한다(관리자 전용, 다중 선택). 관리자 권한을
        회수하는 변경이라면 마지막 활성 관리자가 남는지도 확인한다."""
        member = await self.get_member(member_id)

        losing_admin = member.has_any_role("0202") and "0202" not in new_roles
        if losing_admin:
            await self._ensure_not_last_active_admin(member)

        # 기존 행을 전부 지우고 새 행으로 통째로 바꿔치기하면, 그대로 유지되는 역할(예: 이미
        # 있던 0203에 0204만 추가하는 경우의 0203)까지 같은 (member_pk, role) 값으로
        # DELETE+INSERT가 한 flush 안에서 같이 일어나면서 uq_member_roles_member_role
        # 유니크 제약을 위반해 에러가 났다 — 그대로 남는 역할은 기존 행을 유지하고, 실제로
        # 빠지거나 새로 추가되는 역할만 지우거나 만든다.
        new_role_set = set(new_roles)
        current_role_set = {r.role for r in member.roles}
        member.roles = [r for r in member.roles if r.role in new_role_set]
        for role in new_role_set - current_role_set:
            member.roles.append(MemberRoleAssignment(role=role))
        member.updated_by = actor.pk
        await self._repo.flush()
        return member

    async def create_member(
        self,
        *,
        member_id: str,
        password: str,
        battletag: str,
        replay_aliases: list[str],
        insta: str,
        avatar: str | None,
    ) -> Member:
        if await self._repo.get_by_login_id(member_id) is not None:
            raise ConflictError("이미 존재하는 아이디입니다.")
        if await self._repo.get_by_battletag(battletag) is not None:
            raise ConflictError("이미 존재하는 배틀태그입니다.")

        # 클럽의 첫 번째 가입자는 자동으로 운영자로 지정하고, 승인 절차 없이 바로 active로
        # 시작한다 (그래야 다른 회원을 승인하고 운영자를 임명해줄 사람이 존재한다). 그 외
        # 신규 가입자는 관리자가 승인(active로 전환)하기 전까지 로그인/이용이 막히는 pending
        # 상태로 시작한다.
        is_first_member = len(await self._repo.list_all()) == 0

        avatar_url = await self._store_avatar_if_needed(member_id, avatar)
        member = Member(
            id=member_id,
            password_hash=hash_password(password),
            nickname=battletag.split("#")[0] or member_id,
            battletag=battletag,
            replay_aliases=[ReplayAlias(raw_name=a, kind="member") for a in _dedupe_aliases(replay_aliases)],
            insta=insta,
            avatar_url=avatar_url,
            roles=[MemberRoleAssignment(role="0202" if is_first_member else "0203")],
            status="active" if is_first_member else "pending",
        )
        self._repo.add(member)
        await self._repo.flush()
        # 본인 가입이므로 등록자/수정자는 방금 만들어진 본인이다. pk는 flush 이후에만 확정된다.
        member.created_by = member.pk
        member.updated_by = member.pk
        await self._repo.flush()
        return member

    async def create_member_by_admin(
        self,
        *,
        member_id: str,
        password: str,
        battletag: str,
        replay_aliases: list[str],
        insta: str,
        avatar: str | None,
        actor: Member,
    ) -> Member:
        """슈퍼관리자가 회원관리 화면에서 바로 회원을 생성한다. 자기 가입(create_member)과
        달리 승인 대기 없이 즉시 active로 시작하고, 등록자는 만든 슈퍼관리자다."""
        if await self._repo.get_by_login_id(member_id) is not None:
            raise ConflictError("이미 존재하는 아이디입니다.")
        if await self._repo.get_by_battletag(battletag) is not None:
            raise ConflictError("이미 존재하는 배틀태그입니다.")

        avatar_url = await self._store_avatar_if_needed(member_id, avatar)
        member = Member(
            id=member_id,
            password_hash=hash_password(password),
            nickname=battletag.split("#")[0] or member_id,
            battletag=battletag,
            replay_aliases=[ReplayAlias(raw_name=a, kind="member") for a in _dedupe_aliases(replay_aliases)],
            insta=insta,
            avatar_url=avatar_url,
            roles=[MemberRoleAssignment(role="0203")],
            status="active",
        )
        self._repo.add(member)
        await self._repo.flush()
        member.created_by = actor.pk
        member.updated_by = actor.pk
        await self._repo.flush()
        return member

    async def update_profile(self, member_id: str, patch: MemberUpdate, *, actor_pk: int) -> Member:
        member = await self.get_member(member_id)
        data = patch.model_dump(exclude_unset=True)

        # 로그인 아이디는 pk가 아니라 바뀔 수 있는 값이다(인증은 pk 기반 토큰을 쓰므로 안전).
        if "id" in data and data["id"] != member.id:
            existing = await self._repo.get_by_login_id(data["id"])
            if existing is not None and existing.pk != member.pk:
                raise ConflictError("이미 존재하는 아이디입니다.")
            member.id = data["id"]

        if "battletag" in data and data["battletag"] != member.battletag:
            existing = await self._repo.get_by_battletag(data["battletag"])
            if existing is not None and existing.id != member.id:
                raise ConflictError("이미 존재하는 배틀태그입니다.")
            member.battletag = data["battletag"]

        if "nickname" in data:
            member.nickname = data["nickname"]
        if "insta" in data:
            member.insta = data["insta"]

        if "avatar" in data:
            # 이력 기능이 있던 시절엔 옛 파일을 일부러 안 지웠지만(확대보기에서 다시 볼 수
            # 있어야 해서), 이제는 그 이유가 없으니 실제로 바뀐 경우에만(같은 URL 유지가
            # 아닌 경우) reprocess_avatar와 같은 방식으로 옛 파일을 지운다.
            old_avatar_url = member.avatar_url
            member.avatar_url = await self._store_avatar_if_needed(member.id, data["avatar"])
            if old_avatar_url and old_avatar_url != member.avatar_url:
                await self._delete_by_url(old_avatar_url)

        member.updated_by = actor_pk
        await self._repo.flush()
        return member

    async def update_password(
        self, member_id: str, current_password: str, new_password: str, *, actor: Member
    ) -> Member:
        """본인 전용 — 현재 비밀번호가 맞는지 확인한 뒤에만 바꾼다."""
        member = await self.get_member(member_id)
        if member.pk != actor.pk:
            raise ForbiddenError("본인 계정만 비밀번호를 변경할 수 있습니다.")
        if not verify_password(current_password, member.password_hash):
            raise ValidationError("현재 비밀번호가 올바르지 않습니다.")
        member.password_hash = hash_password(new_password)
        member.updated_by = actor.pk
        await self._repo.flush()
        return member

    async def replace_replay_aliases(self, member_id: str, aliases: list[str], *, actor_pk: int) -> Member:
        """인게임 아이디 목록 전체를 통째로 교체한다 (관리자 화면의 입력칸 편집용, 개수 제한
        없음). 그대로 유지되는 이름은 기존 행을 건드리지 않고, 실제로
        빠지거나 새로 추가되는 것만 지우거나 만든다 — update_roles와 같은 이유로, 같은 값에
        대해 한 flush 안에서 DELETE+INSERT가 겹치면 유니크 제약을 위반할 수 있어서다."""
        member = await self.get_member(member_id)
        cleaned = _dedupe_aliases(aliases)

        new_set = set(cleaned)
        current_set = {e.raw_name for e in member.replay_aliases}
        member.replay_aliases = [e for e in member.replay_aliases if e.raw_name in new_set]
        for alias in cleaned:
            if alias not in current_set:
                member.replay_aliases.append(ReplayAlias(raw_name=alias, kind="member"))
        member.updated_by = actor_pk
        await self._repo.flush()
        return member

    async def add_replay_alias(self, member_id: str, alias: str, *, actor_pk: int) -> Member:
        """리플레이 매칭 중 못 찾은 이름 하나를 추가한다. 이미 등록돼 있으면 아무 것도 안
        한다. 개수 제한은 없다."""
        member = await self.get_member(member_id)
        alias = alias.strip()
        if not alias or alias in {e.raw_name for e in member.replay_aliases}:
            return member
        member.replay_aliases.append(ReplayAlias(raw_name=alias, kind="member"))
        member.updated_by = actor_pk
        await self._repo.flush()
        return member

    async def reprocess_avatar(self, member_id: str, *, actor_pk: int) -> Member:
        """이미 저장된 사진을 서버에서 다시 불러와 같은 축소 파이프라인으로 재인코딩한다.
        브라우저에서 canvas로 처리하면 사진 서버 오리진의 CORS 설정에 좌우되므로, 항상
        동작하도록 서버 로컬 파일을 직접 읽어 처리한다."""
        member = await self.get_member(member_id)
        if not member.avatar_url:
            raise ValidationError("사진이 없는 회원입니다.")

        key = self._key_from_url(member.avatar_url)
        if key is None:
            raise ValidationError("재처리할 수 없는 사진입니다.")

        content = await self._storage.read(key)
        resized = resize_image_bytes(content)
        stored = await self._storage.save(
            subdir="avatars", filename=f"{member.id}.jpg", content=resized, content_type="image/jpeg"
        )
        old_url = member.avatar_url
        await self._delete_by_url(old_url)
        member.avatar_url = stored.url
        member.updated_by = actor_pk
        await self._repo.flush()
        return member

    async def _store_avatar_if_needed(self, member_id: str, avatar: str | None) -> str | None:
        """avatar 가 새 data URL 이면 디스크에 저장하고 그 URL을 반환한다. null 이면 None,
        이미 서버 URL(변경 없음)이면 그 URL을 그대로 반환한다. 이전 사진 파일은 지우지
        않는다 — 아직 다른 곳(예: 오래된 브라우저 캐시)에서 그 URL을 참조 중일 수 있어,
        당장 안전하게 지울 수 있다는 보장이 없다."""
        if avatar is None:
            return None
        if not is_data_url(avatar):
            return avatar  # 기존 URL 그대로 유지

        content, content_type = decode_data_url(avatar)
        ext = guess_extension(content_type)
        stored = await self._storage.save(
            subdir="avatars", filename=f"{member_id}{ext}", content=content, content_type=content_type
        )
        return stored.url

    def _key_from_url(self, url: str) -> str | None:
        # LocalFileStorage.read/delete 는 내부 저장 경로(key)를 받는다. 호스트(PUBLIC_BASE_URL)는
        # 무시하고 경로에서 storage_url_path 마커 뒤쪽만 key로 취급한다. 그래야 배포 도메인이
        # 바뀌어도(로컬 -> 운영 등) 예전에 저장된 URL의 파일을 정상적으로 다룰 수 있다.
        from urllib.parse import urlparse

        from app.core.config import settings

        marker = f"{settings.storage_url_path.rstrip('/')}/"
        path = urlparse(url).path
        idx = path.find(marker)
        if idx == -1:
            return None
        return path[idx + len(marker) :]

    async def _delete_by_url(self, url: str) -> None:
        key = self._key_from_url(url)
        if key is not None:
            await self._storage.delete(key)
