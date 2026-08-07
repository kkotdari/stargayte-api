from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 참가표시 두 값 — 행이 없으면 '아직 답 안 함'이라 여기 안 들어간다(models 주석 참고).
AttendResponse = Literal["going", "notGoing"]

# 첨부파일 하나의 상한 — data URL 문자열 길이다(base64라 실제 바이트의 약 4/3).
# 8MB짜리 파일이 대략 이 길이가 된다.
FILE_MAX_CHARS = 11_000_000
MAX_FILES = 5
TITLE_MAX = 60
CONTENT_MAX = 2000
LINK_MAX = 500
FILENAME_MAX = 120


class ScheduleAuthor(BaseModel):
    id: str
    nickname: str
    avatar: str | None = None


class ScheduleFileOut(BaseModel):
    """올려 둔 첨부파일 한 개 — 내려받을 주소와 사람이 붙인 이름."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    url: str
    # 바이트 수 — 카드에서 "1.2MB"처럼 적는 데만 쓴다. 옛 행에는 없을 수 있어 0으로 물러선다.
    size: int = 0


class ScheduleAttendeeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    avatar: str | None = None
    response: AttendResponse


class ScheduleOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    title: str
    # 날짜는 늘 있고(필수), 시각은 안 정했으면 None이다.
    scheduled_date: str = Field(alias="scheduledDate")
    scheduled_time: str | None = Field(default=None, alias="scheduledTime")
    content: str = ""
    link_url: str = Field(default="", alias="linkUrl")
    files: list[ScheduleFileOut] = Field(default_factory=list)
    attendees: list[ScheduleAttendeeOut] = Field(default_factory=list)
    created_by: ScheduleAuthor = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    # 활동 목록이 NEW(올라온 것)와 UPDATE(달라진 것)를 가르는 데 쓴다 — 너 나와와 같은 규칙.
    updated_at: datetime = Field(alias="updatedAt")


class ScheduleFileIn(BaseModel):
    """새로 올리는 첨부파일 한 개 — 브라우저가 FileReader로 만든 data URL 그대로 온다."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(max_length=FILENAME_MAX)
    data: str = Field(max_length=FILE_MAX_CHARS)

    @field_validator("data")
    @classmethod
    def _must_be_data_url(cls, v: str) -> str:
        if not v.startswith("data:") or ";base64," not in v:
            raise ValueError("첨부파일은 data URL(base64) 형식이어야 합니다.")
        return v


class ScheduleWrite(BaseModel):
    """등록과 수정이 같은 모양이다 — 폼이 하나라서다(요청: "등록/수정에 쓰는 모달").

    files는 '최종 목록'이다: 이미 올라가 있는 파일은 url만, 새로 고른 파일은 data까지 담아
    보낸다. 여기 없는 파일은 지운 것으로 본다 — 무엇을 지웠는지 따로 보내면 화면이 쥔
    목록과 서버가 쥔 목록이 어긋날 때 어느 쪽이 옳은지 정할 수가 없다.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    scheduled_date: date = Field(alias="scheduledDate")
    scheduled_time: time | None = Field(default=None, alias="scheduledTime")
    content: str = Field(default="", max_length=CONTENT_MAX)
    link_url: str = Field(default="", max_length=LINK_MAX, alias="linkUrl")
    # 그대로 두는 파일(url만) + 새로 올리는 파일(data까지) — 순서가 곧 카드에 보이는 순서다.
    files: list[ScheduleFileIn | ScheduleFileOut] = Field(default_factory=list, max_length=MAX_FILES)

    # 앞뒤 공백은 길이를 재기 전에 턴다(mode="before") — 뒤에 털면 공백뿐인 제목이
    # min_length=1을 통과한 뒤 빈 문자열로 저장된다.
    @field_validator("title", "content", "link_url", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ScheduleAttendIn(BaseModel):
    """참가표시 — null이면 표시 자체를 거둔다(다시 '아직 답 안 함'으로)."""

    response: AttendResponse | None = None


class ScheduleListOut(BaseModel):
    items: list[ScheduleOut]
