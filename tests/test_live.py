"""Real ingestion edges, durable replay, account isolation and bounded streams."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from src.auth_api import create_app
from src.telemetry.amqp import NotificationProcessor
from src.telemetry.live import LiveHub
from test_steps import StepDatabase, notification


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {
            "APP_SECRET_KEY": "alerts-test-only", "LOG_LEVEL": "CRITICAL",
            "LOG_DIR": self.directory.name,
            "DAILY_STEPS_STATE_PATH": self.directory.name + "/state.sqlite3",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.db = StepDatabase()
        self.create()
        self.tick = 0

    def create(self):
        self.app = create_app(self.db, device_verifier=lambda *_: True)
        self.addCleanup(self.app.extensions["step_history"].close)
        self.service = self.app.extensions["telemetry_service"]
        self.processor = NotificationProcessor(self.service)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, live_session_id="session-a")

    def send(self, fall=0, sedentary=0, cloud="cloud-a-1", *, body=None):
        if body is None:
            self.tick += 1
            body = notification(identity=f"alert-{self.tick}", cloud=cloud, increment=1)
            body["notify_data"]["body"]["services"][0]["properties"].update(
                is_fall=fall, is_sedentary=sedentary,
            )
            body["event_time_ms"] = f"2026-10-13T04:00:00.{self.tick:03d}Z"
        self.assertEqual(self.processor.process(body), "accepted")
        return body

    def events(self, **params):
        response = self.client.get("/api/telemetry/alerts", query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.json["data"]

    def test_short_pulse_survives_latest_returning_to_zero(self):
        self.send(0)
        self.send(1)
        self.send(0)
        self.assertEqual(self.service.latest(1, 7)["is_fall"], 0)
        events = self.events()["events"]
        self.assertEqual([e["type"] for e in events], ["fall"])

    def test_edges_are_independent_per_device_and_flag(self):
        for value in (0, 1, 1, 0, 1):
            self.send(value)
        self.send(1, 1, "cloud-a-2")
        self.send(1, 1, "cloud-b-1")
        events = self.events()["events"]
        self.assertEqual([(e["device_id"], e["type"]) for e in events],
                         [(7, "fall"), (7, "fall"), (8, "fall"), (8, "sedentary")])

    def test_restart_keeps_state_and_duplicate_zero_cannot_reset_it(self):
        zero = self.send(0)
        one = self.send(1)
        old_ids = [e["event_id"] for e in self.events()["events"]]
        self.app.extensions["step_history"].close()
        # The previous connection is already closed; do not close it twice.
        self._cleanups.pop()
        self.create()
        self.send(body=zero)
        self.send(body=one)
        self.send(1)
        self.assertEqual([e["event_id"] for e in self.events()["events"]], old_ids)

    def test_out_of_order_zero_does_not_enable_a_false_second_alarm(self):
        self.send(1)
        older = notification(identity="older")
        older["event_time_ms"] = "2026-10-13T03:59:59.000Z"
        self.send(body=older)
        self.send(1)
        self.assertEqual(len(self.events()["events"]), 1)

    def test_pagination_is_owned_and_cursor_returns_only_new_events(self):
        self.send(1)
        self.send(0)
        self.send(1)
        self.send(1, cloud="cloud-b-1")
        first = self.events(limit=1)
        self.assertTrue(first["has_more"])
        second = self.events(after=first["cursor"])
        self.assertEqual(len(second["events"]), 1)
        self.assertFalse(second["has_more"])
        self.assertEqual(self.events(after=second["cursor"])["events"], [])
        self.assertEqual(second["user_id"], 1)

    def test_unbind_revokes_stream_and_hides_former_device_events(self):
        hub = self.app.extensions["live_hub"]
        client = hub.connect(1, "session-a")
        hub.select(client, 7, 1)
        self.send(1)
        self.assertFalse(client.messages.empty())
        self.assertEqual(self.client.delete("/api/devices/7").status_code, 200)
        self.assertEqual(client.closed[0], 4404)
        self.assertTrue(client.messages.empty())
        self.assertEqual(self.events()["events"], [])

    def test_logout_revokes_only_this_browser_session(self):
        hub = self.app.extensions["live_hub"]
        first = hub.connect(1, "session-a")
        other = hub.connect(1, "another-browser")
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(first.closed[0], 4401)
        self.assertIsNone(other.closed)

    def test_legacy_cookie_upgrade_survives_concurrent_readonly_responses(self):
        now = datetime.now(timezone.utc)
        self.db.get_user_by_id = lambda user_id: {
            "id": 1, "username": "legacy", "email": None, "password_hash": "unused",
            "session_version": 1, "created_at": now, "updated_at": now,
        }
        legacy = self.app.test_client()
        with legacy.session_transaction() as session:
            session.update(user_id=1, session_version=1)
            session.permanent = True
        old_cookie = legacy.get_cookie("session").value
        other_tab = self.app.test_client()
        other_tab.set_cookie("session", old_cookie)
        old_response = other_tab.get("/api/devices/7/telemetry/latest")
        self.assertEqual(old_response.status_code, 200)
        self.assertNotIn("Set-Cookie", old_response.headers)
        self.assertEqual(legacy.get("/api/auth/me").status_code, 200)
        self.assertEqual(other_tab.get("/api/auth/me").status_code, 200)
        with legacy.session_transaction() as session:
            first_id = session["live_session_id"]
        with other_tab.session_transaction() as session:
            self.assertEqual(first_id, session["live_session_id"])
        self.assertNotIn("Set-Cookie", legacy.get("/api/auth/me").headers)
        first = self.app.extensions["live_hub"].connect(1, first_id)
        second = self.app.extensions["live_hub"].connect(1, first_id)
        legacy.post("/api/auth/logout")
        self.assertEqual(first.closed[0], 4401)
        self.assertEqual(second.closed[0], 4401)

    def test_alert_write_failure_leaves_message_unconfirmed_and_retry_is_safe(self):
        body = notification(identity="retry", increment=5)
        body["notify_data"]["body"]["services"][0]["properties"]["is_fall"] = 1
        history = self.app.extensions["step_history"]
        with patch.object(history, "record_alerts", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.processor.process(body)
        self.assertEqual(self.processor.process(body), "accepted")
        self.assertEqual(len(self.events()["events"]), 1)
        self.assertEqual(history._connection.execute("SELECT steps FROM day_totals").fetchone()[0], 5)

    def test_alert_endpoint_requires_auth_and_valid_cursor(self):
        self.assertEqual(self.app.test_client().get("/api/telemetry/alerts").status_code, 401)
        for query in ("after=-1", "after=true", "after=1&after=2", "after=99999999999999999999", "limit=0", "limit=201"):
            self.assertEqual(self.client.get("/api/telemetry/alerts?" + query).status_code, 400)

    def test_expired_events_are_excluded(self):
        self.send(1)
        history = self.app.extensions["step_history"]
        with history._connection:
            history._connection.execute("UPDATE alert_events SET received_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.events()["events"], [])


class HubTests(unittest.TestCase):
    def test_user_and_device_isolation_and_alerts_for_unselected_device(self):
        hub = LiveHub()
        a, b = hub.connect(1, "a"), hub.connect(2, "b")
        hub.select(a, 7, 1)
        hub.select(b, 9, 1)
        hub.publish(1, 8, {"device_id": 8}, [{"event_id": "e", "device_id": 8}])
        self.assertEqual(a.messages.get_nowait()["type"], "alert")
        self.assertTrue(b.messages.empty())
        hub.publish(1, 7, {"device_id": 7}, [])
        hub.select(a, 8, 2)
        self.assertTrue(a.messages.empty())

    def test_selection_change_preserves_pending_alerts(self):
        hub = LiveHub()
        client = hub.connect(1, "a")
        hub.select(client, 7, 1)
        hub.publish(1, 7, {"device_id": 7}, [{"event_id": "a"}])
        hub.select(client, 8, 2)
        self.assertEqual(client.messages.get_nowait()["type"], "alert")
        self.assertTrue(client.messages.empty())

    def test_slow_consumer_is_disconnected_without_blocking_publisher(self):
        hub = LiveHub()
        client = hub.connect(1, "a")
        hub.select(client, 7, 1)
        for _ in range(257):
            hub.publish(1, 7, {"device_id": 7}, [])
        self.assertEqual(client.closed[0], 1013)
        self.assertTrue(client.messages.empty())
