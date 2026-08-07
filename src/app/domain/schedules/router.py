from fastapi import APIRouter, status

from app.api.deps import CurrentMember, DbSession, StorageDep
from app.domain.schedules.schemas import (
    ScheduleAttendIn,
    ScheduleListOut,
    ScheduleOut,
    ScheduleWrite,
)
from app.domain.schedules.service import ScheduleService

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=ScheduleListOut)
async def list_schedules(db: DbSession, current: CurrentMember) -> ScheduleListOut:
    return ScheduleListOut(items=await ScheduleService(db).list_schedules())


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleWrite, db: DbSession, current: CurrentMember, storage: StorageDep
) -> ScheduleOut:
    # 첨부파일이 여기로 들어오므로 저장소를 함께 받는다.
    return await ScheduleService(db, storage).create(payload, actor=current)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: int, payload: ScheduleWrite, db: DbSession, current: CurrentMember,
    storage: StorageDep,
) -> ScheduleOut:
    return await ScheduleService(db, storage).update(schedule_id, payload, actor=current)


@router.post("/{schedule_id}/attend", response_model=ScheduleOut)
async def attend_schedule(
    schedule_id: int, payload: ScheduleAttendIn, db: DbSession, current: CurrentMember
) -> ScheduleOut:
    """참가표시 — 누구나 제 몫만 세우거나 거둔다(response=null이면 거둔다)."""
    return await ScheduleService(db).attend(schedule_id, payload.response, actor=current)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: int, db: DbSession, current: CurrentMember, storage: StorageDep
) -> None:
    """일정 삭제 — 올린 사람 또는 운영자만. 댓글과 첨부파일도 함께 사라진다."""
    await ScheduleService(db, storage).delete(schedule_id, actor=current)
