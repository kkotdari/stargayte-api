# Stargayte API

[stargayte](../stargayte) 프론트엔드를 위한 백엔드입니다.

> FastAPI · SQLAlchemy 2.0(async) · PostgreSQL/SQLite · JWT 인증

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# DATABASE_URL, JWT_SECRET_KEY 필수
#   가장 간단한 로컬 시작: sqlite+aiosqlite:///./var/stargayte.db
#   PostgreSQL 사용 시:    postgresql+asyncpg://<user>:<password>@localhost:5432/stargayte
#                          (createdb stargayte 로 데이터베이스만 먼저 만들어 두세요)

uvicorn app.main:app --reload --app-dir src --port 8000
```

별도 마이그레이션 도구는 쓰지 않습니다 — 서버가 부팅하면서 없는 테이블을 자동으로
만듭니다(`create_all`). 이미 데이터가 있는 DB에 붙이면 아무 것도 바꾸지 않고 그대로
사용합니다.

- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

프론트(`npm run dev`, 기본 5173)와 같이 쓰려면 `.env`의 `CORS_ALLOW_ORIGINS`에 그 origin이
포함돼 있어야 합니다.

## 테스트

```bash
pytest   # DATABASE_URL을 SQLite로 override해서 실행 — Postgres 없이도 동작
```
