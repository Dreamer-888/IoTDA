"""Public device response fields."""

from dataclasses import dataclass
from typing import Any

from src.devices.models import Device
from src.common.time import format_utc


@dataclass(slots=True)
class DeviceVO:
    device: Device

    def to_dict(self) -> dict[str, Any]:
        device = self.device
        return {
            "id": device.id,
            "user_id": device.user_id,
            "cloud_device_id": device.cloud_device_id,
            "cloud_product_id": device.cloud_product_id,
            "display_name": device.display_name,
            "hardware_model": device.hardware_model,
            "sample_interval_ms": device.sample_interval_ms,
            "last_seen_at": format_utc(device.last_seen_at),
            "created_at": format_utc(device.created_at),
            "updated_at": format_utc(device.updated_at),
        }
