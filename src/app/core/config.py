from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 기반 설정. .env 파일 또는 실제 환경변수에서 값을 읽는다."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Stargayte API"
    environment: str = "development"
    debug: bool = False

    api_prefix: str = "/api"

    # DB: SQLAlchemy URL. 드라이버만 교체하면 Postgres 외 다른 DB로도 전환 가능
    # (예: sqlite+aiosqlite:///./var/stargayte.db, mysql+asyncmy://... 등).
    # 기본값을 두지 않는다: 로컬은 .env, 운영은 실제 환경변수로 반드시 명시적으로 주입해야 한다.
    database_url: str
    db_echo: bool = False

    # 기본값을 두지 않는다: 예측 가능한 시크릿으로 부팅되는 것을 막기 위해 로컬은 .env,
    # 운영은 실제 환경변수로 반드시 명시적으로 주입해야 한다.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    # 비밀번호 해시 비용(bcrypt 라운드). 운영 기본값은 라이브러리 기본과 같은 12로 둔다 —
    # 낮추면 유출된 해시를 무차별 대입하기 쉬워지므로 실제 서비스에서는 절대 내리지 않는다.
    # 테스트에서만 4로 내린다(tests/conftest.py): 한 번 해시에 0.27초가 걸려 signup/login이
    # 236곳인 스위트 전체 시간의 대부분을 여기서 쓰고 있었다. 테스트는 해시 강도가 아니라
    # "맞는 비밀번호는 통과하고 틀린 건 막힌다"를 보는 것이라 라운드 수와 무관하다.
    password_hash_rounds: int = 12
    # 액세스 토큰은 짧게(1시간), 대신 리프레시 토큰(30일, 로테이션)으로 재로그인 없이 세션을
    # 이어간다. 리프레시 토큰이 만료되기 전까지 다시 방문하면 계속 로그인 상태가 유지되고,
    # 30일 넘게 안 쓰면 다시 로그인해야 한다.
    jwt_access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    storage_backend: str = "local"
    storage_local_root: str = "var/uploads"
    storage_url_path: str = "/uploads"
    public_base_url: str = "http://localhost:8000"

    # 랭크 변동 기능 자체를 켜고 끄는 스위치(요청: 지금 구조가 깔끔하지 않아 일단 멈춘다).
    # 꺼 두면 ① 부팅 때 기준선을 안 깐다(손으로 비운 표가 다시 차지 않게) ② 집계 스케줄러가
    # 아예 안 뜬다 ③ 활동 목록에 카드가 안 나간다(남아 있는 행이 있어도) ④ 제어판의 수동
    # 집계·기준선 버튼이 409로 막힌다. 저장된 행은 건드리지 않는다 — 끄는 것과 지우는 것은
    # 다른 일이고, 지우는 쪽은 사람이 판단할 몫이다.
    # 다시 켤 때는 이 값 하나(RANKING_SHIFT_ENABLED=true)면 된다.
    ranking_shift_enabled: bool = False

    # 랭크 변동을 집계할 시각들(KST, 0~23). 하루 한 번(아침 8시)에서 자정·정오 두 번으로
    # 늘렸다(요청) — 밤에 몰아친 경기 결과가 다음 날 아침까지 순위표에 안 잡혀 있었다.
    # 두 번 돌아도 카드가 두 배가 되진 않는다: 집계는 직전 스냅샷과 견줘 달라진 게 없으면
    # 아무 행도 남기지 않으므로(RankingShiftService.recompute_daily), 조용한 반나절은
    # 그냥 지나간다. 목표 시각을 놓쳐도 다음 확인 때 따라잡는 구조라(_rank_slot_start)
    # "자정엔 컨테이너가 잠들어 있다"는 옛 걱정은 더 이상 이 값을 묶지 않는다.
    # 환경변수(RANK_RECOMPUTE_HOURS="0,12")로 바꾼다 — 코드가 아니라 운영 리듬에 딸린 값이다.
    rank_recompute_hours: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [0, 12]
    )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("rank_recompute_hours", mode="before")
    @classmethod
    def _split_hours(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(h.strip()) for h in value.split(",") if h.strip()]
        return value

    @field_validator("rank_recompute_hours", mode="after")
    @classmethod
    def _check_hours(cls, value: list[int]) -> list[int]:
        # 빈 목록이면 스케줄러가 '구간'을 정할 수 없어 영영 안 돈다 — 잘못 준 환경변수가
        # 조용히 기능을 꺼 버리는 것보다 부팅 때 터지는 편이 낫다.
        if not value:
            raise ValueError("rank_recompute_hours는 최소 한 개여야 한다")
        if any(h < 0 or h > 23 for h in value):
            raise ValueError("rank_recompute_hours는 0~23 사이여야 한다")
        return sorted(set(value))

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> object:
        # Railway/Heroku 류 플랫폼은 DATABASE_URL을 postgres(ql)://로 주입한다.
        # asyncpg 드라이버를 쓰려면 postgresql+asyncpg:// 스킴이 필요하다.
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return "postgresql+asyncpg://" + value[len("postgres://") :]
            if value.startswith("postgresql://"):
                return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
