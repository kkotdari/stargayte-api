import calendar
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.feed.models import FeedComment, FeedCommentMention, RankingShift
from app.domain.feed.repository import FeedCommentRepository
from app.domain.feed.schemas import (
    FeedCommentAuthor,
    FeedCommentMentionOut,
    FeedCommentOut,
    RankingShiftEntry,
    RankingShiftOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository


def to_comment_out(comment: FeedComment, *, actor_pk: int | None, is_admin: bool) -> FeedCommentOut:
    author = comment.creator
    return FeedCommentOut(
        id=comment.id,
        targetType=comment.target_type,
        targetId=comment.target_id,
        text=comment.text,
        author=FeedCommentAuthor(
            memberId=author.id if author else "",
            nickname=author.nickname if author else "(탈퇴한 회원)",
            avatar=author.avatar_url if author else None,
        ),
        createdAt=comment.created_at,
        updatedAt=comment.updated_at,
        canEdit=is_admin or (actor_pk is not None and comment.created_by == actor_pk),
        mentions=[
            FeedCommentMentionOut(
                memberId=m.member.id if m.member else "",
                nickname=m.member.nickname if m.member else "(탈퇴한 회원)",
            )
            for m in comment.mentions
        ],
    )


class FeedCommentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = FeedCommentRepository(session)
        self._member_repo = MemberRepository(session)

    async def list_for_target(self, target_type: str, target_id: int, *, actor: Member) -> list[FeedCommentOut]:
        is_admin = actor.has_any_role("0202")
        comments = await self._repo.list_by_target(target_type, target_id)
        return [to_comment_out(c, actor_pk=actor.pk, is_admin=is_admin) for c in comments]

    async def create(
        self, target_type: str, target_id: int, text: str,
        target_member_ids: list[str], *, actor: Member,
    ) -> FeedCommentOut:
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("댓글 내용을 입력해주세요.")
        mentions = await self._resolve_mentions(target_member_ids)
        comment = FeedComment(
            target_type=target_type, target_id=target_id, text=cleaned,
            created_by=actor.pk, updated_by=actor.pk,
        )
        comment.mentions = [FeedCommentMention(member_pk=m.pk, member=m) for m in mentions]
        self._session.add(comment)
        await self._session.commit()
        refreshed = await self._repo.get(comment.id)
        assert refreshed is not None
        return to_comment_out(refreshed, actor_pk=actor.pk, is_admin=actor.has_any_role("0202"))

    async def update(
        self, comment_id: int, text: str, target_member_ids: list[str], *, actor: Member
    ) -> FeedCommentOut:
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
        comment.mentions = [FeedCommentMention(member_pk=m.pk, member=m) for m in mentions]
        await self._session.commit()
        refreshed = await self._repo.get(comment.id)
        assert refreshed is not None
        return to_comment_out(refreshed, actor_pk=actor.pk, is_admin=actor.has_any_role("0202"))

    async def delete(self, comment_id: int, *, actor: Member) -> None:
        comment = await self._get_for_edit(comment_id, actor)
        await self._session.delete(comment)
        await self._session.commit()

    async def _get_for_edit(self, comment_id: int, actor: Member) -> FeedComment:
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
        matchType=snap.match_type,
        reason=snap.reason,
        createdAt=snap.created_at,
        matchIds=list(snap.match_ids or []),
        shifts=[RankingShiftEntry.model_validate(e) for e in snap.shifts or []],
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
    """포인트·순위 스냅샷 — 경기 등록/삭제 때마다 계산해 저장하고, 변동분을 피드에 내보낸다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(self, limit: int) -> list[RankingShiftOut]:
        """피드에 보여줄 이벤트 — 변동(shifts)이 실제로 있었던 스냅샷만."""
        from sqlalchemy import select

        stmt = select(RankingShift).order_by(RankingShift.created_at.desc()).limit(limit * 3)
        rows = list((await self._session.scalars(stmt)).all())
        return [_to_ranking_shift_out(s) for s in rows if s.shifts][:limit]

    async def _latest(self, match_type: str) -> RankingShift | None:
        from sqlalchemy import select

        stmt = (
            select(RankingShift)
            .where(RankingShift.match_type == match_type)
            .order_by(RankingShift.created_at.desc(), RankingShift.id.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

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
        """하루 한 번(자정) 순위표를 다시 집계해 변동이 있으면 스냅샷으로 남긴다(요청).

        예전에는 경기 등록/삭제 때마다 계산했는데, 그러면 하루에도 여러 번 변동 카드가
        피드에 떠서 목록이 그 카드로 도배됐다(지적: "지금처럼 등록/삭제 시마다 계산을
        하면 너무 자주 목록에 노출되는 문제"). 이제 하루치를 모아 한 번만 남긴다 —
        같은 날 열 경기를 등록해도 카드는 하나다.

        기준선(같은 달 스냅샷)이 없으면 변동 없이 기준선만 남긴다. 최초 도입 직후와
        매달 1일이 그런 경우인데, 그때 전원을 '신규 진입'으로 쏟아내면 안 되기 때문이다.
        """
        for match_type in ("0101", "0102"):
            standings = await self._compute_standings(match_type, compute_entries)
            latest = await self._latest(match_type)
            # 순위표가 그대로면 남길 이야기가 없다.
            if latest is not None and list(latest.standings or []) == standings:
                continue
            baseline = latest is None or _period_of(latest.created_at) != _current_period()
            base = [] if baseline else list(latest.standings or [])
            self._session.add(RankingShift(
                match_type=match_type,
                reason="seed" if baseline else "daily",
                match_ids=[],
                standings=standings,
                shifts=[] if baseline else self._diff(base, standings),
            ))
        await self._session.commit()

    async def reseed_now(self, compute_entries) -> dict[str, int]:
        """지금 데이터 기준으로 기준선을 다시 깐다 — 제어판에서 손으로 누르는 1회용(요청).

        변동 없이(reason="seed", shifts=[]) 저장되므로 피드 목록에는 안 보인다. 이번 달
        기준선이 이미 있으면 그 행을 갱신한다 — 여러 번 눌러도 행이 쌓이지 않는다.
        돌려주는 값은 유형별로 몇 명이 순위표에 들어갔는지다.
        """
        out: dict[str, int] = {}
        for match_type in ("0101", "0102"):
            standings = await self._compute_standings(match_type, compute_entries)
            latest = await self._latest(match_type)
            if (
                latest is not None
                and latest.reason == "seed"
                and _period_of(latest.created_at) == _current_period()
            ):
                latest.standings = standings
                latest.shifts = []
            else:
                self._session.add(RankingShift(
                    match_type=match_type, reason="seed",
                    match_ids=[], standings=standings, shifts=[],
                ))
            out[match_type] = len(standings)
        await self._session.commit()
        return out

    async def seed_if_empty(self, compute_entries) -> None:
        """최초 부팅 시 현재 순위표를 기준선으로 적재 — 다음 등록/삭제의 비교 대상이 된다.

        변동분 없이(reason="seed") 저장되므로 피드에는 보이지 않는다. 멱등.

        멱등 판정은 반드시 경기유형별로 한다(지적) — 예전엔 테이블 전체 건수를 봤는데,
        개인전 행만 있고 팀전 행이 없는 DB에서는 팀전 기준선이 영영 안 깔려서 팀전 첫
        이벤트가 전원 신규 진입으로 나왔다.
        """
        from sqlalchemy import func, select

        for match_type in ("0101", "0102"):
            count = await self._session.scalar(
                select(func.count()).select_from(RankingShift)
                .where(RankingShift.match_type == match_type)
            )
            if count and count > 0:
                continue
            standings = await self._compute_standings(match_type, compute_entries)
            self._session.add(RankingShift(
                match_type=match_type, reason="seed",
                match_ids=[], standings=standings, shifts=[],
            ))
        await self._session.commit()
