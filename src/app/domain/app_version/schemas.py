from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# 숫자 버전 — 정수(예: "3") 또는 소수 한 단계(예: "3.1"). 앞자리 0 없음. DB에는 문자열로
# 저장하되 앱은 숫자로 비교한다(3.1 >= 3 처럼). 계속 늘어나는 걸 전제로 상한은 없다.
AppVersion = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*(\.[0-9]+)?$")]


class AppVersionStatusOut(BaseModel):
    """로그인한 회원 누구나 조회 — 지금 실제로 보여줄 화면 세트가 뭔지, 그리고 버전 안내를
    띄울지(전역 토글)를 담는다. noticeEnabled는 회원이 부트스트랩에서 함께 받아, 버전이
    바뀌었을 때 안내 모달을 띄울지 판단하는 데 쓴다."""

    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")
    notice_enabled: bool = Field(alias="noticeEnabled")


class AppVersionInfoOut(BaseModel):
    """등록된 버전 하나 — 관리자 패널의 버전 선택 팝업이 나열하고, 버전별 안내 내용(notes)도
    함께 담는다(회원은 이 목록에서 활성 버전의 notes를 찾아 안내 모달에 보여준다)."""

    model_config = ConfigDict(populate_by_name=True)

    number: AppVersion
    # DB에는 없으면 NULL이지만, 프론트가 다루기 쉽게 빈 문자열로 내려준다.
    notes: str = ""


class AppVersionSetIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")


class AppVersionAddIn(BaseModel):
    """새 버전 등록 — 관리자만. 숫자(정수/소수 한 단계) 형식은 AppVersion 패턴이 검증한다."""

    model_config = ConfigDict(populate_by_name=True)

    number: AppVersion


class VersionNoticeToggleIn(BaseModel):
    """버전 안내 표시 여부(전역) 설정 — 관리자만."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool


class VersionNoticeSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool


class AppVersionNotesIn(BaseModel):
    """특정 버전의 안내 내용 편집 — 관리자만. 한 줄에 한 항목(줄바꿈 구분). 비우면 그 버전은
    안내를 띄우지 않는다. 지나치게 긴 입력은 막는다."""

    model_config = ConfigDict(populate_by_name=True)

    notes: Annotated[str, StringConstraints(max_length=4000)]
