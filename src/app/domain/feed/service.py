import calendar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.feed.models import FeedComment, FeedCommentMention, RankSnapshot
from app.domain.feed.repository import FeedCommentRepository
from app.domain.feed.schemas import (
    FeedCommentAuthor,
    FeedCommentMentionOut,
    FeedCommentOut,
    RankShiftEntry,
    RankSnapshotOut,
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


def _to_snapshot_out(snap: RankSnapshot) -> RankSnapshotOut:
    return RankSnapshotOut(
        id=snap.id,
        matchType=snap.match_type,
        reason=snap.reason,
        createdAt=snap.created_at,
        matchIds=list(snap.match_ids or []),
        shifts=[RankShiftEntry.model_validate(e) for e in snap.shifts or []],
    )


# 같은 이유(등록/삭제)의 이벤트가 이 시간 안에 연달아 오면 한 이벤트로 합친다 — 리플레이
# 배치 등록/연속 삭제는 경기 하나마다 API가 따로 오지만 사용자 입장에선 한 번의 행동이라
# 스냅샷(과 피드 변동 카드)도 하나만 남긴다(요청: "여러개 등록시 한번만 저장").
MERGE_WINDOW = timedelta(seconds=180)

# 스냅샷 산정 기간 — 랭킹과 동일하게 "이번 달"(KST 기준) 성적만으로 매긴다.
KST = ZoneInfo("Asia/Seoul")


def _current_month_range() -> tuple[str, str]:
    today = datetime.now(KST).date()
    first = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return first.isoformat(), today.replace(day=last_day).isoformat()


class RankSnapshotService:
    """포인트·순위 스냅샷 — 경기 등록/삭제 때마다 계산해 저장하고, 변동분을 피드에 내보낸다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(self, limit: int) -> list[RankSnapshotOut]:
        """피드에 보여줄 이벤트 — 변동(shifts)이 실제로 있었던 스냅샷만."""
        from sqlalchemy import select

        stmt = select(RankSnapshot).order_by(RankSnapshot.created_at.desc()).limit(limit * 3)
        rows = list((await self._session.scalars(stmt)).all())
        return [_to_snapshot_out(s) for s in rows if s.shifts][:limit]

    async def _latest(self, match_type: str) -> RankSnapshot | None:
        from sqlalchemy import select

        stmt = (
            select(RankSnapshot)
            .where(RankSnapshot.match_type == match_type)
            .order_by(RankSnapshot.created_at.desc(), RankSnapshot.id.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def _previous_of(self, snap: RankSnapshot) -> RankSnapshot | None:
        from sqlalchemy import select

        stmt = (
            select(RankSnapshot)
            .where(RankSnapshot.match_type == snap.match_type, RankSnapshot.id < snap.id)
            .order_by(RankSnapshot.id.desc())
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
        변동 후 순위가 높은 순으로."""
        base_rank = {e["memberId"]: e["rank"] for e in base}
        shifts = [
            {
                "memberId": e["memberId"], "nickname": e["nickname"],
                "from": base_rank.get(e["memberId"]), "to": e["rank"],
            }
            for e in new
            if base_rank.get(e["memberId"]) != e["rank"]
        ]
        return sorted(shifts, key=lambda s: s["to"])

    async def record_event(
        self, *, reason: str, match_ids: list[int], match_types: list[str], compute_entries,
    ) -> None:
        """경기 등록/삭제 직후 호출 — 영향받은 유형의 순위표를 다시 계산해 저장한다.

        직전 스냅샷과 순위표가 같으면 아무것도 안 남긴다. 같은 이유의 이벤트가
        MERGE_WINDOW 안에 연달아 오면(배치 등록/연속 삭제) 최신 스냅샷을 갱신해 한
        이벤트로 합치고, 변동분은 그 배치 시작 전 순위표와 다시 비교해 만든다.
        """
        for match_type in dict.fromkeys(match_types):  # 순서 보존 중복 제거
            if match_type not in ("0101", "0102"):
                continue
            standings = await self._compute_standings(match_type, compute_entries)
            latest = await self._latest(match_type)
            if latest is not None and list(latest.standings or []) == standings:
                continue

            mergeable = (
                latest is not None
                and latest.reason == reason
                and reason in ("register", "delete")
                and self._age_of(latest) < MERGE_WINDOW
            )
            if mergeable:
                prev = await self._previous_of(latest)
                base = list(prev.standings or []) if prev is not None else []
                latest.match_ids = list(dict.fromkeys([*(latest.match_ids or []), *match_ids]))
                latest.standings = standings
                latest.shifts = self._diff(base, standings)
            else:
                base = list(latest.standings or []) if latest is not None else []
                self._session.add(RankSnapshot(
                    match_type=match_type,
                    reason=reason,
                    match_ids=list(match_ids),
                    standings=standings,
                    shifts=self._diff(base, standings),
                ))
        await self._session.commit()

    async def seed_if_empty(self, compute_entries) -> None:
        """최초 부팅 시 현재 순위표를 기준선으로 적재 — 다음 등록/삭제의 비교 대상이 된다.

        변동분 없이(reason="seed") 저장되므로 피드에는 보이지 않는다. 멱등."""
        from sqlalchemy import func, select

        count = await self._session.scalar(select(func.count()).select_from(RankSnapshot))
        if count and count > 0:
            return
        for match_type in ("0101", "0102"):
            standings = await self._compute_standings(match_type, compute_entries)
            self._session.add(RankSnapshot(
                match_type=match_type, reason="seed",
                match_ids=[], standings=standings, shifts=[],
            ))
        await self._session.commit()

    @staticmethod
    def _age_of(snap: RankSnapshot) -> timedelta:
        created = snap.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return datetime.now(UTC) - created
