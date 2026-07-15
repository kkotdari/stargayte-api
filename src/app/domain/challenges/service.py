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

# 응답 없이 이 기간이 지나면(pending 상태 그대로) "기한 내 미응답"으로 보고 재신청을
# 허용한다 — 프론트의 화면 표시 기준(ChallengeScreen.tsx의 EXPIRE_MS)과 같은 1일이다
# (처음엔 3일이었다가 줄였다 — 요청: "응답가능시간 1일로 축소").
REAPPLY_EXPIRE = timedelta(days=1)


def _to_utc_naive(dt: datetime) -> datetime:
    # Postgres(timestamptz)는 aware로, SQLite는 tz 정보 없이 naive로 돌아오는 등 방언마다
    # 달라서, 비교 전에 항상 "UTC 기준 naive"로 맞춘다(matches/service.py의 같은 이름
    # 헬퍼와 같은 이유 — 여긴 그 모듈을 참조하지 않는 독립된 도메인이라 그대로 복제한다).
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _status_of(challenge: Challenge) -> str:
    if challenge.canceled_at is not None:
        return "canceled"
    responses = [p.response for p in challenge.participants if p.side == "target"]
    if any(r == "rejected" for r in responses):
        return "rejected"
    if responses and all(r == "accepted" for r in responses):
        return "confirmed"
    return "pending"


# 응답(무응답거절) 마감 = 예정 시간 지정 여부와 무관하게 무조건 요청일(created_at)로부터
# 1일이다(요청: "예정시간 지정이든 아니든 응답 마감시간은 무조건 요청일로부터 1일이야").
def _response_deadline(challenge: Challenge) -> datetime:
    return _to_utc_naive(challenge.created_at) + REAPPLY_EXPIRE


def _stamp_schedule_on_end(challenge: Challenge) -> None:
    """도전장이 거절/무응답으로 끝나는 순간, 예정 일시가 없으면 요청일+1일로 확정한다
    (요청: "응답 마감 기한 지나면 자동으로 백엔드 배치에 의해 스케쥴이 박힐거고 그게 아직
    없다는건 응답 대기중이라는 거지" + "거절하는 순간 scheduled_at도 업데이트... 요청일시
    +1일로 통일성있게"). 이 규칙 덕에 "scheduled_at이 없다 = 아직 응답 대기중"이 성립해
    프론트는 마감 계산 없이 그걸로만 판단한다. 이미 시간이 정해진 도전장은 실제 매치
    시각이라 건드리지 않는다."""
    if challenge.scheduled_at is None:
        challenge.scheduled_at = challenge.created_at + REAPPLY_EXPIRE


def _is_auto_stamped(challenge: Challenge) -> bool:
    """예정 일시가 위 _stamp_schedule_on_end로 찍힌 값(요청일+1일)인지 — 재신청 때 이
    값을 새 도전장의 매치 시각으로 물려주면 안 되므로(무응답으로 박제된 값이지 실제로 잡은
    시각이 아니다) 구분한다. 사용자가 '요청 시각 정확히 +24시간'을 고를 일은 사실상 없어
    이 동치 비교로 충분하다."""
    if challenge.scheduled_at is None:
        return False
    return _to_utc_naive(challenge.scheduled_at) == _to_utc_naive(challenge.created_at) + REAPPLY_EXPIRE


def _is_expired(challenge: Challenge) -> bool:
    if _status_of(challenge) != "pending":
        return False
    return _to_utc_naive(datetime.now(UTC)) > _response_deadline(challenge)


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
        message=challenge.message,
        status=_status_of(challenge),
        chainKind=challenge.chain_kind,
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
    # 응답 한마디(수락/거절 모두)는 전체 공개다 — 요청자가 아니어도 누구나 볼 수 있다
    # (예전엔 요청자만 봤지만, 요청에 따라 제한을 없앴다).
    targets = [p for p in challenge.participants if p.side == "target"]
    own_members = [
        p for p in challenge.participants if p.side == "creator" and p.member_pk != challenge.created_by
    ]
    return ChallengeOut(
        id=challenge.id,
        matchType=challenge.match_type,
        scheduledAt=challenge.scheduled_at,
        message=challenge.message,
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
        reappliedFromId=challenge.reapplied_from_id,
        chainKind=challenge.chain_kind,
        resultWinnerSide=challenge.result_winner_side,
        history=[_history_entry(c) for c in (history or [])],
    )


class ChallengeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ChallengeRepository(session)
        self._member_repo = MemberRepository(session)

    # 재신청 체인(reapplied_from_id를 따라 올라가는 사슬)에서 이 도전장보다 앞선 기록을
    # 오래된 순으로 모은다 — 단일 도전장 하나만 다루는 엔드포인트(respond/cancel/reapply)
    # 에서 쓴다. 체인은 실제로는 몇 단계 안 넘을 것으로 보고(계속 거절만 당하는 극단적인
    # 경우가 아니면), 매번 get()으로 한 단계씩 거슬러 올라가는 정도의 비용은 감수한다 —
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

    async def _expire_stale_challenges(self, challenges: list[Challenge]) -> None:
        """응답 대기시간(REAPPLY_EXPIRE)이 지난 pending 도전장의 미응답 지목자를
        '무응답거절'로 처리한다(요청: "너나와 화면을 조회하면 현재 상태 업데이트 배치가
        실행돼... 응답 대기시간이 지난 건들은 무응답거절로 처리"). 다루는 방식은 거절과
        똑같다 — 지목자의 response를 rejected로 바꿔 상태가 rejected가 되고, 요청자는
        재신청할 수 있다. 다만 사람이 직접 거절한 게 아니라 무응답이라 한마디(message)는
        남기지 않는다(요청: "대신 메시지는 없어"). 너나와 목록을 조회할 때마다 실행되는
        가벼운 배치라, 이미 로드된 목록을 그대로 받아 메모리에서 처리하고 바뀐 게 있을
        때만 한 번 커밋한다."""
        now = _to_utc_naive(datetime.now(UTC))
        changed = False
        for c in challenges:
            if c.canceled_at is not None:
                continue
            status = _status_of(c)
            # 마감(요청일+1일)이 지난 pending → 미응답 지목자를 무응답거절로 확정.
            if status == "pending" and now > _response_deadline(c):
                for p in c.participants:
                    if p.side == "target" and p.response == "pending":
                        p.response = "rejected"
                        p.response_message = None
                        p.responded_at = datetime.now(UTC)
                        changed = True
                status = "rejected"
            # 거절로 끝났는데 예정 일시가 없으면 요청일+1일로 확정한다 — 무응답거절이든 사람이
            # 직접 누른 거절이든(과거 데이터 포함) 모두 스탬프해, "일정 미정"은 응답 대기중에만
            # 남게 한다(요청: "왜 거절/무응답 거절 건중 아직도 일정미정이라고 뜨는게 있지").
            if status == "rejected" and c.scheduled_at is None:
                _stamp_schedule_on_end(c)
                changed = True
        if changed:
            await self._session.commit()

    async def list_challenges(self, *, actor: Member) -> list[ChallengeOut]:
        challenges = await self._repo.list_all()
        # 조회 시점에 응답 기한이 지난 건들을 무응답거절로 확정한다(위 배치) — 이 뒤로는
        # 그 도전장의 _status_of가 rejected가 되어 아래 목록/정렬에 그대로 반영된다.
        await self._expire_stale_challenges(challenges)
        by_id = {c.id: c for c in challenges}
        # 재신청으로 새 행이 생기면 원래 행은 화면에서 더 안 보여야 한다(요청: "최신 1건만
        # 목록에 나오고, 카드 안에서 좌우로 슬라이드해 이전 기록을 본다") — 어떤 행의 id를
        # reapplied_from_id로 가리키는 다른 행이 있으면(=그 행이 나중에 재신청됐으면) 그
        # 원래 행은 숨긴다. 체인이 길어도(재신청을 여러 번 거쳐도) 맨 끝(가장 최신)만
        # 자연히 남는다.
        superseded_ids = {c.reapplied_from_id for c in challenges if c.reapplied_from_id is not None}
        # 취소된 도전장도 이제 목록에 보여준다(요청: "너 나와 목록에 취소된 도전장도 노출") —
        # 재신청/설욕전으로 이어져 더 최신 행이 있는 것(superseded)만 숨긴다.
        visible = [c for c in challenges if c.id not in superseded_ids]
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
            scheduled_at=payload.scheduled_at,
            message=payload.message,
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
            # 취소된 도전장의 초대는 띄우지 않는다 — 상대가 팝업을 보기 전에 요청자가
            # 취소하면, 수락을 눌러도 400만 나는 죽은 초대가 한 번 뜨는 문제가 있었다.
            # notified는 위에서 이미 표시했으므로 다음 조회에서 다시 잡히지도 않는다.
            if challenge.canceled_at is not None:
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
        reason: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> ChallengeOut:
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        target = next(
            (p for p in challenge.participants if p.side == "target" and p.member_pk == actor.pk), None
        )
        if target is None:
            raise ForbiddenError("이 도전장에 지목되지 않았습니다.")
        if challenge.canceled_at is not None:
            raise ValidationError("취소된 도전장입니다.")
        if target.response != "pending":
            raise ValidationError("이미 응답한 도전장입니다.")
        # 요청자가 "시간 지정"을 끄고 보낸(scheduled_at=None) 도전장은 "상대가 정해도
        # 된다"는 뜻이라, 수락하는 이 시점에 상대가 직접 정하게 한다 — 안 그러면 시간이
        # 영원히 안 채워진 채 "승락" 상태로 박제된다(요청: "도전자/상대 모두 시간을
        # 지정하지 않았는데 수락이 된 경우가 있네 이러면 안되는데"). 이미 시간이 정해진
        # 도전장은 응답하는 쪽이 바꿀 수 없으므로 여기서 들어온 값은 무시한다.
        if response == "accepted" and challenge.scheduled_at is None:
            if scheduled_at is None:
                raise ValidationError("일시가 정해지지 않은 도전장이에요 — 수락하며 시간을 정해주세요.")
            challenge.scheduled_at = scheduled_at
            challenge.updated_by = actor.pk
        target.response = response
        target.responded_at = datetime.now(UTC)
        # 이제 수락에도 한마디를 받는다(요청: "편지지에 수락/거절 한줄 메시지 필수화")
        # — 응답 종류와 무관하게 그대로 저장한다. 팀전에서 최초 응답자만 남길 수 있게
        # 제한했던 적이 있는데(요청: "한마디는 최초응답자만 가능") 되돌렸다(요청: "수락시
        # 메시지 한명만 받기로 했는데 전원 다 받을수 있게 해줘") — 지목된 전원이 각자
        # 자기 한마디를 남긴다.
        target.response_message = reason
        # 이 응답으로 도전장이 거절로 끝났고 예정 일시가 없었으면, 그 순간 예정 일시를
        # 요청일+1일로 확정한다(요청: "거절하는 순간 scheduled_at도 업데이트") — 화면에서
        # "일정 미정"이 아니라 그 날짜로 묶이게.
        if _status_of(challenge) == "rejected":
            _stamp_schedule_on_end(challenge)
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def cancel_challenge(self, challenge_id: int, *, actor: Member) -> ChallengeOut:
        """요청자(도전자)만 취소할 수 있다(요청: "취소는 생성자만 가능") — 응답 대기중
        (pending)뿐 아니라 확정됐지만 아직 결과가 안 들어온 대결도 취소할 수 있다. 결과가
        이미 입력됐거나 거절/취소로 끝난 건은 취소할 수 없다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if challenge.created_by != actor.pk:
            raise ForbiddenError("요청자만 취소할 수 있습니다.")
        status = _status_of(challenge)
        # 결과 입력 전(pending/결과 없는 confirmed)이거나, 결과가 "미실시"(not_held)인 대결까지
        # 취소할 수 있다(요청: "미실시 상태면 카드에 취소/연기 노출") — 승패가 난 결과만 취소
        # 불가. 거절/취소로 끝난 건도 불가.
        if status not in ("pending", "confirmed") or (
            challenge.result_winner_side is not None and challenge.result_winner_side != "not_held"
        ):
            raise ValidationError("결과가 입력됐거나 이미 처리된 도전장은 취소할 수 없습니다.")
        challenge.canceled_at = datetime.now(UTC)
        # 미실시 결과를 취소하는 경우 잔여 결과값을 지워 "취소"로만 깔끔히 보이게 한다.
        challenge.result_winner_side = None
        challenge.result_entered_by = None
        challenge.result_entered_at = None
        # 취소된 건도 이제 목록에 보이므로(요청: "취소된 도전장도 노출), 일정 미정으로 뜨지
        # 않게 예정 일시를 요청일+1일로 스탬프한다(거절/무응답과 같은 처리).
        _stamp_schedule_on_end(challenge)
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def reapply_challenge(
        self,
        challenge_id: int,
        *,
        actor: Member,
        scheduled_at: datetime | None = None,
        message: str | None = None,
    ) -> ChallengeOut:
        """거절됐거나 기한(1일) 내 무응답인 도전장을 재신청 — 원래 행은 그대로 두고
        같은 구성원으로 새 도전장을 만든다(요청: "재신청하면 원래건은 종료되고 새로운
        도전 행이 만들어져 새 아이디로... refer라던지 그런 느낌의 컬럼을 만들어서
        어디서 이어졌는지 저장해둬" + "기한내 미응답시 재신청 가능"). 시간/메모를
        원하면 이 참에 고쳐서 다시 보내고, 안 넘기면 원래 도전장의 값을 그대로
        물려받는다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if challenge.created_by != actor.pk:
            raise ForbiddenError("요청자만 다시 신청할 수 있습니다.")
        status = _status_of(challenge)
        # 거절/무응답거절(rejected)뿐 아니라 취소(canceled)된 건도 다시 신청할 수 있다(요청:
        # "취소된 건은 재신청 가능해야 하지 않나") — 아직 다른 행으로 이어지지 않았다면(아래
        # is_superseded 체크). 미실시 "상태" 자체는 확정(confirmed)이라 여기 안 걸리고 재신청
        # 불가지만, 취소하면 canceled가 되어 가능해진다.
        if status not in ("rejected", "canceled") and not _is_expired(challenge):
            raise ValidationError(
                "거절/취소되었거나 기한 내 무응답인 도전장만 다시 신청할 수 있습니다."
            )
        if await self._repo.is_superseded(challenge.id):
            raise ValidationError("이미 이어진 도전장이 있습니다.")

        # 시간/메모를 안 넘기면 원래 값을 물려받되, 예정 일시가 무응답거절로 자동 스탬프된
        # 값(요청일+1일)이면 실제로 잡은 시각이 아니라 박제값이라 물려주지 않고 다시 미정으로
        # 시작한다 — 안 그러면 새 도전장이 과거 시각으로 만들어져 만들자마자 만료된다.
        inherited_schedule = None if _is_auto_stamped(challenge) else challenge.scheduled_at
        new_challenge = Challenge(
            match_type=challenge.match_type,
            scheduled_at=scheduled_at if scheduled_at is not None else inherited_schedule,
            message=message if message is not None else challenge.message,
            created_by=actor.pk,
            updated_by=actor.pk,
            reapplied_from_id=challenge.id,
            chain_kind="reapply",
        )
        new_challenge.participants = [
            ChallengeParticipant(member_pk=p.member_pk, side=p.side) for p in challenge.participants
        ]
        self._repo.add(new_challenge)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(new_challenge, attribute_names=["creator", "participants"])
        return to_challenge_out(new_challenge, history=await self._history_chain(new_challenge))

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
        if challenge.scheduled_at is None or _to_utc_naive(challenge.scheduled_at) > _to_utc_naive(
            datetime.now(UTC)
        ):
            raise ValidationError("예정 일시가 지난 뒤에만 결과를 입력할 수 있습니다.")
        if not any(p.member_pk == actor.pk for p in challenge.participants):
            raise ForbiddenError("이 대결의 참가자만 결과를 입력할 수 있습니다.")
        if challenge.result_winner_side is not None:
            raise ValidationError("이미 결과가 입력됐습니다.")

        challenge.result_winner_side = winner_side
        challenge.result_entered_by = actor.pk
        challenge.result_entered_at = datetime.now(UTC)
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))

    async def revenge_challenge(
        self,
        challenge_id: int,
        *,
        actor: Member,
        scheduled_at: datetime | None = None,
        message: str | None = None,
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
            message=message if message is not None else "",
            created_by=actor.pk,
            updated_by=actor.pk,
            reapplied_from_id=challenge.id,
            chain_kind="revenge",
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

    async def postpone_challenge(
        self, challenge_id: int, scheduled_at: datetime, *, actor: Member,
    ) -> ChallengeOut:
        """확정된 대결을 연기 — 도전자/상대 누구든 가능하고, 예정 일시가 지난 뒤에도
        가능하다(요청: "수락된 대결 연기 가능(도전자/상대 모두 가능)... 예정 일시 지난
        뒤에도 연기 가능"). 잘못 입력됐을 수 있는 기존 결과는 새 일정으로 초기화한다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if _status_of(challenge) != "confirmed":
            raise ValidationError("확정된 대결만 연기할 수 있습니다.")
        if not any(p.member_pk == actor.pk for p in challenge.participants):
            raise ForbiddenError("이 대결의 참가자만 연기할 수 있습니다.")

        challenge.scheduled_at = scheduled_at
        challenge.result_winner_side = None
        challenge.result_entered_by = None
        challenge.result_entered_at = None
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge, history=await self._history_chain(challenge))
