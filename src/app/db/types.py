from sqlalchemy import BigInteger, Integer

# SQLite는 "INTEGER PRIMARY KEY" 컬럼만 rowid 자동증가로 인식하고 BIGINT PK는 채워주지
# 않는다. with_variant로 SQLite에서만 Integer를 쓰게 하면 Postgres(BIGSERIAL)와
# SQLite(rowid autoincrement) 양쪽에서 자동증가가 정상 동작한다.
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")
