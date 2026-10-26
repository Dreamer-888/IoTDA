"""Parse the real device.property / six_axis notification contract.

The device identity always comes from IoTDA's notification header, never
from the device-controlled message content. SQL-rewritten envelopes are
deliberately not guessed.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from src.common.errors import ServiceError
from src.telemetry.dto import TelemetryDTO
from src.telemetry.models import SIX_AXIS_SERVICE_ID

MAX_MESSAGE_BYTES = 1024 * 1024


class InvalidNotification(ValueError):
    """Permanent format error; messages are rejected without logging their body."""


def json_object(value):
    try:
        if isinstance(value, (bytes, bytearray, memoryview)):
            if len(value) > MAX_MESSAGE_BYTES:
                raise InvalidNotification("消息超过 1 MiB")
            value = bytes(value).decode("utf-8")
        if isinstance(value, str):
            if len(value.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise InvalidNotification("消息超过 1 MiB")
            value = json.loads(value)
        if not isinstance(value, dict):
            raise InvalidNotification("消息必须是 JSON 对象")
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise InvalidNotification("消息超过 1 MiB")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise InvalidNotification("消息不是有效且有界的 JSON 对象") from exc
    return value


@dataclass(frozen=True)
class CloudReport:
    report: TelemetryDTO
    request_id: str | None
    payload: dict


def _compact_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
        raise InvalidNotification("event_time 必须是 YYYYMMDDTHHMMSSZ 格式的 UTC 时间")
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise InvalidNotification("event_time 不是有效时间") from exc


def _millisecond_time(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", value
    ):
        raise InvalidNotification("event_time_ms 必须是包含毫秒的 ISO UTC 时间")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise InvalidNotification("event_time_ms 不是有效时间") from exc


def parse_notification(body, *, service_id=SIX_AXIS_SERVICE_ID):
    if service_id not in ("", SIX_AXIS_SERVICE_ID):
        raise ValueError("真实报文的 AMQP_SERVICE_ID 必须为 six_axis")
    payload = json_object(body)
    if not isinstance(payload.get("resource"), str) or not isinstance(payload.get("event"), str):
        raise InvalidNotification("缺少 resource/event，请保留原始 IoTDA 通知结构")
    if payload["event"] != "report" or payload["resource"] != "device.property":
        return None
    notify = payload.get("notify_data")
    if not isinstance(notify, dict):
        raise InvalidNotification("缺少 notify_data")
    header, content = notify.get("header"), notify.get("body")
    if not isinstance(header, dict) or not isinstance(content, dict):
        raise InvalidNotification("缺少通知 header/body")
    services = content.get("services")
    if not isinstance(services, list):
        raise InvalidNotification("缺少 services 数组")
    candidates = []
    for service in services:
        if not isinstance(service, dict):
            raise InvalidNotification("service 必须是对象")
        if service.get("service_id") == SIX_AXIS_SERVICE_ID:
            candidates.append(service)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise InvalidNotification("six_axis 服务不能重复，避免混合不同样本")
    service = candidates[0]
    # The notification time and service report time have different meanings.
    # Keep both; neither is substituted for this backend's reception time.
    _compact_time(payload.get("event_time"))
    reported_at = _compact_time(service.get("event_time"))
    cloud_event_at = _millisecond_time(payload.get("event_time_ms"))
    try:
        report = TelemetryDTO.from_properties(
            cloud_device_id=header.get("device_id"), cloud_product_id=header.get("product_id"),
            properties=service.get("properties"),
            reported_at=reported_at, cloud_event_at=cloud_event_at,
        )
    except ServiceError as exc:
        raise InvalidNotification(str(exc)) from exc
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
        request_id = None
    return CloudReport(report, request_id, payload)
