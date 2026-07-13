from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemberStatus = Literal["pending", "active", "suspended", "withdrawn"]
# 0202=운영자(관리자), 0203=회원. 예전엔 슈퍼관리자(0201)/테스터(0204)/개발자(0205)가
# 더 있었지만 역할 체계를 단순화하면서 다 없앴다(0201은 0202로 병합).
MemberRole = Literal["0202", "0203"]


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    nickname: str
    battletag: str
    insta: str
    avatar_url: str | None = Field(alias="avatar", default=None)
    # Member.replay_aliases(연관객체 목록)가 아니라 Member.replay_alias_values(문자열 목록)
    # 프로퍼티에서 읽는다. 오래된 순으로 정렬돼 있다.
    replay_aliases: list[str] = Field(
        validation_alias="replay_alias_values", alias="replayAliases", default_factory=list
    )
    # Member.roles(연관객체 목록)가 아니라 Member.role_codes(문자열 코드 목록) 프로퍼티에서 읽는다.
    roles: list[MemberRole] = Field(validation_alias="role_codes")
    status: MemberStatus
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class MemberStatusUpdate(BaseModel):
    """관리자 전용 회원 상태 변경. pending -> active 는 승인, active -> suspended 는 사용
    중지, suspended -> active 는 재개로 프론트에서 문맥에 맞는 버튼으로 노출한다."""

    status: Literal["active", "suspended"]


class MemberRolesUpdate(BaseModel):
    """관리자(운영자) 전용 — 회원의 역할 집합을 통째로 교체한다(다중 선택). 최소 하나는
    있어야 한다."""

    model_config = ConfigDict(populate_by_name=True)

    roles: list[Literal["0202", "0203"]] = Field(min_length=1)


class MemberPasswordUpdate(BaseModel):
    """본인 전용 비밀번호 변경 — 현재 비밀번호를 확인한 뒤에만 바꾼다."""

    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(alias="currentPassword")
    new_password: str = Field(min_length=4, max_length=128, alias="newPassword")


class MemberReplayAliasesReplace(BaseModel):
    """인게임 아이디 목록을 관리자/본인이 화면에서 직접 통째로 교체할 때 쓴다 (개수 제한 없음,
    입력칸 + 버튼으로 추가/삭제한 결과를 그대로 저장). 배틀태그와 무관한 별도 정보라
    본인/관리자가 아니어도(로그인한 회원이면 누구나) 바꿀 수 있다 — 경기결과 등록을 누구나
    할 수 있는 것과 같은 맥락."""

    model_config = ConfigDict(populate_by_name=True)

    aliases: list[str] = Field(default_factory=list)


class MemberReplayAliasAdd(BaseModel):
    """리플레이 일괄 등록 화면에서 미매칭 선수를 회원과 연결할 때, 그 회원의 인게임 아이디로
    이름 하나를 추가한다. 개수 제한은 없다."""

    model_config = ConfigDict(populate_by_name=True)

    alias: str = Field(min_length=1, max_length=100)


class MemberCreateByAdmin(BaseModel):
    """관리자(운영자)가 회원관리 화면에서 바로 회원을 생성할 때 쓴다 — 가입 승인 절차 없이
    즉시 active 상태로 만들어지고, 역할은 일반 회원(0203)으로 시작한다. replay_aliases는
    자기 가입(SignupRequest)과 달리 필수가 아니다(0개 허용) — 관리자가 대신 만들어주는
    계정이라 아직 실제 플레이 이름을 모를 수 있다."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    battletag: str = Field(min_length=1, max_length=50)
    replay_aliases: list[str] = Field(default_factory=list, alias="replayAliases")
    insta: str = ""
    avatar: str | None = None


class MemberUpdate(BaseModel):
    """프로필 수정 요청 (부분 업데이트).

    필드가 요청 본문에 아예 없으면 변경하지 않고, avatar 는
    data URL(신규 업로드) / 기존 URL(변경 없음) / null(제거) 중 하나로 온다.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, min_length=2, max_length=64)
    nickname: str | None = None
    battletag: str | None = None
    insta: str | None = None
    avatar: str | None = Field(default=None, alias="avatar")
