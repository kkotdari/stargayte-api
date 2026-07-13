from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.env_vars.repository import EnvVarRepository

# 숨겨진 제어판 잠금 비밀번호 — env_vars 테이블의 key. 값 자체는 DB에서만 바꾼다(코드
# 배포 불필요). 마이그레이션이 기본값을 시드해두지만, 혹시 그 행이 지워졌다면 이
# 상수가 아니라 None을 돌려받아 항상 실패하게 한다(빈 문자열 등으로 아무 입력이나
# 통과하는 사고를 막기 위해 EnvVarRepository.get_value가 없으면 None을 주는 것과 짝).
ADMIN_PANEL_PASSWORD_KEY = "admin_panel_password"


class EnvVarService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = EnvVarRepository(session)

    async def verify_admin_panel_password(self, candidate: str) -> bool:
        value = await self._repo.get_value(ADMIN_PANEL_PASSWORD_KEY)
        return value is not None and candidate == value
