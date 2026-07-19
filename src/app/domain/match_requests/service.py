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
    MatchRequestListOut,
    MatchRequestOut,
    MatchRequestTargetOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository

# 페이지당 노출 개수(요청: "페이지당 5개까지 노출").
PAGE_SIZE = 5
# 최소 지목 인원(요청: "최소 두명을 지목").
MIN_TARGETS = 2


class MatchRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MatchRequestRepository(session)
        self._member_repo = MemberRepository(session)

    def _to_out(
        self, request: MatchRequest, actor: Member, *, author: Member | None = None
    ) -> MatchRequestOut:
        recommends = list(request.recommends)
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
            mine=request.created_by == actor.pk,
            targets=[
                MatchRequestTargetOut(
                    memberId=t.member.id if t.member else "",
                    nickname=t.member.nickname if t.member else "(탈퇴한 회원)",
                )
                for t in targets
            ],
            iAmTarget=any(t.member_pk == actor.pk for t in targets),
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
        cleaned = text.strip()
        if not cleaned:
            raise ValidationError("요청 내용을 입력해주세요.")

        # @태그로 지목한 회원들 — 중복 제거 후 최소 2명, 본인은 지목 불가(자신에게 도전장을
        # 보낼 수 없으므로).
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
                raise ValidationError("자기 자신은 지목할 수 없어요.")
            targets.append(m)
        if len(targets) < MIN_TARGETS:
            raise ValidationError("최소 두 명 이상을 @태그로 지목해주세요.")

        # 같은 구성원(지목된 사람들의 집합)으로 이미 살아있는 요청이 있으면 막는다(요청: "같은
        # 구성원의 요청이 존재하면 만들 수 없음", "구성원에서 지목자(작성자)는 제외"). 작성자와
        # 무관하게 @태그로 지목된 대상 집합이 완전히 같은지로만 판정한다.
        new_targets = frozenset(m.pk for m in targets)
        for existing in await self._repo.list_all_alive():
            existing_targets = frozenset(t.member_pk for t in existing.targets)
            if existing_targets == new_targets:
                raise ValidationError("같은 구성원으로 올라온 대결 요청이 이미 있어요.")

        request = MatchRequest(text=cleaned, created_by=actor.pk, updated_by=actor.pk)
        # member=m을 채워 두면 _to_out에서 target.member 지연로드가 안 일어난다. recommends는
        # 빈 컬렉션으로 미리 세팅해 "로드됨(비어있음)"으로 표시(지연로드 회피).
        request.targets = [MatchRequestTarget(member_pk=m.pk, member=m) for m in targets]
        request.recommends = []
        self._repo.add(request)
        await self._session.commit()
        return self._to_out(request, actor, author=actor)

    async def toggle_recommend(self, request_id: int, *, actor: Member) -> MatchRequestOut:
        request = await self._get_alive(request_id)
        existing = await self._repo.get_recommend(request_id, actor.pk)
        if existing is not None:
            # 다시 누르면 추천 취소 — delete-orphan cascade로 flush 때 삭제된다.
            request.recommends.remove(existing)
        else:
            request.recommends.append(
                MatchRequestRecommend(member_pk=actor.pk, created_by=actor.pk, updated_by=actor.pk)
            )
        await self._session.commit()
        return self._to_out(request, actor)

    async def fulfill(self, request_id: int, *, actor: Member) -> None:
        """"들어주기"로 실제 도전장을 보낸 뒤, 그 요청을 목록에서 내린다(요청: "요청을 들어준
        경우 해당 요청은 사라짐"). 자기 자신의 요청은 들어줄 수 없다(자신에게 도전장을 보낼 수
        없으므로)."""
        request = await self._get_alive(request_id)
        if request.created_by == actor.pk:
            raise ValidationError("자신의 요청은 들어줄 수 없어요.")
        # @태그로 지목된 사람만 들어줄 수 있다(요청: "그 사람들만 들어줄 수 있는 시스템").
        if not any(t.member_pk == actor.pk for t in request.targets):
            raise ForbiddenError("이 요청에 지목된 회원만 들어줄 수 있어요.")
        request.fulfilled_at = datetime.now(UTC)
        request.updated_by = actor.pk
        await self._session.commit()

    async def delete_request(self, request_id: int, *, actor: Member) -> None:
        """작성자 본인 또는 운영자가 요청을 내린다. (들어주기와 같은 소프트삭제.)"""
        request = await self._get_alive(request_id)
        is_admin = actor.has_any_role("0202")
        if request.created_by != actor.pk and not is_admin:
            raise ForbiddenError("자신이 올린 요청만 내릴 수 있어요.")
        request.fulfilled_at = datetime.now(UTC)
        request.updated_by = actor.pk
        await self._session.commit()

    async def _get_alive(self, request_id: int) -> MatchRequest:
        request = await self._repo.get(request_id)
        if request is None or request.fulfilled_at is not None:
            raise NotFoundError("대결 요청을 찾을 수 없어요.")
        return request
