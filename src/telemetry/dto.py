"""Validate properties from the real IoTDA six_axis service."""

from dataclasses import dataclass
from datetime import datetime
import math

from src.common.errors import ServiceError
from src.telemetry.models import AXES, ANGLES


@dataclass(frozen=True, slots=True)
class TelemetryDTO:
    cloud_device_id: str
    cloud_product_id: str
    reported_at: datetime
    cloud_event_at: datetime
    values: dict[str, float | int]

    @classmethod
    def from_properties(cls, *, cloud_device_id, cloud_product_id, properties,
                        reported_at, cloud_event_at):
        for name, value in (("device_id", cloud_device_id), ("product_id", cloud_product_id)):
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128:
                raise ServiceError(f"通知头 {name} 必须是 1 到 128 个字符的字符串")
        if not isinstance(properties, dict):
            raise ServiceError("six_axis.properties 必须是 JSON 对象")
        values = {}
        for name in AXES + ANGLES:
            value = properties.get(name)
            if type(value) not in (int, float):
                raise ServiceError(f"{name} 必须是有限数值，不能是字符串、布尔值或 null")
            try:
                number = float(value)
            except (OverflowError, ValueError) as exc:
                raise ServiceError(f"{name} 超出有效数值范围") from exc
            if not math.isfinite(number):
                raise ServiceError(f"{name} 必须是有限数值")
            values[name] = number
        steps = properties.get("step_increment")
        if type(steps) is not int or steps < 0:
            raise ServiceError("step_increment 必须是非负整数")
        values["step_increment"] = steps
        for name in ("is_sedentary", "is_fall"):
            value = properties.get(name)
            if type(value) is not int or value not in (0, 1):
                raise ServiceError(f"{name} 必须是整数 0 或 1")
            values[name] = value
        return cls(cloud_device_id.strip(), cloud_product_id.strip(),
                   reported_at, cloud_event_at, values)
