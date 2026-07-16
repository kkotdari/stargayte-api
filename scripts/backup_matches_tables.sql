-- 일회성 안전장치: "리플레이 전체 다운로드 -> 등록 테스트 -> 경기 전체 삭제 -> 재등록"
-- 순서로 진행하기 전에, matches/match_participants/match_results/replays 네 테이블을
-- 그대로 복제해 둔다. CREATE TABLE ... AS TABLE ...은 컬럼/데이터를 통째로 복사하지만
-- PK/FK/인덱스/시퀀스는 안 딸려온다 — 이 백업 테이블은 실제로 쓰는 용도가 아니라 나중에
-- 대조/복구용 스냅샷이라 그걸로 충분하다.
--
-- 실행: Railway 대시보드의 Postgres 서비스 > Query 탭에 붙여넣거나,
--       psql "$DATABASE_URL" -f scripts/backup_matches_tables.sql
--
-- 검증이 다 끝나면(재등록 결과가 문제없다고 확신되면) 아래 DROP 문들로 정리한다:
--   DROP TABLE IF EXISTS matches_bk, match_participants_bk, match_results_bk, replays_bk;

DROP TABLE IF EXISTS matches_bk;
DROP TABLE IF EXISTS match_participants_bk;
DROP TABLE IF EXISTS match_results_bk;
DROP TABLE IF EXISTS replays_bk;

CREATE TABLE matches_bk AS TABLE matches;
CREATE TABLE match_participants_bk AS TABLE match_participants;
CREATE TABLE match_results_bk AS TABLE match_results;
CREATE TABLE replays_bk AS TABLE replays;

-- 실행 직후 건수를 원본과 바로 대조해본다 — 하나라도 안 맞으면 위 CREATE 문 중 하나가
-- 조용히 실패했다는 뜻이니 이 스크립트를 다시 실행하기 전에 원인부터 확인한다.
SELECT 'matches' AS table_name, (SELECT count(*) FROM matches) AS original, (SELECT count(*) FROM matches_bk) AS backup
UNION ALL
SELECT 'match_participants', (SELECT count(*) FROM match_participants), (SELECT count(*) FROM match_participants_bk)
UNION ALL
SELECT 'match_results', (SELECT count(*) FROM match_results), (SELECT count(*) FROM match_results_bk)
UNION ALL
SELECT 'replays', (SELECT count(*) FROM replays), (SELECT count(*) FROM replays_bk);
