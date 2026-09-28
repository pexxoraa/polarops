from pydantic import BaseModel, Field


class NormalizedFacility(BaseModel):
    source_key: str
    name: str = Field(min_length=1, max_length=240)
    country: str = Field(default="", max_length=160)
    programme: str = Field(default="", max_length=320)
    facility_type: str = Field(default="Facility", max_length=120)
    seasonality: str = Field(default="", max_length=120)
    status: str = Field(default="", max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    source: str = "COMNAP"
    source_url: str
    source_updated_at: str = "November 2024"
    raw_json: str
