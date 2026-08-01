from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.match_requests.repository import MatchRequestRepository
from app.domain.match_requests.schemas import (
    MatchRequestAuthor,
    MatchRequestInboxItem,
    MatchRequestInboxOut,
    MatchRequestTargetOut,
)
from app.domain.members.models import Member


class MatchRequestService:
    """대결 요청은 인박스(언급 알림)만 남았다 — 목록/등록/추천/완료는 쓰는 화면이 없어져
    지웠다. 등록 경로가 사라졌으므로 새 알림은 더 이상 생기지 않는다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MatchRequestRepository(session)

    async def list_inbox(self, *, actor: Member) -> MatchRequestInboxOut:
        """내가 언급된, 아직 안 읽은(read_at NULL) 살아있는 요청들 — 앱 열 때 인박스 팝업용."""
        targets = await self._repo.list_unread_targets_for(actor.pk)
        items: list[MatchRequestInboxItem] = []
        for t in targets:
            req = t.request
            author = req.creator
            items.append(
                MatchRequestInboxItem(
                    requestId=req.id,
                    text=req.text,
                    author=MatchRequestAuthor(
                        memberId=author.id if author else "",
                        nickname=author.nickname if author else "(탈퇴한 회원)",
                        avatar=author.avatar_url if author else None,
                    ),
                    createdAt=req.created_at,
                    mentioned=[
                        MatchRequestTargetOut(
                            memberId=mt.member.id if mt.member else "",
                            nickname=mt.member.nickname if mt.member else "(탈퇴한 회원)",
                        )
                        for mt in req.targets
                    ],
                )
            )
        return MatchRequestInboxOut(items=items)

    async def mark_inbox_read(self, *, actor: Member) -> None:
        """내 안 읽은 언급 알림을 모두 읽음 처리한다(인박스 팝업 닫을 때)."""
        now = datetime.now(UTC)
        for t in await self._repo.list_unread_targets_for(actor.pk):
            t.read_at = now
        await self._session.commit()

