from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.domain.members.models import Member
from app.domain.schedules.models import Schedule, ScheduleAttendee
from app.domain.schedules.schemas import (
    ScheduleAttendeeOut,
    ScheduleAuthor,
    ScheduleFileIn,
    ScheduleFileOut,
    ScheduleOut,
    ScheduleWrite,
)
from app.storage.base import FileStorage
from app.storage.data_url import decode_data_url

# 파일 이름에서 살려 둘 확장자 — 저장되는 파일명은 uuid라 이것만 붙는다. 실행 파일로
# 읽힐 수 있는 꼬리는 통째로 떼어 .bin으로 둔다(브라우저가 열 일이 없는 첨부다).
_BLOCKED_EXT = {".html", ".htm", ".svg", ".xhtml", ".js", ".mjs", ".php", ".sh", ".exe"}


def _files_of(schedule: Schedule) -> list[ScheduleFileOut]:
    """저장된 JSON을 그대로 믿지 않고 필요한 칸만 꺼낸다 — 옛 행에 없는 칸은 물러선 값으로."""
    out: list[ScheduleFileOut] = []
    for f in schedule.files or []:
        if not isinstance(f, dict) or not f.get("url"):
            continue
        out.append(ScheduleFileOut(
            name=str(f.get("name") or "첨부파일"),
            url=str(f["url"]),
            size=int(f.get("size") or 0),
        ))
    return out


def to_schedule_out(schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        title=schedule.title,
        scheduledDate=schedule.scheduled_date.isoformat(),
        scheduledTime=(
            schedule.scheduled_time.strftime("%H:%M") if schedule.scheduled_time else None
        ),
        content=schedule.content or "",
        linkUrl=schedule.link_url or "",
        files=_files_of(schedule),
        attendees=[
            ScheduleAttendeeOut(
                memberId=a.member.id,
                nickname=a.member.nickname,
                avatar=a.member.avatar_url,
                response=a.response,
            )
            for a in schedule.attendees
        ],
        createdBy=ScheduleAuthor(
            id=schedule.creator.id,
            nickname=schedule.creator.nickname,
            avatar=schedule.creator.avatar_url,
        ) if schedule.creator is not None else ScheduleAuthor(id="", nickname="(탈퇴)"),
        createdAt=schedule.created_at,
        updatedAt=schedule.updated_at,
    )


class ScheduleService:
    """모임 일정 — 등록·수정·삭제와 참가표시.

    너 나와!와 달리 상태 계산이 없다: 지목한 상대도, 성사 조건도, 마감도 없어서 저장된 값이
    곧 전부다. 그래서 조회 시점 배치(_run_batches 같은 것)도 필요 없다.
    """

    def __init__(self, session: AsyncSession, storage: FileStorage | None = None) -> None:
        self._session = session
        # 첨부파일을 받는 경로(등록·수정)에서만 필요해서 선택 인자다.
        self._storage = storage

    async def list_schedules(self) -> list[ScheduleOut]:
        # 일정은 경기결과처럼 무한히 쌓이는 것이 아니라 전부 내려준다 — 활동 목록이
        # 페이지를 자르는 건 그 다음 단계다.
        rows = await self._session.execute(
            select(Schedule).order_by(Schedule.scheduled_date.desc(), Schedule.id.desc())
        )
        return [to_schedule_out(s) for s in rows.scalars().unique().all()]

    async def get(self, schedule_id: int) -> ScheduleOut:
        return to_schedule_out(await self._require(schedule_id))

    async def create(self, payload: ScheduleWrite, *, actor: Member) -> ScheduleOut:
        schedule = Schedule(
            title=payload.title,
            scheduled_date=payload.scheduled_date,
            scheduled_time=payload.scheduled_time,
            content=payload.content,
            link_url=payload.link_url,
            files=await self._store_files(payload, kept=[]),
            created_by=actor.pk,
            updated_by=actor.pk,
        )
        self._session.add(schedule)
        await self._session.flush()
        await self._session.commit()
        return to_schedule_out(await self._require(schedule.id))

    async def update(self, schedule_id: int, payload: ScheduleWrite, *, actor: Member) -> ScheduleOut:
        schedule = await self._require(schedule_id)
        self._require_owner(schedule, actor)
        kept = list(schedule.files or [])
        schedule.title = payload.title
        schedule.scheduled_date = payload.scheduled_date
        schedule.scheduled_time = payload.scheduled_time
        schedule.content = payload.content
        schedule.link_url = payload.link_url
        schedule.files = await self._store_files(payload, kept=kept)
        schedule.updated_by = actor.pk
        await self._session.commit()
        return to_schedule_out(await self._require(schedule_id))

    async def delete(self, schedule_id: int, *, actor: Member) -> None:
        """일정과 거기 달린 댓글·첨부파일을 함께 지운다 — 되돌릴 자리(휴지통)는 없다.

        너 나와의 폐기·7일 보관과 다른 이유는, 일정에는 '없던 일이 된 기록'이라는 뜻이
        없어서다. 지운 일정은 그냥 사라진 공지다.
        """
        from app.domain.activity.models import ActivityComment

        schedule = await self._require(schedule_id)
        self._require_owner(schedule, actor)
        # 저장소의 실제 파일도 함께 — 행만 지우면 디스크에 주인 없는 파일이 남는다.
        for f in schedule.files or []:
            path = isinstance(f, dict) and f.get("path")
            if path and self._storage is not None:
                await self._storage.delete(str(path))
        await self._session.execute(
            sa_delete(ActivityComment).where(
                ActivityComment.target_type == "schedule", ActivityComment.target_id == schedule_id
            )
        )
        await self._session.delete(schedule)
        await self._session.commit()

    async def attend(self, schedule_id: int, response: str | None, *, actor: Member) -> ScheduleOut:
        """참가표시를 세우거나(going/notGoing) 거둔다(None) — 누구나 제 몫만 바꾼다."""
        schedule = await self._require(schedule_id)
        mine = next((a for a in schedule.attendees if a.member_pk == actor.pk), None)
        if response is None:
            if mine is not None:
                schedule.attendees.remove(mine)
        elif mine is None:
            schedule.attendees.append(ScheduleAttendee(
                member_pk=actor.pk, response=response, responded_at=datetime.now(UTC),
            ))
        else:
            mine.response = response
            mine.responded_at = datetime.now(UTC)
        await self._session.commit()
        return to_schedule_out(await self._require(schedule_id))

    async def _require(self, schedule_id: int) -> Schedule:
        schedule = await self._session.scalar(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        if schedule is None:
            raise NotFoundError("일정을 찾을 수 없어요.")
        # 참가표시를 방금 손댔으면 파이썬 쪽 목록이 최신이다 — 다시 읽어 오면 그 사이의
        # 변경이 반영된 값이 나오도록 만료시켜 둔다.
        await self._session.refresh(schedule, ["attendees"])
        return schedule

    @staticmethod
    def _require_owner(schedule: Schedule, actor: Member) -> None:
        """올린 사람 또는 운영자만 고치고 지운다 — 참가표시·댓글은 누구나 한다."""
        if schedule.created_by != actor.pk and not actor.has_any_role("0202"):
            raise ForbiddenError("내가 올린 일정만 고칠 수 있어요.")

    async def _store_files(self, payload: ScheduleWrite, *, kept: list) -> list[dict]:
        """폼이 보낸 최종 파일 목록을 저장 모양으로 — 새 것은 올리고, 빠진 것은 지운다.

        url만 온 항목은 이미 저장돼 있는 파일이라 그 행(path 포함)을 그대로 이어 쓴다.
        서버가 쥔 목록에 없는 url이 오면 무시한다 — 남의 파일 주소를 실어 보내 남의 것을
        제 일정에 붙이는 길을 열지 않는다.
        """
        by_url = {f["url"]: f for f in kept if isinstance(f, dict) and f.get("url")}
        out: list[dict] = []
        for item in payload.files:
            if isinstance(item, ScheduleFileIn):
                stored = await self._save_one(item)
                if stored is not None:
                    out.append(stored)
            elif item.url in by_url:
                out.append(by_url[item.url])
        # 이번 목록에서 빠진 파일은 저장소에서도 지운다.
        surviving = {f["url"] for f in out}
        for url, f in by_url.items():
            if url not in surviving and f.get("path") and self._storage is not None:
                await self._storage.delete(str(f["path"]))
        return out

    async def _save_one(self, item: ScheduleFileIn) -> dict | None:
        if self._storage is None:
            return None
        content, content_type = decode_data_url(item.data)
        ext = Path(item.name).suffix.lower()
        if ext in _BLOCKED_EXT or len(ext) > 10:
            ext = ".bin"
        stored = await self._storage.save(
            subdir="schedules", filename=f"file{ext}", content=content, content_type=content_type,
        )
        return {"name": item.name, "url": stored.url, "path": stored.path, "size": len(content)}
