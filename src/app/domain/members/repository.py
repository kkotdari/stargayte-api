from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.members.models import Member


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_pk(self, pk: int) -> Member | None:
        return await self._session.get(Member, pk)

    async def get_by_login_id(self, login_id: str) -> Member | None:
        stmt = select(Member).where(Member.id == login_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_battletag(self, battletag: str) -> Member | None:
        stmt = select(Member).where(Member.battletag == battletag)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Member]:
        stmt = select(Member).order_by(Member.id)
        return list((await self._session.execute(stmt)).scalars().all())

    def add(self, member: Member) -> None:
        self._session.add(member)

    async def flush(self) -> None:
        await self._session.flush()
