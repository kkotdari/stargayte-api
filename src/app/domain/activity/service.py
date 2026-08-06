import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.activity.models import ActivityComment, ActivityCommentMention, RankingShift
from app.domain.activity.repository import ActivityCommentRepository
from app.domain.activity.schemas import (
    ActivityCommentAuthor,
    ActivityCommentMentionOut,
    ActivityCommentOut,
    ActivityItemOut,
    KNOWN_TARGET_TYPES,
    RankingShiftEntry,
    RankingShiftOut,
    RankingShiftSection,
    normalize_target_type,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository

logger = logging.getLogger(__name__)


def snapshot_sections(sections: object) -> list[dict]:
    """스냅샷의 칸 목록 — 지금 모양이 아닌 것은 조용히 버린다.

    sections는 JSON 칸이라 스키마가 강제되지 않는다. 지금은 [{matchType, standings,
    shifts}, ...]지만, 유형마다 한 행이던 시절의 행이 운영 DB에 그대로 남아 있어 모양이
    다르다 — 실제로 이것 때문에 운영에서 활동 목록(GET /activity/list)이 통째로 500이었다
    (dict가 오면 `for sec in sections`가 키(str)를 돌아 sec.get에서 터진다).

    그 줄 하나가 목록 전체를 죽이면 안 된다. 화면에 못 그릴 옛 행은 안 보이는 게 맞고,
    나머지 줄은 그대로 나와야 한다. 새 이벤트 목록(list_events)이 운영에서 멀쩡했던 건
    그쪽만 최근 100건으로 끊어 봐서 옛 행에 안 닿았기 때문이다 — 같은 잣대를 두 곳이
    함께 쓰도록 여기로 모은다.
    """
    if not isinstance(sections, list):
        return []
    return [sec for sec in sections if isinstance(sec, dict)]


def snapshot_has_shifts(sections: object) -> bool:
    """어느 칸에든 변동이 하나라도 있나 — 목록에 뜨는 스냅샷의 잣대."""
    return any((sec.get("shifts") or []) for sec in snapshot_sections(sections))


def to_comment_out(comment: ActivityComment, *, actor_pk: int | None, is_admin: bool) -> ActivityCommentOut:
    author = comment.creator
    return ActivityCommentOut(
        id=comment.id,
        targetType=normalize_target_type(comment.target_type),
        targetId=comment.target_id,
        text=comment.text,
        author=ActivityCommentAuthor(
            memberId=author.id if author else "",
            nickname=author.nickname if author else "(탈퇴한 회원)",
            avatar=author.avatar_url if author else None,
        ),
        createdAt=comment.created_at,
        updatedAt=comment.updated_at,
        canEdit=is_admin or (actor_pk is not None and comment.created_by == actor_pk),
        mentions=[
            ActivityCommentMentionOut(
                memberId=m.member.id if m.member else "",
                nickname=m.member.nickname if m.member else "(탈퇴한 회원)",
            )
            for m in comment.mentions
        ],
    )


class ActivityCommentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ActivityCommentRepository(session)
        self._member_repo = MemberRepository(session)

    async def list_all(self, *, actor: Member) -> list[ActivityCommentOut]:
        """활동가 목록과 함께 한 번에 받아 가는 전체 댓글(위 repository.list_all 주석).

        지금 쓰는 대상 종류가 아닌 줄은 건너뛴다. 대상 종류는 문자열 칸이라 무엇이든 들어갈
        수 있고, 운영 DB에는 예전 이름으로 저장된 줄이 남아 있다 — 그 한 줄이 스키마 검증에
        걸리면 전체 목록이 500이 되어 댓글 미리받기가 통째로 죽었다(운영에서 실제로 그랬다).
        대상별 조회(list_for_target)가 멀쩡했던 건 그쪽은 물어본 종류만 골라 오기 때문이다.
        여기서 버리는 줄은 어차피 화면 어느 카드에도 못 붙는다 — 그 종류의 카드가 없다.
        """
        is_admin = actor.has_any_role("0202")
        comments = await self._repo.list_all()
        return [
            to_comment_out(c, actor_pk=actor.pk, is_admin=is_admin)
            for c in comments
            if normalize_target_type(c.target_type) in KNOWN_TARGET_TYPES
        ]

    async def list_for_target(self, target_type: str, target_id: int, *, actor: Member) -> list[ActivityCommentOut]:
        is_admin = actor.has_any_role("0202")
        comments = await self._repo.list_by_target(normalize_target_type(target_type), target_id)
        return [to_comment_out(c, actor_pk=actor.pk, is_admin=is_admin) for c in comments]

    async def create(
        self, target_type: str, target_id: int, text: str,
        target_member_ids: list[str], *, actor: Member,
    ) -> ActivityCommentOut:
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("댓글 내용을 입력해주세요.")
        mentions = await self._resolve_mentions(target_member_ids)
        comment = ActivityComment(
            target_type=normalize_target_type(target_type), target_id=target_id, text=cleaned,
            created_by=actor.pk, updated_by=actor.pk,
        )
        comment.mentions = [ActivityCommentMention(member_pk=m.pk, member=m) for m in mentions]
        self._session.add(comment)
        await self._session.commit()
        refreshed = await self._repo.get(comment.id)
        assert refreshed is not None
        return to_comment_out(refreshed, actor_pk=actor.pk, is_admin=actor.has_any_role("0202"))

    async def update(
        self, comment_id: int, text: str, target_member_ids: list[str], *, actor: Member
    ) -> ActivityCommentOut:
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("댓글 내용을 입력해주세요.")
        comment = await self._get_for_edit(comment_id, actor)
        comment.text = cleaned
        comment.updated_by = actor.pk
        mentions = await self._resolve_mentions(target_member_ids)
        # 기존 멘션을 지우고 flush로 DELETE를 먼저 반영한 뒤 새로 넣는다 — 한 flush에서
        # 통째로 재할당하면 SQLAlchemy가 같은 멘션(comment_id, member_pk)을 지우기 전에
        # 다시 INSERT해 UNIQUE 제약에 걸려 500이 났다(버그: 같은 유저 언급을 유지한 채
        # 수정 시 실패). 지운 뒤 flush하면 재삽입 충돌이 없다.
        comment.mentions.clear()
        await self._session.flush()
        comment.mentions = [ActivityCommentMention(member_pk=m.pk, member=m) for m in mentions]
        await self._session.commit()
        refreshed = await self._repo.get(comment.id)
        assert refreshed is not None
        return to_comment_out(refreshed, actor_pk=actor.pk, is_admin=actor.has_any_role("0202"))

    async def delete(self, comment_id: int, *, actor: Member) -> None:
        comment = await self._get_for_edit(comment_id, actor)
        await self._session.delete(comment)
        await self._session.commit()

    async def _get_for_edit(self, comment_id: int, actor: Member) -> ActivityComment:
        comment = await self._repo.get(comment_id)
        if comment is None:
            raise NotFoundError("댓글을 찾을 수 없어요.")
        if not actor.has_any_role("0202") and comment.created_by != actor.pk:
            raise ForbiddenError("작성자 본인 또는 운영자만 수정·삭제할 수 있어요.")
        return comment

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


def _to_ranking_shift_out(snap: RankingShift) -> RankingShiftOut:
    return RankingShiftOut(
        id=snap.id,
        reason=snap.reason,
        createdAt=snap.created_at,
        matchIds=list(snap.match_ids or []),
        # 옛 모양으로 남은 칸은 여기서도 걸러 낸다(snapshot_sections) — 이 함수만 그냥
        # `for sec in snap.sections`를 돌고 있었다. 지금은 부르는 쪽이 변동 있는 스냅샷만
        # 넘겨 주어 안 걸리지만, 잣대가 한 곳만 다르면 다음에 부르는 자리가 생기는 순간
        # 같은 사고가 다시 난다(운영 500을 이미 그렇게 겪었다).
        sections=[
            RankingShiftSection(
                matchType=sec.get("matchType", ""),
                shifts=[RankingShiftEntry.model_validate(e) for e in sec.get("shifts") or []],
            )
            for sec in snapshot_sections(snap.sections)
        ],
    )


# 스냅샷 산정 기간 — 랭킹과 동일하게 "이번 달"(KST 기준) 성적만으로 매긴다.
KST = ZoneInfo("Asia/Seoul")


def _current_month_range() -> tuple[str, str]:
    today = datetime.now(KST).date()
    first = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return first.isoformat(), today.replace(day=last_day).isoformat()


def _period_of(dt: datetime) -> str:
    """그 시각이 속한 KST 기준 '연-월' — 그 스냅샷이 어느 달 순위표인지의 라벨.

    별도 컬럼을 두지 않고 created_at에서 뽑는다. 순위표는 언제나 '저장 시점이 속한 달'의
    성적으로 계산되므로(_current_month_range), 저장 시각의 달이 곧 산정 기간이다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    kst = dt.astimezone(KST)
    return f"{kst.year:04d}-{kst.month:02d}"


def _current_period() -> str:
    return _period_of(datetime.now(UTC))


class RankingShiftService:
    """포인트·순위 스냅샷 — 경기 등록/삭제 때마다 계산해 저장하고, 변동분을 활동에 내보낸다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(self, limit: int) -> list[RankingShiftOut]:
        """활동에 보여줄 이벤트 — 변동(shifts)이 실제로 있었던 스냅샷만."""
        from sqlalchemy import select

        # 기능이 꺼져 있으면 남아 있는 행이 있어도 카드로 내보내지 않는다(요청) — 끄는 것과
        # 지우는 것은 다른 일이라, 저장된 행은 그대로 두고 화면에서만 사라지게 한다.
        if not settings.ranking_shift_enabled:
            return []

        stmt = select(RankingShift).order_by(RankingShift.created_at.desc()).limit(limit * 3)
        rows = list((await self._session.scalars(stmt)).all())
        # 어느 칸에든 변동이 하나라도 있는 날만 보여준다 — 기준선만 남은 날은 다음 비교의
        # 재료일 뿐이라 카드가 될 이야기가 없다.
        shown = [s for s in rows if snapshot_has_shifts(s.sections)]
        return [_to_ranking_shift_out(s) for s in shown][:limit]

    async def _latest(self) -> RankingShift | None:
        """가장 최근 스냅샷 한 행 — 하루에 한 행이므로 유형을 안 가린다."""
        from sqlalchemy import select

        stmt = (
            select(RankingShift)
            .order_by(RankingShift.created_at.desc(), RankingShift.id.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    @staticmethod
    def _section_of(snap: RankingShift | None, match_type: str) -> dict:
        """그 스냅샷에서 이 유형 칸 — 없으면 빈 칸(순위표도 변동도 없음)."""
        for sec in snapshot_sections(snap.sections if snap else None):
            if sec.get("matchType") == match_type:
                return sec
        return {"matchType": match_type, "standings": [], "shifts": []}

    async def latest_snapshot_at(self) -> datetime | None:
        """가장 최근 스냅샷을 남긴 시각 — 유형 구분 없이 하나. 하루 한 번 집계를 '밀린 일
        찾아 하기'로 돌리는 스케줄러가 "오늘 이미 남겼나"를 이 값으로 판단한다
        (app/main.py의 _rank_recompute_due)."""
        from sqlalchemy import func, select

        return await self._session.scalar(select(func.max(RankingShift.created_at)))

    async def _compute_standings(self, match_type: str, compute_entries) -> list[dict]:
        """이번 달 기준 현재 순위표 — 활성 회원 중 그 유형 경기를 뛴 사람만.

        compute_entries(match_type, date_from, date_to)는 MatchService.get_stats를 감싼
        콜백이다(순환 임포트를 피하려고 호출부가 넘겨준다). 정렬은 서버 산정 결과
        (sort_order)를 그대로 쓰고, 완전 동률(tie_group)은 공동순위(1,1,3)로 매긴다.
        """
        date_from, date_to = _current_month_range()
        entries = await compute_entries(match_type, date_from, date_to)
        members = await MemberRepository(self._session).list_all()
        active = {m.id: m for m in members if m.status == "active"}
        ranked = sorted(
            (
                e for e in entries
                if e.sort_order is not None and e.tie_group is not None
                and e.overall.plays > 0 and e.member_id in active
            ),
            key=lambda e: e.sort_order,
        )
        standings: list[dict] = []
        rank = 0
        for i, e in enumerate(ranked):
            if i == 0 or e.tie_group != ranked[i - 1].tie_group:
                rank = i + 1
            standings.append({
                "memberId": e.member_id,
                "nickname": active[e.member_id].nickname,
                "points": round(e.rank_score or 0),
                "rank": rank,
            })
        return standings

    @staticmethod
    def _diff(base: list[dict], new: list[dict]) -> list[dict]:
        """직전 순위표 대비 순위 변동 — 새 순위표에 있는 사람 중 순위가 바뀐/신규인 사람만,
        변동 후 순위가 높은 순으로.

        순위와 함께 포인트도 담는다(요청: 순위 변동 옆에 포인트 변동도 수치로) — 몇 계단
        올랐는지만으로는 그게 한 판 차이인지 몰아친 결과인지 알 수가 없다.
        """
        base_rank = {e["memberId"]: e["rank"] for e in base}
        base_points = {e["memberId"]: e["points"] for e in base}
        shifts = [
            {
                "memberId": e["memberId"], "nickname": e["nickname"],
                "from": base_rank.get(e["memberId"]), "to": e["rank"],
                "fromPoints": base_points.get(e["memberId"]), "toPoints": e["points"],
            }
            for e in new
            if base_rank.get(e["memberId"]) != e["rank"]
        ]
        return sorted(shifts, key=lambda s: s["to"])

    async def recompute_daily(self, compute_entries) -> None:
        """하루 한 번(아침) 순위표를 다시 집계해 변동이 있으면 스냅샷으로 남긴다(요청).

        예전에는 경기 등록/삭제 때마다 계산했는데, 그러면 하루에도 여러 번 변동 카드가
        활동에 떠서 목록이 그 카드로 도배됐다(지적) — 이제 하루치를 모아 한 번만 남긴다.
        행도 하루에 하나다(요청): 개인전·팀전을 한 행의 sections에 나란히 담아, 카드도
        댓글도 하나로 묶인다.

        기준선(같은 달 스냅샷)이 없으면 변동 없이 기준선만 남긴다. 최초 도입 직후와
        매달 1일이 그런 경우인데, 그때 전원을 '신규 진입'으로 쏟아내면 안 되기 때문이다.
        """
        latest = await self._latest()
        baseline = latest is None or _period_of(latest.created_at) != _current_period()
        sections: list[dict] = []
        changed = False
        for match_type in ("0101", "0102"):
            standings = await self._compute_standings(match_type, compute_entries)
            base = [] if baseline else list(self._section_of(latest, match_type).get("standings") or [])
            if standings != base:
                changed = True
            sections.append({
                "matchType": match_type,
                "standings": standings,
                "shifts": [] if baseline else self._diff(base, standings),
            })
        # 어느 유형도 안 움직였으면 남길 이야기가 없다 — 다만 기준선이 아예 없으면
        # (첫 집계) 순위표가 비어 있어도 한 행은 남겨야 한다. 그게 다음 날의 비교 대상이다.
        if not changed and latest is not None:
            return
        self._session.add(RankingShift(
            reason="seed" if baseline else "daily", match_ids=[], sections=sections,
        ))
        await self._session.commit()

    async def reseed_now(self, compute_entries) -> dict[str, int]:
        """지금 데이터 기준으로 기준선을 다시 깐다 — 제어판에서 손으로 누르는 1회용(요청).

        변동 없이(reason="seed", shifts 빈 배열) 저장되므로 활동 목록에는 안 보인다. 이번 달
        기준선이 이미 있으면 그 행을 갱신한다 — 여러 번 눌러도 행이 쌓이지 않는다.
        돌려주는 값은 유형별로 몇 명이 순위표에 들어갔는지다.
        """
        out: dict[str, int] = {}
        sections: list[dict] = []
        for match_type in ("0101", "0102"):
            standings = await self._compute_standings(match_type, compute_entries)
            sections.append({"matchType": match_type, "standings": standings, "shifts": []})
            out[match_type] = len(standings)
        latest = await self._latest()
        if (
            latest is not None
            and latest.reason == "seed"
            and _period_of(latest.created_at) == _current_period()
        ):
            latest.sections = sections
        else:
            self._session.add(RankingShift(reason="seed", match_ids=[], sections=sections))
        await self._session.commit()
        return out

    async def seed_if_empty(self, compute_entries) -> None:
        """최초 부팅 시 현재 순위표를 기준선으로 적재 — 다음 집계의 비교 대상이 된다.

        변동분 없이(reason="seed") 저장되므로 활동에는 보이지 않는다. 멱등: 행이 하나라도
        있으면 아무 일도 안 한다. 예전에는 유형마다 행이 따로 있어 "개인전만 있고 팀전은
        없는" 어중간한 상태를 따로 살펴야 했는데, 이제 한 행이 두 칸을 다 갖는다.
        """
        from sqlalchemy import func, select

        count = await self._session.scalar(select(func.count()).select_from(RankingShift))
        if count and count > 0:
            return
        sections = [
            {
                "matchType": match_type,
                "standings": await self._compute_standings(match_type, compute_entries),
                "shifts": [],
            }
            for match_type in ("0101", "0102")
        ]
        self._session.add(RankingShift(reason="seed", match_ids=[], sections=sections))
        await self._session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 활동 목록 번호 매기기
#
# 화면의 활동 목록은 세 곳(도전장·게임결과·랭크변동)에서 받은 것을 시간순으로 섞은 뒤,
# 같은 자리에서 이어 친 게임결과를 한 줄로 묶어 만든다. "전체에서 몇 번째 줄인가"는 그
# 셋을 다 봐야 나오는 값이라, 어느 한 도메인 엔드포인트도 답할 수 없다. 게다가 게임결과는
# 페이지 단위로 나눠 받으므로 프론트는 늘 일부만 쥐고 있다 — 거기서 센 번호는 아직 안
# 받아온 과거만큼 통째로 어긋난다. 그래서 전부를 한 번에 보는 이 자리에서 센다(요청).
#
# 번호를 DB에 박지 않는 이유(요청): 한참 지난 리플레이를 나중에 등록하는 일이 흔해서,
# 등록 시점에 번호를 주면 그 줄이 목록 한가운데에 끼어들며 제 번호와 자리가 어긋난다.
# 이 번호는 이름표가 아니라 "지금 목록에서 몇 번째"라는 표시일 뿐이므로 매번 다시 센다.
_KST = ZoneInfo("Asia/Seoul")
# 게임 한 판이 아니라 "한 자리에서 이어 한 묶음"이 줄의 단위다. 밤에 시작한 자리는 자정을
# 넘겨 이어지는 일이 흔해 달력 날짜로 끊으면 같은 자리가 두 줄로 쪼개진다 — 새벽 경기는
# 전날 밤의 연장으로 보고 전날에 붙인다. 프론트의 SESSION_DAY_START_HOUR과 같은 값이라야
# 두 쪽이 같은 묶음을 만든다.
_SESSION_DAY_START_HOUR = 8


def _kst(dt: datetime) -> datetime:
    """저장된 시각을 KST 벽시계로 — tz가 없으면 UTC로 읽는다(DB에 따라 붙기도 안 붙기도 한다)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(_KST)


@dataclass(frozen=True)
class _Row:
    """목록의 한 줄 — 내용 없이 '무엇이 어디에 있는가'만.

    ids는 그 줄이 품은 원본 id들이다. 게임결과 묶음만 여럿이고(한 자리에서 이어 친 경기들)
    나머지는 하나다. 순서와 번호는 이 목록으로 정하고, 내용은 페이지에 실린 줄만 채운다.
    """

    kind: str
    key: str
    ids: list[int]


def _cursor_index(rows: list[_Row], cursor: str | None) -> int:
    """커서가 가리키는 줄 다음부터 — 커서는 앞 페이지 마지막 줄의 열쇠다.

    번호(index)가 아니라 열쇠로 잡는 이유는, 페이지를 받는 사이에 새 활동이 맨 위에 끼면
    번호가 통째로 밀려 다음 페이지가 겹치거나 건너뛰기 때문이다. 열쇠는 그 줄 자체를
    가리키므로 위에 무엇이 끼든 '그 다음'이 그대로다.

    그 줄이 사라졌으면(그 사이 삭제) 이어 붙일 자리를 알 수 없다 — 처음부터 준다.
    같은 줄을 다시 받는 편이, 조용히 건너뛰어 목록에 구멍이 나는 것보다 낫다.
    """
    if not cursor:
        return 0
    for idx, r in enumerate(rows):
        if r.key == cursor:
            return idx + 1
    return 0


class ActivityListService:
    """활동 목록의 줄 순서와 번호 — 프론트의 sortMsOf/challengeSortMs/sessionDateOf와
    같은 규칙을 서버에서 한 번 더 계산한다.

    같은 규칙이 두 곳에 있는 건 바람직하지 않지만, 프론트가 계속 카드를 그리는 이상
    (그쪽은 실제 내용을 쥐고 있어야 한다) 순서 규칙 자체는 양쪽에 있을 수밖에 없다.
    한쪽만 바뀌면 번호가 줄과 어긋나므로, 규칙을 고칠 때는 반드시 두 곳을 같이 고친다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _ordered_rows(self, *, actor: Member) -> tuple[list["_Row"], list]:
        """줄을 순서대로 — 내용 없이 (종류, 열쇠, 담긴 id들)만. 도전장은 이미 다 받아 왔으므로
        함께 돌려준다(페이지를 채울 때 다시 조회하지 않는다).

        번호도 이 목록에서 나오고 내용도 이 목록에서 채운다 — 한 곳에서 나와야 "번호는
        붙었는데 그 줄이 없다"가 원천적으로 안 생긴다.
        """
        from app.domain.challenges.service import ChallengeService

        # 도전장은 서비스를 거쳐 받는다 — 상태(pending/confirmed/done/discarded)가 조회
        # 시점 배치(무응답 폐기 등)까지 거쳐야 확정되고, 정렬이 그 상태에 달려 있다.
        challenges = await ChallengeService(self._session).list_challenges(actor=actor)
        games = await self._game_rows()
        shifts = await self._shift_rows()
        leagues = await self._league_match_rows()

        now_ms = datetime.now(UTC).timestamp() * 1000
        entries: list[tuple[float, str, str, str]] = []  # (정렬키ms, kind, key, 세션날짜)
        for c in challenges:
            entries.append((_challenge_sort_ms(c, now_ms), "challenge", f"c-{c.id}", ""))
        for gid, sort_ms, session_day in games:
            entries.append((sort_ms, "gameResult", f"ms-{gid}", session_day))
        for sid, sort_ms in shifts:
            entries.append((sort_ms, "rankingShift", f"rs-{sid}", ""))
        for mid, sort_ms in leagues:
            entries.append((sort_ms, "leagueMatch", f"lm-{mid}", ""))

        # 최신이 위. 같은 시각인 것들의 순서는 '넣은 순서'가 정한다 — 파이썬 sort는 안정
        # 정렬이라 동점끼리는 위에서 담은 차례가 그대로 남는다. 프론트도 [도전장, 게임결과,
        # 랭크변동] 순으로 배열을 만든 뒤 같은 안정 정렬을 쓰므로(Array.prototype.sort),
        # 담는 차례만 맞추면 동점 순서까지 똑같아진다. 시각을 모르는 경기는 죄다 자정으로
        # 잡혀 동점이 흔한데, 여기가 어긋나면 묶음의 '첫 경기'가 달라져 줄 열쇠가 안 맞는다.
        entries.sort(key=lambda e: -e[0])

        # 잇달아 붙은 같은 세션의 게임결과를 한 줄로 묶는다 — 사이에 다른 종류가 끼면
        # 거기서 끊긴다. 줄의 열쇠는 그 묶음의 첫 경기다.
        rows: list[_Row] = []
        i = 0
        while i < len(entries):
            _, kind, key, day = entries[i]
            if kind != "gameResult":
                rows.append(_Row(kind, key, [int(key.split("-", 1)[1])]))
                i += 1
                continue
            j = i + 1
            while j < len(entries) and entries[j][1] == "gameResult" and entries[j][3] == day:
                j += 1
            # 묶음에 담긴 경기 id 전부 — 이 줄을 펴면 나오는 카드들이다.
            ids = [int(entries[k][2].split("-", 1)[1]) for k in range(i, j)]
            rows.append(_Row("gameResultPost", key, ids))
            i = j
        return rows, challenges

    async def list_feed(
        self, *, actor: Member, storage, cursor: str | None, limit: int,
    ) -> "ActivityFeedOut":
        """활동 목록 한 페이지 — 줄 하나가 곧 아이템 하나이고, 내용도 댓글도 그 안에 있다.

        너 나와·랭크 변동·게임결과를 같은 아이템으로 취급한다(요청: 하나의 목록으로).
        예전에는 화면이 세 곳을 따로 받아 제 손으로 섞었는데, 그러면 섞는 규칙이 서버와
        화면 양쪽에 있어야 하고(번호는 서버가 세니까) 한쪽만 고쳐지는 순간 어긋난다.
        이제 섞는 자리가 한 곳뿐이다.

        순서와 번호는 늘 전체를 놓고 센다 — 페이지는 그 다음에 자른다. 화면이 쥔 것만
        세면 아직 안 받아온 과거만큼 번호가 통째로 어긋난다.
        """
        from app.domain.activity.schemas import ActivityFeedOut

        rows, challenges = await self._ordered_rows(actor=actor)
        total = len(rows)
        start = _cursor_index(rows, cursor)
        page = rows[start:start + limit]
        # 다음 페이지가 있으면 이 페이지 마지막 줄의 열쇠를 커서로 준다. 열쇠로 잡아 두면
        # 그 사이 새 활동이 맨 위에 끼어도 다음 페이지가 밀리거나 겹치지 않는다.
        has_more = start + limit < total
        return ActivityFeedOut(
            total=total,
            # 줄이 아니라 '건' — 게임결과 묶음은 그 안의 판 수만큼 센다.
            total_activities=sum(len(r.ids) for r in rows),
            items=await self._hydrate(page, start=start, total=total, challenges=challenges,
                                      actor=actor, storage=storage),
            next_cursor=page[-1].key if has_more and page else None,
        )

    async def _hydrate(
        self, page: list["_Row"], *, start: int, total: int, challenges: list,
        actor: Member, storage,
    ) -> list["ActivityItemOut"]:
        """이 페이지의 줄에 내용과 댓글을 채운다 — 조회는 페이지에 실제로 실린 것만."""
        from app.domain.activity.schemas import ActivityItemOut

        by_kind: dict[str, list[int]] = {}
        for r in page:
            by_kind.setdefault(r.kind, []).extend(r.ids)

        challenge_by_id = {c.id: c for c in challenges}
        shift_by_id = await self._shifts_by_id(by_kind.get("rankingShift", []))
        game_by_id = await self._games_by_id(by_kind.get("gameResultPost", []), actor=actor, storage=storage)
        league_by_id = await self._league_matches_by_id(by_kind.get("leagueMatch", []))

        # 댓글은 한 번에 받아 대상별로 나눠 담는다 — 줄마다 물어보면 페이지 크기만큼
        # 질의가 나간다. 댓글은 한 줄짜리라 전부 합쳐도 가볍다.
        comments = await ActivityCommentService(self._session).list_all(actor=actor)
        by_target: dict[tuple[str, int], list] = {}
        for c in comments:
            by_target.setdefault((c.target_type, c.target_id), []).append(c)

        def mine(kind: str, ids: list[int]) -> list:
            target = {
                "challenge": "challenge",
                "rankingShift": "rankingShift",
                "leagueMatch": "leagueMatch",
            }.get(kind, "gameResult")
            return [c for i in ids for c in by_target.get((target, i), [])]

        items: list[ActivityItemOut] = []
        for offset, r in enumerate(page):
            # 아래에서부터 센 번호(가장 오래된 줄이 1) — 위에서 세면 새 활동 하나에 전부 밀린다.
            no = total - (start + offset)
            try:
                items.append(ActivityItemOut(
                    key=r.key, kind=r.kind, no=no,
                    challenge=challenge_by_id.get(r.ids[0]) if r.kind == "challenge" else None,
                    ranking_shift=shift_by_id.get(r.ids[0]) if r.kind == "rankingShift" else None,
                    game_results=[game_by_id[i] for i in r.ids if i in game_by_id]
                    if r.kind == "gameResultPost" else [],
                    league_match=league_by_id.get(r.ids[0]) if r.kind == "leagueMatch" else None,
                    comments=mine(r.kind, r.ids),
                ))
            except Exception:
                # 한 줄이 못 그려져도 목록 전체를 죽이지 않는다. 이 엔드포인트는 이제 활동
                # 화면의 유일한 입구라 500이 나면 화면이 통째로 빈다 — 운영에서 옛 모양
                # 데이터 한 줄 때문에 그런 일을 세 번 겪었다(스냅샷 sections, 댓글 옛
                # target_type, 문자열로 담긴 약속한 날).
                # 빈 껍데기라도 번호를 달아 남긴다: 줄을 아예 빼면 아래 줄들의 번호가
                # 화면에서 한 칸씩 건너뛰어, 무엇이 빠졌는지조차 안 보인다.
                # 무엇이 걸렸는지는 열쇠와 함께 로그에 남긴다 — 조용히 삼키면 다음에도
                # 똑같이 원인을 못 찾는다.
                logger.exception("활동 목록에서 줄 하나를 못 그렸습니다 — key=%s kind=%s", r.key, r.kind)
                items.append(ActivityItemOut(key=r.key, kind=r.kind, no=no))
        return items

    async def _league_matches_by_id(self, ids: list[int]) -> dict[int, "LeagueMatchActivityOut"]:
        """이 페이지에 실린 리그 경기만 내용을 채운다 — 리그 이름과 라운드 이름까지 함께."""
        if not ids:
            return {}
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.domain.activity.schemas import (
            LeagueMatchActivityOut,
            LeagueMatchMemberOut,
            LeagueMatchTeamActivityOut,
        )
        from app.domain.leagues.models import LeagueMatch

        rows = (await self._session.scalars(
            select(LeagueMatch)
            .where(LeagueMatch.id.in_(ids))
            .options(selectinload(LeagueMatch.league))
        )).all()
        out: dict[int, LeagueMatchActivityOut] = {}
        for m in rows:
            if m.schedule_posted_at is None:
                continue
            def side(team) -> "LeagueMatchTeamActivityOut | None":
                if team is None:
                    return None
                return LeagueMatchTeamActivityOut(
                    label=team.label,
                    members=[
                        LeagueMatchMemberOut(memberId=r.member.id, nickname=r.member.nickname)
                        for r in team.roster if r.member
                    ],
                )

            won = (
                "a" if m.winner_team_id and m.team_a and m.team_a.id == m.winner_team_id
                else "b" if m.winner_team_id and m.team_b and m.team_b.id == m.winner_team_id
                else None
            )
            out[m.id] = LeagueMatchActivityOut(
                id=m.id, leagueId=m.league_id, leagueName=m.league.name,
                roundName=_round_name(m.round, m.league.draw_size),
                teamA=side(m.team_a), teamB=side(m.team_b),
                scheduledAt=m.scheduled_at,
                setsWonA=m.sets_won_a, setsWonB=m.sets_won_b,
                winnerSide=won,
                postedAt=m.schedule_posted_at, updatedAt=m.updated_at,
            )
        return out

    async def _shifts_by_id(self, ids: list[int]) -> dict[int, "RankingShiftOut"]:
        if not ids:
            return {}
        from sqlalchemy import select

        rows = (await self._session.scalars(
            select(RankingShift).where(RankingShift.id.in_(ids))
        )).all()
        return {s.id: _to_ranking_shift_out(s) for s in rows}

    async def _games_by_id(self, ids: list[int], *, actor: Member, storage) -> dict[int, object]:
        if not ids:
            return {}
        from app.domain.game_results.service import GameResultService, to_game_result_out

        service = GameResultService(self._session, storage)
        matches = await service.get_matches_by_ids(ids)
        # 별칭 표는 한 번만 만들어 목록 전체가 나눠 쓴다 — 경기마다 다시 조회하지 않는다.
        alias_by_player_name = await service.alias_by_player_name()
        is_admin = actor.has_any_role("0202")
        return {
            m.id: to_game_result_out(m, storage, alias_by_player_name,
                                     actor_pk=actor.pk, is_admin=is_admin)
            for m in matches
        }

    async def _game_rows(self) -> list[tuple[int, float, str]]:
        """(경기id, 정렬키ms, 세션날짜) — 시각을 아는 경기는 시작 시각, 모르면 그날 자정."""
        from sqlalchemy import select

        from app.domain.game_results.models import GameOutcome, GameResult

        # match_no 내림차순 — 프론트가 목록을 받는 순서(sort=latest)와 같아야 동점 순서가
        # 맞는다(위 entries.sort 주석 참고).
        result = await self._session.execute(
            select(GameResult.id, GameResult.match_date, GameOutcome.game_started_at)
            .outerjoin(GameOutcome, GameOutcome.match_id == GameResult.id)
            .order_by(GameResult.match_no.desc())
        )
        out: list[tuple[int, float, str]] = []
        for gid, match_date, started in result.all():
            if started is not None:
                local = _kst(started)
                # 시각을 모르는 경기(날짜만 등록된 건)는 자정으로 잡혀 있다 — 그걸 새벽으로
                # 읽고 전날로 밀면 안 되니, 시계가 있는 경기에만 이 보정을 건다.
                day = local.date()
                if local.hour < _SESSION_DAY_START_HOUR:
                    day = date.fromordinal(day.toordinal() - 1)
                out.append((gid, local.timestamp() * 1000, day.isoformat()))
            else:
                midnight = datetime(match_date.year, match_date.month, match_date.day, tzinfo=_KST)
                out.append((gid, midnight.timestamp() * 1000, match_date.isoformat()))
        return out

    async def _league_match_rows(self) -> list[tuple[int, float]]:
        """일정이 적힌 리그 경기만 — 활동에 뜨는 것은 그것뿐이다(요청: 리그 매치에 일정
        등록 시 활동에 띄움).

        schedule_posted_at이 없는 줄은 뺀다: 이 컬럼이 생기기 전부터 일정이 적혀 있던
        경기까지 지금 와서 새것으로 올릴 이유가 없다(models.py 주석 참고).

        꽂히는 자리는 너 나와와 같은 생각이다 — 아직 결과가 안 들어온 경기는 '남은 일'이라
        지금 바로 위에 두고, 결과가 들어온 경기는 그 경기가 열린 때에 둔다.
        """
        from sqlalchemy import select

        from app.domain.leagues.models import LeagueMatch

        rows = (await self._session.scalars(
            select(LeagueMatch)
            .where(LeagueMatch.scheduled_at.is_not(None))
            .where(LeagueMatch.schedule_posted_at.is_not(None))
            .where(LeagueMatch.is_dead.is_(False))
        )).all()
        now_ms = datetime.now(UTC).timestamp() * 1000
        out: list[tuple[int, float]] = []
        for m in rows:
            played = m.sets_won_a is not None or m.sets_won_b is not None
            at_ms = _kst(m.scheduled_at).timestamp() * 1000
            out.append((m.id, at_ms if played else max(at_ms, now_ms + 1)))
        # 최신이 위 — 위 entries.sort가 전체를 다시 세우지만, 같은 시각끼리의 순서는
        # 담은 차례가 정하므로(안정 정렬) 여기서도 정해 둔다.
        out.sort(key=lambda x: -x[1])
        return out

    async def _shift_rows(self) -> list[tuple[int, float]]:
        """화면에 실제로 뜨는 스냅샷만 — list_events와 같은 잣대여야 한다.

        어느 칸에든 변동이 하나라도 있는 날만 카드가 된다. 기준선만 남은 날(reason="seed",
        매달 1일과 최초 도입)은 다음 비교의 재료일 뿐이라 목록에 안 보인다. 그런 날까지
        세면 화면에 없는 줄이 번호를 먹어, 보이는 줄들의 번호가 중간중간 건너뛴다.
        """
        from sqlalchemy import select

        # 카드가 안 나가면 번호를 먹을 줄도 없다 — list_events와 같은 잣대다(위 주석).
        if not settings.ranking_shift_enabled:
            return []
        # created_at 내림차순 — list_events가 내려주는 순서와 같게(위 entries.sort 주석).
        result = await self._session.execute(
            select(RankingShift.id, RankingShift.created_at, RankingShift.sections)
            .order_by(RankingShift.created_at.desc())
        )
        return [
            (sid, _kst(created).timestamp() * 1000)
            for sid, created, sections in result.all()
            if snapshot_has_shifts(sections)
        ]


def _round_name(round_no: int, draw_size: int | None) -> str:
    """"8강"처럼 사람이 부르는 라운드 이름 — round는 결승까지의 거리라(leagues/models 주석)
    맨 끝 라운드가 결승이다. 판 크기를 모르면 라운드 번호만 적는다."""
    if not draw_size or draw_size < 2:
        return f"{round_no}라운드"
    total = max(1, (draw_size - 1).bit_length())
    left = total - round_no  # 결승까지 남은 판 수
    if left <= 0:
        return "결승"
    if left == 1:
        return "4강"
    return f"{2 ** (left + 1)}강"


def _sched_date(value: object) -> date | None:
    """약속한 날 — ChallengeOut은 이 값을 ISO 문자열로 담는다(to_challenge_out의 isoformat()).

    date가 올 것으로 넘겨짚고 .year를 바로 읽었다가 운영에서 활동 목록이 통째로 500이었다
    ('str' object has no attribute 'year'). 로컬에서 안 잡힌 건 순전히 데이터 탓이다:
    개발 DB의 도전장이 전부 폐기 상태라 이 함수가 그 갈래에서 먼저 돌아왔고, 테스트도
    날짜를 실제로 읽는 갈래까지 가는 것이 없었다.

    모양이 어느 쪽이든 받아 준다 — 문자열이면 파싱하고, 이미 date면 그대로, 못 읽으면
    None(날짜를 모르는 것과 같게 다룬다). 여기서 예외를 내면 그 한 줄이 목록 전체를 죽인다.
    """
    if value is None or isinstance(value, datetime):
        return value.date() if isinstance(value, datetime) else None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _challenge_sort_ms(c, now_ms: float) -> float:
    """너 나와가 활동 어디에 꽂히나 — 프론트 challengeSortMs와 같은 규칙.

    · 아직 안 끝난 것(응답대기·성사)은 "지금" 바로 위에 둔다. 약속한 날이 지났어도
      결과가 안 들어온 이상 그건 여전히 남은 일이다.
    · 취소·거절·버림·만료로 끝난 것은 '끝난 때'에 꽂는다 — 카드가 적는 시각과 같아야 한다.
    · 결과까지 들어온 것(완료)은 그날 경기들 아래(오전 8시)로 내린다.
    """
    base = _kst(c.scheduled_at or c.created_at).timestamp() * 1000
    day = _sched_date(c.scheduled_date)
    if c.status in ("pending", "confirmed"):
        end_of_day = (
            datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_KST).timestamp() * 1000
            if day else base
        )
        return max(end_of_day, now_ms + 1)
    if c.status == "discarded" and c.discarded_at:
        return _kst(c.discarded_at).timestamp() * 1000
    if day is None:
        return base
    return (
        datetime(day.year, day.month, day.day, tzinfo=_KST).timestamp() * 1000
        + _SESSION_DAY_START_HOUR * 3_600_000
    )
