from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.leagues.models import League, LeagueMatch, LeagueTeam


class LeagueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, league: League) -> None:
        self._session.add(league)

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, league_id: int) -> League | None:
        return await self._session.get(League, league_id)

    async def list_all(self) -> list[League]:
        result = await self._session.execute(
            select(League).order_by(League.created_at.desc())
        )
        return list(result.scalars().unique().all())

    async def get_team(self, team_id: int) -> LeagueTeam | None:
        return await self._session.get(LeagueTeam, team_id)

    async def get_match(self, match_id: int) -> LeagueMatch | None:
        return await self._session.get(LeagueMatch, match_id)
