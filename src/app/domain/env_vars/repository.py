from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.env_vars.models import EnvVar


class EnvVarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_value(self, key: str) -> str | None:
        row = await self._session.get(EnvVar, key)
        return row.value if row is not None else None
