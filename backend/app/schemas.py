import os
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

DEFAULT_WALK_SPEED_M_S = 1.2
DEFAULT_MAX_POINTS = 2000
DEFAULT_MAX_REQUESTS = 150
DRIVING_MAX_POINTS = 20000
DRIVING_MAX_REQUESTS = 300


class DrivingBudget(BaseModel):
    max_points: int = Field(default=DEFAULT_MAX_POINTS, ge=1, le=DRIVING_MAX_POINTS)
    max_requests: int = Field(default=DEFAULT_MAX_REQUESTS, ge=1, le=DRIVING_MAX_REQUESTS)

    @classmethod
    def from_env(cls) -> "DrivingBudget":
        names = {
            "max_points": "TISO_DRIVING_DEFAULT_MAX_POINTS",
            "max_requests": "TISO_DRIVING_DEFAULT_MAX_REQUESTS",
        }
        return cls.model_validate({field: os.environ[name] for field, name in names.items() if name in os.environ})


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lng: float = Field(ge=73, le=135)
    lat: float = Field(ge=18, le=54)
    crs: Literal["GCJ-02"] = "GCJ-02"


class Parameters(DrivingBudget):
    model_config = ConfigDict(extra="forbid")

    destination: Destination
    destination_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)] | None = None
    mode: Literal["driving", "metro", "transit"] = "driving"
    traffic_mode: Literal["current", "arrive_by", "static"] = "current"
    arrival_time: AwareDatetime | None = None
    citycode: str | None = Field(default=None, pattern=r"^[0-9]{3,4}$")
    provider: Literal["amap"] = "amap"
    sampling: Literal["adaptive"] = "adaptive"
    radius_m: int | None = Field(default=None, ge=1000, le=30000)
    spacing_m: Literal[500, 1000, 2000] = 1000
    min_spacing_m: Literal[250, 500, 1000] = 250
    walk_speed_m_s: float = Field(default=DEFAULT_WALK_SPEED_M_S, ge=0.5, le=7)
    max_duration_min: int = Field(default=60, ge=15, le=120)
    contour_interval_min: Literal[5, 10, 15, 20, 30] = 15

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.mode == "driving":
            if self.traffic_mode != "current" or self.arrival_time is not None or self.citycode is not None:
                raise ValueError("驾车仅支持当前路况")
        else:
            if self.max_points > DEFAULT_MAX_POINTS or self.max_requests > DEFAULT_MAX_REQUESTS:
                raise ValueError("地铁任务超出计算预算上限")
            arrival: datetime | None = self.arrival_time
            if self.traffic_mode == "arrive_by" and arrival is None:
                raise ValueError("到达时间缺失")
            if arrival is not None and (not 1900 <= arrival.year <= 9998 or arrival.second or arrival.microsecond):
                raise ValueError("地铁到达时间须精确到分钟")
            # Accept the old mode and citycode fields from existing callers.
            self.mode = "metro"
            self.traffic_mode = "static"
            self.citycode = None
        return self
