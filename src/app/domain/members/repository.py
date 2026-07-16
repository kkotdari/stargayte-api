from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.members.models import Member


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_pk(self, pk: int) -> Member | None:
        return await self._session.get(Member, pk)

    # 아이디/배틀태그/닉네임 중복 확인은 전부 대소문자를 구분하지 않는다(요청: "로그인시
    # 회원가입시 중복체크할때 닉네임 아이디 배틀태그 전부 대소문자 구분 안하게") — 대소문자만
    # 다른 값으로 가입/로그인 계정을 새로 만들 수 있으면 사람 눈엔 같은 값으로 보여 혼란만
    # 준다.
    async def get_by_login_id(self, login_id: str) -> Member | None:
        stmt = select(Member).where(func.lower(Member.id) == login_id.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_battletag(self, battletag: str) -> Member | None:
        stmt = select(Member).where(func.lower(Member.battletag) == battletag.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_nickname(self, nickname: str) -> Member | None:
        stmt = select(Member).where(func.lower(Member.nickname) == nickname.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Member]:
        stmt = select(Member).order_by(Member.id)
        return list((await self._session.execute(stmt)).scalars().all())

    def add(self, member: Member) -> None:
        self._session.add(member)

    async def flush(self) -> None:
        await self._session.flush()
