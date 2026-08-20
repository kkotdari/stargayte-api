import asyncio
import base64
import io
import logging
import re
import tempfile
import zipfile
from collections import OrderedDict, defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.game_results import openbw
from app.domain.game_results.models import (
    GameResult,
    GameResultParticipant,
    GameOutcome,
    Replay,
    MinimapImage,
    ReplayMap,
)
from app.domain.game_results.rating import MU0, SIGMA0, RatingEngine
from app.domain.game_results.repository import GameResultRepository
from app.domain.game_results.schemas import (
    COMPUTER_ID_PREFIX,
    BuildMix,
    UNREGISTERED_ID_PREFIX,
    GameResultAuthor,
    GameResultOut,
    GameResultReplayMerge,
    GameResultSlot,
    GameResultWrite,
    MemberStatsEntry,
    RaceStatsEntry,
    RatingHistoryResponse,
    ReplayMapData,
    MapCatalog,
    MapCatalogEntry,
    MinimapAssignWrite,
    MinimapChoice,
    MinimapImageOut,
    MinimapImageWrite,
    ReplayMapLinkWrite,
    ReplayMapOut,
    ReplayOut,
    ReplayUpload,
    SummaryRewrite,
    RivalryPairOut,
    RivalryResponse,
    is_computer_slot,
    is_placeholder_slot,
    is_unregistered_slot,
)
from app.domain.members.models import Member, ReplayAlias
from app.domain.members.repository import MemberRepository
from app.storage.base import FileStorage
from app.storage.data_url import decode_data_url, is_data_url

# 실제 경기결과에 저장되는 종족(슬롯 등록 시 "랜덤"은 막혀 있다) — 종족별 통계 병기 기준.
BASE_RACES = ("테란", "프로토스", "저그")

# 유효APM/유효커맨드 이상치 제외 — 한 회원의 여러 경기 중 그 회원의 다른 경기들과 편차가
# 너무 심한 경기(리플레이 파싱 오류, 접속 종료 직전 렉 등으로 튀는 값)를 그 항목 평균에서만
# 뺀다. 표본이 너무 적으면(_OUTLIER_MIN_SAMPLES 미만) 뭐가 "편차가 심한지" 판단할 근거가
# 부족해 왜곡 위험이 크므로 그대로 둔다.
#
# 평균/표준편차가 아니라 중앙값(median)/MAD(중앙값 절대편차)로 이상치를 판단한다 — 평균과
# 표준편차는 이상치 값 자신이 계산에 끼어들어가 둘 다 함께 끌어올려버려서, 표본이 적을 때
# (기준선인 5~6경기) 그 이상치 스스로가 "평균에서 표준편차 2배 이내"를 통과해 버젓이 살아남는
# 문제(마스킹 효과)가 있었다. 중앙값과 MAD는 이상치 한두 개로는 거의 흔들리지 않아 표본이
# 적어도 안정적으로 잡아낸다.
_OUTLIER_MIN_SAMPLES = 5
_OUTLIER_Z = 2.0
# 정규분포를 가정할 때 표준편차 1에 대응하는 MAD 값의 역수(1/Φ⁻¹(0.75) ≈ 1.4826) — MAD에
# 곱해서 "이 분포가 정규분포였다면 표준편차가 이 정도였을" 스케일로 맞춰주면, 기존에 쓰던
# _OUTLIER_Z(2배) 기준값을 그대로 재사용할 수 있다.
_MAD_TO_STDEV = 1.4826


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2


def _outlier_keep_mask(values: list[float]) -> list[bool]:
    """values와 같은 길이의 bool 목록 — 중앙값에서 (표준편차 스케일로 환산한) MAD의
    _OUTLIER_Z배를 넘게 벗어난 값만 False. 표본 부족/MAD 0(값이 거의 다 같음)/전부 이상치로
    잡히는(방어적) 경우는 전부 True로 그대로 둔다."""
    n = len(values)
    if n < _OUTLIER_MIN_SAMPLES:
        return [True] * n
    med = _median(values)
    mad = _median([abs(v - med) for v in values]) * _MAD_TO_STDEV
    if mad == 0:
        return [True] * n
    mask = [abs(v - med) <= _OUTLIER_Z * mad for v in values]
    return mask if any(mask) else [True] * n


# 이상치 제거를 적용하는 지표 — 화면에 평균으로 나가는 다섯 가지 전부다. (응답 필드 이름,
# 경기별 원본 행의 컬럼 이름).
#
# 한때 APM·유효APM·유효커맨드 셋만 이 처리를 받고 커맨드·생산 둘은 SQL 단순 평균이 그대로
# 나갔는데, 다섯 값 모두 같은 리플레이 파싱에서 같이 나오는 값이라 한 경기가 튀면 대개 같이
# 튄다 — 유효커맨드만 걷어내고 총커맨드·생산은 그대로 두면 같은 경기가 한쪽 평균에서만
# 빠져서, 화면에 나란히 놓인 숫자끼리 앞뒤가 안 맞았다(예: 유효커맨드는 정상인데 총커맨드만
# 혼자 부풀어 유효/전체 비율이 말이 안 되는 값이 된다).
#
# APM이 특히 잘 튀는 건 '분당' 값이기 때문이다: 20초 만에 끝난 기록에 시작 직후의 핫키 연타가
# 그대로 들어가면 그 판 하나가 수천이 된다. 그런 판 하나가 단순 평균에 섞이면 열 판 넘게
# 정상으로 친 사람도 700대가 된다(실측한 화면에서 커맨드 평균 2314에 APM 732 — 역산하면 평균
# 경기 길이가 3.2분이라는 말이 안 되는 값이었다). 커맨드·생산은 경기당 총합이라 방향이 반대다
# — 아주 짧은 판은 낮은 쪽으로, 아주 긴 판은 높은 쪽으로 끌어당긴다. 어느 쪽이든 그 회원의
# 다른 경기들과 편차가 심한 판이라는 점은 같아서 같은 자를 쓴다.
# 이 둘은 이미 '분당'이라 경기 길이와 무관하다 — 그대로 평균 낸다.
_TRIMMED_RATES = (
    ("avg_apm", "apm"),
    ("avg_eapm", "eapm"),
)
# 이쪽은 경기당 총합이라 긴 판일수록 그냥 커진다 — 분당으로 환산한다(요청: 게임시간에
# 영향을 받는 요소는 모두 분당).
#
# 분모가 경기 전체 길이인 것은 생산 구성(주요시간대 분당)과 다르다 — 여기 분자로 쓰는
# screp의 커맨드 수는 경기 하나당 총합 하나뿐이라, 구간을 좁히려면 커맨드 스트림을 한 번
# 더 훑어 사람별로 다시 세어야 한다. 그 값은 아직 없다.
#
# 경기마다 분당을 내서 평균 내지 않고 '총합 ÷ 총 시간'으로 낸다: 3분짜리 판과 40분짜리 판의 비율을 같은 무게로 섞으면 짧은 판 한 번이
# 그 사람의 값을 통째로 흔든다(생산 구성 비율을 합쳐서 내는 것과 같은 이유).
_TRIMMED_VOLUMES = (
    ("avg_cmd", "cmd_count"),
    ("avg_ecmd", "effective_cmd_count"),
    ("avg_build", "build_count"),
)


def _trimmed_avg(rows: list, attr: str) -> int | None:
    """경기별 원본 행에서 attr 값을 모아 이상치를 뺀 뒤 낸 단순 평균. 값이 있는 경기가
    하나도 없으면 None(0이 아니다 — 잰 적이 없다는 뜻이라 다른 말이다)."""
    values = [float(v) for v in (getattr(r, attr) for r in rows) if v is not None]
    if not values:
        return None
    mask = _outlier_keep_mask(values)
    kept = [v for v, keep in zip(values, mask) if keep]
    return round(sum(kept) / len(kept))


# 1분(초) — 시간에 끌려가는 값을 전부 분당으로 통일한다(요청: 단위 표시는 "단위/분").
# 한때 10분이었는데, 그 자리에 함께 서는 생산·건설·유닛 값이 주요시간대 분당으로 바뀌면서
# 같은 표 안에 10분당과 분당이 섞이게 됐다 — 자릿수가 커 보이는 것보다 자가 하나인 편이
# 낫다(프론트 replayBuildMix의 PER_WINDOW_SECONDS와 같은 값).
PER_WINDOW_SECONDS = 60


def _trimmed_per_min(rows: list, attr: str) -> int | None:
    """attr 총합을 총 경기시간으로 나눠 분당 값으로 낸다(요청). 길이를 안 잰 경기는 환산할
    자가 없으므로 분자·분모 양쪽에서 함께 뺀다 — 한쪽만 빼면 시간 없는 판의 값이 다른 판의
    시간에 얹혀 값이 부풀어 오른다.

    이상치 판정은 원래 값(경기당 총합)으로 한다 — '이 회원의 다른 판과 유독 다른 판'을 찾는
    일이라, 환산 뒤의 값으로 보면 길이가 반영돼 판정 자체가 달라진다."""
    pairs = [
        (float(getattr(r, attr)), float(r.duration_seconds))
        for r in rows
        if getattr(r, attr) is not None and r.duration_seconds
    ]
    if not pairs:
        return None
    mask = _outlier_keep_mask([v for v, _ in pairs])
    kept = [p for p, keep in zip(pairs, mask) if keep]
    seconds = sum(d for _, d in kept)
    if seconds <= 0:
        return None
    return round(sum(v for v, _ in kept) / seconds * PER_WINDOW_SECONDS)


# 지표 평균에서 아예 빼는 경기 길이 — 2분 미만은 '치른 판'으로 보지 않는다.
#
# 브루드워 1:1에서 가장 빠른 올인(4드론·BBS)도 2분에는 아직 안 붙는다. 그 전에 결과가 찍힌
# 기록은 오등록이거나 즉시 나간(드랍) 판이지 경기가 아니다. 그런 판은 다섯 지표를 전부 망가
# 뜨린다 — APM은 '분당' 값이라 시작 직후 핫키 연타만 들어가고 나눠지는 시간이 없어 수천으로
# 치솟고(실측 20~30초대), 커맨드·생산은 경기당 총합이라 반대로 바닥을 끌어내린다.
#
# 아래 _outlier_keep_mask(중앙값/MAD)와 겹치는 게 아니라 서로 다른 구멍을 막는다: MAD 쪽은
# 표본이 _OUTLIER_MIN_SAMPLES(5판) 미만이면 아예 동작하지 않아서, 서너 판만 뛴 회원의 20초
# 짜리 기록은 지금껏 그대로 평균에 들어갔다. 판수 문턱을 걷어내면서 서너 판만 뛴 회원도
# 그대로 표에 서게 됐는데, 거기가 정확히 MAD의 사각지대다. 경기 길이 기준은 표본이 한 판이어도
# 판단할 수 있어 그 구간을 메운다.
_MIN_DURATION_SECONDS = 120


def _mix_json(v: object) -> dict | None:
    """생산 구성을 DB에 넣을 dict로 — 검증을 지난 모델일 수도, 이미 dict일 수도 있다
    (등록 payload는 모델이지만, 다른 경로에서 만든 슬롯은 dict 그대로 온다).

    dict로 온 것도 모델을 한 번 거쳐 보낸다. 그래야 어느 경로로 들어왔든 저장되는 키가
    한 가지(필드명)로 통일된다 — camelCase 별칭 그대로 저장되면 나중에 합산(_build_mix_agg)이
    아는 키를 하나도 못 찾아 통계가 조용히 0이 된다."""
    if v is None:
        return None
    if not hasattr(v, "model_dump"):
        try:
            v = BuildMix.model_validate(v)
        except PydanticValidationError:
            # 우리가 아는 형식이 아니면 아예 안 싣는다 — 반쯤 맞는 값이 통계에 섞이는 것보다 낫다.
            return None
    return v.model_dump()  # type: ignore[union-attr]


def _trimmed_avgs(rows: list) -> dict[str, int | None]:
    """_TRIMMED_RATES·_TRIMMED_VOLUMES 다섯 항목을 RaceStatsEntry.model_copy(update=...)에 바로 넣을
    모양으로 낸다 — 종족별/전체 두 곳에서 같은 목록을 쓰게 해서 한쪽만 빠지는 일을 막는다.

    지표 평균에만 영향을 준다 — 전적(판수/승/무/승률)은 짧은 판도 그대로 센다. 나간 판도
    진 건 진 거라 전적에서까지 빼면 다른 이야기가 된다."""
    # duration_seconds가 NULL인 기록은 남긴다 — 짧다는 게 아니라 잰 적이 없다는 뜻이라
    # 뺄 근거가 없다(실데이터에는 없지만 컬럼이 nullable이고 수동 등록 경로로 생길 수 있다).
    rows = [
        r for r in rows
        if r.duration_seconds is None or r.duration_seconds >= _MIN_DURATION_SECONDS
    ]
    out: dict[str, object] = {field: _trimmed_avg(rows, attr) for field, attr in _TRIMMED_RATES}
    out.update({field: _trimmed_per_min(rows, attr) for field, attr in _TRIMMED_VOLUMES})
    out.update(_build_mix_agg(rows))
    return out  # type: ignore[return-value]


# 생산 구성 합계(요청: 도넛 셋 + 초반 일꾼) — 경기마다 비율을 내서 평균 내지 않고 통째로
# 더한다. 3분짜리 판과 40분짜리 판의 비율을 같은 무게로 섞으면 짧은 판 한 번이 그 사람의
# 그림을 흔들기 때문이다. 초반 일꾼만은 '경기당 몇 기'라야 뜻이 서서 따로 나눠 낸다.
# 공/방/실드는 다른 항목과 분모가 다르다(아래 _UPGRADE_FIELDS) — 여기서는 뺀다.
_BUILD_MIX_FIELDS = (
    "b_prod", "b_def", "u_basic", "u_adv", "u_caster", "u_ground", "u_air", "worker5",
    # 주요시간대 안에서만 센 건물·유닛 커맨드 수 — 도넛 옆 "분당 몇 채/몇 기"의 분자다.
    # 위 구성비 항목들과 자가 다르다: 그쪽은 경기 전체, 이쪽은 주요시간대(요청).
    "core_build", "core_unit",
)
# 공/방/실드 단계 — 이것만 '충분히 긴 경기'에서만 센다(요청: 일정 시간 이상 경기 대상).
# 브루드워에서 한 줄을 3단계까지 올리는 연구 시간만 11분이 넘고, 그 연구는 건물과 가스가
# 갖춰진 뒤에야 시작된다 — 20분이 안 되는 판은 구조적으로 3이 나올 수 없어서, 분모에 넣으면
# 평균이 실제보다 낮게 나온다(지적: 공방업이 너무 낮게 나온다).
_UPGRADE_FIELDS = ("up_gw", "up_ga", "up_aw", "up_aa", "up_sh")
_MIN_UPGRADE_SECONDS = 20 * 60
# 사전으로 쌓이는 갈래(건물·유닛·스킬 원장) — 수를 더하는 위 항목들과 달리 이름별로 더한다.
# 값과 함께 '그 이름이 나온 경기들의 길이 합'도 센다 — 화면이 총합을 이 시간으로 나눠 분당
# 값을 낸다(요청). 그 이름이 안 나온 판의 시간은 안 얹는다: 얹으면 프로토스만 쓰는 기술의
# 값이 종족 비율만큼 깎인다.
# 분모가 주요시간대가 아니라 경기 전체 길이인 이유: 이 원장들은 경기 전체로 세기 때문이다
# (요청: 도넛·Top5는 전체 경기) — 분자와 분모의 자가 같아야 한다.
_BUILD_MIX_TALLIES = {"buildings": "building_secs", "units": "unit_secs", "skills": "skill_secs"}


def _build_mix_agg(rows: list) -> dict[str, object]:
    mixed = [r for r in rows if getattr(r, "build_mix", None)]
    if not mixed:
        return {
            "build_mix": None, "avg_worker5": None,
            "mix_plays": None, "mix_seconds": None, "up_plays": None,
        }
    total: dict[str, object] = {f: 0 for f in (*_BUILD_MIX_FIELDS, *_UPGRADE_FIELDS)}
    tallies: dict[str, dict[str, int]] = {t: {} for t in _BUILD_MIX_TALLIES}
    tallies.update({k: {} for k in _BUILD_MIX_TALLIES.values()})
    seconds = 0
    up_plays = 0
    # 업그레이드 줄별 합과 '그 줄이 실린 경기 수' — 줄이 종족마다 다르므로 분모도 줄마다
    # 따로 세야 한다(요청: 종족별로 보여주기). 하나로 세면 종족이 섞인 기간에 한 줄의
    # 평균이 다른 종족 경기 수만큼 눌린다.
    up_lines: dict[str, int] = {}
    up_line_plays: dict[str, int] = {}
    for r in mixed:
        m = r.build_mix
        # 저장은 JSON이라 무엇이든 들어올 수 있다 — 아는 키의 숫자만 더한다.
        if not isinstance(m, dict):
            continue
        # 분모는 경기 전체가 아니라 그 판의 주요시간대다(요청) — 분자(위 커맨드 수)도 이미
        # 그 구간 것만 세어 저장돼 있어서, 전체 길이로 나누면 분자와 분모의 자가 어긋난다.
        # 옛 경기에는 이 값이 없다(재분석 전) — 그런 판은 0이라 분모에 안 얹히고, 그 결과
        # 분모가 하나도 안 쌓이면 화면이 그 칸을 비운다.
        core = m.get("core_seconds")
        dur = int(core) if isinstance(core, (int, float)) and core > 0 else 0
        seconds += dur
        # Top5 원장의 분모는 경기 전체 길이다 — 그 원장이 경기 전체로 세어 담기기 때문이다.
        full = r.duration_seconds if isinstance(r.duration_seconds, int) and r.duration_seconds > 0 else 0
        for f in _BUILD_MIX_FIELDS:
            v = m.get(f)
            if isinstance(v, (int, float)) and v >= 0:
                total[f] = int(total[f]) + int(v)  # type: ignore[arg-type]
        # 공/방/실드는 충분히 긴 경기만, 그리고 그 값을 실제로 실은 기록만 센다.
        # 값을 안 실은 옛 기록(재분석 전)을 0으로 세면 평균이 그만큼 깎인다 — 실측으로
        # 그런 기록이 한 사람의 절반 가까이였다.
        if all(isinstance(m.get(f), (int, float)) for f in _UPGRADE_FIELDS) and full >= _MIN_UPGRADE_SECONDS:
            up_plays += 1
            for f in _UPGRADE_FIELDS:
                total[f] = int(total[f]) + int(m[f])  # type: ignore[arg-type,index]
        # 줄별 값도 같은 시간 조건을 건다 — 3단계까지 올리려면 그만큼 시간이 필요하다는
        # 사실은 줄이 갈려도 그대로다. 줄이 실린 판만 그 줄의 분모에 얹는다.
        ups = m.get("ups")
        if isinstance(ups, dict) and full >= _MIN_UPGRADE_SECONDS:
            for name, v in ups.items():
                if not isinstance(name, str) or not isinstance(v, (int, float)):
                    continue
                up_lines[name] = up_lines.get(name, 0) + int(v)
                up_line_plays[name] = up_line_plays.get(name, 0) + 1
        for t, secs_key in _BUILD_MIX_TALLIES.items():
            d = m.get(t)
            if not isinstance(d, dict):
                continue
            for name, v in d.items():
                if isinstance(name, str) and isinstance(v, (int, float)) and v > 0:
                    tallies[t][name] = tallies[t].get(name, 0) + int(v)
                    # 이 경기에 그 이름이 나왔다 — 그 판의 길이를 이 이름의 분모에 얹는다.
                    tallies[secs_key][name] = tallies[secs_key].get(name, 0) + full
    total.update(tallies)
    total["ups"] = up_lines
    total["up_counts"] = up_line_plays
    return {
        "build_mix": BuildMix(**total),
        "avg_worker5": round(int(total["worker5"]) / len(mixed), 1),  # type: ignore[arg-type]
        # 합계를 되돌릴 두 분모 — 경기 수(공/방 평균 단계처럼 판마다 하나인 값)와 주요시간대
        # 총 길이(분당으로 환산할 값). 무엇을 무엇으로 나눌지가 칸마다 달라서 나눗셈은
        # 화면이 하고 서버는 분모만 준다.
        "mix_plays": len(mixed),
        "mix_seconds": seconds or None,
        # 공/방/실드만의 분모 — 조건을 넘긴 판이 하나도 없으면 None이라 화면이 그 칸을 비운다.
        "up_plays": up_plays or None,
    }


def _split_terms(query: str | None) -> list[str]:
    if not query:
        return []
    return query.split()


def _encode_cursor(match_no: str) -> str:
    return base64.urlsafe_b64encode(match_no.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError) as e:
        raise ValidationError("잘못된 커서입니다.") from e


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


def _match_no_base(match_date: date, game_started_at: datetime | None) -> str:
    # 리플레이가 있으면 실제 경기 시작 시각(KST)을, 없으면(수동 등록) 경기 날짜만 알 수
    # 있으니 자정(000000)으로 채운다 — 같은 날 여러 건이면 뒤 2자리 일련번호로 갈린다.
    #
    # 수기등록은 실제 경기 시각을 몰라도 "제N경기" 순서(gameStartedAt 비교, GameResultList.tsx의
    # compareByPlayOrder)를 매길 기준값이 필요해서, 프론트가 신규 등록 시점의 "지금"을
    # gameStartedAt에 채워 넣는다(서비스 다른 곳 참고) — 그 값은 사용자가 고른 경기
    # 날짜(match_date)와 전혀 무관한 "등록한 시각"일 뿐이라 match_no에 그대로 쓰면 안 된다
    # (실제로 지적받은 문제 — 4월 1일자로 등록한 경기의 match_no가 등록한 날(오늘)로 붙음).
    # 리플레이로 파싱된 진짜 시각은 항상 match_date와 같은 날짜이므로(그 시각으로부터
    # match_date 자체를 계산해서 채운다), 날짜가 어긋나면 신뢰할 수 없는 값(수기등록의
    # "지금")으로 보고 자정 기준으로 대체한다.
    if game_started_at is not None:
        local = game_started_at.astimezone(_KST) if game_started_at.tzinfo else game_started_at
        if local.date() == match_date:
            return local.strftime("%y%m%d%H%M%S")
    return match_date.strftime("%y%m%d") + "000000"


# 리플레이 다운로드 파일명 생성(요청):
#   [경기번호] 팀1로스터 VS 팀2로스터 (맵이름).rep
# 로스터의 유저 이름은 자르지 않고 전부 넣고(,로 구분), 맵 이름은 특수문자를 지운다. 경기번호는
# 서버가 부여한 match_no이므로 서버에서만 만들 수 있다 — 등록(_apply_replay)·중복 재등록
# (merge_replay) 모두 이 함수로 최신 포맷 이름을 새로 만든다. 저장 경로는 UUID라(local.py 참고)
# 이 이름은 다운로드 시 Content-Disposition에만 쓰여 공백/괄호/쉼표가 섞여도 안전하다.
_FNAME_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FNAME_FORBIDDEN = re.compile(r'[/\\:*?"<>|]')
# 맵 이름의 "특수문자"만 지운다 — 글자(모든 언어)/숫자/밑줄/공백과 일반 문장기호 ()[].-_~'&는
# 남기고 그 외(색상코드 잔여·기호류·파일명 금지문자 <>)는 제거한다(요청). 프론트 mapName.ts와 동일.
_MAP_SPECIAL = re.compile(r"[^\w\s()\[\].~'&-]", re.UNICODE)
REPLAY_NAME_MAX = 200


def _fname_safe(s: str) -> str:
    s = _FNAME_CONTROL.sub("", s or "")
    s = _FNAME_FORBIDDEN.sub("_", s)
    return re.sub(r"\s+", " ", s).strip()


def build_replay_display_name(match: "GameResult") -> str:
    """리플레이 다운로드 파일명 — SG_경기번호.rep(재지적: 더 짧게. 긴 한글 이름은
    브루드워가 리플레이 목록에서 인식을 못 한다. 전부 ASCII·짧게)."""
    return f"SG_{match.match_no}.rep"


def _to_utc_naive(dt: datetime) -> datetime:
    # Postgres(timestamptz)는 aware로, SQLite는 tz 정보 없이 naive로 돌아오는 등 방언마다
    # 달라서, 비교 전에 항상 "UTC 기준 naive"로 맞춘다(입력값은 항상 UTC로 정규화해서 만듦).
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


class _RaceAgg:
    """aggregate_stats가 돌려주는 (member_pk, race) 단위 원본 행 하나 또는 여러 개를
    합산해서 RaceStatsEntry로 만드는 중간 누산기 — 전적(판수/승/무)만 센다.

    지표 평균(APM·유효APM·커맨드·유효커맨드·생산)은 여기서 내지 않는다. 이상치를 뺀
    평균이라 경기 단위 원본이 있어야 하고(_trimmed_avgs), 호출부가 to_entry() 결과에
    그 값들을 덮어 넣는다. 한때 SQL 합계로도 같은 평균을 내서 두 벌이 공존했는데,
    덮어쓰는 쪽만 화면에 나가는데도 안 쓰이는 계산이 남아 있어 "어느 게 진짜 나가는
    값인지" 읽는 사람이 헷갈렸다 — 한 벌만 남긴다."""

    __slots__ = ("plays", "wins", "draws")

    def __init__(self) -> None:
        self.plays = 0
        self.wins = 0
        self.draws = 0

    def add_row(self, row) -> None:
        self.plays += row.plays
        self.wins += row.wins
        self.draws += row.draws

    def to_entry(self) -> RaceStatsEntry:
        losses = self.plays - self.wins - self.draws
        win_rate = round((self.wins / self.plays) * 1000) / 10 if self.plays else 0.0
        # 지표 평균은 전부 기본값(None)으로 두고 호출부가 _trimmed_avgs로 채운다.
        return RaceStatsEntry(
            plays=self.plays,
            wins=self.wins,
            losses=losses,
            draws=self.draws,
            win_rate=win_rate,
        )


class _Record:
    """한 방향 전적(내가 상대에게) — 승점은 승 +1, 무 0, 패 -1."""

    __slots__ = ("plays", "wins", "draws")

    def __init__(self, plays: int, wins: int, draws: int) -> None:
        self.plays = plays
        self.wins = wins
        self.draws = draws

    @property
    def points(self) -> int:
        losses = self.plays - self.wins - self.draws
        return self.wins - losses


# member_pk -> 상대 member_pk -> 그 상대에게의 전적
HeadToHead = dict[int, dict[int, _Record]]


# 표본이 모자란다고 지표를 가리지는 않는다(요청: "그런 제한 다 없애줘 다 보여주기").
# 한때 개인전 2판·팀전 5판을 못 채우면 APM·커맨드·생산을 전부 null로 내렸는데, 표에 "-"만
# 늘어서는 값이 그 자체로 정보가 없었다 — 적게 뛴 사람의 값은 적게 뛴 값으로 읽으면 된다.
# 튀는 한 판을 걸러내는 장치는 그대로 남는다: 경기 길이(_MIN_DURATION_SECONDS)와 중앙값/MAD
# (_outlier_keep_mask). 그쪽은 "몇 판 뛰었나"가 아니라 "이 판이 정상인가"를 본다.


def _replay_order_key(game_started_at, match_date, match_no):
    """경기를 시간순으로 세울 정렬 키 — 리플레이 실제 시작시각(game_started_at)이 있으면 그걸,
    없으면 경기 날짜 자정(UTC)을 쓰고, 마지막으로 match_no로 안정 정렬한다. tz 없는 값은
    UTC로 맞춰 비교 가능하게 한다(백테스트 ORDER BY와 같은 규칙)."""
    if game_started_at is not None:
        ts = game_started_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    else:
        ts = datetime(match_date.year, match_date.month, match_date.day, tzinfo=UTC)
    return (ts, match_no or "")


# 재생 결과 기억 — 통계 화면이 기간·종족을 바꿔 가며 같은 재생을 여러 번 부르는데, 상관을
# 들고 가면서 한 경기 갱신이 회원 수의 제곱이 됐다(회원 60명·5000판에 7.5초).
#
# 한 벌은 (종족별 여부, 경기 지문 목록, 엔진, 경기별 점수 이동)이다. 지문 목록을 들고 있는
# 이유는 '이어 붙이기' 때문이다: 경기는 거의 언제나 뒤에만 덧붙으므로, 기억해 둔 목록이 새
# 목록의 앞부분과 같으면 앞은 다시 안 돌고 뒤에 늘어난 것만 이어 재생한다. 그래서 경기 하나
# 등록한 뒤의 첫 조회도 5000판이 아니라 한 판어치다.
#
# 중간이 바뀌었거나(경기 수정·삭제) 아예 다른 목록이면 앞부분이 안 맞으니 처음부터 돈다.
# 프로세스 안에만 사는 값이라 서버가 다시 뜨면 비고, 그때 한 번은 제값을 치른다.
_REPLAY_CACHE: list = []
_REPLAY_CACHE_MAX = 6

# 레이팅 = 실력 추정치(μ)를 화면용 눈금으로 옮긴 값(요청: "포인트라기보다 레벨이나 그런 용어가
# 나을 듯" → 배틀넷 래더와 같은 말인 "레이팅").
#
# 한때는 '경기마다 번 점수를 쌓은 값'이었다. 그 구조로는 우려먹기를 못 막는다 — 한 판마다
# 이긴 쪽이 X를 얻고 진 쪽이 X를 잃는 어떤 규칙을 써도 한 판의 기댓값이
#     기본점 × (2p − 1) × (1 − 기울기)
# 라서, 기울기가 1보다 조금이라도 작으면 만만한 상대를 고르는 것이 언제나 이득이고 기울기가
# 1이면 모두의 기댓값이 0이 되어 실력을 아예 못 가린다. 중간이 없다(지적: "이길 게 뻔한 판을
# 계속하는 게 비등한 판을 하는 것보다 훨씬 낫잖아").
#
# 레이팅에는 그 문제가 없다. 결과를 모형이 보는 확률대로 뽑아 20판을 더 두게 하면(2000번
# 반복), 상대를 어떻게 고르든 기댓값이 0이다 — 베이즈 갱신이라 '지금 믿는 것'이 곧 기댓값이기
# 때문이다. 같은 조건에서 쌓는 점수는 대진에 따라 갈린다:
#     상대                  레이팅        쌓는 점수(기울기 0.5)
#     94.5% 이길 만만한 상대  −0.02 ±0.27      +177.3 ±1.0
#     65.6% 비등한 상대      +0.45 ±0.34       +65.1 ±2.3
#     18.8% 센 상대         +0.41 ±0.33      −121.3 ±1.9
# 레이팅이 움직이는 경우는 그 맞대결에 대한 평가가 틀렸을 때뿐이고, 그때 움직이는 것이 맞다.
#
# 그래서 점수를 아예 레이팅 자체로 둔다. 기간 필터는 '그 기간에 번 점수'가 아니라 '그 시점까지의
# 기록으로 본 점수'라는 뜻이 된다(요청: "이번 달에 번 점수는 필요없어").
#
# 화면 눈금은 배틀넷 래더를 따른다(요청: "배틀넷 구조로 가자") — 새 회원이 1000에서 시작하고
# 음수가 없다.
#
# 실력 추정치(μ)를 그대로 쓰면 표본이 얕은 사람이 위로 튄다. 실제로 세 판 뛴 사람이 열두 판
# 뛴 사람을 눌렀다(지적: "각각 타센과 곰세마리한테만 3판씩 이긴 정구와 Rex가 미친마법사보다
# 위인 게 이상해" → "정구가 미친마법사보다 위인 건 말이 안 돼").
#
# 그래서 판이 적으면 그만큼 덜 반영한다:
#
#     레이팅 = 1000 + (μ − μ0) × 20 × 판수 / (판수 + RATING_SETTLE_GAMES)
#
# 한때 σ(모형이 들고 있는 불확실성)로 깎아 봤는데 두 가지가 안 맞았다.
#   · 우리 클럽은 아직 경기가 적어 모두의 σ가 높은 구간이다 — 실측으로 3판 σ=6.33, 12판
#     σ=5.90로 7%밖에 안 벌어졌다. 판수는 4배 차이인데. 계수를 5 넘게 올려야 겨우 뒤집혔고
#     그러면 새 회원이 444에서 시작했다.
#   · 이긴 판의 15.2%에서 레이팅이 되레 내려갔다. 이길 게 뻔한 판은 μ가 거의 안 오르는데
#     σ는 시간 몫(TAU)만큼 부풀어서, μ−kσ가 순감소해 버린다. 판수로 깎으면 0%다.
#
# RATING_SETTLE_GAMES는 '이만큼 뛰면 절반쯤 반영된다'는 뜻이다. 10이면 3판 23%, 12판 55%,
# 30판 75%. 기존 회원끼리의 순위는 거의 안 흔들린다(24개월 클럽 실측 순위상관 0.990 → 0.987,
# 상위 5명 5/5 그대로) — 판이 쌓인 사람들은 비중이 다 1에 가까워 서로 상쇄되기 때문이다.
RATING_BASE = 1000.0
RATING_SCALE = 20.0
RATING_SETTLE_GAMES = 10.0


def _rating_of(r, games: int) -> float:
    """화면에 뜨는 레이팅 — 실력 추정치를 판수만큼만 반영한 값(위 주석)."""
    return RATING_BASE + (r.mu - MU0) * RATING_SCALE * games / (games + RATING_SETTLE_GAMES)


def _replay_ratings(
    rows, focal=None, by_race: bool = False, count_from: date | None = None,
) -> tuple[RatingEngine, dict[str, float], dict]:
    """rank_replay_rows 결과를 경기 단위로 묶어 '시간순'으로 레이팅을 누적한다.

    재생은 늘 맨 처음부터 한다(요청: "월이 바뀌어도 지난달까지의 누적치를 가지고 상대강도를
    계산해서 평가를 시작"). 예전에는 SQL에서 그 달 이전 경기를 아예 잘라내 매달 전원이 μ0에서
    다시 시작했다. 그러면 지난달까지 쌓인 정보가 통째로 버려져, 3연승한 신입과 3연승한 고수가
    같은 점수를 받는다.

    count_from은 '어느 경기부터 상세 목록에 실을까'만 정한다 — 그 앞의 경기도 레이팅은 똑같이
    만든다. 조회 기간의 끝(date_to)은 호출부가 SQL에 걸어 rows 자체를 자른다. 그래서 어느 달을
    조회하든 그 달 경기의 Δ는 똑같이 나온다.

    by_race=False면 레이팅 대상이 '회원'(member_pk)이고, True면 '(회원, 그 경기 종족)' 조합이다
    (요청: "종족은 랭커의 종족" — 저그로 낸 경기는 그 회원의 저그 레이팅에만 쌓인다). 상대가
    무슨 종족이든 상관없이, 각 참가자는 자기가 그 경기에서 낸 종족 레이팅으로 서로 겨뤄 갱신된다.

    반환: (엔진, focal의 경기별 점수 변화, 창 안에서 사람마다 움직인 총량).

    카드에 뜨는 포인트는 이 반환값이 아니라 엔진의 실력 추정치에서 바로 뽑는다(RATING_SCALE
    주석). 여기 값들은 '그 경기가 포인트를 얼마나 움직였나'로, 상세 목록에 쓴다. 셋째 반환값은
    '이 기간에 뛰었나'를 가리는 데도 쓴다 — 통산 판수로 보면 지난달까지만 뛴 사람이 이번 달
    상세에서 0점으로 떠 목록과 어긋난다.

    눌러담기(승리 0 이상·패배 0 이하)는 남겨 둔다 — 상관 모형에서는 이긴 사람의 μ가 내려가는
    일이 구조적으로 없어(실측 300판 0건) 값이 거의 안 드는 안전장치이지만, "패배는 0 이하
    승리는 0 이상"이라는 규칙 자체는 화면의 약속이다(요청).

    컴퓨터나 비회원이 한 명이라도 낀 경기는 아무의 점수도 움직이지 않는다 — 0점 처리다
    (요청: "비회원이 들어간 경기도 0점 처리해야 해"). 포인트는 상대의 실력치와 견줘
    오르내리는 값인데 컴퓨터·비회원에는 그 값이 없다. 일대일이라면 겨룰 상대가 아예 없어
    원래도 0이었지만(rating.py의 RatingEngine.update — 한쪽 편이 비면 그대로 반환), 팀전에서
    한 자리만 그런 경우에는 남은 사람들이 '한 명 모자란 상대'와 싸운 것으로 계산돼 실제로는
    없던 실력차가 점수에 들어갔다. 그 자리 하나 때문에 나머지 점수가 흔들릴 이유가 없으므로
    경기 단위로 0점으로 둔다.

    전적(판수·승·무·승률)은 이와 무관하게 그대로 센다 — 뛴 건 뛴 거다."""
    def _ident(member_pk, race):
        if member_pk is None:
            return None
        return (member_pk, race) if by_race else member_pk

    matches: dict[int, dict] = {}
    for r in rows:
        m = matches.get(r.match_id)
        if m is None:
            m = matches[r.match_id] = {
                "team1": [], "team2": [], "result": r.result, "match_no": r.match_no,
                "key": _replay_order_key(r.game_started_at, r.match_date, r.match_no),
                # 점수를 셀 창(count_from)의 기준은 match_date다 — 화면의 기간 필터가
                # 거는 것도 이 컬럼이라, 창 경계에서 어긋나지 않는다.
                "date": r.match_date,
                "outsider": False,
            }
        if r.team in ("team1", "team2"):
            m[r.team].append(_ident(r.member_pk, r.race))
            # 회원이 아닌 참가자(컴퓨터·비회원)가 한 명이라도 있으면 그 경기는 0점이다.
            if r.member_pk is None:
                m["outsider"] = True

    order = sorted(matches, key=lambda k: matches[k]["key"])
    # 재생 결과는 (경기 목록, 종족별 여부)만의 함수다 — focal·count_from은 그 위에서 싸게
    # 걸러내면 된다. 상관 행렬을 들고 가면서 한 경기 갱신이 회원 수의 제곱이 됐고(회원 60명·
    # 5000판에 7초), 통계 화면은 필터를 바꿀 때마다 이걸 다시 부른다. 그래서 재생 자체를
    # 기억해 둔다 — 경기가 하나라도 늘거나 바뀌면 열쇠가 달라져 저절로 다시 돈다.
    marks = tuple(
        (matches[mid]["match_no"], matches[mid]["result"],
         tuple(matches[mid]["team1"]), tuple(matches[mid]["team2"]))
        for mid in order
    )
    engine, log = _replay_from_cache(by_race, marks, matches, order)

    deltas: dict[str, float] = {}
    running: dict = defaultdict(float)
    for match_no, when, moved in log:
        # 창 밖(count_from 이전)의 경기는 레이팅만 만들고 표시 점수에는 안 들어간다 —
        # 이게 곧 "지난달까지의 누적치로 상대강도를 계산해서 평가를 시작"이다(요청).
        if count_from is not None and when < count_from:
            continue
        for ident, raw in moved:
            running[ident] += raw
            if ident == focal:
                deltas[match_no] = raw
    return engine, deltas, dict(running)


def _replay_from_cache(by_race: bool, marks: tuple, matches: dict, order: list):
    """기억해 둔 것 중 앞부분이 맞는 게 있으면 이어 붙이고, 없으면 처음부터 돈다.

    맞는 것이 여럿이면 가장 길게 맞는 쪽을 고른다 — 다시 돌 판이 가장 적다."""
    best = None
    for i, (flag, kept, *_rest) in enumerate(_REPLAY_CACHE):
        if flag is by_race and len(kept) <= len(marks) and marks[:len(kept)] == kept:
            if best is None or len(kept) > len(_REPLAY_CACHE[best][1]):
                best = i
    if best is None:
        engine, log = _run_replay(matches, order, RatingEngine(), [], 0)
    else:
        _, kept, base, base_log = _REPLAY_CACHE.pop(best)
        if len(kept) == len(marks):
            engine, log = base, base_log      # 그대로 — 다시 돌 것이 없다
        else:
            engine, log = _run_replay(matches, order, base.clone(), list(base_log), len(kept))
    _REPLAY_CACHE.append((by_race, marks, engine, log))
    while len(_REPLAY_CACHE) > _REPLAY_CACHE_MAX:
        _REPLAY_CACHE.pop(0)
    return engine, log


def _run_replay(matches: dict, order: list, engine: RatingEngine, log: list, start: int):
    """시간순으로 한 번 재생하고, 경기마다 '누가 몇 점 얻고 잃었나'를 남긴다.

    start부터 이어 돈다 — 앞은 이미 engine·log에 담겨 온다(_replay_from_cache)."""
    for mid in order[start:]:
        mm = matches[mid]
        # 0점 경기 — 레이팅을 갱신하지도, 표시 점수를 더하지도 않는다(위 주석).
        if mm["outsider"]:
            continue
        won = lost = []
        if mm["result"] in ("team1", "team2"):
            won = [p for p in mm[mm["result"]] if p is not None]
            lost = [p for p in mm["team2" if mm["result"] == "team1" else "team1"] if p is not None]
        participants = won + lost if (won and lost) else [
            p for p in (mm["team1"] + mm["team2"]) if p is not None
        ]
        pre = {p: engine.get(p).mu for p in participants}
        engine.update(mm["team1"], mm["team2"], mm["result"])
        # 이 경기가 내 점수를 얼마나 움직였나 — 카드 점수와 같은 단위다. 눌러담기(승리 0 이상·
        # 패배 0 이하)는 값이 거의 안 드는 안전장치로 남긴다.
        won_set = set(won)
        moved = []
        for p in participants:
            raw = (engine.get(p).mu - pre[p]) * RATING_SCALE
            if won and lost:
                raw = max(raw, 0.0) if p in won_set else min(raw, 0.0)
            moved.append((p, raw))
        log.append((mm["match_no"], mm["date"], moved))
    return engine, log


def _to_match_slot(p: GameResultParticipant, alias_by_player_name: dict[str, ReplayAlias]) -> GameResultSlot:
    # 회원인지, 아니면 컴퓨터(AI)/비회원 참가자인지는 더 이상 member_pk 컬럼이 아니라
    # player_name → replay_aliases 조회로 판단한다(alias_by_player_name, 라우터에서
    # 한 번만 가져와 여러 경기를 직렬화하는 동안 재사용 — list_all_replay_aliases는
    # ReplayAlias.member까지 eager load 되어 있다). 회원이 아니면 실제로 저장된 고유
    # 아이디가 없으니 team 내 position으로 매 조회마다 안정적으로 재생성한다(같은 경기를
    # 다시 읽어도 동일한 값). 컴퓨터/비회원 중 어느 쪽인지는 alias.kind == "computer"면
    # 컴퓨터로 취급한다. 분류가 없으면
    # 비회원으로 본다 — 컴퓨터는 등록 시점에 항상 kind="computer"로 기억되므로
    # (_remember_placeholder_raw_names), 조회가 안 되는 이름은 "아직 아무도 분류하지 않은
    # 사람"이라는 뜻이다. 예전엔 반대로 컴퓨터를 기본값으로 뒀는데, 그러면 비회원을
    # 기억시키려고 매번 alias를 만들어야 했고 그 탓에 그 이름을 회원으로 연결할 기회가
    # 사라졌다.
    alias = alias_by_player_name.get(p.player_name)
    if alias is not None and alias.kind == "member":
        member_id = alias.member.id
    elif alias is not None and alias.kind == "computer":
        member_id = f"{COMPUTER_ID_PREFIX}{p.position}"
    else:
        member_id = f"{UNREGISTERED_ID_PREFIX}{p.position}"
    return GameResultSlot(
        member_id=member_id,
        race=p.race,
        player_name=p.player_name,
        apm=p.apm,
        eapm=p.eapm,
        cmd_count=p.cmd_count,
        effective_cmd_count=p.effective_cmd_count,
        build_count=p.build_count,
        build_mix=_mix_json(p.build_mix),
    )


def to_game_result_out(
    match: GameResult,
    storage: FileStorage,
    alias_by_player_name: dict[str, ReplayAlias],
    *,
    actor_pk: int | None = None,
    is_admin: bool = False,
) -> GameResultOut:
    team1 = [_to_match_slot(p, alias_by_player_name) for p in match.participants if p.team == "team1"]
    team2 = [_to_match_slot(p, alias_by_player_name) for p in match.participants if p.team == "team2"]
    author = None
    if match.creator is not None:
        author = GameResultAuthor(id=match.creator.id, nickname=match.creator.nickname)
    # 공식경기 예약(scheduled, 결과 없이 등록) 기능이 없어진 뒤로는 모든 경기가 등록과
    # 동시에 결과를 함께 저장하므로 result_row가 항상 존재한다.
    result_row = match.result_row
    assert result_row is not None, "모든 경기는 result_row를 가져야 합니다."
    replay = None
    if result_row.replay is not None:
        replay = ReplayOut(
            id=result_row.replay.id,
            original_name=result_row.replay.original_name,
            display_name=result_row.replay.display_name,
            url=storage.url_for(result_row.replay.file_path),
        )
    return GameResultOut(
        id=match.id,
        match_no=match.match_no,
        date=match.match_date.isoformat(),
        team1=team1,
        team2=team2,
        result=result_row.result,
        match_type=match.match_type,
        replay=replay,
        created_by=author,
        map_name=result_row.map_name,
        game_started_at=result_row.game_started_at,
        duration_seconds=result_row.duration_seconds,
        map_hash=result_row.map_hash,
        view_count=match.view_count or 0,
    )


class GameResultService:
    def __init__(self, session: AsyncSession, storage: FileStorage) -> None:
        self._session = session
        self._repo = GameResultRepository(session)
        self._member_repo = MemberRepository(session)
        self._storage = storage

    async def list_matches_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        sort: str,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        user_query: str | None,
        match_all_users: bool,
        has_placeholder: bool = False,
        team_member_ids: list[str] | None = None,
    ) -> tuple[list[GameResult], str | None, bool]:
        decoded_cursor = _decode_cursor(cursor) if cursor else None
        matches, has_more = await self._repo.list_page(
            cursor=decoded_cursor,
            limit=limit,
            sort=sort,
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            match_type=match_type,
            terms=_split_terms(user_query),
            match_all_terms=match_all_users,
            has_placeholder=has_placeholder,
            team_member_pks=await self._team_member_pks(team_member_ids),
        )
        next_cursor = _encode_cursor(matches[-1].match_no) if has_more and matches else None
        return matches, next_cursor, has_more

    async def _team_member_pks(self, team_member_ids: list[str] | None) -> list[int] | None:
        """팀 랭킹에서 넘어온 로그인 아이디들을 pk로 바꾼다 — 하나라도 없는 회원이 섞여 있으면
        그 팀 자체가 성립하지 않으므로, 아무 경기도 안 걸리도록 존재하지 않는 pk를 하나 남긴다
        (조건을 통째로 무시해서 전체 경기를 보여주는 것보다 이쪽이 안전하다)."""
        if not team_member_ids:
            return None
        pks: list[int] = []
        for login_id in team_member_ids:
            member = await self._member_repo.get_by_login_id(login_id)
            if member is None:
                return [-1]
            pks.append(member.pk)
        return pks

    async def count_matches(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        user_query: str | None,
        match_all_users: bool,
        has_placeholder: bool = False,
        team_member_ids: list[str] | None = None,
    ) -> int:
        """무한스크롤로 화면엔 일부만 로드돼도, list_matches_page와 같은 필터 조건에
        해당하는 전체 건수를 알려주기 위한 조회(커서/limit 없음)."""
        return await self._repo.count_page(
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            match_type=match_type,
            terms=_split_terms(user_query),
            match_all_terms=match_all_users,
            has_placeholder=has_placeholder,
            team_member_pks=await self._team_member_pks(team_member_ids),
        )

    async def get_stats(
        self,
        *,
        member_ids: list[str] | None,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        race: str | None,
    ) -> list[MemberStatsEntry]:
        if member_ids is not None:
            members = []
            for login_id in member_ids:
                member = await self._member_repo.get_by_login_id(login_id)
                if member is not None:
                    members.append(member)
        else:
            members = await self._member_repo.list_all()
        if not members:
            return []

        parsed_date_from = _parse_date(date_from)
        parsed_date_to = _parse_date(date_to)
        rows = await self._repo.aggregate_stats(
            member_pks=[m.pk for m in members],
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
        )
        by_member: dict[int, dict[str, object]] = {}
        for row in rows:
            by_member.setdefault(row.member_pk, {})[row.race] = row

        # 평균으로 나가는 지표(_TRIMMED_RATES·_TRIMMED_VOLUMES)는 합계만으로는 이상치(그 회원의 다른 경기들과
        # 편차가 너무 심한 경기 하나)를 가려낼 수 없어, 경기 단위 원본을 따로 받아 회원+종족별로
        # 묶어둔다.
        raw_rows = await self._repo.raw_metric_rows(
            member_pks=[m.pk for m in members],
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
        )
        raw_by_member_race: dict[int, dict[str, list]] = {}
        for raw in raw_rows:
            raw_by_member_race.setdefault(raw.member_pk, {}).setdefault(raw.race, []).append(raw)

        entries: list[MemberStatsEntry] = []
        # 사람마다의 주종족 — "main"으로 볼 때 순위·포인트를 이 종족 기준으로 매긴다.
        main_race_by_pk: dict[int, str | None] = {}
        for member in members:
            race_rows = by_member.get(member.pk, {})
            raw_race_rows = raw_by_member_race.get(member.pk, {})

            by_race: dict[str, RaceStatsEntry] = {}
            for r in BASE_RACES:
                agg = _RaceAgg()
                if r in race_rows:
                    agg.add_row(race_rows[r])
                entry = agg.to_entry()
                raw_for_race = raw_race_rows.get(r, [])
                by_race[r] = entry.model_copy(update=_trimmed_avgs(raw_for_race))

            overall_agg = _RaceAgg()
            # "main"(주종족)은 집계로는 '전체'다 — 사람마다 다른 종족이라 한 잣대로 걸 수가
            # 없고, 화면이 by_race에서 그 사람 것을 골라 쓴다. 갈리는 건 순위·포인트뿐이고
            # 그건 아래 _apply_rank_order가 사람마다 제 주종족으로 매긴다.
            if race and race not in ("all", "main"):
                if race in race_rows:
                    overall_agg.add_row(race_rows[race])
                overall_raw = raw_race_rows.get(race, [])
            else:
                for row in race_rows.values():
                    overall_agg.add_row(row)
                overall_raw = [raw for rows_for_race in raw_race_rows.values() for raw in rows_for_race]

            # 종족 필터와 무관하게 항상 실제 참가 기록 기준 최다 종족 — 동률이면 테란→프로토스→
            # 저그 고정 순서로 결정한다(사전순 등 우연에 맡기지 않기 위해).
            most_played_race = None
            best_plays = 0
            for r in BASE_RACES:
                plays = race_rows[r].plays if r in race_rows else 0
                if plays > best_plays:
                    best_plays = plays
                    most_played_race = r

            main_race_by_pk[member.pk] = most_played_race
            overall_entry = overall_agg.to_entry().model_copy(update=_trimmed_avgs(overall_raw))
            entries.append(
                MemberStatsEntry(
                    member_id=member.id,
                    overall=overall_entry,
                    by_race=by_race,
                    most_played_race=most_played_race,
                )
            )

        await self._apply_rank_order(
            entries,
            members,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
            race=race,
            main_race_by_pk=main_race_by_pk,
        )
        return entries

    async def _apply_rank_order(
        self,
        entries: list[MemberStatsEntry],
        members: list[Member],
        *,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        race: str | None,
        main_race_by_pk: dict[int, str | None] | None = None,
    ) -> None:
        """랭킹 정렬(sort_order/tie_group)을 entries에 채워 넣는다 — entries[i]는 members[i]의 것이다.

        순위는 TrueSkill 레이팅으로 가른다(요청: "완전 교체"):

          레이팅 = 이 경기유형의 경기 중 조회 기간(date_from~date_to)만 시간순으로 재생해 얻은
            회원별 실력 추정(μ)과 불확실성(σ). 순위·카드 점수는 보수추정(μ−3σ)으로 매긴다 —
            강한 상대를 이길수록 오르고, 표본이 적으면 σ가 커서 보수값이 낮게(잠정) 잡혀
            소수표본 인플레를 막는다. date_from을 기간 시작으로 그대로 걸어 그 이전 경기는
            재생 대상에서 빼므로, 매 기간(월/년)마다 전원이 기본 레이팅(μ0)에서 새로 시작한
            것처럼 리셋된다(요청: "랭킹 조회시 해당 월이나 년도만의 리셋된 데이터로 조회").
          참가 우선 — 이 기간에 1경기라도 뛴 사람은 레이팅이 아무리 낮아도(음수여도) 0경기
            회원보다 무조건 위다(요청). 그다음 보수레이팅(높은 순) → 닉네임 → 로그인 아이디.

        우세/동등/열세 인원과 person_score(우열)는 순위 산정에서 빠지고 상세 참고값으로만 남는다.
        0경기 회원도 모두 목록에 넣는다(요청) — 맨 아래에 공동으로 모인다.

        여기서만 정렬을 하고 entries 자체의 순서(=회원 목록 순서)는 바꾸지 않는다 — 이 응답은
        랭킹 말고 전적통계/상세 모달도 함께 쓰기 때문이다."""
        pairs = list(zip(entries, members))  # 0경기 포함 전원
        if not pairs:
            return

        # 사람 단위 우세/동등/열세 판정용 맞대결 전적(전원 대상).
        rows = await self._repo.head_to_head_rows(
            member_pks=[m.pk for _, m in pairs],
            date_from=date_from,
            date_to=date_to,
            match_type=match_type,
            # 주종족은 사람마다 달라 한 종족으로 걸 수가 없다 — 맞대결 전적은 순위 산정에
            # 안 쓰이는 참고값이라 전 종족으로 둔다.
            race=None if race == "main" else race,
        )
        h2h: HeadToHead = {}
        for row in rows:
            h2h.setdefault(row.member_pk, {})[row.opponent_pk] = _Record(
                plays=row.plays, wins=row.wins, draws=row.draws,
            )
        pks = {m.pk for _, m in pairs}

        def _person_record(pk: int) -> tuple[int, int, int]:
            """붙어본 상대(랭킹 대상 회원)를 한 명씩 보고 우세/동등/열세 인원을 센다 — 경기
            수·점수차는 안 본다(팍규만 10번 이겨도 '한 명 우세'). (우세 수, 동등 수, 열세 수)."""
            sup = eq = inf = 0
            for opp_pk, rec in h2h.get(pk, {}).items():
                if opp_pk not in pks:
                    continue
                losses = rec.plays - rec.wins - rec.draws
                if rec.wins > losses:
                    sup += 1
                elif rec.wins < losses:
                    inf += 1
                else:
                    eq += 1
            return sup, eq, inf

        person = {m.pk: _person_record(m.pk) for _, m in pairs}
        # 우세/동등/열세 인원은 이 기간 상세 표시에만 쓰고 순위 산정에는 더는 쓰지 않는다
        # (아래 레이팅이 대신한다). person_score(우열)도 상세 참고값으로만 남긴다.

        # 랭킹 점수 = TrueSkill 레이팅(요청: "완전 교체"). 순우열 정규화 대신, 이 경기유형의
        # 경기를 '시간순으로 재생'해 회원별 실력(μ)과 불확실성(σ)을 추정한다. 카드에 보여줄
        # 점수·순위는 보수추정(μ−3σ)으로 매긴다 — 표본이 적으면 σ가 커서 값이 낮게(잠정)
        # 잡혀 소수표본 인플레를 막는다.
        #
        # 재생은 조회 기간이 아니라 맨 처음부터 한다(요청: "월별로 제로베이스에서 시작하는 게
        # 오히려 객관적이지 않다 — 지난달까지의 누적치를 가지고 상대강도를 계산해서 평가를
        # 시작"). date_from은 이제 '재생을 시작하는 날'이 아니라 '점수를 세기 시작하는 날'이라
        # count_from으로만 넘긴다 — 그 앞의 경기는 상대강도를 만드는 데만 쓰인다.
        # date_to는 그대로 SQL에 건다: 지난달을 보는데 이번 달 결과가 섞이면 안 된다.
        # 이 기간에 한 판이라도 뛴 사람만 순위 대상으로 앞세우는 것은 그대로다.
        replay_rows = await self._repo.rank_replay_rows(
            match_type=match_type, date_from=None, date_to=date_to,
        )
        # 종족 필터가 걸리면 레이팅 대상을 '(회원, 종족)'으로 나눠 그 종족으로 낸 경기만 그
        # 회원의 그 종족 레이팅에 쌓는다(요청: "종족은 랭커의 종족"). '전체'면 회원 단위 하나.
        #
        # 주종족("main")도 여기서 갈린다(요청: "주종족으로 했을 때 포인트를 다시 계산 못해?").
        # 한 번의 재생이 이미 (회원, 종족) 조합 전부의 점수를 만들어 두므로, 사람마다 제
        # 주종족 칸을 집기만 하면 된다 — 조회가 늘지도, 재생을 더 돌지도 않는다.
        race_active = race is not None and race != "all"
        by_main = race == "main"
        mains = main_race_by_pk or {}

        def _rk(pk: int):
            if by_main:
                return (pk, mains.get(pk))
            return (pk, race) if race_active else pk

        engine, _, running = _replay_ratings(
            replay_rows, by_race=race_active, count_from=date_from,
        )
        # 카드/정렬에 쓰는 점수 = 실력 추정치 자체다(RATING_SCALE 주석). 경기마다 번 점수를
        # 쌓는 값이 아니라 상한이 있는 값이라, 만만한 상대를 우려먹어도 안 오른다.
        # running은 '이 기간에 얼마나 움직였나'라 순위 대상 판정에만 쓴다.
        score = {
            m.pk: round(_rating_of(engine.get(_rk(m.pk)), engine.games.get(_rk(m.pk), 0)), 1)
            for _, m in pairs
        }

        # 레이팅에는 판수 문턱이 없다(요청: "포인트 컬럼은 최소 경기수 제약을 적용 안 하는
        # 곳이야"). 승률·APM 같은 평균값은 표본이 얕으면 못 믿을 수가 나오지만, 레이팅은
        # 모르는 만큼 기준값(1000) 근처에 머무는 값이라 적게 뛴 사람이 위로 튀지 않는다.
        #
        # 갈라 두는 것은 '이 시점까지 한 판이라도 뛰었나' 하나뿐이다 — 조회한 달에 뛰었나가
        # 아니다(요청: "월 선택하면 레이팅과 랭킹은 그 월 당시의 기록이 나와야 해. 다른
        # 데이터는 플레이를 했어야만 나오지만 그 두개는 나올 수밖에 없어").
        #
        # 레이팅은 처음부터 date_to까지 재생해 얻은 값이라 이미 '그 달 말일의 실력'이다.
        # 지난달에 쉰 사람도 그날 그 레이팅을 들고 있었고 순위표에도 그 자리에 있었다 —
        # 그 달에 안 뛰었다는 이유로 값을 지우면, 남은 사람끼리 순위를 다시 매긴 다른 표가
        # 된다. 게임수·승률·APM처럼 그 달에 실제로 친 것에서 나오는 값만 계속 비어 있는다.
        #
        # 0경기와 낮은 레이팅은 다른 말이라(잰 적이 없는 것과 실제로 낮은 것) 한 판도 안 뛴
        # 사람만 null로 두고 맨 아래 한 덩어리로 모은다.
        # 두 갈래를 합친다: 재생이 실제로 값을 매긴 사람(engine.games)과, 이 기간에 나와서
        # 치기는 했는데 잴 것이 없었던 사람. 뒤쪽은 무승부만 했거나 비회원·컴퓨터하고만 붙은
        # 경우다 — 그런 판은 레이팅을 못 움직이지만(update가 건너뛴다) 나온 건 나온 거라
        # 예전부터 기준값(1000)으로 표에 서 있었다. 앞쪽만 보면 그 사람들이 "-"로 사라진다.
        def _rated(idx: int) -> bool:
            return (
                engine.games.get(_rk(pairs[idx][1].pk), 0) > 0
                or pairs[idx][0].overall.plays > 0
            )

        order = sorted(
            range(len(pairs)),
            key=lambda i: (
                0 if _rated(i) else 1,
                -score[pairs[i][1].pk],
                pairs[i][1].nickname,
                pairs[i][1].id,
            ),
        )
        # tie_group = (레이팅이 있나, 점수)가 같으면 동률. 레이팅이 없는 회원은 맨 아래 한 덩어리.
        prev_key: tuple[bool, float | None] | None = None
        group = -1
        for pos, i in enumerate(order):
            entry, m = pairs[i]
            played = _rated(i)
            key = (played, score[m.pk] if played else None)
            if key != prev_key:
                group += 1
                prev_key = key
            entry.sort_order = pos
            entry.tie_group = group
            s, e, inf = person[m.pk]
            entry.superior_count = s
            entry.equal_count = e
            entry.inferior_count = inf
            entry.person_score = s - inf  # 우열(우세-열세) — 상세 참고용
            # 카드에 보여줄 점수(패배 비증가/승리 비감소 누적) — 최소 판수는 안 걸고,
            # 한 판도 안 뛴 사람만 null이다(위 주석).
            entry.rank_score = score[m.pk] if played else None
            r = engine.get(_rk(m.pk))
            entry.mu = round(r.mu, 1)
            entry.sigma = round(r.sigma, 1)
            # 이제 통산 판수다(재생이 맨 처음부터라) — 조회한 달에 몇 판 뛰었나가 아니라
            # 그 사람의 레이팅이 몇 판으로 여물었나. '잠정'이 뜻하려던 것도 원래 이쪽이다:
            # 매달 리셋되던 시절에는 달이 바뀔 때마다 전원이 다시 잠정이 됐는데, 5년 친
            # 사람이 새 달 첫 판에서 신입과 같은 취급을 받을 이유가 없다.
            entry.rating_games = engine.games.get(_rk(m.pk), 0)
            entry.provisional = engine.is_provisional(_rk(m.pk)) if played else None

    async def get_rating_history(
        self, *, member_id: str, match_type: str | None,
        date_from: str | None = None, date_to: str | None = None, race: str | None = None,
    ) -> RatingHistoryResponse:
        """랭킹 상세의 '경기당 레이팅 변화' — 이 회원이 뛴 경기마다의 μ 증감. 목록(get_stats)과
        똑같은 방식으로 재생해야 이 상세의 μ/σ/Δ 합이 목록 값과 어긋나지 않으므로, 거기와 같이
        '재생은 맨 처음부터, 점수는 date_from부터'로 맞춘다. 종족 필터가 걸리면 레이팅 대상이
        '(회원, 그 종족)'이라 그 회원이 그 종족으로 낸 경기의 Δ만 병기한다.
        프론트는 상세에 띄운 경기들(같은 period로 좁힌)만 match_no로 골라 병기한다."""
        member = await self._member_repo.get_by_login_id(member_id)
        if member is None:
            return RatingHistoryResponse(deltas={})
        rows = await self._repo.rank_replay_rows(
            match_type=match_type, date_from=None, date_to=_parse_date(date_to),
        )
        race_active = race is not None and race != "all"
        focal = (member.pk, race) if race_active else member.pk
        engine, deltas, running = _replay_ratings(
            rows, focal=focal, by_race=race_active, count_from=_parse_date(date_from),
        )
        r = engine.get(focal)
        # '이 기간에 뛰었나'는 engine.games(이제 통산 판수다)가 아니라 점수를 센 경기에
        # 이름이 올랐는가로 본다 — 지난달까지만 뛴 사람이 이번 달 상세에서 0점으로 뜨면
        # 목록('이 기간 0경기'라 빈칸)과 어긋난다.
        played = focal in running
        return RatingHistoryResponse(
            deltas={mno: round(d, 1) for mno, d in deltas.items()},
            mu=round(r.mu, 1) if played else None,
            sigma=round(r.sigma, 1) if played else None,
            # 카드에 뜨는 점수 — get_stats의 score와 같은 식(실력 추정치)이다.
            conservative=round(_rating_of(r, engine.games.get(focal, 0)), 1) if played else None,
            games=engine.games.get(focal, 0),
            provisional=engine.is_provisional(focal) if played else False,
        )

    async def get_rivalries(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
        team: bool = False,
    ) -> RivalryResponse:
        """유저 상성 — 두 회원이 맞붙은 상대전적을 쌍 단위로 집계한다(요청: 상성 맵).
        기본은 양 팀이 각각 '등록 회원 1명'인 1:1 경기만. team=True면 팀전을 개인
        단위로 환산한다(요청: "팀전도 개인화") — 랭킹의 팀전 개인환산과 같은 원칙으로,
        서로 반대 팀이었던 회원 조합 전부에 그 경기의 승/패/무를 1씩 준다. 비회원/
        컴퓨터가 낀 참가자는 repository의 회원 조인에서 빠져 자연히 걸러진다."""
        rows = await self._repo.rivalry_rows(
            date_from=_parse_date(date_from), date_to=_parse_date(date_to), team=team,
        )
        by_match: dict[int, dict[str, object]] = {}
        for r in rows:
            m = by_match.setdefault(r.match_id, {"result": r.result, "team1": [], "team2": []})
            if r.team in ("team1", "team2"):
                m[r.team].append(r.member_pk)  # type: ignore[union-attr]
        # (작은 pk, 큰 pk) → [작은쪽 승, 큰쪽 승, 무] — 방향을 정규화해 한 쌍당 한 행으로 모은다.
        counts: dict[tuple[int, int], list[int]] = {}
        for m in by_match.values():
            team1 = m["team1"]
            team2 = m["team2"]
            if team:
                # 팀전 개인화 — 반대 팀 회원 조합 전부(한쪽이라도 매칭 회원이 없으면 스킵).
                matchups = [(p1, p2) for p1 in team1 for p2 in team2 if p1 != p2]
            else:
                if len(team1) != 1 or len(team2) != 1:
                    continue
                matchups = [(team1[0], team2[0])] if team1[0] != team2[0] else []
            result = m["result"]
            for p1, p2 in matchups:
                lo, hi = (p1, p2) if p1 < p2 else (p2, p1)
                c = counts.setdefault((lo, hi), [0, 0, 0])
                if result == "draw":
                    c[2] += 1
                elif result == "team1":
                    c[0 if p1 == lo else 1] += 1
                elif result == "team2":
                    c[0 if p2 == lo else 1] += 1
        members = await self._member_repo.list_all()
        login_by_pk = {mem.pk: mem.id for mem in members}
        pairs = [
            RivalryPairOut(a=login_by_pk[lo], b=login_by_pk[hi], a_wins=c[0], b_wins=c[1], draws=c[2])
            for (lo, hi), c in counts.items()
            if lo in login_by_pk and hi in login_by_pk
        ]
        return RivalryResponse(pairs=pairs)

    async def get_earliest_match_date(self) -> str | None:
        d = await self._repo.earliest_match_date()
        return d.isoformat() if d else None

    async def check_duplicates(self, game_started_at: list[str]) -> list[str]:
        candidates: dict[datetime, str] = {}
        for raw in game_started_at:
            try:
                candidates[_to_utc_naive(datetime.fromisoformat(raw.replace("Z", "+00:00")))] = raw
            except ValueError:
                continue
        if not candidates:
            return []
        existing = {_to_utc_naive(dt) for dt in await self._repo.list_game_started_ats()}
        return [raw for dt, raw in candidates.items() if dt in existing]

    async def lookup_replay_name_classifications(self, raw_names: list[str]) -> list[ReplayAlias]:
        return await self._repo.list_replay_name_classifications(raw_names)

    async def set_replay_name_classification(self, raw_name: str, kind: str) -> ReplayAlias:
        existing = await self._repo.get_replay_name_classification(raw_name)
        if existing is not None:
            existing.kind = kind
            await self._session.commit()
            return existing
        entry = ReplayAlias(raw_name=raw_name, kind=kind)
        self._repo.add_replay_name_classification(entry)
        await self._session.commit()
        await self._session.refresh(entry)
        return entry

    async def list_replay_name_mappings(self) -> list[dict]:
        """유저 매핑 관리 화면 — 리플레이 원본 이름(rawName) 하나를 기준으로, replay_aliases
        (회원 별칭/컴퓨터·비회원 분류)와 아직 그 어느 쪽도 아닌 미해결(match_participants에만
        남아있는) 항목을 합쳐서 중복 없이 보여준다. raw_name이 replay_aliases 안에서 유일하므로
        회원/분류가 겹칠 일은 원천적으로 없다."""
        aliases = await self._repo.list_all_replay_aliases()
        placeholder_rows = await self._repo.list_placeholder_raw_names_with_last_seen()
        last_seen_by_raw_name = dict(placeholder_rows)
        # 이 이름으로 등록된 경기가 하나라도 있는지 — 삭제(휴지통) 가능 여부와 같은 기준이다.
        # 화면에서 삭제를 막고 경고를 띄우는 데 쓴다(요청: "등록된 경기기록이 있을 땐 경고
        # 보여주고 삭제 안 되게"). member로 소급 연결된 이름은 placeholder에서 빠지므로
        # last_seen이 아니라 이 집합으로 판단해야 정확하다.
        names_with_matches = await self._repo.all_participant_player_names()

        entries: dict[str, dict] = {
            a.raw_name: {
                "raw_name": a.raw_name, "kind": a.kind, "member": a.member,
                "last_seen": last_seen_by_raw_name.get(a.raw_name),
                "has_matches": a.raw_name in names_with_matches,
            }
            for a in aliases
        }
        for raw_name, last_seen in placeholder_rows:
            entries.setdefault(
                raw_name,
                {
                    "raw_name": raw_name, "kind": "unresolved", "member": None,
                    "last_seen": last_seen, "has_matches": raw_name in names_with_matches,
                },
            )

        # 미해결(아직 아무 것도 연결 안 된) 항목을 맨 위에, 그 안에서는 최근에 나온 순으로 —
        # 운영자가 당장 처리해야 할 것부터 보이게 한다. 나머지(이미 연결된 것들)는 그 아래
        # 이름순으로 이어붙인다.
        unresolved = sorted(
            (e for e in entries.values() if e["kind"] == "unresolved"),
            key=lambda e: e["last_seen"] or date.min, reverse=True,
        )
        resolved = sorted(
            (e for e in entries.values() if e["kind"] != "unresolved"),
            key=lambda e: e["raw_name"],
        )
        return unresolved + resolved

    async def set_replay_name_mapping(
        self, raw_name: str, kind: str, member_id: str | None, *, actor_pk: int
    ) -> dict:
        # 새 매핑을 걸기 전에, 이 raw_name에 걸려 있던 예전 매핑(분류/다른 회원의 별칭)은
        # 항상 먼저 지운다 — 한 raw_name은 항상 하나의 대상만 가리켜야 목록에서 중복 없이
        # 보인다.
        await self._repo.delete_replay_alias(raw_name)

        member_out: Member | None = None
        if kind == "member":
            if not member_id:
                raise ValidationError("회원으로 연결하려면 회원을 선택해야 합니다.")
            member = await self._member_repo.get_by_login_id(member_id)
            if member is None:
                raise NotFoundError("회원을 찾을 수 없습니다.")
            member.replay_aliases.append(ReplayAlias(raw_name=raw_name, kind="member"))
            member.updated_by = actor_pk
            await self._repo.resolve_placeholder_raw_name_to_member(raw_name, member.pk)
            member_out = member
        elif kind in ("computer", "unregistered"):
            # slot_kind 컬럼이 없어진 뒤로는 이 alias 행 하나가 분류의 유일한 근거라,
            # match_participants 쪽엔 따로 업데이트할 게 없다(_to_match_slot이 조회 시점에
            # raw_name → kind를 그때그때 찾는다).
            entry = ReplayAlias(raw_name=raw_name, kind=kind)
            self._repo.add_replay_name_classification(entry)
        elif kind == "unresolved":
            # 회원으로 연결돼 있었다면 member_pk가 이미 채워져 있으니 다시 비워야
            # "미지정"으로 목록에 되돌아온다(위 revert_raw_name_to_unresolved 참고).
            await self._repo.revert_raw_name_to_unresolved(raw_name)
        else:
            raise ValidationError(f"알 수 없는 매핑 종류입니다: {kind}")

        await self._session.commit()
        return {"raw_name": raw_name, "kind": kind, "member": member_out}

    async def get_match(self, match_id: int) -> GameResult:
        # 경기번호(YYMMDDHHMMSS+2자리 = 14자리 숫자)도 받는다(요청: 상세 주소를 경기번호로)
        # — 10억을 넘는 값은 등록 id일 수 없으니 경기번호로 해석한다. 옛 id 링크는 그대로.
        if match_id > 999_999_999:
            by_no = await self._repo.get_by_match_no(str(match_id))
            if by_no is not None:
                return by_no
        match = await self._repo.get(match_id)
        if match is None:
            raise NotFoundError("경기결과를 찾을 수 없습니다.")
        return match

    async def get_matches_by_ids(self, match_ids: list[int]) -> list[GameResult]:
        """여러 경기를 한 번에 — 활동 목록이 한 줄에 담긴 경기들을 채울 때 쓴다.
        없는 id는 조용히 빠진다(그 사이 지워진 경기)."""
        return await self._repo.get_many(match_ids)

    async def build_replay_archive(self) -> bytes:
        """등록된 모든 리플레이(.rep 첨부)를 zip 바이트로 묶는다(운영자 제어판의 '리플레이
        전체 다운로드'). 폴더 구분 없이 평평하게 담는다(요청). 파일이 유실된 건은 조용히
        건너뛰고, 파일명이 겹치면 " (2)"식으로 유일하게 만든다."""
        rows = await self._repo.list_all_replays()
        used: set[str] = set()

        def unique(name: str) -> str:
            if name not in used:
                used.add(name)
                return name
            stem, dot, ext = name.rpartition(".")
            i = 2
            while True:
                cand = f"{stem} ({i}).{ext}" if dot else f"{name} ({i})"
                if cand not in used:
                    used.add(cand)
                    return cand
                i += 1

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for display_name, file_path in rows:
                file_name = display_name
                try:
                    data = await self._storage.read(file_path)
                except Exception:
                    continue
                zf.writestr(unique(file_name), data)
        return buf.getvalue()

    async def alias_by_player_name(self) -> dict[str, ReplayAlias]:
        """to_match_out이 참가자의 회원/컴퓨터/비회원 여부를 판단할 때 쓰는 조회용 —
        라우터에서 한 번만 가져와 여러 경기를 직렬화하는 동안 재사용한다."""
        aliases = await self._repo.list_all_replay_aliases()
        return {a.raw_name: a for a in aliases}

    async def list_replay_maps(self, hashes: list[str], full: bool = False) -> list[ReplayMapOut]:
        """미니맵 격자 조회 — 클라이언트가 아직 안 받아 둔 해시만 모아서 묻는다.

        full은 "원본 그림까지 달라"다. 기본은 512px 작은 판(thumb)을 싣는다 — 활동 목록은
        한 화면에 그 페이지에 나온 맵 종류만큼 이 응답을 받으므로, 재생 화면 하나를 위해
        2048px을 전부 나르면 목록이 무거워진다. 크게 그릴 때만 그 한 장을 다시 묻는다.
        옛 행은 thumb가 NULL이라 그대로 image로 되돌아간다(마이그레이션 없이 호환)."""
        # 한 번에 물을 수 있는 개수를 묶어 둔다: 해시가 통째로 IN 절에 들어가고 격자 하나가
        # 22KB라, 상한이 없으면 한 요청으로 수 MB를 뽑아 갈 수 있다.
        uniq = list(dict.fromkeys(h for h in hashes if h))[:32]
        rows = await self._repo.list_replay_maps(uniq)
        # 사람이 올려 둔 실제 미니맵 그림 — 여러 맵이 한 장을 함께 가리킬 수 있으므로(요청:
        # 이름·판본만 다른 맵 묶기) 한 번만 읽어 나눠 쓴다. 이 응답에 실제로 실릴 것만
        # 읽는다(여태 등록된 그림을 통째로 읽었다 — 2048px에서는 그게 수 MB다).
        images = {
            img.id: img
            for img in await self._repo.list_minimap_images([r.image_id for r in rows if r.image_id])
        }
        return [
            ReplayMapOut(
                hash=r.map_hash, name=r.name, width=r.width, height=r.height,
                palette=list(r.palette or []), tiles=r.tiles,
                resources=list(r.resources or []),
                image=(
                    (images[r.image_id].image if full else
                     (images[r.image_id].thumb or images[r.image_id].image))
                    if r.image_id in images else None
                ),
                image_id=r.image_id if r.image_id in images else None,
                # 검수된 지형(요청) - 재생 화면이 어림 대신 쓴다.
                walk=images[r.image_id].walk if r.image_id in images else None,
                # 대표맵 이름(요청: 지형 검수 창 제목) — 그림에 사람이 지어 둔 그 이름.
                image_name=images[r.image_id].name if r.image_id in images else None,
            )
            for r in rows
        ]

    async def map_catalog(self) -> MapCatalog:
        """제어판용 — 어떤 맵이 있고 몇 경기를 치렀는지, 그림은 어느 것을 가리키는지."""
        rows = await self._repo.list_map_catalog()
        images = await self._repo.list_minimap_images()
        return MapCatalog(
            maps=[
                MapCatalogEntry(
                    hash=r.map_hash, name=r.name, width=r.width, height=r.height,
                    matches=int(r.matches or 0), image_id=r.image_id,
                )
                for r in rows
            ],
            # 제어판 목록의 그림 칸은 56px이고, 지형 검수도 가로 128로 줄여 본다 —
            # 512px 작은 판이면 충분하다(옛 행은 thumb가 없으니 image로 되돌아간다).
            images=[
                MinimapImageOut(id=i.id, name=i.name, image=i.thumb or i.image, walk=i.walk)
                for i in images
            ],
        )

    async def create_minimap_image(self, payload: MinimapImageWrite) -> MinimapImageOut:
        if not payload.image:
            raise ValidationError("미니맵 그림을 함께 올려야 합니다.")
        row = MinimapImage(
            name=payload.name, image=payload.image, thumb=payload.thumb or None,
            walk=payload.walk or None,
        )
        self._repo.add_minimap_image(row)
        await self._repo.flush()
        if payload.hashes:
            await self._repo.assign_minimap_image(payload.hashes, row.id)
        await self._session.commit()
        return MinimapImageOut(id=row.id, name=row.name, image=row.image, walk=row.walk)

    async def update_minimap_image(self, image_id: int, payload: MinimapImageWrite) -> MinimapImageOut:
        """등록된 미니맵의 이름·그림을 고친다(요청: 미니맵 메뉴에서 그림 변경).

        지우고 다시 올리는 길밖에 없던 자리다 — 그런데 지우면 그 그림에 붙어 있던 맵 매핑이
        통째로 풀려서, 그림 한 장을 더 나은 것으로 바꾸려다 매핑을 처음부터 다시 해야 했다.
        여기서 고치면 id가 그대로라 매핑도 그대로다.

        그림을 안 보내면(None) 이름만 고친다 — 수백 KB짜리를 이름 때문에 다시 올릴 이유가
        없다. hashes를 함께 주면 그 맵들이 이 그림을 가리키게 된다(create와 같은 규칙)."""
        row = await self._repo.get_minimap_image(image_id)
        if row is None:
            raise NotFoundError("미니맵 그림을 찾을 수 없습니다.")
        row.name = payload.name
        if payload.image:
            row.image = payload.image
            # 그림을 갈면 작은 판도 함께 갈아야 한다 — 안 보냈으면 옛 작은 판이 남아
            # 목록에는 옛 그림이, 재생 화면에는 새 그림이 뜨는 어긋남이 생긴다.
            row.thumb = payload.thumb or None
        # 지형(요청) - 보냈을 때만 갈고, 빈 문자열은 지우기다.
        if payload.walk is not None:
            row.walk = payload.walk or None
        if payload.hashes:
            await self._repo.assign_minimap_image(payload.hashes, row.id)
        await self._session.commit()
        return MinimapImageOut(id=row.id, name=row.name, image=row.image, walk=row.walk)

    async def update_minimap_walk(self, image_id: int, walk: str) -> MinimapImageOut:
        """지형 격자만 갈아 끼운다(요청: 회원 누구나) — 빈 문자열이면 지운다."""
        row = await self._repo.get_minimap_image(image_id)
        if row is None:
            raise NotFoundError("미니맵 그림을 찾을 수 없습니다.")
        row.walk = walk or None
        await self._session.commit()
        return MinimapImageOut(id=row.id, name=row.name, image=row.image, walk=row.walk)

    async def delete_minimap_image(self, image_id: int) -> None:
        row = await self._repo.get_minimap_image(image_id)
        if row is None:
            raise NotFoundError("미니맵 그림을 찾을 수 없습니다.")
        # 가리키던 맵을 먼저 떼어 낸다 — SQLite는 기본 설정에서 FK ON DELETE를 안 지킨다.
        await self._repo.assign_minimap_image(
            [m.map_hash for m in await self._repo.list_map_catalog() if m.image_id == image_id],
            None,
        )
        await self._repo.delete_minimap_image(image_id)
        await self._session.commit()

    async def assign_minimap_image(self, payload: MinimapAssignWrite) -> int:
        """맵 여러 개를 한 그림에 붙이거나 떼어 낸다(요청: 거의 같은 맵을 한데 묶기)."""
        if payload.image_id is not None and await self._repo.get_minimap_image(payload.image_id) is None:
            raise NotFoundError("미니맵 그림을 찾을 수 없습니다.")
        changed = await self._repo.assign_minimap_image(payload.hashes, payload.image_id)
        await self._session.commit()
        return changed

    async def list_minimap_choices(self) -> list[MinimapChoice]:
        """맵연결 고르기 목록(요청: 아무나) — 그림 썸네일과 그 그림에 연결된 리플레이 수까지
        (요청: 목록 왼쪽 썸네일, 오른쪽 작은 글씨로 연결된 리플레이 수). 그림은 맵 종류
        수(몇 장)뿐이라 목록에 실어도 가볍다."""
        counts = await self._repo.count_matches_by_image()
        return [
            # 이 목록의 그림이 놓이는 자리는 44px 칸이다 — 작은 판이면 넘치고도 남는다.
            MinimapChoice(id=i.id, name=i.name, image=i.thumb or i.image, matches=counts.get(i.id, 0))
            for i in await self._repo.list_minimap_images()
        ]

    async def put_unit_tracks(self, match_id: int, data: str) -> None:
        """개체 트랙(v2) 저장(요청: 별도 테이블로 비교) — 경기가 없으면 404."""
        if await self._repo.get(match_id) is None:
            raise NotFoundError("경기를 찾을 수 없습니다.")
        await self._repo.upsert_unit_tracks(match_id, data)
        await self._session.commit()

    async def bake_unit_tracks(self, match_id: int) -> str:
        """리플레이를 실제로 시뮬레이션해 참값 트랙을 굽고 저장한다(openbw.py).

        굽기가 안 되는 사정(덤퍼·게임 자료 없음, 리플레이 없음, 시간 초과)은 **예외로 올린다** —
        수동으로 부른 사람은 왜 안 됐는지 알아야 하기 때문이다. 자동 굽기 쪽은 이걸 감싸서
        조용히 삼킨다(등록이 굽기 때문에 실패하면 안 된다).

        돌려주는 값은 사람에게 보일 한 줄 요약이다.
        """
        if not openbw.is_available():
            raise ValidationError(openbw.unavailable_reason())
        match = await self._repo.get(match_id)
        if match is None:
            raise NotFoundError("경기를 찾을 수 없습니다.")
        replay = match.result_row.replay if match.result_row else None
        if replay is None:
            raise ValidationError("이 경기에는 리플레이가 없습니다.")

        # 저장소가 로컬이 아닐 수도 있으니 바이트로 읽어 임시 파일에 내려놓고 돌린다 —
        # 덤퍼는 파일 경로를 받는다. 리플레이는 수백 KB라 부담이 없다.
        try:
            content = await self._storage.read(replay.file_path)
        except OSError as exc:
            raise ValidationError(f"리플레이 파일을 읽지 못했습니다: {exc}") from exc

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "match.rep"
            await asyncio.to_thread(path.write_bytes, content)
            data = await openbw.bake(path)
        if data is None:
            raise ValidationError("참값을 굽지 못했습니다. 서버 로그를 확인하세요.")

        await self._repo.upsert_motion_tracks(match_id, data)
        await self._session.commit()
        return f"참값 트랙 {len(data) / 1_048_576:.2f}MB 를 구웠습니다."

    async def get_motion_tracks(self, match_id: int) -> str | None:
        """참값 자취 조회 — 없으면 None(아직 안 구웠거나 못 굽는 경기)."""
        return await self._repo.get_motion_tracks(match_id)

    async def get_unit_tracks(self, match_id: int) -> str | None:
        """개체 트랙(v2) 조회 — 없으면 None(프론트가 토글을 감춘다)."""
        return await self._repo.get_unit_tracks(match_id)

    async def mark_viewed(self, match_id: int) -> None:
        """게임 상세 페이지 조회수 +1(요청: 테이블에 기록) — 없는 경기는 404."""
        if await self._repo.bump_view_count(match_id) == 0:
            raise NotFoundError("경기를 찾을 수 없습니다.")
        await self._session.commit()

    async def link_replay_map(self, map_hash: str, payload: ReplayMapLinkWrite, member_id: int) -> ReplayMapOut:
        """게임 상세의 맵연결(요청: 아무나 저장된 맵 중 골라 연결) — 이 경기의 맵 행이
        고른 미니맵 그림을 가리키게 하고, 마지막 연결자(회원 pk)와 시각을 남긴다."""
        if payload.image_id is not None and await self._repo.get_minimap_image(payload.image_id) is None:
            raise NotFoundError("미니맵 그림을 찾을 수 없습니다.")
        changed = await self._repo.link_replay_map(map_hash, payload.image_id, member_id)
        if changed == 0:
            raise NotFoundError("맵 격자를 찾을 수 없습니다.")
        await self._session.commit()
        maps = await self.list_replay_maps([map_hash])
        if not maps:
            raise NotFoundError("맵 격자를 찾을 수 없습니다.")
        return maps[0]

    async def rewrite_summary(self, match_id: int, payload: SummaryRewrite) -> None:
        """등록된 경기의 리플레이 파생 데이터를 다시 써 넣는다(재분석) — 경기 내용은 안 건드린다.

        파생 데이터라 규칙이 좋아지면 옛 경기도 함께 좋아져야 하는데, 지금까지는
        리플레이를 다시 올리는 수밖에 없었다. 화면이 리플레이를 내려받아 다시 분석한 결과를
        여기로 보내면 그 값만 갈아 끼운다.
        """
        match = await self._repo.get(match_id)
        if match is None or match.result_row is None:
            raise NotFoundError("경기결과를 찾을 수 없습니다.")
        rr = match.result_row
        # 재분석 김에 리플레이 파일명도 새 양식으로(지적: 긴 한글 이름은 브루드워가
        # 인식을 못 한다) — 옛 경기의 저장된 표시 이름을 SG_경기번호로 통일.
        if rr.replay is not None:
            rr.replay.display_name = build_replay_display_name(match)
        map_hash = await self._store_replay_map(payload.map_data)
        if map_hash is not None:
            rr.map_hash = map_hash
        # 리플레이에서 다시 나오는 값들(요청: 모든 데이터를 재분석). 사람이 정한 것
        # (등록자·등록시각·경기번호·날짜·분류·승패·회원 연결)은 안 건드린다. 값이 None인
        # 항목도 안 덮어쓴다 — 어쩌다 한 지표를 못 읽어도 멀쩡한 기존 값을 날리지 않게
        # (merge_replay와 같은 원칙).
        if payload.map_name is not None:
            rr.map_name = payload.map_name
        if payload.game_started_at is not None:
            rr.game_started_at = _to_utc_naive(payload.game_started_at)
        if payload.duration_seconds is not None:
            rr.duration_seconds = payload.duration_seconds
        by_name = {s.raw_name: s for s in (payload.slots or [])}
        matched = 0
        for p in match.participants:
            s = by_name.get(p.player_name)
            if s is None:
                continue
            matched += 1
            if s.race:
                p.race = s.race
            if s.apm is not None:
                p.apm = s.apm
            if s.eapm is not None:
                p.eapm = s.eapm
            if s.cmd_count is not None:
                p.cmd_count = s.cmd_count
            if s.effective_cmd_count is not None:
                p.effective_cmd_count = s.effective_cmd_count
            if s.build_count is not None:
                p.build_count = s.build_count
            if s.build_mix is not None:
                p.build_mix = _mix_json(s.build_mix)
        # 짝이 하나도 안 맞으면 로그로 남긴다 — 경기 행은 새것이 되는데 수치(참가자
        # 행)만 옛것으로 남는, 겉보기에는 "재분석했는데 통계가 그대로"인 상태가 된다. 짝은
        # 리플레이 원본 게임 아이디(player_name)로 맞추므로 그 이름이 바뀐 경기에서 이런
        # 일이 난다. 조용히 넘기면 다음에도 원인을 못 찾는다.
        if payload.slots and matched == 0:
            logger.warning(
                "재분석: 참가자 짝이 하나도 안 맞았습니다 — match_id=%s 보낸이름=%s 저장된이름=%s",
                match_id, sorted(by_name), sorted(p.player_name for p in match.participants),
            )
        await self._session.commit()

    async def _store_replay_map(self, data: ReplayMapData | None) -> str | None:
        """맵 격자를 저장하고 그 해시를 돌려준다 — 이미 있으면 저장하지 않는다(요청: 같은
        맵이면 하나를 함께 쓰자). 격자를 안 보냈으면 None이고, 그때 호출부는 기존 연결을
        그대로 둔다."""
        if data is None:
            return None
        if not await self._repo.replay_map_exists(data.hash):
            # 세이브포인트 삽입(지적: 동시 재분석 둘이 같은 새 맵을 넣으면 유니크 제약으로
            # 요청째 500) — 충돌이면 그 삽입만 물리고 남이 넣은 같은 맵을 그대로 쓴다.
            # 같은 배치의 뒤이은 exists 조회는 flush된 행을 본다(예전 flush 주석과 동일).
            await self._repo.add_replay_map_safely(ReplayMap(
                map_hash=data.hash, name=data.name,
                width=data.width, height=data.height,
                palette=data.palette, tiles=data.tiles,
                resources=data.resources,
            ))
        return data.hash

    async def create_match(self, payload: GameResultWrite, *, actor: Member) -> GameResult:
        await self._ensure_no_duplicate_members(payload)
        members_by_id = await self._ensure_members_exist(payload.team1 + payload.team2)
        await self._remember_placeholder_raw_names(payload)
        await self._ensure_player_name_classifications(payload.team1, payload.team2, members_by_id)

        match_date = date.fromisoformat(payload.date)
        match_no_base = _match_no_base(match_date, payload.game_started_at)
        match_no_suffix = await self._repo.next_match_no_suffix(match_no_base)

        # replay=None 을 명시해 flush 이후 접근 시 비동기 lazy-load가 걸리지 않게 한다.
        match = GameResult(
            match_no=f"{match_no_base}{match_no_suffix:02d}",
            match_date=match_date,
            match_type=payload.match_type,
            result_row=GameOutcome(
                result=payload.result,
                map_name=payload.map_name,
                game_started_at=payload.game_started_at,
                duration_seconds=payload.duration_seconds,
                map_hash=await self._store_replay_map(payload.map_data),
                replay=None,
            ),
            created_by=actor.pk,
            updated_by=actor.pk,
        )
        match.participants = self._build_participants(
            payload.team1, payload.team2, members_by_id, actor_pk=actor.pk
        )
        self._repo.add(match)
        await self._repo.flush()

        if payload.replay is not None:
            await self._apply_replay(match, payload.replay, actor_pk=actor.pk)

        await self._session.commit()
        # 등록으로 달라진 포인트/순위를 스냅샷으로 남긴다 — 배치 등록(연속 POST)은 활동
        # 서비스가 시간창 안에서 한 이벤트로 합친다. 실패해도 등록 자체는 성공으로 둔다.
        return await self._repo.refresh(match)

    async def merge_replay(self, payload: GameResultReplayMerge, *, actor: Member) -> GameResult | None:
        """이미 등록된 경기(game_started_at 일치)에 리플레이 내부 정보만 다시 덮어쓴다(요청:
        중복 리플레이 재등록 시 새 컬럼 백필). 지표(APM/커맨드/생산)·맵·플레이시간은 항상,
        승패는 리플레이가 승자를 확실히 가린 경우(payload.result is not None)에만 갱신한다.
        경기번호·등록자·등록일시·메모·match_date·match_type·참가자 회원연결은 절대 건드리지
        않는다. 참가자는 player_name(리플레이 원본 게임 아이디)으로 매칭한다.

        기존 값을 실수로 지우지 않도록, 지표/종족은 '새 값이 있을 때만' 덮어쓴다 — build_count
        처럼 예전엔 없던 컬럼(NULL)이 이번에 채워지는 백필은 되지만, 어쩌다 파싱이 한 지표를
        못 읽어 None으로 와도 기존 정상값을 날리지 않는다. 게임 시각이 매칭되는 경기가 없으면
        None을 돌려준다(중복이 아니었던 것 — 호출부가 조용히 건너뛴다)."""
        target = _to_utc_naive(payload.game_started_at)
        rows = await self._repo.list_match_id_game_started_ats()
        match_id = next((mid for mid, dt in rows if dt is not None and _to_utc_naive(dt) == target), None)
        if match_id is None:
            return None
        match = await self._repo.get(match_id)
        if match is None or match.result_row is None:
            return None

        rr = match.result_row
        if payload.map_name is not None:
            rr.map_name = payload.map_name
        if payload.duration_seconds is not None:
            rr.duration_seconds = payload.duration_seconds
        if payload.result is not None:
            rr.result = payload.result
        # 옛 경기에 미니맵을 채워 넣는 자리 — 리플레이를 다시 올리면 여기로 들어온다.
        map_hash = await self._store_replay_map(payload.map_data)
        if map_hash is not None:
            rr.map_hash = map_hash

        by_name = {s.player_name: s for s in payload.players}
        for p in match.participants:
            s = by_name.get(p.player_name)
            if s is None:
                continue
            if s.race:
                p.race = s.race
            if s.apm is not None:
                p.apm = s.apm
            if s.eapm is not None:
                p.eapm = s.eapm
            if s.cmd_count is not None:
                p.cmd_count = s.cmd_count
            if s.effective_cmd_count is not None:
                p.effective_cmd_count = s.effective_cmd_count
            if s.build_count is not None:
                p.build_count = s.build_count
            if s.build_mix is not None:
                p.build_mix = _mix_json(s.build_mix)
            p.updated_by = actor.pk

        # 중복 리플레이 재등록이면 저장된 다운로드 파일명도 최신 포맷으로 갱신한다(요청). 위에서
        # rr.map_name이 갱신됐을 수 있으니 그 뒤에 만든다. 리플레이 파일이 없으면 갱신 안 함.
        if rr.replay is not None:
            rr.replay.display_name = build_replay_display_name(match)
            rr.replay.updated_by = actor.pk

        match.updated_by = actor.pk
        await self._session.commit()
        return await self._repo.refresh(match)

    async def delete_match(self, match_id: int, *, actor: Member) -> None:
        match = await self.get_match(match_id)
        self._ensure_can_delete(actor)
        match_type = match.match_type
        if match.result_row.replay is not None:
            await self._storage.delete(match.result_row.replay.file_path)
        # 경기를 지우면 delete-orphan으로 result_row가, 그 아래로 replay 행도 함께
        # 삭제된다(파일은 위에서 이미 삭제).
        await self._repo.delete(match)
        await self._session.commit()
        # 삭제로 달라진 포인트/순위 스냅샷 — 연속 삭제(배치)도 한 이벤트로 합쳐진다.

    async def delete_all_matches(self, *, actor: Member) -> int:
        """모든 경기기록을 삭제한다(운영자 제어판). 리플레이(.rep) 파일과 replays 행도 함께
        지운다. 반환값은 삭제된 경기 수.

        matches.replay_id → replays.id라, 경기(matches)를 먼저 지운 뒤 replays를 지운다
        (반대로 하면 FK 참조 때문에 막힌다). 참가자/결과는 matches의 FK CASCADE로 정리된다."""
        self._ensure_can_delete(actor)
        for _display_name, file_path in await self._repo.list_all_replays():
            try:
                await self._storage.delete(file_path)
            except Exception:
                pass
        count = await self._repo.delete_all_matches()
        await self._repo.delete_all_replays()
        await self._session.commit()
        return count

    # (삭제) 등록/삭제 직후 랭크 스냅샷을 남기던 훅 — 하루에도 여러 번 변동 카드가 활동에
    # 떠서 목록이 그 카드로 도배됐다(지적: "지금처럼 등록/삭제 시마다 계산을 하면 너무 자주
    # 목록에 노출되는 문제"). 이제 재집계는 매일 자정 스케줄러 한 곳에서만 한다
    # (app/main.py의 _ranking_shift_scheduler → RankingShiftService.recompute_daily).

    # ── 경기 댓글(메모) ─────────────────────────────────────────────────────
    # 게시판 댓글처럼 회원 누구나 한 줄(최대 50자)을 남기고, 본인/운영자만 수정·삭제한다.
    # 본문에 @닉네임으로 언급하면 그 회원을 mentions에 함께 저장해 렌더 시 칩으로 그린다.

    async def _resolve_mentions(self, target_member_ids: list[str]) -> list[Member]:
        seen: set[str] = set()
        members: list[Member] = []
        for member_id in target_member_ids:
            if member_id in seen:
                continue
            seen.add(member_id)
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            members.append(m)
        return members

    def _ensure_can_modify(self, match: GameResult, actor: Member) -> None:
        if not actor.has_any_role("0202") and match.created_by != actor.pk:
            raise ForbiddenError("작성자 또는 운영자만 수정할 수 있습니다.")

    def _ensure_can_delete(self, actor: Member) -> None:
        # 삭제는 수정보다 엄격하게 — 작성자 본인이어도 안 되고 운영자만 가능하다(오삭제 방지).
        if not actor.has_any_role("0202"):
            raise ForbiddenError("운영자만 삭제할 수 있습니다.")

    def _player_name(self, slot: GameResultSlot, members_by_id: dict[str, Member]) -> str:
        # 리플레이에서 파싱된 원본 게임 아이디는 무슨 일이 있어도 그대로 보존한다 — 회원으로
        # 매칭됐든, 비회원/컴퓨터로 남았든 상관없다(models.py의 GameResultParticipant.player_name
        # 참고). 예전엔 비회원/컴퓨터면 이 값을 버리고 공용 예약값으로 덮어썼는데, 그러면
        # 그 사람이 실제로 누구였는지가 영영 사라져 나중에 회원과 연결할 수조차 없었다.
        if slot.player_name:
            return slot.player_name
        # 리플레이 등록은 모든 슬롯의 이름을 항상 채워 보내므로 여기 도달하면 회원 슬롯인데
        # 이름만 빠진 경우다 — player_name은 절대 비워둘 수 없으므로, 그 회원이 등록해둔
        # 게임 아이디 중 가장 최근 것으로 대신한다(등록된 별칭이 없으면 방어적으로 배틀태그).
        member = members_by_id[slot.member_id]
        if member.replay_aliases:
            return member.replay_aliases[-1].raw_name
        return member.battletag

    def _build_participants(
        self,
        team1: list[GameResultSlot],
        team2: list[GameResultSlot],
        members_by_id: dict[str, Member],
        *,
        actor_pk: int,
    ) -> list[GameResultParticipant]:
        participants = [
            GameResultParticipant(
                team="team1",
                position=i,
                race=slot.race,
                player_name=self._player_name(slot, members_by_id),
                apm=slot.apm,
                eapm=slot.eapm,
                cmd_count=slot.cmd_count,
                effective_cmd_count=slot.effective_cmd_count,
                build_count=slot.build_count,
                build_mix=_mix_json(slot.build_mix),
                created_by=actor_pk,
                updated_by=actor_pk,
            )
            for i, slot in enumerate(team1)
        ]
        participants += [
            GameResultParticipant(
                team="team2",
                position=i,
                race=slot.race,
                player_name=self._player_name(slot, members_by_id),
                apm=slot.apm,
                eapm=slot.eapm,
                cmd_count=slot.cmd_count,
                effective_cmd_count=slot.effective_cmd_count,
                build_count=slot.build_count,
                build_mix=_mix_json(slot.build_mix),
                created_by=actor_pk,
                updated_by=actor_pk,
            )
            for i, slot in enumerate(team2)
        ]
        return participants

    async def _ensure_player_name_classifications(
        self,
        team1: list[GameResultSlot],
        team2: list[GameResultSlot],
        members_by_id: dict[str, Member],
    ) -> None:
        """실제 회원 슬롯에 그 회원의 replay_aliases에 아직 없는 새 player_name이 쓰이면,
        그 이름을 즉시 이 회원의 별칭으로 등록해 이후 조회(_to_match_slot, 통계 집계)가
        곧바로 이 회원으로 연결되게 한다 — "수기입력 시 선택한 이름을 회원과 연결한다"가
        구현되는 지점이다. 이미 이 회원의 별칭이면 손대지 않는다. 다른 회원이나 컴퓨터/
        비회원으로 이미 등록된 이름을 쓰려고 하면(예: 오타로 남의 아이디를 고른 경우)
        충돌로 보고 거부한다 — replay_aliases.raw_name은 항상 하나의 대상만 가리켜야
        목록/통계가 꼬이지 않는다."""
        for slot in team1 + team2:
            if is_placeholder_slot(slot.member_id) or not slot.player_name:
                continue
            member = members_by_id[slot.member_id]
            if slot.player_name in {a.raw_name for a in member.replay_aliases}:
                continue
            existing = await self._repo.get_alias_by_raw_name(slot.player_name)
            if existing is not None:
                raise ValidationError(f"'{slot.player_name}'은(는) 이미 다른 대상으로 등록된 이름입니다.")
            member.replay_aliases.append(ReplayAlias(raw_name=slot.player_name, kind="member"))

    async def _remember_placeholder_raw_names(self, payload: GameResultWrite) -> None:
        """리플레이에서 컴퓨터(AI)/비회원으로 등록되는 슬롯의 분류를 replay_aliases에 남긴다.

        새 게임아이디(rawName)는 저장 전에 반드시 회원/컴퓨터/비회원 중 하나로 확정되고,
        미분류인 채로 저장되는 경로가 없다(요청: "매핑 안 하고 저장할 경로가 없으니 그
        분류를 alias 테이블에 자동 등록하는 게 맞다"). 그래서 회원은 _associate_member_aliases가,
        컴퓨터/비회원은 여기서 각각 kind='computer'/'unregistered'로 자동 등록해
        replay_aliases를 모든 게임아이디의 단일 레지스트리로 유지한다 — 게임아이디 화면에
        컴퓨터/비회원도 바로 뜨고, 다음 리플레이에서 같은 이름을 또 물어보지 않는다.
        (예전엔 비회원을 일부러 안 남겼는데, 그 이름을 나중에 회원으로 연결할 기회를
        지키려는 의도였다 — 이제 그 연결은 게임아이디 화면 재매핑으로 하면 되고,
        set_replay_name_mapping이 기존 별칭을 지우고 회원으로 다시 건다.)

        이미 있는 매핑은 절대 건드리지 않는다 — 특히 kind='member'(누군가의 게임 아이디로
        이미 등록된 이름)를 덮어쓰면 그 회원의 과거 경기 매칭이 통째로 어긋난다."""
        for slot in payload.team1 + payload.team2:
            if not slot.player_name:
                continue
            if is_computer_slot(slot.member_id):
                kind = "computer"
            elif is_unregistered_slot(slot.member_id):
                kind = "unregistered"
            else:
                continue
            if await self._repo.replay_alias_exists(slot.player_name):
                continue
            self._repo.add_replay_name_classification(ReplayAlias(raw_name=slot.player_name, kind=kind))

    async def _ensure_no_duplicate_members(self, payload: GameResultWrite) -> None:
        # 컴퓨터/비회원 슬롯은 실제 회원이 아니라 여러 개 있어도 "중복"이 아니므로 제외한다.
        ids = [
            s.member_id
            for s in payload.team1 + payload.team2
            if not is_placeholder_slot(s.member_id)
        ]
        if len(ids) != len(set(ids)):
            raise ValidationError("같은 회원이 양 팀에 동시에 포함될 수 없습니다.")

    async def _ensure_members_exist(self, slots: list[GameResultSlot]) -> dict[str, Member]:
        members_by_id: dict[str, Member] = {}
        for member_id in {s.member_id for s in slots if not is_placeholder_slot(s.member_id)}:
            member = await self._member_repo.get_by_login_id(member_id)
            if member is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            members_by_id[member_id] = member
        return members_by_id

    async def _apply_replay(self, match: GameResult, payload: ReplayUpload, *, actor_pk: int) -> None:
        if not is_data_url(payload.url):
            return  # 기존에 저장된 리플레이 그대로 유지 (변경 없음)

        if not payload.original_name.lower().endswith(".rep"):
            raise ValidationError("스타크래프트 리플레이 파일(.rep)만 첨부할 수 있습니다.")

        content, content_type = decode_data_url(payload.url)
        # 표시(다운로드) 이름은 서버가 경기번호(match_no)를 포함해 최신 포맷으로 만든다(요청) —
        # 프론트가 보낸 display_name은 무시한다(경기번호는 서버만 안다). original_name은 보존.
        display_name = build_replay_display_name(match)
        # 저장 파일명은 알아보기 쉬운 생성 이름(display_name)으로 — 다운로드 시 그대로 쓰인다.
        stored = await self._storage.save(
            subdir="replays",
            filename=display_name,
            content=content,
            content_type=content_type,
        )
        # 시작시각/맵은 result_row에 이미 반영돼 있으니 그 값을 replay 메타에도 함께 보존한다.
        game_started_at = match.result_row.game_started_at if match.result_row else None
        map_name = match.result_row.map_name if match.result_row else None
        if match.result_row.replay is not None:
            await self._storage.delete(match.result_row.replay.file_path)
            match.result_row.replay.original_name = payload.original_name
            match.result_row.replay.display_name = display_name
            match.result_row.replay.file_path = stored.path
            match.result_row.replay.content_type = content_type
            match.result_row.replay.file_size = len(content)
            match.result_row.replay.game_started_at = game_started_at
            match.result_row.replay.map_name = map_name
            match.result_row.replay.updated_by = actor_pk
        else:
            match.result_row.replay = Replay(
                original_name=payload.original_name,
                display_name=display_name,
                file_path=stored.path,
                content_type=content_type,
                file_size=len(content),
                game_started_at=game_started_at,
                map_name=map_name,
                created_by=actor_pk,
                updated_by=actor_pk,
            )
