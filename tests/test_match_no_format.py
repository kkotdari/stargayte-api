"""경기번호는 초까지 열두 자리다(요청: 뒤 두 자리 빼기).

예전에는 늘 두 자리 일련번호를 붙여 열네 자리였는데, 실제 경기 시각은 초까지 가면 겹칠
일이 거의 없어 그 두 자리가 대부분 `00`으로 낭비됐다. 다만 **겹칠 때 붙는 자리**까지
없애면 안 된다 — 수기 등록은 시각을 몰라 하루치가 한 번호(YYMMDD000000)로 모인다.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.domain.game_results.service import _match_no_base


def test_리플레이가_있으면_초까지_열두_자리() -> None:
    got = _match_no_base(date(2026, 8, 16), datetime(2026, 8, 16, 23, 59, 3, tzinfo=timezone.utc))
    # UTC 23:59:03 → KST 다음 날 08:59:03이라 날짜가 어긋난다 → 자정으로 떨어진다.
    assert len(got) == 12


def test_수기_등록은_자정_기준() -> None:
    assert _match_no_base(date(2026, 8, 16), None) == "260816000000"
    assert len(_match_no_base(date(2026, 8, 16), None)) == 12
