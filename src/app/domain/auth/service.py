from datetime import UTC, datetime

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
        client_env: str | None = None,
    ) -> tuple[Member, str, str]:
        member = await self._repo.get_by_login_id(member_id)
        if member is None or not verify_password(password, member.password_hash):
            raise UnauthorizedError("아이디 또는 비밀번호가 올바르지 않습니다.")
        ensure_member_usable(member)
        # 화면 이동이 아닌 로그인 자체도 "login" 코드로 남긴다(요청) — 예전엔 NULL이라
        # 접속 기록 목록에서 화면 칸이 비어 무슨 행인지 구분이 안 됐다.
        await self.record_access(
            member, ip_address=ip_address, user_agent=user_agent, screen_code="login",
            client_env=client_env,
        )
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
        client_env: str | None = None,
    ) -> None:
        """/auth/login(screen_code="login")과 /auth/access-ping(화면을 전환할 때마다, 해당
        화면 코드로) 양쪽에서 공유하는 접속 기록. 부를 때마다 항상 새 행을 남긴다(요청).

        예전엔 같은 사람이 같은 화면을 30분 안에 다시 보면 새 행 대신 기존 행의 시각만
        갱신했다(행 폭증 방지). 그런데 그러면 그 화면에 '처음 들어온 시각'이 덮여 사라져서,
        이력에서 언제부터 봤는지를 알 수 없었다 — 새로고침 한 번에 앞선 방문 기록이 통째로
        지워지는 셈이다. 지금은 한 줄도 잃지 않는 쪽을 택한다.

        운영에서만 쌓는다(요청) — 로컬 개발은 화면을 옮길 때마다 행이 쌓여 실제 접속
        이력을 덮어버리고, 개발자 한 명의 기록이라 분석 가치도 없다. 다만 여기서 말하는
        '운영'은 이 백엔드가 아니라 요청을 보낸 프론트 기준이다(지적) — 로컬 프론트가
        운영 백엔드를 바라보고 개발하는 경우가 실제로 그런 경우라, 백엔드의 ENVIRONMENT로
        걸면 정작 막아야 할 그 경우를 못 막고 반대로 로컬 백엔드에서만 막힌다. 프론트가
        빌드 모드를 X-Client-Env로 실어 보내고(client.ts) 그 값으로 판단한다.

        헤더가 아예 없으면 기록한다 — 값을 안 보내는 건 우리 프론트가 아닌 호출(옛 빌드,
        외부 도구)이라 개발 중이라고 단정할 근거가 없고, 기본값이 '안 남김'이면 헤더가
        빠지는 순간 접속 이력이 조용히 통째로 비어버린다."""
        if client_env is not None and client_env != "production":
            return
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
