from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.domain.auth.models import AccessHistory, RefreshToken
from app.domain.auth.schemas import AccessHistoryEntry, SignupRequest
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository
from app.domain.members.service import MemberService, ensure_member_usable
from app.storage.base import FileStorage


# 같은 사람이 같은 화면(screen_code)에서 이 시간 안에 또 기록되면(예: 짧은 새로고침 반복)
# 새 행을 추가하는 대신 기존 최근 행의 시각만 갱신한다. 다른 화면으로 넘어가는 것 자체는
# 항상 새 행이 된다 — 세션 단위가 아니라 화면 단위로 구분하는 게 목적이라서.
ACCESS_DEDUPE_WINDOW = timedelta(minutes=30)


class AuthService:
    def __init__(self, session: AsyncSession, storage: FileStorage) -> None:
        self._session = session
        self._repo = MemberRepository(session)
        self._member_service = MemberService(session, storage)

    async def login(
        self,
        member_id: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Member, str, str]:
        member = await self._repo.get_by_login_id(member_id)
        if member is None or not verify_password(password, member.password_hash):
            raise UnauthorizedError("아이디 또는 비밀번호가 올바르지 않습니다.")
        ensure_member_usable(member)
        await self.record_access(member, ip_address=ip_address, user_agent=user_agent)
        refresh_token = await self._issue_refresh_token(member)
        return member, create_access_token(str(member.pk)), refresh_token

    async def _issue_refresh_token(self, member: Member) -> str:
        raw = generate_refresh_token()
        self._session.add(
            RefreshToken(
                member_pk=member.pk, token_hash=hash_refresh_token(raw), expires_at=refresh_token_expiry()
            )
        )
        await self._session.commit()
        return raw

    async def refresh(self, raw_token: str) -> tuple[Member, str, str]:
        """리프레시 토큰으로 새 액세스 토큰을 발급한다. 로테이션 방식이라 기존 토큰은 즉시
        폐기하고 새 리프레시 토큰을 함께 내려준다 (탈취된 옛 토큰 재사용 시도는 이미 폐기된
        토큰이라 거부된다). 접속 기록은 남기지 않는다(record_access 참고)."""
        token_hash = hash_refresh_token(raw_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        stored = (await self._session.execute(stmt)).scalar_one_or_none()
        now = datetime.now(UTC)
        if stored is None or stored.revoked_at is not None or stored.expires_at < now:
            raise UnauthorizedError("세션이 만료되었습니다. 다시 로그인해 주세요.")

        member = await self._repo.get_by_pk(stored.member_pk)
        if member is None:
            raise UnauthorizedError("세션이 만료되었습니다. 다시 로그인해 주세요.")
        ensure_member_usable(member)

        stored.revoked_at = now
        new_refresh = await self._issue_refresh_token(member)
        # 리프레시는 이미 열려 있던 세션이 액세스 토큰만 갈아 끼우는 것뿐이라 접속 기록을
        # 남기지 않는다 — 새 방문 신호는 /login과 /access-ping에서 이미 잡힌다.
        return member, create_access_token(str(member.pk)), new_refresh

    async def logout(self, raw_token: str) -> None:
        token_hash = hash_refresh_token(raw_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        stored = (await self._session.execute(stmt)).scalar_one_or_none()
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
            await self._session.commit()

    async def record_access(
        self,
        member: Member,
        *,
        ip_address: str | None,
        user_agent: str | None,
        screen_code: str | None = None,
    ) -> None:
        """/auth/login(screen_code=None)과 /auth/access-ping(화면을 전환할 때마다, 해당 화면
        코드로) 양쪽에서 공유하는 접속 기록. "같은 세션이면 한 행"이 아니라 화면별로 구분해서
        남기는 게 목적이라, 중복 판단은 member_pk뿐 아니라 screen_code까지 함께 봐서 같은
        화면을 짧은 시간 안에 또 조회한 경우만(=진짜 중복) 새 행 대신 그 행의 시각/IP/기기
        정보를 갱신한다 — 다른 화면으로 넘어가면 항상 새 행이 생긴다."""
        now = datetime.now(UTC)
        stmt = (
            select(AccessHistory)
            .where(AccessHistory.member_pk == member.pk)
            .where(
                AccessHistory.screen_code.is_(None)
                if screen_code is None
                else AccessHistory.screen_code == screen_code
            )
            .order_by(AccessHistory.logged_in_at.desc())
            .limit(1)
        )
        latest = (await self._session.execute(stmt)).scalar_one_or_none()
        if latest is not None and now - latest.logged_in_at < ACCESS_DEDUPE_WINDOW:
            latest.logged_in_at = now
            latest.ip_address = ip_address
            latest.user_agent = user_agent
        else:
            self._session.add(
                AccessHistory(
                    member_pk=member.pk,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    screen_code=screen_code,
                )
            )
        await self._session.commit()

    async def list_access_history(self, *, limit: int = 300) -> list[AccessHistoryEntry]:
        stmt = (
            select(AccessHistory, Member)
            .join(Member, Member.pk == AccessHistory.member_pk)
            .order_by(AccessHistory.logged_in_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AccessHistoryEntry(
                id=history.id,
                member_id=member.id,
                member_nickname=member.nickname,
                logged_in_at=history.logged_in_at,
                screen_code=history.screen_code,
            )
            for history, member in rows
        ]

    async def signup(self, payload: SignupRequest) -> tuple[Member, str, str]:
        member = await self._member_service.create_member(
            member_id=payload.id,
            password=payload.password,
            battletag=payload.battletag,
            replay_aliases=payload.replay_aliases,
            insta=payload.insta,
            avatar=payload.avatar,
        )
        await self._session.commit()
        refresh_token = await self._issue_refresh_token(member)
        return member, create_access_token(str(member.pk)), refresh_token
