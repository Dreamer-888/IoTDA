"""Device binding, listing, renaming, and ownership business rules."""

from typing import Any, Callable
import threading

from src.DBcontrol import DBControl
from src.devices.dto import DeviceDTO
from src.devices.models import Device
from src.common.errors import ServiceError


class DeviceServiceError(ServiceError):
    """Device validation, ownership, or conflict error."""


class DeviceService:
    def __init__(self, database: DBControl, verifier: Callable[[int, DeviceDTO], bool],
                 *, lifecycle_lock=None, on_unbind=None):
        self.database = database
        self.verifier = verifier
        self.lifecycle_lock = lifecycle_lock if lifecycle_lock is not None else threading.RLock()
        self.on_unbind = on_unbind

    @staticmethod
    def _text(value: Any, name: str, limit: int) -> str:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise DeviceServiceError(f"{name} 必须是 1 到 {limit} 个字符的字符串")
        return value.strip()

    def bind(self, user_id: int, device_info: DeviceDTO) -> Device:
        device = DeviceDTO(
            self._text(device_info.cloud_device_id, "cloud_device_id", 128),
            self._text(device_info.cloud_product_id, "cloud_product_id", 128),
            self._text(device_info.display_name, "display_name", 100),
            None if device_info.hardware_model is None else
            self._text(device_info.hardware_model, "hardware_model", 100),
        )
        if not self.verifier(user_id, device):
            raise DeviceServiceError("设备尚未分配给当前用户或产品标识不匹配", 403)
        try:
            record = self.database.bind_device_and_get(
                user_id, device.cloud_device_id, device.cloud_product_id,
                device.display_name, device.hardware_model,
            )
        except Exception as exc:
            if getattr(exc, "errno", None) == 1062:
                raise DeviceServiceError("该云端设备已绑定", 409) from exc
            raise
        return Device.from_record(record)

    def list_by_user(self, user_id: int) -> list[Device]:
        return [Device.from_record(row) for row in self.database.list_devices_for_user(user_id)]

    def assert_owner(self, user_id: int, device_id: int) -> Device:
        row = self.database.get_device_for_user(user_id, device_id)
        if row is None:
            raise DeviceServiceError("设备不存在或不属于当前用户", 404)
        return Device.from_record(row)

    def rename(self, user_id: int, device_id: int, name: str) -> bool:
        name = self._text(name, "display_name", 100)
        changed = self.database.rename_device(user_id, device_id, name)
        if changed is None:
            raise DeviceServiceError("设备不存在或不属于当前用户", 404)
        return changed

    def unbind(self, user_id: int, device_id: int) -> bool:
        with self.lifecycle_lock:
            if not self.database.delete_device(user_id, device_id):
                raise DeviceServiceError("设备不存在或不属于当前用户", 404)
            if self.on_unbind is not None:
                self.on_unbind(device_id)
        return True

    def find_by_cloud_id(self, cloud_device_id: str) -> Device | None:
        cloud_device_id = self._text(cloud_device_id, "cloud_device_id", 128)
        row = self.database.get_device_by_cloud_id(cloud_device_id)
        return Device.from_record(row) if row is not None else None
