from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.challenges.models import Challenge, ChallengeParticipant
from app.domain.challenges.repository import ChallengeRepository
from app.domain.challenges.schemas import (
    ChallengeAuthor,
    ChallengeCreate,
    ChallengeHistoryEntry,
    ChallengeOut,
    ChallengeOwnMemberOut,
    ChallengeTargetOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository

# 응답 없이 이 기간이 지나면(pending 상태 그대로) "무응답 거절"로 보고 폐기(휴지통) 처리한다
# — 요청: 72시간. 단, 예정 시각이 그보다 먼저면 예정 시각이 마감이다(_response_deadline).
# 프론트의 화면 표시 기준(ChallengeScreen.tsx의 EXPIRE_MS)과 같은 72시간이다.
RESPONSE_EXPIRE = timedelta(hours=72)
# 폐기(휴지통)된 지 이 기간이 지나면 소프트 삭제한다(요청: "휴지통은 폐기된 지 7일 지나면
# 사라짐, DB에서는 소프트 삭제").
TRASH_RETENTION = timedelta(days=7)


def _to_utc_naive(dt: datetime) -> datetime:
    # Postgres(timestamptz)는 aware로, SQLite는 tz 정보 없이 naive로 돌아오는 등 방언마다
    # 달라서, 비교 전에 항상 "UTC 기준 naive"로 맞춘다(matches/service.py의 같은 이름
    # 헬퍼와 같은 이유 — 여긴 그 모듈을 참조하지 않는 독립된 도메인이라 그대로 복제한다).
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _is_discarded(challenge: Challenge) -> bool:
    return challenge.discarded_at is not None


def _status_of(challenge: Challenge) -> str:
    """4개 상태만 있다 — 응답대기(pending)/성사(confirmed)/완료(done)/폐기(discarded).
    폐기는 discarded_at 하나로만 판정한다(명시적 거절/무응답 거절/미실시/레거시 취소가
    모두 그 순간 discarded_at을 찍는다). 예정 시간이 지나도 결과가 안 들어왔으면 계속
    성사(confirmed)다(요청: "예정 시간 지나도 결과 입력 안 된 건은 성사 상태")."""
    if challenge.discarded_at is not None:
        return "discarded"
    # 실제 승부 결과(creator/target/draw)가 들어오면 완료 — 미실시(not_held)는 결과 입력
    # 순간 discarded_at이 찍혀 위에서 이미 폐기로 걸러진다(enter_result 참고).
    if challenge.result_winner_side is not None:
        return "done"
    responses = [p.response for p in challenge.participants if p.side == "target"]
    if responses and all(r == "accepted" for r in responses):
        return "confirmed"
    return "pending"


# 응답 마감 = 요청일(created_at) + 72시간. 단, 예정 시각이 그보다 먼저면 예정 시각이
# 마감이다(요청: "예정시간이 그 전이면 예정시간 지나면 자동 거절 처리") — 그 시각까지
# 응답이 없으면 무응답 거절(폐기)된다.
def _response_deadline(challenge: Challenge) -> datetime:
    base = _to_utc_naive(challenge.created_at) + RESPONSE_EXPIRE
    if challenge.scheduled_at is not None:
        return min(base, _to_utc_naive(challenge.scheduled_at))
    return base


def _stamp_schedule_on_end(challenge: Challenge) -> None:
    """도전장이 폐기(거절/무응답/미실시)로 끝나는 순간, 예정 일시가 없으면 요청일+1일로
    찍는다 — 휴지통에서도 날짜별로 묶여 보이도록. 이미 시간이 정해진 도전장은 실제 매치
    시각이라 건드리지 않는다."""
    if challenge.scheduled_at is None:
        challenge.scheduled_at = challenge.created_at + RESPONSE_EXPIRE


def _discard(challenge: Challenge, now: datetime) -> None:
    """도전장을 폐기(휴지통)로 넘긴다 — discarded_at을 찍고, 날짜 그루핑용으로 예정 일시가
    없으면 스탬프한다. 이미 폐기된 건 그대로 둔다(최초 폐기 시각을 보존)."""
    if challenge.discarded_at is None:
        challenge.discarded_at = now
        _stamp_schedule_on_end(challenge)


def _losing_side(challenge: Challenge) -> str | None:
    # 승패가 갈린 경우에만 패자가 있다 — 무승부(draw)/미실시(not_held)/미입력(None)은 없다.
    if challenge.result_winner_side == "creator":
        return "target"
    if challenge.result_winner_side == "target":
        return "creator"
    return None


def _history_entry(challenge: Challenge) -> ChallengeHistoryEntry:
    targets = [p for p in challenge.participants if p.side == "target"]
    return ChallengeHistoryEntry(
        id=challenge.id,
        scheduledAt=challenge.scheduled_at,
        status=_status_of(challenge),
        resultWinnerSide=challenge.result_winner_side,
        targets=[
            ChallengeTargetOut(
                memberId=p.member.id,
                nickname=p.member.nickname,
                battletag=p.member.battletag,
                avatar=p.member.avatar_url,
                response=p.response,
                responseMessage=p.response_message,
            )
            for p in targets
        ],
        createdAt=challenge.created_at,
    )


def to_challenge_out(challenge: Challenge, history: list[Challenge] | None = None) -> ChallengeOut:
    targets = [p for p in challenge.participants if p.side == "target"]
    own_members = [
        p for p in challenge.participants if p.side == "creator" and p.member_pk != challenge.created_by
    ]
    return ChallengeOut(
        id=challenge.id,
        matchType=challenge.match_type,
        message=challenge.message,
        scheduledAt=challenge.scheduled_at,
        status=_status_of(challenge),
        createdBy=ChallengeAuthor(id=challenge.creator.id, nickname=challenge.creator.nickname),
        targets=[
            ChallengeTargetOut(
                memberId=p.member.id,
                nickname=p.member.nickname,
                battletag=p.member.battletag,
                avatar=p.member.avatar_url,
                response=p.response,
                responseMessage=p.response_message,
            )
            for p in targets
        ],
        ownMembers=[
            ChallengeOwnMemberOut(
                memberId=p.member.id,
                nickname=p.member.nickname,
                battletag=p.member.battletag,
                avatar=p.member.avatar_url,
            )
            for p in own_members
        ],
        createdAt=challenge.created_at,
        discardedAt=challenge.discarded_at,
        reappliedFromId=challenge.reapplied_from_id,
        resultWinnerSide=challenge.result_winner_side,
        history=[_history_entry(c) for c in (history or [])],
        fromMatchRequest=challenge.from_match_request,
    )


class ChallengeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ChallengeRepository(session)
        self._member_repo = MemberRepository(session)

    # 재대결 체인(reapplied_from_id를 따라 올라가는 사슬)에서 이 도전장보다 앞선 기록을
    # 오래된 순으로 모은다 — 단일 도전장 하나만 다루는 엔드포인트(respond/result/revenge)
    # 에서 쓴다. 체인은 실제로는 몇 단계 안 넘을 것으로 보고, 매번 get()으로 한 단계씩
    # 거슬러 올라가는 정도의 비용은 감수한다 —
    # list_challenges처럼 전체 목록을 한 번에 다룰 때는 그 안에서 이미 불러온 것들로
    # 메모리에서 처리한다(_history_chain_from_map 참고).
    async def _history_chain(self, challenge: Challenge) -> list[Challenge]:
        chain: list[Challenge] = []
        cur = challenge
        while cur.reapplied_from_id is not None:
            parent = await self._repo.get(cur.reapplied_from_id)
            if parent is None:
                break
            chain.append(parent)
            cur = parent
        chain.reverse()
        return chain

    def _history_chain_from_map(self, challenge: Challenge, by_id: dict[int, Challenge]) -> list[Challenge]:
        chain: list[Challenge] = []
        cur = challenge
        while cur.reapplied_from_id is not None:
            parent = by_id.get(cur.reapplied_from_id)
            if parent is None:
                break
            chain.append(parent)
            cur = parent
        chain.reverse()
        return chain

    async def _run_batches(self, challenges: list[Challenge]) -> None:
        """목록을 조회할 때마다 도는 가벼운 배치 두 가지 — 이미 로드된 목록을 메모리에서
        처리하고 바뀐 게 있을 때만 한 번 커밋한다.
         (1) 무응답 거절: 응답 마감(요청일+1일)이 지난 pending 도전장을 폐기(휴지통)로 넘긴다.
             지목자의 response는 그대로 pending으로 둔다 — 실제로 아무도 응답하지 않았다는
             사실을 보존한다(폐기 판정은 discarded_at 하나로만 한다).
         (2) 휴지통 7일 자동 비움: 폐기된 지 TRASH_RETENTION(7일)이 지난 건에 deleted_at을
             찍어 소프트 삭제한다 — 이후 어떤 조회에도 안 나온다(DB에는 남는다)."""
        now = _to_utc_naive(datetime.now(UTC))
        stamp = datetime.now(UTC)
        changed = False
        for c in challenges:
            if not _is_discarded(c) and _status_of(c) == "pending" and now > _response_deadline(c):
                _discard(c, stamp)  # 무응답 거절 → 폐기
                changed = True
            if (
                c.deleted_at is None
                and c.discarded_at is not None
                and now > _to_utc_naive(c.discarded_at) + TRASH_RETENTION
            ):
                c.deleted_at = stamp  # 폐기 7일 경과 → 소프트 삭제
                changed = True
        if changed:
            await self._session.commit()

    async def list_challenges(self, *, actor: Member) -> list[ChallengeOut]:
        challenges = await self._repo.list_all()  # deleted_at IS NULL만
        # 조회 시점 배치 — 무응답 거절 폐기 + 휴지통 7일 자동 비움(소프트 삭제).
        await self._run_batches(challenges)
        # 방금 배치가 소프트 삭제한 건은 이번 응답에서도 바로 빼준다(메모리 값 반영).
        alive = [c for c in challenges if c.deleted_at is None]
        by_id = {c.id: c for c in alive}
        # 재대결로 새 행이 생기면 원래(완료) 행은 그 새 행에 가려 목록에서 숨는다 — 단
        # 그 새 행이 폐기(휴지통)됐다면 가리지 못한다(요청: "완료된 건에 재대결했는데 그게
        # 버려지면 원래 완료된 건은 다시 재대결 신청 가능"). 그래서 폐기된 자식은
        # superseded 집합에서 제외해, 원래 완료 건이 목록에 되살아나 재대결 대상이 된다.
        superseded_ids = {
            c.reapplied_from_id for c in alive
            if c.reapplied_from_id is not None and not _is_discarded(c)
        }
        visible = [c for c in alive if c.id not in superseded_ids]
        return [
            to_challenge_out(c, history=self._history_chain_from_map(c, by_id))
            for c in visible
        ]

    async def create_challenge(self, payload: ChallengeCreate, *, actor: Member) -> ChallengeOut:
        target_members: list[Member] = []
        for member_id in payload.target_member_ids:
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            if m.pk == actor.pk:
                raise ValidationError("자기 자신을 지목할 수 없습니다.")
            target_members.append(m)

        # 본인은 자동 포함(뺄 수 없음)이라 own_team_member_ids엔 "본인 제외 나머지 내
        # 팀원"만 들어온다.
        own_members: list[Member] = []
        for member_id in payload.own_team_member_ids:
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            if m.pk == actor.pk:
                raise ValidationError("본인은 이미 자동으로 포함돼 있습니다.")
            own_members.append(m)

        # 폼에서 직접 고르지 않고 양쪽 인원수로 정한다: 양쪽 다 1명(나 혼자 vs 상대
        # 1명)이면 1:1, 그 외(어느 한쪽이라도 2명 이상)엔 팀전.
        match_type = (
            "0101" if len(target_members) == 1 and len(own_members) == 0 else "0102"
        )

        challenge = Challenge(
            match_type=match_type,
            message=payload.message.strip(),
            scheduled_at=payload.scheduled_at,
            from_match_request=payload.from_match_request,
            created_by=actor.pk,
            updated_by=actor.pk,
        )
        challenge.participants = (
            [ChallengeParticipant(member_pk=actor.pk, side="creator")]
            + [ChallengeParticipant(member_pk=m.pk, side="creator") for m in own_members]
            + [ChallengeParticipant(member_pk=m.pk, side="target") for m in target_members]
        )
        self._repo.add(challenge)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["creator", "participants"])
        return to_challenge_out(challenge)

    async def get_pending_for_me(self, *, actor: Member) -> list[ChallengeOut]:
        pending = await self._repo.list_pending_targets_for_member(actor.pk)
        challenges: list[Challenge] = []
        for p in pending:
            p.notified = True
            challenge = await self._repo.get(p.challenge_id)
            if challenge is None:
                continue
            # 폐기(휴지통)/소프트삭제된 도전장의 초대는 띄우지 않는다 — 상대가 팝업을 보기
            # 전에 거절 마감/미실시 등으로 이미 끝난 죽은 초대가 뜨면 응답해도 400만 난다.
            # notified는 위에서 이미 표시했으므로 다음 조회에서 다시 잡히지도 않는다.
            if _is_discarded(challenge) or challenge.deleted_at is not None:
                continue
            challenges.append(challenge)
        await self._session.commit()
        return [to_challenge_out(c) for c in challenges]

    async def get_result_pending_for_me(self, *, actor: Member) -> list[ChallengeOut]:
        """"결과 입력" 팝업 큐 — 내가 참가한(도전자편/상대편 무관) 확정 대결 중 예정
        일시가 지났는데 아직 결과가 안 들어온 것을, 참가자별로 한 번만 내려준다(요청:
        "결과 입력 팝업 확인 여부는 디비에 관리"). 초대 팝업(get_pending_for_me)과 같은
        원리 — 내려주는 즉시 "봤음"(result_notified)으로 표시해 다음 조회부터는 안 잡히고,
        결과 입력 자체는 대결 화면의 버튼으로 언제든 할 수 있다. 아직 자격이 안 되는
        것(예정 일시 전, 미확정)은 표시하지 않고 그대로 둬서, 나중에 자격이 되면 그때
        팝업 대상으로 잡힌다."""
        now = _to_utc_naive(datetime.now(UTC))
        candidates = await self._repo.list_result_unnotified_for_member(actor.pk)
        challenges: list[Challenge] = []
        for p in candidates:
            challenge = await self._repo.get(p.challenge_id)
            if challenge is None:
                continue
            if (
                _status_of(challenge) == "confirmed"
                and challenge.scheduled_at is not None
                and _to_utc_naive(challenge.scheduled_at) < now
                and challenge.result_winner_side is None
            ):
                p.result_notified = True
                challenges.append(challenge)
        await self._session.commit()
        return [to_challenge_out(c) for c in challenges]

    async def respond(
        self,
        challenge_id: int,
        response: str,
        *,
        actor: Member,
        scheduled_at: datetime | None = None,
        message: str = "",
    ) -> ChallengeOut:
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        target = next(
            (p for p in challenge.participants if p.side == "target" and p.member_pk == actor.pk), None
        )
        if target is None:
            raise ForbiddenError("이 도전장에 지목되지 않았습니다.")
        if _is_discarded(challenge):
            raise ValidationError("이미 종료된 도전장입니다.")
        if target.response != "pending":
            raise ValidationError("이미 응답한 도전장입니다.")
        # 시간 미정(scheduled_at=None) 도전장도 시간을 안 정한 채 그대로 수락할 수 있다(요청:
        # "시간 미정 수락 가능하게 변경 — 완료 시점으로 입력됨"). 이때는 결과 입력(enter_result)
        # 시점에 그 순간을 예정 일시로 기록한다. 수락하며 시간을 정하고 싶으면 scheduled_at을
        # 넘길 수 있고, 그 경우 그 값으로 확정한다. 이미 시간이 정해진 도전장은 응답하는 쪽이
        # 바꿀 수 없으므로 여기서 들어온 값은 무시한다.
        if response == "accepted" and challenge.scheduled_at is None and scheduled_at is not None:
            challenge.scheduled_at = scheduled_at
            challenge.updated_by = actor.pk
        target.response = response
        target.response_message = message.strip()
        target.responded_at = datetime.now(UTC)
        # 명시적 거절이든 버림(discarded)이든 그 즉시 도전장을 폐기
        # (휴지통)로 넘긴다 — 팀전이라도 한 명이 거절/버리면 그 대결은 끝이다. discarded_at을
        # 찍고 날짜 그루핑용 스탬프까지 한다.
        if response in ("rejected", "discarded"):
            _discard(challenge, datetime.now(UTC))
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def reschedule(
        self, challenge_id: int, scheduled_at: datetime, *, actor: Member,
    ) -> ChallengeOut:
        """성사(진행중)된 대결의 예정 일시를 바꾼다(요청: "너나와 목록에서 진행중인건은
        날짜와 시간 수정이 가능하게"). 참가자(도전자편/상대편 무관) 또는 운영자만 —
        구경꾼이 남의 대결 시간을 바꿀 수는 없다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if _status_of(challenge) != "confirmed":
            raise ValidationError("성사된 대결만 일정을 수정할 수 있습니다.")
        is_participant = any(p.member_pk == actor.pk for p in challenge.participants)
        if not is_participant and not actor.has_any_role("0202"):
            raise ForbiddenError("참가자 또는 운영자만 일정을 수정할 수 있습니다.")
        challenge.scheduled_at = scheduled_at
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def enter_result(
        self, challenge_id: int, winner_side: str, *, actor: Member,
    ) -> ChallengeOut:
        """확정된 대결의 결과(이긴 쪽)를 입력 — 참가자 누구든 먼저 입력하는 쪽이 그대로
        인정되고, 이미 입력된 뒤엔 다시 바꿀 수 없다(요청: "먼저 입력하는 쪽 인정")."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if _status_of(challenge) != "confirmed":
            raise ValidationError("확정된 대결만 결과를 입력할 수 있습니다.")
        now = datetime.now(UTC)
        if challenge.scheduled_at is None:
            # 시간 미정으로 수락된 대결은 언제든 결과를 입력할 수 있고, 그 완료 시점을 예정
            # 일시로 기록한다(요청: "시간 미정 수락 가능, 완료 시점으로 입력됨").
            challenge.scheduled_at = now
        elif _to_utc_naive(challenge.scheduled_at) > _to_utc_naive(now):
            raise ValidationError("예정 일시가 지난 뒤에만 결과를 입력할 수 있습니다.")
        if not any(p.member_pk == actor.pk for p in challenge.participants):
            raise ForbiddenError("이 대결의 참가자만 결과를 입력할 수 있습니다.")
        if challenge.result_winner_side is not None:
            raise ValidationError("이미 결과가 입력됐습니다.")

        challenge.result_winner_side = winner_side
        challenge.result_entered_by = actor.pk
        challenge.result_entered_at = datetime.now(UTC)
        challenge.updated_by = actor.pk
        # 미실시(not_held)는 완료가 아니라 폐기(휴지통)로 간다(요청: "수락했지만 미실시한
        # 경우도 휴지통으로"). 실제 승부 결과(creator/target/draw)만 완료로 남는다.
        if winner_side == "not_held":
            _discard(challenge, datetime.now(UTC))
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def revenge_challenge(
        self,
        challenge_id: int,
        *,
        actor: Member,
        scheduled_at: datetime | None = None,
        message: str = "",
    ) -> ChallengeOut:
        """결과가 입력된 확정 대결에서, 패배한 쪽 참가자가 같은 대진으로 설욕전을
        신청한다(요청: "완료시 패배한 쪽에서 설욕전 신청 가능... 이경우 너나와 체인으로
        연결"). 패배한 편 전원이 새 도전장의 요청자 쪽이 되고, 승리한 편이 새 지목
        대상이 된다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if challenge.result_winner_side is None:
            raise ValidationError("결과가 입력된 대결만 재대결을 신청할 수 있습니다.")
        losing_side = _losing_side(challenge)
        if losing_side is None:
            # 무승부/미실시는 패자가 없어 재대결 대상이 아니다(요청: "무승부나 미실시도 있게").
            raise ValidationError("무승부/미실시 대결은 재대결을 신청할 수 없습니다.")
        loser_pks = {p.member_pk for p in challenge.participants if p.side == losing_side}
        if actor.pk not in loser_pks:
            raise ForbiddenError("패배한 쪽만 재대결을 신청할 수 있습니다.")
        if await self._repo.is_superseded(challenge.id):
            raise ValidationError("이미 이어진 도전장이 있습니다.")

        winning_side = "creator" if losing_side == "target" else "target"
        new_challenge = Challenge(
            match_type=challenge.match_type,
            scheduled_at=scheduled_at,
            message=message.strip(),
            created_by=actor.pk,
            updated_by=actor.pk,
            reapplied_from_id=challenge.id,
        )
        new_challenge.participants = (
            [ChallengeParticipant(member_pk=pk, side="creator") for pk in loser_pks]
            + [
                ChallengeParticipant(member_pk=p.member_pk, side="target")
                for p in challenge.participants
                if p.side == winning_side
            ]
        )
        self._repo.add(new_challenge)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(new_challenge, attribute_names=["creator", "participants"])
        return to_challenge_out(new_challenge, history=await self._history_chain(new_challenge))
