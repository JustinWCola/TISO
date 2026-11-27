import os

from pydantic import BaseModel, ConfigDict, Field


class AmapRoutingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_size: int = Field(default=20, ge=1, le=100)
    qps: int = Field(default=1, ge=1)
    max_concurrency: int = Field(default=1, ge=1)

    @classmethod
    def from_env(cls) -> "AmapRoutingConfig":
        names = {
            "batch_size": "AMAP_BATCH_SIZE",
            "qps": "AMAP_QPS",
            "max_concurrency": "AMAP_MAX_CONCURRENCY",
        }
        return cls(**{field: os.environ[name] for field, name in names.items() if name in os.environ})
