"""Device binding request fields; ownership comes from the login session."""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DeviceDTO:
    cloud_device_id: str
    cloud_product_id: str
    display_name: str
    hardware_model: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DeviceDTO":
        return cls(
            cloud_device_id=payload.get("cloud_device_id"),
            cloud_product_id=payload.get("cloud_product_id"),
            display_name=payload.get("display_name"),
            hardware_model=payload.get("hardware_model"),
        )
