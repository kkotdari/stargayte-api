from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.match_requests.models import (
    MatchRequest,
    MatchRequestRecommend,
    MatchRequestTarget,
)
from app.domain.match_requests.repository import MatchRequestRepository
from app.domain.match_requests.schemas import (
    MatchRequestAuthor,
    MatchRequestInboxItem,
    MatchRequestInboxOut,
    MatchRequestListOut,
    MatchRequestOut,
    MatchRequestTargetOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository

# 페이지당 노출 개수(요청: "요청 목록은 3개씩 페이징 처리해줘 너무 많은 공간을 차지하지 않게").
PAGE_SIZE = 3


class MatchRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MatchRequestRepository(session)
        self._member_repo = MemberRepository(session)

    def _to_out(
        self, request: MatchRequest, actor: Member, *, author: Member | None = None
    ) -> MatchRequestOut:
        recommends = sorted(request.recommends, key=lambda r: r.created_at)
        targets = list(request.targets)
        # 갓 만든(아직 select로 다시 로드 안 한) 요청은 viewonly creator가 지연로드라
        # 비동기 밖에서 만지면 터진다 — 작성자를 명시로 넘겨 그 로드를 피한다.
        author = author if author is not None else request.creator
        return MatchRequestOut(
            id=request.id,
            text=request.text,
            author=MatchRequestAuthor(
                memberId=author.id if author else "",
                nickname=author.nickname if author else "(탈퇴한 회원)",
                avatar=author.avatar_url if author else None,
            ),
            createdAt=request.created_at,
            recommendCount=len(recommends),
            recommendedByMe=any(r.member_pk == actor.pk for r in recommends),
            recommenders=[
                MatchRequestAuthor(
                    memberId=r.member.id if r.member else "",
                    nickname=r.member.nickname if r.member else "(탈퇴한 회원)",
                    avatar=r.member.avatar_url if r.member else None,
                )
                for r in recommends
            ],
            mine=request.created_by == actor.pk,
            targets=[
                MatchRequestTargetOut(
                    memberId=t.member.id if t.member else "",
                    nickname=t.member.nickname if t.member else "(탈퇴한 회원)",
                )
                for t in targets
            ],
        )

    async def list_requests(self, *, actor: Member, page: int) -> MatchRequestListOut:
        page = max(0, page)
        total = await self._repo.count_alive()
        items = await self._repo.list_page(page=page, page_size=PAGE_SIZE)
        has_more = (page + 1) * PAGE_SIZE < total
        return MatchRequestListOut(
            items=[self._to_out(r, actor) for r in items],
            page=page,
            pageSize=PAGE_SIZE,
            total=total,
            hasMore=has_more,
        )

    async def create_request(
        self, text: str, target_member_ids: list[str], *, actor: Member
    ) -> MatchRequestOut:
        # @태그 기능은 폐지됐지만 언급된 사람은 계속 관리한다(요청). 최소 인원/중복 제한 없이
        # 0명 이상 언급 가능. 언급된 사람은 표시 + 알림 대상으로만 쓰고(권한 등 다른 기능과
        # 연결 안 함), 등록 시 각자에게 알림(target.read_at=NULL)이 간다.
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("요청 내용을 입력해주세요.")

        seen: set[str] = set()
        targets: list[Member] = []
        for member_id in target_member_ids:
            if member_id in seen:
                continue
            seen.add(member_id)
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            if m.pk == actor.pk:
                # 자기 자신 언급은 무시(자신에게 알림 보낼 필요 없음).
                continue
            targets.append(m)

        request = MatchRequest(text=cleaned, created_by=actor.pk, updated_by=actor.pk)
        # member=m을 미리 채워 _to_out의 지연로드를 피한다. read_at=NULL이면 안 읽은 알림.
        request.targets = [
            MatchRequestTarget(member_pk=m.pk, member=m) for m in targets
        ]
        request.recommends = []
        self._repo.add(request)
        await self._session.commit()
        return self._to_out(request, actor, author=actor)

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

    async def toggle_recommend(self, request_id: int, *, actor: Member) -> MatchRequestOut:
        request = await self._get_alive(request_id)
        existing = await self._repo.get_recommend(request_id, actor.pk)
        if existing is not None:
            # 다시 누르면 추천 취소 — delete-orphan cascade로 flush 때 삭제된다.
            request.recommends.remove(existing)
        else:
            # member 관계를 relationship 객체로도 명시해야 한다 — member_pk만 주면 이
            # 새로 만든(아직 DB에서 다시 읽어온 적 없는) 객체의 .member는 메모리에 채워지지
            # 않고, 비동기 세션에서 그 상태로 .member를 읽으면(_to_out의 recommenders
            # 직렬화) 동기 lazy-load가 걸려 즉시 500 에러가 난다 — 프론트는 그 에러를
            # 조용히 무시해서 "추천 눌러도 반영 안 됨"으로 보였다(실제로 지적받은 문제).
            request.recommends.append(
                MatchRequestRecommend(
                    member_pk=actor.pk, member=actor, created_by=actor.pk, updated_by=actor.pk
                )
            )
        await self._session.commit()
        return self._to_out(request, actor)

    async def complete_request(self, request_id: int, *, actor: Member) -> None:
        """대결이 성사되면 작성자 본인 또는 운영자가 "성사됨"으로 완료 처리한다(요청). 목록에서
        사라지는 소프트삭제(fulfilled_at)."""
        request = await self._get_alive(request_id)
        is_admin = actor.has_any_role("0202")
        if request.created_by != actor.pk and not is_admin:
            raise ForbiddenError("작성자 본인 또는 운영자만 완료 처리할 수 있어요.")
        request.fulfilled_at = datetime.now(UTC)
        request.updated_by = actor.pk
        await self._session.commit()

    async def _get_alive(self, request_id: int) -> MatchRequest:
        request = await self._repo.get(request_id)
        if request is None or request.fulfilled_at is not None:
            raise NotFoundError("대결 요청을 찾을 수 없어요.")
        return request
