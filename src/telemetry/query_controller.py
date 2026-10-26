"""Session-authenticated telemetry queries for the frontend."""

from datetime import date, datetime, timedelta
import re

from flask import Blueprint, request

from src.common.errors import ServiceError
from src.common.http import require_user, response
from src.telemetry.steps import BUSINESS_TIMEZONE


class TelemetryQueryController:
    def __init__(self, service):
        self.service = service
        self.blueprint = Blueprint("telemetry_queries", __name__)
        self.blueprint.add_url_rule(
            "/api/telemetry/reports", view_func=self.reports, methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/telemetry/steps/daily", view_func=self.daily_steps, methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/telemetry/alerts", view_func=self.alerts, methods=["GET"],
        )
        for suffix, operation in (
            ("latest", self.latest), ("window", self.window),
            ("summary", self.summary), ("status", self.status),
        ):
            self.blueprint.add_url_rule(
                f"/api/devices/<int:device_id>/telemetry/{suffix}",
                view_func=operation, methods=["GET"],
            )

    def _user_id(self):
        return require_user(self.service.database, include_profile=False)["id"]

    def reports(self):
        result = response(200, "查询成功", self.service.reports_for_user(self._user_id()))
        result[0].headers["Cache-Control"] = "no-store"
        return result

    def daily_steps(self):
        user_id = self._user_id()
        end_date = self._date("end_date", datetime.now(BUSINESS_TIMEZONE).date())
        default_start = max(date(1000, 1, 1), end_date - timedelta(days=29))
        start_date = self._date("start_date", default_start)
        result = response(200, "查询成功", self.service.daily_steps(user_id, start_date, end_date))
        result[0].headers["Cache-Control"] = "no-store"
        return result

    def alerts(self):
        user_id = self._user_id()
        raw = self._single("after", "0")
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or int(raw) > (1 << 63) - 1:
            raise ServiceError("after 必须为非负整数游标")
        result = response(200, "查询成功", self.service.alerts_for_user(
            user_id, after=int(raw), limit=self._integer("limit", 100, 200),
        ))
        result[0].headers["Cache-Control"] = "no-store"
        return result

    def _date(self, name, default):
        raw = self._single(name, default.isoformat())
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            raise ServiceError(f"{name} 必须为 YYYY-MM-DD 日期")
        try:
            result = date.fromisoformat(raw)
        except ValueError as exc:
            raise ServiceError(f"{name} 不是有效日期") from exc
        if result.year < 1000:
            raise ServiceError(f"{name} 必须在 1000-01-01 到 9999-12-31 之间")
        return result

    def _seconds(self):
        maximum = self.service.buffer.retention_seconds
        return self._integer("seconds", min(60, maximum), maximum)

    @staticmethod
    def _single(name, default):
        values = request.args.getlist(name)
        if len(values) > 1:
            raise ServiceError(f"{name} 不能重复提交")
        return values[0] if values else default

    @staticmethod
    def _integer(name, default, maximum):
        raw = TelemetryQueryController._single(name, str(default))
        if not raw.isascii() or not raw.isdecimal():
            raise ServiceError(f"{name} 必须是 1 到 {maximum} 的整数")
        normalized = raw.lstrip("0") or "0"
        if len(normalized) > len(str(maximum)):
            raise ServiceError(f"{name} 必须是 1 到 {maximum} 的整数")
        value = int(normalized)
        if not 1 <= value <= maximum:
            raise ServiceError(f"{name} 必须是 1 到 {maximum} 的整数")
        return value

    def latest(self, device_id):
        return response(200, "查询成功", self.service.latest(self._user_id(), device_id))

    def window(self, device_id):
        user_id = self._user_id()
        seconds = self._seconds()
        mode = self._single("filter", "none")
        if mode not in ("none", "moving_average"):
            raise ServiceError("filter 仅支持 none 或 moving_average")
        size = self._integer("filter_window", 5, 50)
        return response(200, "查询成功", self.service.window(
            user_id, device_id, seconds, filter_mode=mode, filter_window=size,
        ))

    def summary(self, device_id):
        user_id = self._user_id()
        seconds = self._seconds()
        return response(200, "查询成功", self.service.summary(user_id, device_id, seconds))

    def status(self, device_id):
        return response(200, "查询成功", self.service.status(self._user_id(), device_id))
