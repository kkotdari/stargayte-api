from pydantic import BaseModel


class AdminPanelUnlockIn(BaseModel):
    password: str


class AdminPanelUnlockOut(BaseModel):
    # 맞았는지 여부만 돌려준다 — 실제 저장된 값은 어떤 응답에도 절대 담지 않는다.
    ok: bool
