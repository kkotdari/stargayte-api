from typing import Literal

from pydantic import BaseModel

# 운영자가 교체 가능한 이미지 슬롯 — 이제 종족(4종) 아이콘만 남는다(홈 로고 슬롯은
# 프론트 정적 자산(BrandLogo)으로 대체되어 제거 — DB에 남은 home_logo 행은 get_map이
# 걸러낸다).
IconSlot = Literal["테란", "프로토스", "저그", "랜덤"]
IconType = Literal["text", "image"]


class ImageSettingSchema(BaseModel):
    type: IconType
    value: str


ImageSettingMap = dict[IconSlot, ImageSettingSchema]
