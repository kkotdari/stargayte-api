"""랭크 변동 하루 집계가 '언제 돌아야 하나'를 판단하는 규칙(app.main._rank_recompute_due).

예전에는 "다음 자정까지 남은 초만큼 sleep" 하나였는데 실제로 안 돌았다(지적) — 그 순간에
프로세스가 살아 있어야만 도는 방식이라, 새벽에 컨테이너가 잠들거나 배포로 재시작되면 그
하루를 통째로 건너뛰었다. 이제는 짧은 주기로 깨어나 '밀린 일'을 찾는다. 그 판단이 정확해야
① 목표 시각 전에 돌지 않고 ② 놓친 날을 다음 접속 때 따라잡고 ③ 같은 날 두 번 돌지 않는다.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.main import _rank_recompute_due

KST = ZoneInfo("Asia/Seoul")


def _kst(y: int, m: int, d: int, h: int) -> datetime:
    return datetime(y, m, d, h, 0, tzinfo=KST)


def test_hour_comes_from_settings():
    # 자정이 아니라 아침이 기본이다(요청: 08시로) — 새벽엔 아무도 앱을 안 써서 그 순간을
    # 놓치기 쉽다.
    assert settings.rank_recompute_hour == 8


def test_not_due_before_target_hour(monkeypatch):
    _freeze(monkeypatch, _kst(2026, 7, 30, 7))
    # 목표 시각(08시) 전이면 어제 것을 안 남겼어도 기다린다.
    assert _rank_recompute_due(_kst(2026, 7, 28, 9).astimezone(UTC), None) is False


def test_due_when_yesterday_was_the_last_one(monkeypatch):
    _freeze(monkeypatch, _kst(2026, 7, 30, 9))
    assert _rank_recompute_due(_kst(2026, 7, 29, 8).astimezone(UTC), None) is True


def test_due_when_never_run(monkeypatch):
    _freeze(monkeypatch, _kst(2026, 7, 30, 9))
    assert _rank_recompute_due(None, None) is True


def test_not_due_twice_in_a_day(monkeypatch):
    """오늘 이미 남겼으면 다시 돌지 않는다 — 아침에 한 번 돈 뒤 낮에 경기가 등록되고
    재시작이 걸려도 같은 날 두 번째 변동 카드가 뜨면 안 된다(하루에 카드 하나)."""
    _freeze(monkeypatch, _kst(2026, 7, 30, 15))
    assert _rank_recompute_due(_kst(2026, 7, 30, 8).astimezone(UTC), None) is False


def test_not_due_again_after_a_no_change_day(monkeypatch):
    """순위가 그대로인 날은 아무 행도 안 남으므로 DB만 보면 계속 '안 했다'로 읽힌다 —
    이 프로세스에서 오늘 이미 해 봤다는 기억으로 10분마다 헛도는 것을 막는다."""
    _freeze(monkeypatch, _kst(2026, 7, 30, 9))
    old = _kst(2026, 7, 20, 8).astimezone(UTC)
    assert _rank_recompute_due(old, None) is True
    assert _rank_recompute_due(old, _kst(2026, 7, 30, 9).date()) is False
    # 다음 날이 되면 그 기억은 더 이상 막지 않는다.
    _freeze(monkeypatch, _kst(2026, 7, 31, 9))
    assert _rank_recompute_due(old, _kst(2026, 7, 30, 9).date()) is True


def test_naive_timestamp_is_read_as_utc(monkeypatch):
    """created_at이 tz 없이 저장되는 DB(SQLite)에서도 KST 날짜로 옳게 비교해야 한다.
    KST 30일 오전 8시는 UTC로 29일 23시라, 그냥 날짜만 보면 '어제'로 잘못 읽힌다."""
    _freeze(monkeypatch, _kst(2026, 7, 30, 15))
    naive_utc = _kst(2026, 7, 30, 8).astimezone(UTC).replace(tzinfo=None)
    assert naive_utc.date() == datetime(2026, 7, 29).date()  # UTC로는 어제
    assert _rank_recompute_due(naive_utc, None) is False     # 그래도 '오늘 이미 함'


def _freeze(monkeypatch, when: datetime) -> None:
    """datetime.now(KST)만 고정한다 — main이 부르는 그 한 곳이다."""
    import app.main as main

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(main, "datetime", _Now)


def test_interval_is_short_enough_to_catch_a_wake_up():
    """잠에서 깬 직후를 빨리 잡아야 하므로 확인 주기는 짧게 둔다 — 확인 자체는 스냅샷
    한 줄을 읽는 것뿐이라 부담이 없다."""
    from app.main import _RANK_CHECK_INTERVAL_SEC

    assert 0 < _RANK_CHECK_INTERVAL_SEC <= 15 * 60


def test_target_hour_is_respected_across_a_day(monkeypatch):
    """하루를 시간별로 훑어 08시부터만 돌게 되는지 확인한다."""
    yesterday = _kst(2026, 7, 29, 8).astimezone(UTC)
    due = []
    for hour in range(24):
        _freeze(monkeypatch, _kst(2026, 7, 30, hour))
        if _rank_recompute_due(yesterday, None):
            due.append(hour)
    assert due == list(range(settings.rank_recompute_hour, 24))
