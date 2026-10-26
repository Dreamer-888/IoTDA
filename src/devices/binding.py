"""Server-side device preallocation verification.

DEVICE_BINDINGS maps cloud device IDs to their assigned application user ID
and cloud product ID. This supports administrator preallocation without
trusting ownership claims sent by the browser.
"""

import json
import os
from typing import Any

from src.devices.dto import DeviceDTO


class PreallocatedDeviceVerifier:
    def __init__(self, allocations: dict[str, Any]):
        if not isinstance(allocations, dict):
            raise ValueError("DEVICE_BINDINGS must be a JSON object")
        self.allocations = {}
        for cloud_id, assignment in allocations.items():
            if (
                not isinstance(cloud_id, str) or not cloud_id
                or not isinstance(assignment, dict)
                or type(assignment.get("user_id")) is not int
                or assignment["user_id"] <= 0
                or not isinstance(assignment.get("cloud_product_id"), str)
                or not assignment["cloud_product_id"]
            ):
                raise ValueError("Invalid device preallocation in DEVICE_BINDINGS")
            self.allocations[cloud_id] = dict(assignment)

    @classmethod
    def from_env(cls) -> "PreallocatedDeviceVerifier":
        return cls(json.loads(os.getenv("DEVICE_BINDINGS", "{}")))

    def __call__(self, user_id: int, device: DeviceDTO) -> bool:
        assignment = self.allocations.get(device.cloud_device_id)
        return bool(
            assignment and assignment["user_id"] == user_id
            and assignment["cloud_product_id"] == device.cloud_product_id
        )
