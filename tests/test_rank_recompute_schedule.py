"""랭크 변동 집계가 '언제 돌아야 하나'를 판단하는 규칙(app.main의 구간 계산).

예전에는 "다음 자정까지 남은 초만큼 sleep" 하나였는데 실제로 안 돌았다(지적) — 그 순간에
프로세스가 살아 있어야만 도는 방식이라, 새벽에 컨테이너가 잠들거나 배포로 재시작되면 그
하루를 통째로 건너뛰었다. 이제는 짧은 주기로 깨어나 '밀린 일'을 찾는다. 그 판단이 정확해야
① 목표 시각 전에 돌지 않고 ② 놓친 구간을 다음 접속 때 따라잡고 ③ 한 구간에 두 번 돌지 않는다.

집계는 하루 한 번(아침)에서 자정·정오 두 번으로 늘었다(요청). 그래서 '날짜'가 아니라
'구간'이 판단의 단위가 됐다 — 날짜만 보면 오전 것과 오후 것을 가를 수 없다.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.main import _rank_recompute_due, _rank_slot_start

KST = ZoneInfo("Asia/Seoul")


def _kst(y: int, m: int, d: int, h: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=KST)


def test_hours_come_from_settings():
    # 자정과 정오, 하루 두 번이다(요청).
    assert settings.rank_recompute_hours == [0, 12]


def test_slot_is_the_last_target_hour_passed():
    assert _rank_slot_start(_kst(2026, 7, 30, 0)) == _kst(2026, 7, 30, 0)
    assert _rank_slot_start(_kst(2026, 7, 30, 11, 59)) == _kst(2026, 7, 30, 0)
    assert _rank_slot_start(_kst(2026, 7, 30, 12)) == _kst(2026, 7, 30, 12)
    assert _rank_slot_start(_kst(2026, 7, 30, 23, 59)) == _kst(2026, 7, 30, 12)


def test_slot_before_the_first_hour_belongs_to_yesterday(monkeypatch):
    """오늘 첫 목표 시각도 안 지났으면 어제의 마지막 구간이다 — 자정이 첫 시각인 지금은
    해당 없지만, 운영이 시각을 옮기면(예: 8시/20시) 새벽 3시가 여기에 걸린다."""
    monkeypatch.setattr(settings, "rank_recompute_hours", [8, 20])
    assert _rank_slot_start(_kst(2026, 7, 30, 3)) == _kst(2026, 7, 29, 20)


def test_due_when_never_run():
    assert _rank_recompute_due(None, _kst(2026, 7, 30, 12)) is True


def test_due_when_last_snapshot_is_older_than_the_slot():
    # 자정 구간에 남긴 뒤 정오가 됐다 — 정오 구간 몫이 아직 없으므로 돌 차례다.
    last = _kst(2026, 7, 30, 0, 5).astimezone(UTC)
    assert _rank_recompute_due(last, _kst(2026, 7, 30, 12)) is True


def test_not_due_twice_in_a_slot():
    """이 구간에 이미 남겼으면 다시 돌지 않는다 — 정오에 한 번 돈 뒤 오후에 경기가
    등록되고 재시작이 걸려도 같은 구간에 두 번째 변동 카드가 뜨면 안 된다."""
    last = _kst(2026, 7, 30, 12, 3).astimezone(UTC)
    assert _rank_recompute_due(last, _kst(2026, 7, 30, 12)) is False


def test_missed_slot_is_caught_up_not_skipped():
    """정오에 컨테이너가 잠들어 있었다면, 그 뒤 처음 깨어난 때(구간은 여전히 정오)에 돈다."""
    last = _kst(2026, 7, 29, 12).astimezone(UTC)
    slot = _rank_slot_start(_kst(2026, 7, 30, 18))
    assert slot == _kst(2026, 7, 30, 12)
    assert _rank_recompute_due(last, slot) is True


def test_naive_timestamp_is_read_as_utc():
    """created_at이 tz 없이 저장되는 DB(SQLite)에서도 KST로 옳게 비교해야 한다.
    KST 30일 정오는 UTC로 30일 03시라, 그냥 시각만 보면 '정오 전'으로 잘못 읽힌다."""
    naive_utc = _kst(2026, 7, 30, 12, 5).astimezone(UTC).replace(tzinfo=None)
    assert naive_utc.hour == 3  # UTC로는 새벽
    assert _rank_recompute_due(naive_utc, _kst(2026, 7, 30, 12)) is False


def test_two_slots_a_day_across_a_day():
    """하루를 시간별로 훑어 구간이 자정/정오 둘로만 갈리는지 확인한다."""
    slots = {_rank_slot_start(_kst(2026, 7, 30, h)) for h in range(24)}
    assert slots == {_kst(2026, 7, 30, 0), _kst(2026, 7, 30, 12)}


def test_interval_is_short_enough_to_catch_a_wake_up():
    """잠에서 깬 직후를 빨리 잡아야 하므로 확인 주기는 짧게 둔다 — 확인 자체는 스냅샷
    한 줄을 읽는 것뿐이라 부담이 없다."""
    from app.main import _RANK_CHECK_INTERVAL_SEC

    assert 0 < _RANK_CHECK_INTERVAL_SEC <= 15 * 60
