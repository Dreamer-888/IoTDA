"""Device HTTP endpoints backed by session authentication."""

from typing import Any, Callable
import logging

from flask import Blueprint

from src.common.http import json_payload, require_user, response
from src.devices.dto import DeviceDTO
from src.devices.service import DeviceService
from src.devices.vo import DeviceVO

logger = logging.getLogger(__name__)


class DeviceController:
    def __init__(self, service: DeviceService):
        self.service = service
        self.blueprint = Blueprint("device_management", __name__)
        self.blueprint.add_url_rule("/api/devices", view_func=self.bind_device, methods=["POST"])
        self.blueprint.add_url_rule("/api/devices", view_func=self.list_by_user, methods=["GET"])
        self.blueprint.add_url_rule("/api/devices/<int:device_id>", view_func=self.rename_device, methods=["PATCH"])
        self.blueprint.add_url_rule("/api/devices/<int:device_id>", view_func=self.unbind_device, methods=["DELETE"])

    def _run(self, operation: Callable[[int], Any], status: int, message: str):
        user = require_user(self.service.database, include_profile=False)
        result = operation(user["id"])
        return response(status, message, result)

    def bind_device(self):
        def bind(user_id):
            payload = json_payload()
            device = self.service.bind(user_id, DeviceDTO.from_payload(payload))
            logger.info("event=device.bound device_id=%s", device.id)
            return DeviceVO(device).to_dict()
        return self._run(bind, 201, "设备绑定成功")

    def list_by_user(self):
        return self._run(
            lambda user_id: [DeviceVO(d).to_dict() for d in self.service.list_by_user(user_id)],
            200, "查询成功",
        )

    def rename_device(self, device_id: int):
        def rename(user_id):
            payload = json_payload()
            changed = self.service.rename(user_id, device_id, payload.get("display_name"))
            logger.info("event=device.renamed device_id=%s changed=%s", device_id, changed)
            return "设备重命名成功" if changed else "设备名称未发生变化"
        return self._run(rename, 200, "请求处理完成")

    def unbind_device(self, device_id: int):
        def unbind(user_id):
            self.service.unbind(user_id, device_id)
            logger.info("event=device.unbound device_id=%s", device_id)
            return "设备解绑成功"
        return self._run(unbind, 200, "请求处理完成")
