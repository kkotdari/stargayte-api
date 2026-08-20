"""경기번호는 **언제나 열두 자리**다(YYMMDDHHMMSS).

예전에는 뒤에 두 자리 일련번호를 붙여 열네 자리였는데, 실제 경기 시각은 초까지 가면 겹칠
일이 거의 없어 그 두 자리가 대부분 00으로 낭비됐다(요청: 빼기).

그럼 시각을 모르는 경기(수기 등록)는 — **없는 시각**을 준다: `YYMMDD99####`. 99시는 실제로
없는 시각이라 리플레이가 만든 번호와 절대 안 부딪히고, 번호만 봐도 "시각을 모르는 경기"임이
드러난다. 자릿수는 그대로 열둘이다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.domain.game_results.service import _match_no_base

_KST = timezone(timedelta(hours=9))


def test_리플레이가_있으면_그_시각_열두_자리() -> None:
    got = _match_no_base(date(2026, 8, 16), datetime(2026, 8, 16, 23, 59, 3, tzinfo=_KST))
    assert got == "260816235903"
    assert len(got) == 12


def test_시각을_모르면_None이다() -> None:
    """번호를 붙이는 것은 서비스 몫이다 — DB를 봐야 다음 빈 자리를 알 수 있다."""
    assert _match_no_base(date(2026, 8, 16), None) is None


def test_날짜가_어긋난_시각은_안_믿는다() -> None:
    """수기 등록은 '지금'을 실어 보낸다 — 경기 날짜와 다르면 그 값은 경기 시각이 아니다."""
    assert _match_no_base(date(2026, 4, 1), datetime(2026, 8, 20, 12, 0, tzinfo=_KST)) is None
