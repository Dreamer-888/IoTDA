"""Opt-in loopback integration tests using real WebSocket upgrade and frames.

IOTDA_SOCKET_TESTS=1 PYTHONPATH=tests .venv/bin/python -m unittest test_live_socket
No cloud connection or real database is used.
"""

import json
from datetime import datetime, timezone
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from simple_websocket import Client, ConnectionClosed, ConnectionError
from werkzeug.serving import make_server

from src.auth_api import create_app
from src.telemetry.amqp import NotificationProcessor
from test_steps import StepDatabase, notification


class BrowserClient(Client):
    def handshake(self):
        # simple-websocket 1.1 omits the port in Host; browsers include it.
        self.host = f"{self.host}:{self.port}"
        try:
            return super().handshake()
        except Exception:
            self.sock.close()
            raise


@unittest.skipUnless(os.getenv("IOTDA_SOCKET_TESTS") == "1", "enable loopback integration explicitly")
class SocketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "APP_SECRET_KEY": "local-socket-test-only", "LOG_LEVEL": "ERROR",
            "LOG_DIR": self.directory.name,
            "DAILY_STEPS_STATE_PATH": self.directory.name + "/journal.sqlite3",
        })
        self.environment.start()
        self.db = StepDatabase()
        self.app = create_app(self.db, device_verifier=lambda *_: True)
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.url = self.origin.replace("http:", "ws:") + "/api/telemetry/live"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sockets = []
        self.sequence = 0

    def tearDown(self):
        for socket in self.sockets:
            try:
                socket.close()
            except ConnectionClosed:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.extensions["step_history"].close()
        self.environment.stop()
        self.directory.cleanup()

    def browser(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, session_version=1, live_session_id=f"browser-{user_id}")
        return client

    def connect(self, browser):
        socket = BrowserClient.connect(self.url, headers={"Origin": self.origin,
                                "Cookie": "session=" + browser.get_cookie("session").value})
        self.sockets.append(socket)
        ready = json.loads(socket.receive(timeout=2))
        self.assertEqual(ready["type"], "ready")
        return socket

    def until(self, socket, predicate):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            raw = socket.receive(timeout=0.2)
            if raw is not None:
                message = json.loads(raw)
                if predicate(message):
                    return message
        self.fail("expected WebSocket message did not arrive")

    def subscribe(self, socket, device_id, version=1):
        socket.send(json.dumps({"type": "subscribe", "subscription_id": version,
                               "device_id": device_id, "seconds": 60}))
        return self.until(socket, lambda m: m["type"] == "snapshot")

    def report(self, cloud, fall=0, sedentary=0):
        self.sequence += 1
        body = notification(cloud=cloud, identity=f"socket-{self.sequence}")
        body["event_time_ms"] = f"2026-10-13T04:00:00.{self.sequence:03d}Z"
        body["notify_data"]["body"]["services"][0]["properties"].update(is_fall=fall, is_sedentary=sedentary)
        processor = NotificationProcessor(self.app.extensions["telemetry_service"])
        self.assertEqual(processor.process(body), "accepted")

    def test_real_frames_scoped_to_user_and_selected_device(self):
        a, b = self.connect(self.browser(1)), self.connect(self.browser(2))
        self.assertEqual(self.subscribe(a, 7)["data"]["user_id"], 1)
        self.subscribe(b, 9)
        self.report("cloud-a-1", fall=1)
        alarm = self.until(a, lambda m: m["type"] == "alert")
        self.assertEqual(alarm["data"]["type"], "fall")
        sample = self.until(a, lambda m: m["type"] == "sample")
        self.assertEqual(sample["data"]["device_id"], 7)
        self.assertIsNone(b.receive(timeout=0.1))
        self.report("cloud-a-2", sedentary=1)
        alert = self.until(a, lambda m: m["type"] == "alert")
        self.assertEqual(alert["data"]["device_id"], 8)
        self.report("cloud-b-1", fall=1)
        self.assertEqual(self.until(b, lambda m: m["type"] == "alert")["data"]["device_id"], 9)

    def test_logout_closes_live_session(self):
        browser = self.browser(1)
        socket = self.connect(browser)
        self.subscribe(socket, 7)
        browser.post("/api/auth/logout")
        with self.assertRaises(ConnectionClosed) as closed:
            while socket.receive(timeout=2) is not None:
                pass
        self.assertEqual(closed.exception.reason, 4401)

    def test_other_users_device_subscription_is_rejected(self):
        socket = self.connect(self.browser(1))
        socket.send(json.dumps({"type": "subscribe", "device_id": 9, "subscription_id": 1}))
        self.assertEqual(self.until(socket, lambda m: m["type"] == "error")["code"], 404)

    def test_bad_origin_and_no_cookie_cannot_upgrade(self):
        for origin in (self.origin, "http://untrusted.invalid"):
            with self.subTest(origin=origin), self.assertRaises(ConnectionError):
                BrowserClient.connect(self.url, headers={"Origin": origin})
        cookie = "session=" + self.browser(1).get_cookie("session").value
        with self.assertRaises(ConnectionError) as rejected:
            BrowserClient.connect(self.url, headers={"Origin": "http://untrusted.invalid", "Cookie": cookie})
        self.assertEqual(rejected.exception.status_code, 403)

    def test_unbind_closes_subscribed_socket(self):
        browser = self.browser(1)
        socket = self.connect(browser)
        self.subscribe(socket, 7)
        browser.delete("/api/devices/7")
        with self.assertRaises(ConnectionClosed) as closed:
            while socket.receive(timeout=2) is not None:
                pass
        self.assertEqual(closed.exception.reason, 4404)

    def test_session_version_change_closes_socket(self):
        socket = self.connect(self.browser(1))
        self.subscribe(socket, 7)
        self.db.versions[1] = 2
        with self.assertRaises(ConnectionClosed) as closed:
            while socket.receive(timeout=2) is not None:
                pass
        self.assertEqual(closed.exception.reason, 4401)

    def test_legacy_cookie_upgrades_before_successful_websocket_connection(self):
        browser = self.browser(1)
        with browser.session_transaction() as session:
            session.pop("live_session_id")
            session.permanent = True
        cookie = "session=" + browser.get_cookie("session").value
        with self.assertRaises(ConnectionError) as rejected:
            BrowserClient.connect(self.url, headers={"Origin": self.origin, "Cookie": cookie})
        self.assertEqual(rejected.exception.status_code, 401)
        now = datetime.now(timezone.utc)
        self.db.get_user_by_id = lambda _: {
            "id": 1, "username": "legacy", "email": None, "password_hash": "unused",
            "session_version": 1, "created_at": now, "updated_at": now,
        }
        self.assertEqual(browser.get("/api/auth/me").status_code, 200)
        socket = self.connect(browser)
        self.assertEqual(self.subscribe(socket, 7)["data"]["device_id"], 7)
