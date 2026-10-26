"""Internal sample representation independent of a cloud forwarding envelope."""

from dataclasses import dataclass
from datetime import datetime

from src.common.time import format_utc

AXES = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")
ANGLES = ("roll", "pitch", "yaw")
ACTIVITY_FIELDS = ("step_increment", "is_sedentary", "is_fall")
PROPERTY_FIELDS = AXES + ANGLES + ACTIVITY_FIELDS
SIX_AXIS_SERVICE_ID = "six_axis"


@dataclass(frozen=True, slots=True)
class SensorSample:
    device_id: int
    received_at: datetime
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    roll: float
    pitch: float
    yaw: float
    step_increment: int
    is_sedentary: int
    is_fall: int
    reported_at: datetime
    cloud_event_at: datetime

    def to_dict(self):
        return {
            "device_id": self.device_id,
            "received_at": format_utc(self.received_at),
            "reported_at": format_utc(self.reported_at),
            "cloud_event_at": format_utc(self.cloud_event_at),
            **{name: getattr(self, name) for name in PROPERTY_FIELDS},
        }
