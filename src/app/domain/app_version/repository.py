from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app_version.models import SEED_VERSIONS, AppVersionEntry, AppVersionState


class AppVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self) -> AppVersionState:
        # 운영 DB는 마이그레이션이 이 싱글턴 행을 항상 시드해두지만, 테스트는 create_all로
        # 스키마만 만들고 데이터는 안 채우므로 없으면 기본값(1)으로 만든다.
        state = await self._session.get(AppVersionState, 1)
        if state is None:
            state = AppVersionState(id=1, active_version="1")
            self._session.add(state)
            await self._session.flush()
        return state

    async def list_versions(self) -> list[AppVersionEntry]:
        # get_state와 같은 이유 — 아직 시드가 안 된 DB(테스트 등)에서는 기본 목록을 채운다.
        await self._ensure_seeded()
        result = await self._session.execute(select(AppVersionEntry))
        entries = list(result.scalars())
        # 숫자 기준 오름차순(문자열 정렬은 "10"이 "2" 앞에 오므로 float로 비교한다).
        entries.sort(key=lambda e: float(e.number))
        return entries

    async def version_registered(self, number: str) -> bool:
        return await self.get_entry(number) is not None

    async def get_entry(self, number: str) -> AppVersionEntry | None:
        await self._ensure_seeded()
        result = await self._session.execute(
            select(AppVersionEntry).where(AppVersionEntry.number == number)
        )
        return result.scalar_one_or_none()

    async def _ensure_seeded(self) -> None:
        existing = await self._session.execute(select(AppVersionEntry.id).limit(1))
        if existing.scalar_one_or_none() is not None:
            return
        for number in SEED_VERSIONS:
            self._session.add(AppVersionEntry(number=number))
        await self._session.flush()
