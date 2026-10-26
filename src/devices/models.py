"""Device metadata persisted in MySQL."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Device:
    id: int
    user_id: int
    cloud_device_id: str
    cloud_product_id: str
    display_name: str
    hardware_model: str | None
    sample_interval_ms: int | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Device":
        return cls(**{name: record[name] for name in cls.__dataclass_fields__})
