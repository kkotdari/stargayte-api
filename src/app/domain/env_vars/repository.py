from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.env_vars.models import EnvVar


class EnvVarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_value(self, key: str) -> str | None:
        row = await self._session.get(EnvVar, key)
        return row.value if row is not None else None

    async def set_value(self, key: str, value: str) -> None:
        # 있으면 갱신, 없으면 새로 만든다(단순 upsert) — 관리자가 코드 배포 없이 DB 값을
        # 바꾸는 설정(예: 버전 안내 표시 토글)에 쓴다. commit은 호출부(service)가 한다.
        row = await self._session.get(EnvVar, key)
        if row is None:
            self._session.add(EnvVar(key=key, value=value))
        else:
            row.value = value
        await self._session.flush()
