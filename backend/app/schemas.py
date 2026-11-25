from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lng: float = Field(ge=73, le=135)
    lat: float = Field(ge=18, le=54)
    crs: Literal["GCJ-02"] = "GCJ-02"


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: Destination
    mode: Literal["driving"] = "driving"
    traffic_mode: Literal["current"] = "current"
    provider: Literal["amap"] = "amap"
    sampling: Literal["uniform"] = "uniform"
    radius_m: int = Field(default=15000, ge=1000, le=30000)
    spacing_m: Literal[500, 1000, 2000] = 1000
    max_duration_min: int = Field(default=60, ge=15, le=120)
    contour_interval_min: Literal[5, 10, 15, 20, 30] = 15
    max_points: int = Field(default=2000, ge=1, le=2000)
    max_requests: int = Field(default=150, ge=1, le=150)
