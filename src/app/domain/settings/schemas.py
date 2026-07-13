from typing import Literal

from pydantic import BaseModel

# 종족(4종) 아이콘뿐 아니라 화면에서 쓰는 다른 이미지 슬롯(예: 홈 로고)도 같은 테이블/맵에
# 함께 담는다 — 관리 화면 하나, 저장 API 하나로 통합하기 위함. home_logo_light는 라이트
# 테마용 홈 로고 — 라이트 테마는 배경이 흰색으로 바뀌므로(Header.tsx의 scr-light-theme),
# 어두운 배경을 전제로 만든 로고 이미지가 잘 안 보일 수 있어 완전히 별도로 등록한다.
IconSlot = Literal["테란", "프로토스", "저그", "랜덤", "home_logo", "home_logo_light"]
IconType = Literal["text", "image"]


class ImageSettingSchema(BaseModel):
    type: IconType
    value: str


ImageSettingMap = dict[IconSlot, ImageSettingSchema]
