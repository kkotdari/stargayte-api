from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.core.config import settings
from app.domain.activity.schemas import (
    ActivityCommentCreate,
    ActivityCommentOut,
    ActivityCommentWrite,
    ActivityFeedOut,
    ActivityNoticeOut,
    ActivityTargetTypeInput,
    RankingRecomputeResult,
    RankingShiftOut,
)
from app.domain.activity.service import (
    ActivityCommentService, ActivityListService, RankingShiftService,
)

# 접두어(/activities)는 api/router.py가 붙인다.
router = APIRouter(tags=["activity"])


# 목록은 접두어 그 자체다 — GET /api/activities(요청).
@router.get("", response_model=ActivityFeedOut)
async def list_activity_feed(
    db: DbSession, storage: StorageDep, current: CurrentMember,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ActivityFeedOut:
    """활동 목록 — 화면이 부르는 API는 이것 하나다(요청: API 딱 하나만 호출하게).

    GET /api/activities. 활동 하나하나가 아이템이고, 이 경로가 곧 그 목록이다.

    너 나와·랭크 변동·게임결과를 같은 아이템으로 취급하고, 내용도 댓글도 그 안에 담아
    보낸다. 예전에는 화면이 세 곳을 따로 받아 제 손으로 섞었는데, 그러면 섞는 규칙이
    서버(번호를 세니까)와 화면 양쪽에 있어야 하고 한쪽만 고쳐지는 순간 번호가 줄과
    어긋난다. 이제 섞는 자리가 한 곳뿐이다.

    순서와 번호는 늘 전체를 놓고 세고, 자르는 건 그 다음이다.
    """
    return await ActivityListService(db).list_feed(
        actor=current, storage=storage, cursor=cursor, limit=limit,
    )


@router.get("/comments", response_model=list[ActivityCommentOut])
async def list_activity_comments(
    db: DbSession, current: CurrentMember,
    target_type: ActivityTargetTypeInput = Query(alias="targetType"),
    target_id: int = Query(alias="targetId"),
) -> list[ActivityCommentOut]:
    return await ActivityCommentService(db).list_for_target(target_type, target_id, actor=current)


@router.post("/comments", response_model=ActivityCommentOut, status_code=status.HTTP_201_CREATED)
async def create_activity_comment(
    payload: ActivityCommentCreate, db: DbSession, current: CurrentMember
) -> ActivityCommentOut:
    return await ActivityCommentService(db).create(
        payload.target_type, payload.target_id, payload.text,
        payload.target_member_ids, actor=current,
    )


@router.patch("/comments/{comment_id}", response_model=ActivityCommentOut)
async def update_activity_comment(
    comment_id: int, payload: ActivityCommentWrite, db: DbSession, current: CurrentMember
) -> ActivityCommentOut:
    return await ActivityCommentService(db).update(
        comment_id, payload.text, payload.target_member_ids, actor=current
    )


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity_comment(comment_id: int, db: DbSession, current: CurrentMember) -> None:
    await ActivityCommentService(db).delete(comment_id, actor=current)


# 알림 한 건 — 카카오 공유 링크(?sv=notice&sid=…)가 여는 화면이 부른다(요청: 알림도 공유).
# 목록(GET /activities)에서 골라내지 않는 이유는 알림이 시간이 갈수록 아래로 밀려나기
# 때문이다: 공유한 링크는 한참 뒤에 열려도 그 한 건을 찾아야 한다.
@router.get("/notices/{notice_id}", response_model=ActivityNoticeOut)
async def get_activity_notice(
    notice_id: int, db: DbSession, current: CurrentMember,
) -> ActivityNoticeOut:
    notice = await ActivityListService(db).get_notice(notice_id)
    if notice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="알림을 찾을 수 없어요.")
    return notice


# 랭크 변동 이벤트 — 서버가 경기 등록/삭제 때마다 계산·저장한 스냅샷 중 실제 변동이
# 있었던 것만 내려준다(활동 카드용). 저장은 서버 내부(matches 서비스 훅)에서만 일어난다.
@router.get("/ranking-shifts", response_model=list[RankingShiftOut])
async def list_ranking_shifts(
    db: DbSession, current: CurrentMember, limit: int = Query(default=100, le=500),
) -> list[RankingShiftOut]:
    return await RankingShiftService(db).list_events(limit)


# 지금 바로 하루치 집계를 돌린다(요청: 제어판에 "현재 랭킹 집계하기") — 스케줄러가 아침에
# 하는 것과 똑같은 일이다. 아침을 기다리지 않고 확인하고 싶을 때, 그리고 스케줄러가 정말
# 도는지 눈으로 보고 싶을 때 쓴다. recompute_daily는 순위표가 그대로면 아무것도 남기지
# 않으므로 여러 번 눌러도 카드가 쌓이지 않는다.
@router.post("/ranking-shifts/recompute")
async def recompute_ranking_shifts(db: DbSession, current: CurrentAdmin) -> RankingRecomputeResult:
    from app.main import _rank_entries_computer

    _require_ranking_shift_enabled()

    service = RankingShiftService(db)
    before = await service.latest_snapshot_at()
    await service.recompute_daily(await _rank_entries_computer(db))
    after = await service.latest_snapshot_at()
    # 새 스냅샷이 남았나 — 안 남았다면 순위표가 그대로였다는 뜻이고, 그것도 알려 줘야
    # 사람이 "안 돌았나?"로 오해하지 않는다.
    return RankingRecomputeResult(changed=after is not None and after != before)


# 순위 기준선 다시 깔기 — 제어판에서 손으로 누르는 1회용(요청). 변동 없이 저장되므로
# 활동 목록에는 안 뜨고, 다음 아침 재집계가 이 기준선과 비교해 변동을 낸다.
@router.post("/ranking-shifts/seed")
async def reseed_ranking_shifts(db: DbSession, current: CurrentAdmin) -> dict[str, int]:
    from app.main import _rank_entries_computer

    _require_ranking_shift_enabled()
    return await RankingShiftService(db).reseed_now(await _rank_entries_computer(db))


def _require_ranking_shift_enabled() -> None:
    """랭크 변동을 쓰는 손잡이들의 공통 관문 — 꺼져 있으면 아무 행도 남기지 않는다(요청).

    조용히 성공한 척하지 않고 막는 이유는, 누른 사람이 "돌았는데 카드가 안 뜬다"로 읽으면
    그 다음에 찾아볼 곳이 없어서다. 화면에서도 이 버튼들을 감추지만, 서버가 마지막 문이다.
    """
    if not settings.ranking_shift_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "랭크 변동 집계가 꺼져 있습니다(RANKING_SHIFT_ENABLED).",
        )
