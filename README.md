# Stargayte API

[stargayte](../stargayte) 프론트엔드를 위한 백엔드입니다.

> FastAPI · SQLAlchemy 2.0(async) · Alembic · PostgreSQL · JWT 인증

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# DATABASE_URL, JWT_SECRET_KEY 필수
#   postgresql+asyncpg://<user>:<password>@localhost:5432/stargayte
createdb stargayte   # DB가 아직 없다면

alembic upgrade head
uvicorn app.main:app --reload --app-dir src --port 8000
```

- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

프론트(`npm run dev`, 기본 5173)와 같이 쓰려면 `.env`의 `CORS_ALLOW_ORIGINS`에 그 origin이
포함돼 있어야 합니다.

## 테스트

```bash
pytest   # DATABASE_URL을 SQLite로 override해서 실행 — Postgres 없이도 동작
```
