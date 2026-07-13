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

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
