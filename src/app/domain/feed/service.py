from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.feed.models import FeedComment, FeedCommentMention, RankShift
from app.domain.feed.repository import FeedCommentRepository
from app.domain.feed.schemas import (
    FeedCommentAuthor,
    FeedCommentMentionOut,
    FeedCommentOut,
    RankShiftCreate,
    RankShiftEntry,
    RankShiftOut,
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


def _to_shift_out(shift: RankShift) -> RankShiftOut:
    return RankShiftOut(
        id=shift.id,
        matchType=shift.match_type,
        createdAt=shift.created_at,
        entries=[RankShiftEntry.model_validate(e) for e in shift.entries],
    )


class RankShiftService:
    """랭킹 변동 이벤트 — 경기 결과 등록 시점의 변동분을 저장해 두고 피드에 그대로 내보낸다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recent(self, limit: int) -> list[RankShiftOut]:
        from sqlalchemy import select

        stmt = select(RankShift).order_by(RankShift.created_at.desc()).limit(limit)
        shifts = list((await self._session.scalars(stmt)).all())
        return [_to_shift_out(s) for s in shifts]

    async def create(self, payload: RankShiftCreate) -> RankShiftOut:
        shift = RankShift(
            match_type=payload.match_type,
            entries=[e.model_dump(by_alias=True) for e in payload.entries],
        )
        self._session.add(shift)
        await self._session.commit()
        await self._session.refresh(shift)
        return _to_shift_out(shift)
