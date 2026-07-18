from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# 숫자 버전 — 정수(예: "3") 또는 소수 한 단계(예: "3.1"). 앞자리 0 없음. DB에는 문자열로
# 저장하되 앱은 숫자로 비교한다(3.1 >= 3 처럼). 계속 늘어나는 걸 전제로 상한은 없다.
AppVersion = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*(\.[0-9]+)?$")]


class AppVersionStatusOut(BaseModel):
    """로그인한 회원 누구나 조회 — 지금 실제로 보여줄 화면 세트가 뭔지만 담는다."""

    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")


class AppVersionInfoOut(BaseModel):
    """등록된 버전 하나 — 관리자 패널의 버전 선택 팝업이 나열한다."""

    model_config = ConfigDict(populate_by_name=True)

    number: AppVersion


class AppVersionSetIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")
