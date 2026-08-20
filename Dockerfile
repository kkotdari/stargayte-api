# ── 1단계: 참값 덤퍼(bwdump) 빌드 ──────────────────────────────────────────────
# 리플레이를 실제로 시뮬레이션해 유닛의 참 자리를 뽑는 도구다(openbw/README.md).
# OpenBW 원본은 라이선스 표기가 없어 리포에 안 두고 여기서 **핀된 커밋으로** 받아 온다.
# 헤더만 있는 라이브러리라 zlib 말고는 딸린 것이 없다.
FROM debian:bookworm-slim AS bwdump

ARG OPENBW_COMMIT=4b046d5f65302b10cb0a745f0fecd37ec85b20a8

RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ git ca-certificates zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY openbw/ ./openbw-src/
RUN git clone --filter=blob:none https://github.com/OpenBW/openbw.git openbw \
    && cd openbw \
    && git checkout "${OPENBW_COMMIT}" \
    && git apply ../openbw-src/openbw-scr.patch \
    && cd .. \
    && cp openbw-src/bwdump.cpp openbw-src/modern_replay.h . \
    && g++ -std=c++17 -O2 -w -I openbw -o bwdump bwdump.cpp -lz

# ── 2단계: 서비스 ────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# bwdump는 zlib에만 기댄다(libstdc++·libgcc는 베이스에 이미 있다).
RUN apt-get update && apt-get install -y --no-install-recommends zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=bwdump /build/bwdump /usr/local/bin/bwdump

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
