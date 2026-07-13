from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app_version.models import AppVersionState


class AppVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self) -> AppVersionState:
        # 운영 DB는 마이그레이션(0038)이 이 싱글턴 행을 항상 시드해두지만, 테스트는
        # create_all로 스키마만 만들고 데이터는 안 채우므로 없으면 기본값(v1)으로 만든다.
        state = await self._session.get(AppVersionState, 1)
        if state is None:
            state = AppVersionState(id=1, active_version="v1")
            self._session.add(state)
            await self._session.flush()
        return state
