from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.domain.activity.schemas import (
    ActivityCommentCreate,
    ActivityCommentOut,
    ActivityCommentWrite,
    ActivityFeedOut,
    ActivityListOut,
    ActivityTargetTypeInput,
    RankingRecomputeResult,
    RankingShiftOut,
)
from app.domain.activity.service import ActivityCommentService, ActivityListService, RankingShiftService

# 접두어는 여기서 안 붙인다 — api/router.py가 같은 라우터를 /activity와 /feed 두 자리에
# 매달기 때문이다(아래 파일 끝 주석 참고). 라우터 자신이 접두어를 갖고 있으면 두 번째 자리는
# /feed/activity/...가 되어 버린다.
router = APIRouter(tags=["activity"])


# 목록은 접두어 그 자체다 — GET /api/activities(요청). /feed 꼬리말은 옛 프론트를 위해
# 별칭으로만 남긴다.
@router.get("", response_model=ActivityFeedOut)
@router.get("/feed", response_model=ActivityFeedOut, include_in_schema=False)
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


@router.get("/list", response_model=ActivityListOut, include_in_schema=False)
async def list_activity_rows(db: DbSession, current: CurrentMember) -> ActivityListOut:
    """활동 목록을 그리는 데 필요한 것 한 벌 — 줄 번호와 댓글(요청: 단일 API로 통합).

    번호를 화면이 직접 셀 수 없는 이유는 두 가지다. 목록은 세 곳(도전장·게임결과·
    랭크변동)을 시간순으로 섞어 만드는데 어느 한 엔드포인트도 나머지를 모르고, 게임결과는
    페이지 단위로 나눠 받으므로 화면은 늘 일부만 쥐고 있다 — 거기서 센 번호는 아직 안
    받아온 과거만큼 통째로 어긋난다.

    댓글이 여기 함께 오는 건 그것 역시 목록 한 벌에 딸린 값이기 때문이다. 요청이 둘이면
    하나가 늦거나 실패할 때 목록이 반쯤 그려진 채로 남는다 — 실제로 운영에서 둘이 나란히
    500이었다. 옛 경로(/activity/comments/all)는 배포가 어긋나는 순간을 위해 남겨 둔다.
    """
    return await ActivityListService(db).list_rows(actor=current)


@router.get("/comments/all", response_model=list[ActivityCommentOut], include_in_schema=False)
async def list_all_activity_comments(db: DbSession, current: CurrentMember) -> list[ActivityCommentOut]:
    """옛 경로 — 이제 GET /activity/list가 댓글까지 함께 준다(요청: 단일 API로 통합).

    지우지 않고 남기는 건 프론트와 API가 동시에 배포되지 않기 때문이다. 새 프론트가 뜨기
    전까지는 옛 프론트가 이 경로를 계속 부른다. 프론트가 모두 새 응답을 쓰게 된 뒤 한참
    지나면 지워도 된다(랭크 스냅샷의 옛 경로와 같은 방식).
    """
    return await ActivityCommentService(db).list_all(actor=current)


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


# 랭크 변동 이벤트 — 서버가 경기 등록/삭제 때마다 계산·저장한 스냅샷 중 실제 변동이
# 있었던 것만 내려준다(활동 카드용). 저장은 서버 내부(matches 서비스 훅)에서만 일어난다.
# 옛 경로(/feed/rank-snapshots)는 배포가 어긋나는 순간을 위해 별칭으로 남겨 둔다(요청:
# API URL도 통일). 프론트가 모두 새 경로를 쓰게 된 뒤 한참 지나면 지워도 된다.
@router.get("/ranking-shifts", response_model=list[RankingShiftOut])
@router.get("/rank-snapshots", response_model=list[RankingShiftOut], include_in_schema=False)
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
@router.post("/rank-snapshots/seed", include_in_schema=False)
async def reseed_ranking_shifts(db: DbSession, current: CurrentAdmin) -> dict[str, int]:
    from app.main import _rank_entries_computer

    return await RankingShiftService(db).reseed_now(await _rank_entries_computer(db))
