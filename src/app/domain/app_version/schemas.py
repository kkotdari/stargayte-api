from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# "v" + 1 이상의 정수(앞자리 0 없음) — DB의 정규식 CHECK 제약과 같은 패턴을 유지한다.
# 버전이 계속 늘어나는 걸 전제로 하므로 상한을 두지 않는다.
AppVersion = Annotated[str, StringConstraints(pattern=r"^v[1-9][0-9]*$")]


class AppVersionStatusOut(BaseModel):
    """로그인한 회원 누구나 조회 — 지금 실제로 보여줄 화면 세트가 뭔지만 담는다."""

    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")


class AppVersionSetIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_version: AppVersion = Field(alias="activeVersion")
