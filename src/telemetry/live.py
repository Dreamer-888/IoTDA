"""Bounded user-scoped WebSocket queues; socket IO never runs on AMQP thread."""

from dataclasses import dataclass, field
import json
import logging
import queue
import threading
import time

from flask import current_app, g, request, session
from flask_sock import Sock
from simple_websocket import ConnectionClosed

from src.common.errors import ServiceError
from src.common.http import require_user

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    user_id: int
    session_id: str
    device_id: int | None = None
    version: int = 0
    messages: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=256))
    closed: tuple | None = None


class LiveHub:
    def __init__(self):
        self._lock = threading.RLock()
        self._clients = set()

    def connect(self, user_id, session_id):
        with self._lock:
            if len(self._clients) >= 128 or sum(c.user_id == user_id for c in self._clients) >= 8:
                raise ServiceError("实时连接过多，请关闭多余页面", 503)
            client = Subscription(user_id, session_id)
            self._clients.add(client)
            return client

    def disconnect(self, client):
        with self._lock:
            self._clients.discard(client)

    def close_all(self):
        with self._lock:
            for client in self._clients:
                self._drain(client)
                client.closed = (1001, "服务正在停止")

    @staticmethod
    def _drain(client):
        messages = []
        while True:
            try:
                messages.append(client.messages.get_nowait())
            except queue.Empty:
                return messages

    def select(self, client, device_id, version):
        with self._lock:
            client.device_id = device_id
            client.version = version
            # Selection changes discard samples, while account alerts survive.
            for message in self._drain(client):
                if message["type"] == "alert":
                    client.messages.put_nowait(message)

    def publish(self, user_id, device_id, sample, alerts):
        with self._lock:
            for client in self._clients:
                if client.user_id != user_id or client.closed:
                    continue
                messages = [{"type": "alert", "data": event} for event in alerts]
                if client.device_id == device_id:
                    messages.append({"type": "sample", "subscription_id": client.version,
                                     "data": sample})
                for message in messages:
                    try:
                        client.messages.put_nowait(message)
                    except queue.Full:
                        self._drain(client)
                        client.closed = (1013, "连接积压，请重连并重新同步")
                        break

    def revoke(self, *, session_id=None, user_id=None, device_id=None):
        with self._lock:
            for client in self._clients:
                if device_id is not None and client.device_id != device_id:
                    for message in self._drain(client):
                        if message.get("data", {}).get("device_id") != device_id:
                            client.messages.put_nowait(message)
                if ((session_id is not None and client.session_id == session_id)
                        or (user_id is not None and client.user_id == user_id)
                        or (device_id is not None and client.device_id == device_id)):
                    self._drain(client)
                    client.closed = ((4404, "设备已解绑") if device_id is not None
                                     else (4401, "登录状态已失效"))


class LiveController:
    def __init__(self, app, service, hub):
        self.service, self.hub = service, hub
        app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25, "max_message_size": 4096}
        sock = Sock(app)

        @app.before_request
        def authorize_live():
            if request.endpoint != "telemetry_live":
                return
            expected = ("https" if request.is_secure else "http") + "://" + request.host
            allowed = {expected, app.config.get("LIVE_FRONTEND_ORIGIN")}
            if request.headers.get("Origin") not in allowed or not request.headers.get("Origin"):
                raise ServiceError("实时连接来源不被允许", 403)
            user = require_user(service.database, include_profile=False)
            if not isinstance(session.get("live_session_id"), str):
                logger.warning("event=websocket.rejected reason=legacy_session user_id=%s", user["id"])
                raise ServiceError("请先通过登录状态接口恢复会话", 401)
            g.live_user = user

        @sock.route("/api/telemetry/live")
        def telemetry_live(ws):
            self.serve(ws)

    @staticmethod
    def send(ws, message):
        ws.send(json.dumps(message, ensure_ascii=False, allow_nan=False))

    def serve(self, ws):
        user_id = g.live_user["id"]
        version = g.live_user["session_version"]
        client = None
        try:
            client = self.hub.connect(user_id, session["live_session_id"])
            serializer = current_app.session_interface.get_signing_serializer(current_app)
            _, issued = serializer.loads(
                request.cookies[current_app.config["SESSION_COOKIE_NAME"]],
                max_age=int(current_app.permanent_session_lifetime.total_seconds()),
                return_timestamp=True,
            )
            expires_at = issued.timestamp() + current_app.permanent_session_lifetime.total_seconds()
            self.send(ws, {"type": "ready", "data": {
                "user_id": user_id, "store_id": self.service.step_history.alerts.store_id,
            }})
            next_check = 0
            next_status = 0
            while not client.closed:
                now = time.monotonic()
                if now >= next_check:
                    record = self.service.database.get_user_session(user_id)
                    if not record or record["session_version"] != version or time.time() >= expires_at:
                        client.closed = (4401, "登录状态已失效")
                        break
                    if client.device_id is not None and not self.service.database.get_device_for_user(user_id, client.device_id):
                        client.closed = (4404, "设备归属已变更")
                        break
                    next_check = now + 1
                incoming = ws.receive(timeout=0.02)
                if incoming is not None:
                    try:
                        command = json.loads(incoming)
                        if not isinstance(command, dict) or command.get("type") != "subscribe":
                            raise ServiceError("仅支持 subscribe 消息")
                        device_id = command.get("device_id")
                        subscription_id = command.get("subscription_id")
                        if (device_id is not None and (type(device_id) is not int or device_id < 1)) or type(subscription_id) is not int or not 0 <= subscription_id <= 2147483647:
                            raise ServiceError("订阅参数无效")
                        with self.service.lifecycle_lock:
                            snapshot = None
                            if device_id is not None:
                                snapshot = self.service.live_snapshot(user_id, device_id, command)
                            self.hub.select(client, device_id, subscription_id)
                        if snapshot is not None:
                            self.send(ws, {"type": "snapshot", "subscription_id": subscription_id, "data": snapshot})
                        next_status = now + 1
                    except (ValueError, TypeError):
                        raise ServiceError("实时订阅必须为有效 JSON 对象") from None
                for _ in range(64):
                    if client.closed:
                        break
                    try:
                        message = client.messages.get_nowait()
                    except queue.Empty:
                        break
                    self.send(ws, message)
                if client.device_id is not None and now >= next_status and not client.closed:
                    self.send(ws, {"type": "status", "subscription_id": client.version,
                                   "data": self.service.status(user_id, client.device_id)})
                    next_status = now + 1
            ws.close(reason=client.closed[0], message=client.closed[1])
        except ConnectionClosed:
            pass
        except ServiceError as error:
            self.send(ws, {"type": "error", "code": error.status_code, "message": str(error)})
            ws.close(reason=4404 if error.status_code == 404 else 4400, message="实时连接已停止")
        except Exception:
            logger.exception("event=websocket.failed user_id=%s", user_id)
            try:
                ws.close(reason=1011, message="实时服务暂不可用")
            except ConnectionClosed:
                pass
        finally:
            if client is not None:
                self.hub.disconnect(client)
