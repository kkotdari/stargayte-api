-- ============================================================
-- Stargayte - PostgreSQL DDL
--
-- 원본: stargayte 프론트엔드 도메인 타입(src/types/index.ts)을 기반으로 설계.
-- ENUM 값은 네이티브 PostgreSQL ENUM 대신 VARCHAR + CHECK 제약으로 두어,
-- 값 추가/변경이나 타 DB(예: MySQL/SQLite)로의 이관 시 마이그레이션 부담을 줄임.
--
-- 애플리케이션(stargayte-api)에서는 이 스키마를 Alembic 마이그레이션으로
-- 관리하며, 이 파일은 참고/수동 셋업용 DDL 스냅샷이다.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 공용 트리거 함수: updated_at 자동 갱신
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------
-- members: 회원 (로그인 계정 겸용)
-- ------------------------------------------------------------
CREATE TABLE members (
    pk            BIGSERIAL     PRIMARY KEY,                  -- 불변 내부 식별자 (FK/토큰이 참조)
    id            VARCHAR(64)   NOT NULL UNIQUE,               -- 로그인 아이디 (변경 가능)
    password_hash VARCHAR(255)  NOT NULL,                     -- bcrypt 해시
    nickname      VARCHAR(100)  NOT NULL,
    battletag     VARCHAR(50)   NOT NULL UNIQUE,
    name          VARCHAR(100)  NOT NULL DEFAULT '',
    insta         VARCHAR(100)  NOT NULL DEFAULT '',
    avatar_url    TEXT,                                       -- 정적 파일 서빙 URL (nullable)
    role          VARCHAR(4)    NOT NULL DEFAULT '0203'        -- 0201=슈퍼관리자, 0202=관리자, 0203=회원, 0204=테스터
                      CHECK (role IN ('0201', '0202', '0203', '0204')),
    status        VARCHAR(20)   NOT NULL DEFAULT 'pending'     -- 가입 시 승인 대기, 관리자가 활성/정지 전환, 본인 탈퇴 시 withdrawn
                      CHECK (status IN ('pending', 'active', 'suspended', 'withdrawn')),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    created_by    BIGINT        REFERENCES members(pk) ON DELETE SET NULL,  -- 등록자
    updated_by    BIGINT        REFERENCES members(pk) ON DELETE SET NULL   -- 수정자
);

CREATE TRIGGER trg_members_updated_at
    BEFORE UPDATE ON members
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------
-- matches: 경기
-- ------------------------------------------------------------
CREATE TABLE matches (
    id             BIGSERIAL     PRIMARY KEY,
    match_date     DATE          NOT NULL,
    result         VARCHAR(10)                                  -- NULL = 아직 결과 없음(예약/공식 경기)
                       CHECK (result IN ('team1', 'team2', 'draw')),
    status         VARCHAR(20)   NOT NULL DEFAULT 'completed'    -- scheduled=예약, canceled=취소, completed=결과 확정
                       CHECK (status IN ('scheduled', 'canceled', 'completed')),
    official_type  VARCHAR(4)    NOT NULL DEFAULT '0302'         -- 0301=공식(예약 거쳐 등록), 0302=비공식(경기기록에서 바로 등록)
                       CHECK (official_type IN ('0301', '0302')),
    note           TEXT          NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    created_by     BIGINT        REFERENCES members(pk) ON DELETE SET NULL,
    updated_by     BIGINT        REFERENCES members(pk) ON DELETE SET NULL
);

CREATE INDEX idx_matches_match_date ON matches (match_date);

CREATE TRIGGER trg_matches_updated_at
    BEFORE UPDATE ON matches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------
-- match_participants: 경기별 팀 슬롯 (팀1/팀2에 참가한 회원 + 종족 + 순서)
-- ------------------------------------------------------------
CREATE TABLE match_participants (
    id         BIGSERIAL    PRIMARY KEY,
    match_id   BIGINT       NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team       VARCHAR(5)   NOT NULL
                   CHECK (team IN ('team1', 'team2')),
    "position" SMALLINT     NOT NULL,                          -- 팀 내 표시/입력 순서 (0부터)
    member_pk  BIGINT       NOT NULL REFERENCES members(pk) ON DELETE RESTRICT,  -- members.id(로그인 아이디)가 아닌 불변 pk 참조
    race       VARCHAR(20)  NOT NULL
                   CHECK (race IN ('테란', '프로토스', '저그', '랜덤')),
    created_by BIGINT       REFERENCES members(pk) ON DELETE SET NULL,
    updated_by BIGINT       REFERENCES members(pk) ON DELETE SET NULL,
    UNIQUE (match_id, team, "position")
);

CREATE INDEX idx_match_participants_match_id  ON match_participants (match_id);
CREATE INDEX idx_match_participants_member_pk ON match_participants (member_pk);

-- ------------------------------------------------------------
-- match_attachments: 경기당 첨부파일 0..1개
-- ------------------------------------------------------------
CREATE TABLE match_attachments (
    id           BIGSERIAL    PRIMARY KEY,
    match_id     BIGINT       NOT NULL UNIQUE REFERENCES matches(id) ON DELETE CASCADE,
    file_name    VARCHAR(255) NOT NULL,                        -- 원본 파일명
    file_path    TEXT         NOT NULL,                        -- 스토리지 상 저장 경로/키
    content_type VARCHAR(100),
    file_size    INTEGER,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by   BIGINT       REFERENCES members(pk) ON DELETE SET NULL,
    updated_by   BIGINT       REFERENCES members(pk) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- official_matches: 공식 경기 예약의 "시간" 메타데이터만 담는 얇은 사이드 테이블.
-- matches가 소스오브트루스이고(날짜/팀/비고 등은 전부 matches에 있음), 여기는 matches.id만
-- 참조하며 예약 시간 관련 정보만 갖는다. 결과가 확정(matches.status -> completed)되면 이 행은
-- 지운다.
-- ------------------------------------------------------------
CREATE TABLE official_matches (
    id         BIGSERIAL    PRIMARY KEY,
    match_id   BIGINT       NOT NULL UNIQUE REFERENCES matches(id) ON DELETE CASCADE,
    match_time VARCHAR(5),                                   -- "HH:MM". NULL이면 시간 미정
    time_tbd   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_official_matches_updated_at
    BEFORE UPDATE ON official_matches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------
-- official_match_participants: 공식 경기 예약의 팀 슬롯 (match_participants와 동일한 구조)
-- ------------------------------------------------------------
CREATE TABLE official_match_participants (
    id                 BIGSERIAL    PRIMARY KEY,
    official_match_id  BIGINT       NOT NULL REFERENCES official_matches(id) ON DELETE CASCADE,
    team               VARCHAR(5)   NOT NULL
                           CHECK (team IN ('team1', 'team2')),
    "position"         SMALLINT     NOT NULL,
    member_pk          BIGINT       NOT NULL REFERENCES members(pk) ON DELETE RESTRICT,
    race               VARCHAR(20)  NOT NULL
                           CHECK (race IN ('테란', '프로토스', '저그', '랜덤')),
    created_by         BIGINT       REFERENCES members(pk) ON DELETE SET NULL,
    updated_by         BIGINT       REFERENCES members(pk) ON DELETE SET NULL,
    UNIQUE (official_match_id, team, "position")
);

CREATE INDEX idx_official_match_participants_official_match_id
    ON official_match_participants (official_match_id);
CREATE INDEX idx_official_match_participants_member_pk
    ON official_match_participants (member_pk);

-- ------------------------------------------------------------
-- race_icons: 관리자 설정 - 종족(테란/프로토스/저그/랜덤)별 아이콘
-- ------------------------------------------------------------
CREATE TABLE race_icons (
    base_race  VARCHAR(10) PRIMARY KEY
                   CHECK (base_race IN ('테란', '프로토스', '저그', '랜덤')),
    icon_type  VARCHAR(10) NOT NULL
                   CHECK (icon_type IN ('text', 'image')),
    icon_value TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by BIGINT      REFERENCES members(pk) ON DELETE SET NULL,
    updated_by BIGINT      REFERENCES members(pk) ON DELETE SET NULL
);

CREATE TRIGGER trg_race_icons_updated_at
    BEFORE UPDATE ON race_icons
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 기본 아이콘 시드 (constants/races.ts 의 DEFAULT_RACE_ICONS 와 동일)
INSERT INTO race_icons (base_race, icon_type, icon_value) VALUES
    ('테란',   'text', 'T'),
    ('프로토스', 'text', 'P'),
    ('저그',   'text', 'Z'),
    ('랜덤',   'text', 'R');

COMMIT;
