"""Resolve bound devices and accept validated samples."""

from datetime import date, datetime, timedelta, timezone
from collections import OrderedDict
import logging
import threading
import time

from src.common.time import format_utc
from src.common.errors import ServiceError

from src.telemetry.models import SensorSample
from src.telemetry.processing import StatisticsService, describe_sample
from src.telemetry.reports import ReportBuffer

logger = logging.getLogger(__name__)


class TelemetryService:
    def __init__(self, database, buffer, *, online_timeout_seconds=30, step_history=None, live_hub=None):
        self.database = database
        self.buffer = buffer
        self.step_history = step_history
        self.live_hub = live_hub
        self.report_buffer = ReportBuffer(
            retention_seconds=buffer.retention_seconds, max_devices=buffer.max_devices,
        )
        if type(online_timeout_seconds) is not int or online_timeout_seconds < 1:
            raise ValueError("online_timeout_seconds must be a positive integer")
        self.online_timeout_seconds = online_timeout_seconds
        # Shared with device unbinding so an in-flight report cannot refill a
        # cleared buffer after the ownership record has been deleted.
        self.lifecycle_lock = threading.RLock()
        self._last_persisted = OrderedDict()

    def receive(self, report, *, raw_notification=None, event_key=None):
        with self.lifecycle_lock:
            device = self.database.get_device_identity_by_cloud_id(report.cloud_device_id)
            if device is None:
                logger.debug("event=telemetry.ignored reason=device_unbound")
                return {"accepted": False, "reason": "device_unbound"}
            device_id = device["id"]
            self.buffer.ensure_capacity(device_id)
            now = time.monotonic()
            # Avoid a database UPDATE for every high-frequency sample.
            persist = now - self._last_persisted.get(device_id, float("-inf")) >= 60
            if persist:
                self.database.touch_last_seen(report.cloud_device_id)
            if self.step_history is not None:
                # The journal commits before this method returns and before
                # AMQP ACK. Unbinding and ingestion share the lifecycle lock.
                self.step_history.record(device["user_id"], report, event_key=event_key)
            sample = SensorSample(
                device_id=device_id, received_at=datetime.now(timezone.utc),
                reported_at=report.reported_at, cloud_event_at=report.cloud_event_at,
                **report.values,
            )
            alerts = (self.step_history.record_alerts(
                device["user_id"], device_id, report, sample.received_at, event_key=event_key,
            ) if self.step_history is not None else [])
            self.buffer.append(sample)
            if raw_notification is not None:
                self.report_buffer.append(device_id, raw_notification, sample.received_at)
            if persist:
                self._last_persisted[device_id] = now
            self._last_persisted.move_to_end(device_id)
            while len(self._last_persisted) > self.buffer.max_devices:
                self._last_persisted.popitem(last=False)
            if self.live_hub is not None:
                self.live_hub.publish(device["user_id"], device_id, describe_sample(sample), alerts)
            for event in alerts:
                logger.warning("event=alert.triggered device_id=%s type=%s event_id=%s",
                               device_id, event["type"], event["event_id"])
            logger.debug("event=telemetry.accepted device_id=%s", device_id)
            return {"accepted": True, "device_id": device_id,
                    "received_at": format_utc(sample.received_at)}

    def clear_device(self, device_id):
        with self.lifecycle_lock:
            self.buffer.clear(device_id)
            self.report_buffer.clear(device_id)
            self._last_persisted.pop(device_id, None)
            if self.step_history is not None:
                self.step_history.clear_alert_state(device_id)
            if self.live_hub is not None:
                self.live_hub.revoke(device_id=device_id)

    def alerts_for_user(self, user_id, *, after=0, limit=100):
        with self.lifecycle_lock:
            device_ids = [d["id"] for d in self.database.list_devices_for_user(user_id)]
            return self.step_history.read_alerts(user_id, device_ids, after=after, limit=limit)

    def live_snapshot(self, user_id, device_id, command):
        self._owned_device(user_id, device_id)
        seconds = command.get("seconds", min(60, self.buffer.retention_seconds))
        mode = command.get("filter", "none")
        size = command.get("filter_window", 5)
        return {"user_id": user_id, "device_id": device_id,
                "raw_samples": [describe_sample(s) for s in self.buffer.window(device_id, seconds)],
                "latest": self.latest(user_id, device_id),
                "window": self.window(user_id, device_id, seconds, filter_mode=mode, filter_window=size),
                "summary": self.summary(user_id, device_id, seconds),
                "status": self.status(user_id, device_id)}

    def reports_for_user(self, user_id):
        # Re-query ownership on every poll, including devices bound or unbound
        # since the browser last refreshed. Never use a browser-supplied user ID.
        with self.lifecycle_lock:
            devices = self.database.list_devices_for_user(user_id)
            result = []
            for device in devices:
                device_id = device["id"]
                sample, age = self.buffer.latest_with_age(device_id)
                state = "unknown" if sample is None else (
                    "online" if age <= self.online_timeout_seconds else "offline"
                )
                result.append({
                    "device_id": device_id, "cloud_device_id": device["cloud_device_id"],
                    "display_name": device["display_name"], "status": state,
                    "latest": describe_sample(sample) if sample else None,
                    "reports": self.report_buffer.snapshot(device_id),
                })
            return {"user_id": user_id, "devices": result,
                    "max_reports_per_device": self.report_buffer.max_reports,
                    "retention_seconds": self.report_buffer.retention_seconds}

    def daily_steps(self, user_id, start_date, end_date):
        if type(start_date) is not date or type(end_date) is not date:
            raise ServiceError("起止日期必须为有效日期")
        days = (end_date - start_date).days + 1
        if not 1 <= days <= 366:
            raise ServiceError("日期范围必须为 1 到 366 天，且开始日期不晚于结束日期")
        records = self.database.get_daily_steps(user_id, start_date, end_date)
        totals = {record["record_date"]: record["steps"] for record in records}
        points = []
        for offset in range(days):
            day = start_date + timedelta(days=offset)
            points.append({"date": day.isoformat(), "steps": totals.get(day), "has_data": day in totals})
        return {"user_id": user_id, "timezone": "Asia/Shanghai",
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                "points": points}

    def _owned_device(self, user_id, device_id):
        device = self.database.get_device_for_user(user_id, device_id)
        if device is None:
            raise ServiceError("设备不存在或不属于当前用户", 404)
        return device

    def latest(self, user_id, device_id):
        self._owned_device(user_id, device_id)
        sample = self.buffer.latest(device_id)
        return describe_sample(sample) if sample else None

    def window(self, user_id, device_id, seconds, *, filter_mode="none", filter_window=5):
        if filter_mode not in ("none", "moving_average"):
            raise ServiceError("filter 仅支持 none 或 moving_average")
        if type(filter_window) is not int or not 1 <= filter_window <= 50:
            raise ServiceError("filter_window 必须是 1 到 50 的整数")
        self.buffer.validate_window(seconds)
        self._owned_device(user_id, device_id)
        samples = self.buffer.window(device_id, seconds)
        records = (StatisticsService.smooth(samples, filter_window)
                   if filter_mode == "moving_average" else
                   [describe_sample(sample) for sample in samples])
        return {"device_id": device_id, "seconds": seconds, "count": len(records),
                "filter": filter_mode,
                "filter_window": filter_window if filter_mode == "moving_average" else None,
                "samples": records}

    def summary(self, user_id, device_id, seconds):
        self.buffer.validate_window(seconds)
        self._owned_device(user_id, device_id)
        samples = self.buffer.window(device_id, seconds)
        return {"device_id": device_id, "seconds": seconds,
                "basis": "raw", **StatisticsService.summarize(samples)}

    def status(self, user_id, device_id):
        device = self._owned_device(user_id, device_id)
        sample, age = self.buffer.latest_with_age(device_id)
        state = "unknown" if sample is None else (
            "online" if age <= self.online_timeout_seconds else "offline"
        )
        return {"device_id": device_id, "status": state,
                "basis": "recent_reports", "online_timeout_seconds": self.online_timeout_seconds,
                "seconds_since_report": round(age, 3) if age is not None else None,
                "last_received_at": format_utc(sample.received_at) if sample else None,
                "last_seen_at": format_utc(device["last_seen_at"])}
