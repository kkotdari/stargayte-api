from pydantic import BaseModel, ConfigDict, Field


class ShareImageUploadIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # 프론트가 캔버스로 그려 FileReader/toDataURL로 만든 data URL(base64).
    data_url: str = Field(alias="dataUrl")


class ShareImageUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
