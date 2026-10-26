"""Flask application factory for the IoT visualization backend."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from flask import Flask, g, request, send_from_directory
from werkzeug.exceptions import HTTPException

from src.DBcontrol import DBControl, DatabasePoolExhaustedError, DatabaseUnavailableError
from src.common.errors import ServiceError
from src.common.http import response as api_response
from src.common.logging import configure_logging, log_request
from src.devices.binding import PreallocatedDeviceVerifier
from src.devices.controller import DeviceController
from src.devices.service import DeviceService
from src.user.controller import AuthController
from src.user.service import AuthService
from src.telemetry.buffer import SensorBuffer
from src.common.config import positive_env_setting
from src.telemetry.service import TelemetryService
from src.telemetry.steps import DailyStepHistory
from src.telemetry.query_controller import TelemetryQueryController
from src.telemetry.live import LiveController, LiveHub


def create_app(db_control: DBControl | None = None, device_verifier=None) -> Flask:
    """Create the app and attach user and device management APIs."""
    secret_key = os.getenv("APP_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("APP_SECRET_KEY must be set before starting the API")

    app = Flask(__name__)
    configure_logging(app)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        # Read-only requests must not overwrite a newer login/migrated cookie
        # when concurrent HTTP responses finish out of order.
        SESSION_REFRESH_EACH_REQUEST=False,
        SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower()
        == "true",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    database = db_control or DBControl.from_env()
    controller = AuthController(AuthService(database))
    app.register_blueprint(controller.blueprint)
    verifier = device_verifier if device_verifier is not None else PreallocatedDeviceVerifier.from_env()
    step_history = DailyStepHistory.from_env(database)
    app.extensions["step_history"] = step_history
    live_hub = LiveHub()
    app.extensions["live_hub"] = live_hub
    telemetry = TelemetryService(
        database, SensorBuffer.from_env(),
        online_timeout_seconds=positive_env_setting("TELEMETRY_ONLINE_TIMEOUT_SECONDS", 30),
        step_history=step_history,
        live_hub=live_hub,
    )
    app.extensions["telemetry_service"] = telemetry
    app.register_blueprint(TelemetryQueryController(telemetry).blueprint)
    devices = DeviceService(
        database, verifier, lifecycle_lock=telemetry.lifecycle_lock,
        on_unbind=telemetry.clear_device,
    )
    app.extensions["device_service"] = devices
    app.register_blueprint(DeviceController(devices).blueprint)
    allowed_origin = os.getenv("FRONTEND_ORIGIN")
    app.config["LIVE_FRONTEND_ORIGIN"] = allowed_origin

    frontend_directory = Path(__file__).resolve().parents[1] / "frontend" / "dist"

    @app.get("/test-ui/")
    def test_ui():
        if not (frontend_directory / "index.html").is_file():
            return api_response(
                503, "前端尚未构建，请在 frontend 目录执行 npm ci 和 npm run build"
            )
        return send_from_directory(frontend_directory, "index.html")

    @app.get("/test-ui/<path:filename>")
    def test_ui_asset(filename):
        return send_from_directory(frontend_directory, filename)

    @app.after_request
    def add_cors_headers(response):
        response.headers["X-Request-ID"] = g.request_id
        log_request(response)
        origin = request.headers.get("Origin")
        if allowed_origin and origin == allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PATCH, DELETE, OPTIONS"
            )
            response.headers.add("Vary", "Origin")
        return response

    @app.before_request
    def handle_preflight():
        g.request_id = uuid.uuid4().hex
        g.request_started = time.monotonic()
        if request.method == "OPTIONS":
            return ("", 204)
        return None

    LiveController(app, telemetry, live_hub)

    @app.errorhandler(ServiceError)
    def business_error(error):
        app.logger.warning("event=business.rejected status=%s", error.status_code)
        return api_response(error.status_code, str(error))

    @app.errorhandler(DatabasePoolExhaustedError)
    def pool_exhausted(_error):
        app.logger.warning("event=database.pool_exhausted")
        return api_response(503, "数据库繁忙，请稍后重试")

    @app.errorhandler(DatabaseUnavailableError)
    def database_unavailable(_error):
        app.logger.exception("event=database.unavailable")
        return api_response(503, "数据库暂时不可用，请稍后重试")

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: "请求格式错误", 404: "接口不存在",
            405: "请求方法不允许", 413: "请求体过大", 415: "请求媒体类型不支持",
        }
        response, status = api_response(
            error.code, messages.get(error.code, error.name)
        )
        # Preserve protocol headers such as Allow on 405 responses.
        for name, value in error.get_headers():
            if name.lower() not in {"content-type", "content-length"}:
                response.headers[name] = value
        return response, status

    @app.errorhandler(Exception)
    def unexpected_error(_error):
        app.logger.exception("event=api.unexpected_error")
        return api_response(500, "服务器内部错误")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
