from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.imaging import image_size, resize_image_bytes
from app.domain.challenges.models import Challenge, ChallengeParticipant
from app.domain.challenges.repository import ChallengeRepository
from app.domain.challenges.schemas import (
    ChallengeAuthor,
    ChallengeCreate,
    ChallengeOut,
    ChallengeOwnMemberOut,
    ChallengeTargetOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository
from app.storage.base import FileStorage
from app.storage.data_url import decode_data_url

# 응답 없이 이 기간이 지나면(pending 상태 그대로) "무응답 거절"로 보고 폐기(휴지통) 처리한다
# — 요청: 하루(72시간이었다가 줄였다). 단, 예정 시각이 그보다 먼저면 예정 시각이
# 마감이다(_response_deadline). 프론트의 화면 표시 기준(ChallengeScreen.tsx의
# CHALLENGE_EXPIRE_MS)과 같은 값이어야 한다 — 갈라지면 화면과 실제 폐기 시점이 어긋난다.
RESPONSE_EXPIRE = timedelta(hours=24)
# 폐기(휴지통)된 지 이 기간이 지나면 소프트 삭제한다(요청: "휴지통은 폐기된 지 7일 지나면
# 사라짐, DB에서는 소프트 삭제").
TRASH_RETENTION = timedelta(days=7)

# 예정 날짜/시간은 한국시간 벽시계값으로 저장한다 — 비교(마감/지남 판정) 때만 KST로 해석해
# UTC로 환산한다.
KST = ZoneInfo("Asia/Seoul")

# 편지지 배경 사진을 저장할 때의 긴 변 상한 — 편지지는 모바일 화면을 꽉 채우는 정도라
# 1440px이면 레티나에서도 충분하고, 공유 카드판은 카카오 대화창에 뜨는 크기라 더 작아도 된다.
_BACKDROP_MAX_SIDE = 1440
_SHARE_MAX_SIDE = 1200
_BACKDROP_QUALITY = 82


def _scheduled_dt(challenge: "Challenge") -> datetime | None:
    """예정 일시를 비교용 UTC-naive datetime으로 환산한다. 시각 개념이 없어졌으므로 늘
    그날 끝(23:59:59 KST)이다 — 요청: "날짜만 지정한 경우 다음 날로 넘어가면 자동 무응답
    취소"(=응답 마감이 그날 끝). 날짜 자체가 없으면 None."""
    if challenge.scheduled_date is None:
        return None
    t = time(23, 59, 59)
    return datetime.combine(challenge.scheduled_date, t, tzinfo=KST).astimezone(UTC).replace(tzinfo=None)


# 시각을 안 정한 너 나와의 기본 시각 — 저녁 8시(요청: "시간은 기본 20시로 해줘").
#
# 예전엔 그날 자정(00:00)이었다. 너 나와는 날짜만 정하는 약속이라 시각 성분이 정렬·그룹핑
# 용도뿐인데, 자정으로 잡으면 그 시각이 화면에 그대로 새어 나왔다 — 오늘 오전에 올린
# 오늘자 너 나와가 활동 목록에 "11시간 전"으로 적혔다(지적). 실제로 모이는 시간대에
# 맞춰 두면 그런 표기가 사람 감각과 어긋나지 않는다. 저장하는 값이 아니라 여기서
# 파생하는 값이라, 바꾸면 지난 기록까지 한 번에 같이 바뀐다(마이그레이션 불필요).
_DEFAULT_SCHEDULED_HOUR = 20


def _scheduled_at_iso(challenge: "Challenge") -> datetime | None:
    """프론트 정렬/그룹핑/카운트다운용 파생 일시(UTC aware) — 그날 저녁 8시(KST)다.
    시각 성분은 정렬·날짜 그룹핑에만 쓴다(표시용 "언제"는 scheduledTimeNote가 따로 맡는다).
    날짜가 없으면 None — 그때는 화면이 '미정'으로 적고 목록 맨 위에 둔다(요청)."""
    if challenge.scheduled_date is None:
        return None
    return datetime.combine(
        challenge.scheduled_date, time(_DEFAULT_SCHEDULED_HOUR, 0), tzinfo=KST,
    ).astimezone(UTC)


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


# 응답 마감 = 요청일(created_at) + 24시간. 단, 예정 시각이 그보다 먼저면 예정 시각이
# 마감이다(요청: "예정시간이 그 전이면 예정시간 지나면 자동 거절 처리") — 그 시각까지
# 응답이 없으면 무응답 거절(폐기)된다.
def _response_deadline(challenge: Challenge) -> datetime:
    base = _to_utc_naive(challenge.created_at) + RESPONSE_EXPIRE
    scheduled = _scheduled_dt(challenge)
    if scheduled is not None:
        return min(base, scheduled)
    return base


def _discard(challenge: Challenge, now: datetime) -> None:
    """도전장을 폐기(휴지통)로 넘긴다 — discarded_at만 찍는다. 예정 일시는 건드리지 않는다
    (요청: "거절/마감초과 거절/취소 시 시간·날짜 없는 상태로 유지, 제약 없이 다 열어두기")
    — 예전엔 날짜 그루핑용으로 요청일+1일을 스탬프했지만, 이제 미정은 미정 그대로 둔다
    (휴지통에서 '일정 미정' 그룹으로 묶인다). 이미 폐기된 건 최초 폐기 시각을 보존한다."""
    if challenge.discarded_at is None:
        challenge.discarded_at = now


def to_challenge_out(challenge: Challenge) -> ChallengeOut:
    targets = [p for p in challenge.participants if p.side == "target"]
    own_members = [
        p for p in challenge.participants if p.side == "creator" and p.member_pk != challenge.created_by
    ]
    return ChallengeOut(
        id=challenge.id,
        matchType=challenge.match_type,
        message=challenge.message,
        scheduledAt=_scheduled_at_iso(challenge),
        scheduledDate=challenge.scheduled_date.isoformat() if challenge.scheduled_date else None,
        scheduledTimeNote=challenge.scheduled_time_note or "",
        status=_status_of(challenge),
        createdBy=ChallengeAuthor(
            id=challenge.creator.id, nickname=challenge.creator.nickname, avatar=challenge.creator.avatar_url,
        ),
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
        updatedAt=challenge.updated_at,
        discardedAt=challenge.discarded_at,
        canceledBy=(
            ChallengeAuthor(
                id=challenge.canceled_by.id,
                nickname=challenge.canceled_by.nickname,
                avatar=challenge.canceled_by.avatar_url,
            )
            if challenge.canceled_by is not None
            else None
        ),
        resultWinnerSide=challenge.result_winner_side,
        backdropUrl=challenge.backdrop_url,
        backdropShareUrl=challenge.backdrop_share_url,
        backdropShareWidth=challenge.backdrop_share_width,
        backdropShareHeight=challenge.backdrop_share_height,
    )


class ChallengeService:
    def __init__(self, session: AsyncSession, storage: FileStorage | None = None) -> None:
        self._session = session
        self._repo = ChallengeRepository(session)
        self._member_repo = MemberRepository(session)
        # 편지지 배경 사진을 저장할 곳 — 사진을 받는 경로(create_challenge)에서만 필요해서
        # 선택 인자다. 나머지 호출부(목록·응답·결과 등)는 예전처럼 세션 하나로 부른다.
        self._storage = storage

    async def _store_image(
        self, data_url: str | None, *, max_side: int
    ) -> tuple[str, int, int] | None:
        """편지지 배경 사진 한 장을 저장하고 (URL, 가로, 세로)를 돌려준다 — 안 올렸으면 None.

        브라우저가 이미 캔버스로 줄여서 보내지만(요청: "용량 줄여서 업로드") 여기서 한 번
        더 줄인다. 화면을 거치지 않고 API를 직접 부르는 길이 늘 열려 있어서, 저장되는
        파일 크기를 실제로 못 박는 곳은 여기뿐이다.

        크기도 브라우저가 알려 준 값을 받는 게 아니라 저장한 바이트에서 직접 읽는다 —
        여기서 한 번 더 줄이는 이상, 실제로 저장된 그림의 크기를 아는 곳도 여기뿐이다.
        """
        if not data_url:
            return None
        if self._storage is None:  # 사진을 받는 경로가 아니면 저장소를 안 넘긴다.
            return None
        content, _ = decode_data_url(data_url)
        content = resize_image_bytes(content, max_side=max_side, quality=_BACKDROP_QUALITY)
        width, height = image_size(content)
        stored = await self._storage.save(
            subdir="challenges", filename="backdrop.jpg", content=content, content_type="image/jpeg",
        )
        return stored.url, width, height

    async def delete(self, challenge_id: int) -> None:
        """운영자 전용 완전 삭제 — 도전장과 그에 달린 활동 댓글을 지운다."""
        from sqlalchemy import delete as sa_delete, select

        from app.domain.challenges.models import Challenge as ChallengeModel
        from app.domain.activity.models import ActivityComment

        challenge = await self._session.scalar(
            select(ChallengeModel).where(ChallengeModel.id == challenge_id)
        )
        if challenge is None:
            raise NotFoundError("너 나와!를 찾을 수 없어요.")
        await self._session.execute(
            sa_delete(ActivityComment).where(
                ActivityComment.target_type == "challenge", ActivityComment.target_id == challenge_id
            )
        )
        await self._session.delete(challenge)
        await self._session.commit()

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
        return [to_challenge_out(c) for c in alive]

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

        # 편지지 배경 사진은 두 장이다 — 편지지에 깔 원본과, 거기에 로고·문구를 얹은
        # 카카오 공유 카드판. 둘 다 사진의 원래 비율이고, 편지지 쪽이 잘라 쓴다.
        backdrop = await self._store_image(payload.backdrop, max_side=_BACKDROP_MAX_SIDE)
        share = await self._store_image(payload.backdrop_share, max_side=_SHARE_MAX_SIDE)

        challenge = Challenge(
            match_type=match_type,
            message=payload.message.strip(),
            scheduled_date=payload.scheduled_date,
            scheduled_time_note=payload.scheduled_time_note.strip(),
            backdrop_url=backdrop[0] if backdrop else None,
            backdrop_share_url=share[0] if share else None,
            backdrop_share_width=share[1] if share else None,
            backdrop_share_height=share[2] if share else None,
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
            scheduled = _scheduled_dt(challenge)
            if (
                _status_of(challenge) == "confirmed"
                and scheduled is not None
                and scheduled < now
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
        scheduled_date: date | None = None,
        scheduled_time_note: str = "",
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
        # 요청자가 일정(날짜)을 안 정하고 보냈으면, 수락하는 이 시점에 상대가 날짜/시간을 정할
        # 수 있다 — 둘 다 선택이라 안 정한 채 수락도 가능하다(요청: "시간 null 가능", "날짜만
        # 지정 가능"). 이미 요청자가 날짜를 정한 도전장은 응답하는 쪽이 바꿀 수 없으므로 무시한다.
        # 날짜와 "언제"는 서로 상관없다(요청) — 날짜 없이 "언제"만 적어 수락할 수도 있다.
        if response == "accepted":
            note = scheduled_time_note.strip()
            if challenge.scheduled_date is None and scheduled_date is not None:
                # 요청자가 날짜를 아예 안 정한 도전장 — 응답자가 날짜(+"언제")를 정한다.
                challenge.scheduled_date = scheduled_date
                if note:
                    challenge.scheduled_time_note = note
                challenge.updated_by = actor.pk
            elif not challenge.scheduled_time_note and note:
                # 요청자가 날짜만 정하고 "언제"는 안 적은 도전장 — 응답자가 그것만 덧붙일 수
                # 있다(요청: "날짜가 입력되었어도 시간은 별도로 입력 가능"). 날짜는 못 바꾼다.
                challenge.scheduled_time_note = note
                challenge.updated_by = actor.pk
        target.response = response
        target.response_message = message.strip()
        target.responded_at = datetime.now(UTC)
        # 응답도 이 도전장을 손댄 것이다 — 여기서 도전장 행을 안 건드리면 바뀌는 것은
        # 참가자 행뿐이라, challenges.updated_at이 그대로 등록 시각에 머문다. 활동 목록의
        # UPDATE 딱지가 바로 그 값을 보는데(사흘 전 호출에 방금 답이 온 것은 새것이 아니라
        # 달라진 것이다), 일정을 함께 고친 경우가 아니면 그 딱지가 영영 안 떴다.
        challenge.updated_by = actor.pk
        # 명시적 거절이든 버림(discarded)이든 그 즉시 도전장을 폐기(휴지통)로 넘긴다 — 팀전이라도
        # 한 명이 거절/버리면 그 대결은 끝이다. 예정 일시는 그대로 둔다(미정이면 미정 유지 — 요청).
        if response in ("rejected", "discarded"):
            _discard(challenge, datetime.now(UTC))
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)

    async def cancel(self, challenge_id: int, *, actor: Member) -> ChallengeOut:
        """부른 사람이 제 너 나와를 거둬들인다(요청) — 폐기로 넘기되 '취소'였다는 사실과
        누가 했는지를 남긴다. 상대의 거절·버림이나 무응답 만료와는 다른 끝이라, 화면이
        그 사람 자리에 "취소"라고 적을 수 있어야 한다.

        아직 안 끝난 것만 취소할 수 있다 — 이미 결과가 들어왔거나 폐기된 건을 다시
        취소하는 것은 뜻이 없다.
        """
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("너 나와!를 찾을 수 없어요.")
        is_admin = "admin" in {r.role for r in actor.roles}
        if challenge.created_by != actor.pk and not is_admin:
            raise ForbiddenError("자신이 보낸 너 나와!만 취소할 수 있습니다.")
        if _status_of(challenge) not in ("pending", "confirmed"):
            raise ValidationError("이미 끝난 너 나와!는 취소할 수 없습니다.")
        now = datetime.now(UTC)
        _discard(challenge, now)
        challenge.canceled_by_pk = actor.pk
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants", "canceled_by"])
        return to_challenge_out(challenge)

    async def reschedule(
        self, challenge_id: int, *, scheduled_date: date | None,
        scheduled_time_note: str = "", actor: Member,
    ) -> ChallengeOut:
        """성사(진행중)된 대결의 예정 일시를 바꾼다(요청: "너나와 목록에서 진행중인건은
        날짜와 시간 수정이 가능하게"). 날짜/시간 모두 선택이라 미정으로 되돌릴 수도 있다(요청:
        "제약 없이 다 열어두기"). 참가자(도전자편/상대편 무관) 또는 운영자만 — 구경꾼이 남의
        대결 시간을 바꿀 수는 없다. 날짜와 "언제"는 서로 상관없이 저장한다(요청: "둘은 이제
        상관없이 별도로 입력가능") — 날짜 없이 "그날 봐서" 한마디만 남겨 두는 것도 일정이다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if _status_of(challenge) != "confirmed":
            raise ValidationError("성사된 대결만 일정을 수정할 수 있습니다.")
        is_participant = any(p.member_pk == actor.pk for p in challenge.participants)
        if not is_participant and not actor.has_any_role("0202"):
            raise ForbiddenError("참가자 또는 운영자만 일정을 수정할 수 있습니다.")
        challenge.scheduled_date = scheduled_date
        challenge.scheduled_time_note = scheduled_time_note.strip()
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)

    async def enter_result(
        self,
        challenge_id: int,
        winner_side: str,
        *,
        actor: Member,
        scheduled_date: date,
    ) -> ChallengeOut:
        """확정된 대결의 결과(이긴 쪽)를 입력 — 참가자 누구든 먼저 입력하는 쪽이 그대로
        인정되고, 이미 입력된 뒤엔 다시 바꿀 수 없다(요청: "먼저 입력하는 쪽 인정"). 결과
        입력 시엔 실제 대결 날짜/시간을 무조건 함께 받아 확정한다(요청: "결과 입력시에는 날짜
        시간 무조건 입력") — 그전까지 미정이었어도 이 시점에 실제 일시로 채워진다."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        status = _status_of(challenge)
        """폐기된 건도 결과를 넣을 수 있다(요청: 만료됐는데 실제로는 경기를 한 경우) —
        마감이 지나 무응답으로 접힌 건이나 미실시로 적힌 건이 그렇다. 실제로 붙었다면 그
        사실이 기록이지, 그때 응답을 제때 눌렀는지가 기록은 아니다."""
        if status not in ("confirmed", "discarded"):
            raise ValidationError("확정됐거나 폐기된 대결만 결과를 입력할 수 있습니다.")
        is_admin = "admin" in {r.role for r in actor.roles}
        if not any(p.member_pk == actor.pk for p in challenge.participants) and not is_admin:
            raise ForbiddenError("이 대결의 참가자나 운영자만 결과를 입력할 수 있습니다.")
        # 실제 승부 결과가 이미 있으면 그대로 둔다(먼저 입력한 쪽 인정) — 다만 '미실시'는
        # 결과라기보다 "안 했다"는 표시라, 실제로 치렀다면 그 위에 덮어쓸 수 있어야 한다.
        if challenge.result_winner_side is not None and challenge.result_winner_side != "not_held":
            raise ValidationError("이미 결과가 입력됐습니다.")

        # 결과와 함께 넘어온 실제 대결 날짜로 확정한다(필수).
        challenge.scheduled_date = scheduled_date
        challenge.result_winner_side = winner_side
        challenge.result_entered_by = actor.pk
        challenge.result_entered_at = datetime.now(UTC)
        challenge.updated_by = actor.pk
        # 미실시(not_held)는 완료가 아니라 폐기(휴지통)로 간다(요청: "수락했지만 미실시한
        # 경우도 휴지통으로"). 실제 승부 결과(creator/target/draw)만 완료로 남는다.
        if winner_side == "not_held":
            _discard(challenge, datetime.now(UTC))
        else:
            """실제 승부가 들어오면 강제로 성사시킨다(요청: 강제 결과 입력 — 취소·만료·거절된
            건이라도 상태가 수락으로 바뀌고 결과가 들어가게).
              · 휴지통에서 꺼내고(discarded_at을 지운다 — _status_of가 이 값을 먼저 본다)
              · 아직 답 안 한 사람, 거절한 사람의 응답을 수락으로 돌린다.
            뒤엣것까지 해야 카드가 앞뒤가 맞는다 — 응답이 '거절'로 남아 있으면 아바타 배지는
            거절인데 맨 아랫줄은 "OO 승"이 되어, 한 도전장이 두 얼굴을 갖는다.
            근거는 사실 쪽이다: 실제로 붙었다면 그 자리는 성사된 것이고, 그때 응답 버튼을
            제때 눌렀는지는 그 판이 있었다는 사실을 바꾸지 못한다."""
            challenge.discarded_at = None
            for p in challenge.participants:
                if p.side == "target" and p.response != "accepted":
                    p.response = "accepted"
                    p.responded_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)
