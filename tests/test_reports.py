"""Offline tests of cloud-report isolation for multiple users and devices."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.auth_api import create_app
from src.telemetry.amqp import NotificationProcessor
from src.telemetry.cloud import InvalidNotification
from src.telemetry.reports import ReportBuffer


class DeviceDatabase:
    def __init__(self):
        self.versions = {1: 1, 2: 1}
        self.devices = {
            number: {"id": number, "user_id": owner, "cloud_device_id": cloud_id,
                     "display_name": f"device-{number}", "last_seen_at": None}
            for number, owner, cloud_id in (
                (7, 1, "cloud-a-1"), (8, 1, "cloud-a-2"), (9, 2, "cloud-b-1"),
            )
        }

    def get_user_session(self, user_id):
        version = self.versions.get(user_id)
        return {"id": user_id, "session_version": version} if version else None

    def list_devices_for_user(self, user_id):
        return [deepcopy(device) for device in self.devices.values() if device["user_id"] == user_id]

    def get_device_identity_by_cloud_id(self, cloud_id):
        return next(({"id": device["id"], "user_id": device["user_id"]} for device in self.devices.values()
                     if device["cloud_device_id"] == cloud_id), None)

    def touch_last_seen(self, cloud_id):
        pass

    def get_device_for_user(self, user_id, device_id):
        device = self.devices.get(device_id)
        return deepcopy(device) if device and device["user_id"] == user_id else None

    def delete_device(self, user_id, device_id):
        if self.get_device_for_user(user_id, device_id) is None:
            return False
        del self.devices[device_id]
        return True


class ReportIsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {
            "APP_SECRET_KEY": "test-only", "LOG_DIR": directory.name,
            "DAILY_STEPS_STATE_PATH": str(Path(directory.name) / "steps.sqlite3"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.database = DeviceDatabase()
        self.app = create_app(self.database, device_verifier=lambda *_: True)
        self.addCleanup(self.app.extensions["step_history"].close)
        self.service = self.app.extensions["telemetry_service"]
        self.processor = NotificationProcessor(self.service)
        self.a, self.b = self.app.test_client(), self.app.test_client()
        for client, user in ((self.a, 1), (self.b, 2)):
            with client.session_transaction() as session:
                session.update(user_id=user, session_version=1)

    @staticmethod
    def report(cloud_id, value, request_id):
        path = Path(__file__).parent / "fixtures/iotda-six-axis-report.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        body["notify_data"]["header"]["device_id"] = cloud_id
        body["request_id"] = request_id
        body["notify_data"]["body"]["services"][0]["properties"]["accel_x"] = value
        return body

    def test_each_user_gets_only_owned_devices_and_each_device_has_its_own_reports(self):
        for cloud, value, identity in (
            ("cloud-a-1", 11, "a1-first"), ("cloud-b-1", 99, "b1"),
            ("cloud-a-2", 22, "a2"), ("cloud-a-1", 12, "a1-second"),
        ):
            self.assertEqual(self.processor.process(self.report(cloud, value, identity)), "accepted")
        response = self.a.get("/api/telemetry/reports?user_id=2&device_id=9")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.json["data"]
        self.assertEqual(data["user_id"], 1)
        self.assertEqual([device["device_id"] for device in data["devices"]], [7, 8])
        a1, a2 = data["devices"]
        self.assertEqual(a1["latest"]["accel_x"], 12)
        self.assertEqual(a2["latest"]["accel_x"], 22)
        for device in data["devices"]:
            self.assertTrue(all(report["device_id"] == device["device_id"] for report in device["reports"]))
            self.assertTrue(all(json.loads(report["raw"])["notify_data"]["header"]["device_id"]
                                == device["cloud_device_id"] for report in device["reports"]))
        self.assertEqual(len(a1["reports"]), 2)
        self.assertEqual(len(a2["reports"]), 1)
        self.assertNotIn("cloud-b-1", response.get_data(as_text=True))
        b_data = self.b.get("/api/telemetry/reports").json["data"]
        self.assertEqual(b_data["user_id"], 2)
        self.assertEqual([device["device_id"] for device in b_data["devices"]], [9])
        self.assertEqual(b_data["devices"][0]["latest"]["accel_x"], 99)
        for client, forbidden in ((self.a, 9), (self.b, 7), (self.b, 8)):
            for endpoint in ("latest", "window", "summary", "status"):
                self.assertEqual(client.get(f"/api/devices/{forbidden}/telemetry/{endpoint}").status_code, 404)

    def test_content_cannot_override_cloud_device_or_application_user(self):
        body = self.report("cloud-a-1", 11, "spoof-attempt")
        values = body["notify_data"]["body"]["services"][0]["properties"]
        values.update(user_id=2, cloud_device_id="cloud-b-1")
        self.processor.process(body)
        a_data = self.a.get("/api/telemetry/reports").json["data"]
        self.assertEqual(len(a_data["devices"][0]["reports"]), 1)
        b_data = self.b.get("/api/telemetry/reports").json["data"]
        self.assertEqual(b_data["devices"][0]["reports"], [])

    def test_invalid_unknown_and_duplicate_reports_are_not_added(self):
        body = self.report("cloud-a-1", 11, "a1")
        self.processor.process(body)
        self.assertEqual(self.processor.process(body), "duplicate")
        invalid = self.report("cloud-a-2", "not-a-number", "bad")
        with self.assertRaises(InvalidNotification):
            self.processor.process(invalid)
        self.assertEqual(self.processor.process(self.report("unknown-device", 55, "unknown")), "unbound_device")
        data = self.a.get("/api/telemetry/reports").json["data"]
        self.assertEqual([len(device["reports"]) for device in data["devices"]], [1, 0])

    def test_unbind_removes_reports_and_next_reports_are_ignored(self):
        self.processor.process(self.report("cloud-a-1", 11, "a1"))
        self.processor.process(self.report("cloud-a-2", 22, "a2"))
        self.processor.process(self.report("cloud-b-1", 99, "b1"))
        self.assertEqual(self.b.delete("/api/devices/7").status_code, 404)
        self.assertEqual(self.a.delete("/api/devices/7").status_code, 200)
        self.assertEqual(self.service.report_buffer.snapshot(7), [])
        self.assertIsNone(self.service.buffer.latest(7))
        self.assertEqual(self.processor.process(self.report("cloud-a-1", 12, "late")), "unbound_device")
        self.assertEqual([device["device_id"] for device in self.a.get("/api/telemetry/reports").json["data"]["devices"]], [8])
        self.assertEqual(len(self.b.get("/api/telemetry/reports").json["data"]["devices"][0]["reports"]), 1)

    def test_anonymous_logged_out_and_expired_sessions_cannot_read_reports(self):
        self.processor.process(self.report("cloud-a-1", 11, "a1"))
        self.assertEqual(self.app.test_client().get("/api/telemetry/reports").status_code, 401)
        self.assertEqual(self.a.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.a.get("/api/telemetry/reports").status_code, 401)
        self.database.versions[2] = 2
        self.assertEqual(self.b.get("/api/telemetry/reports").status_code, 401)

    def test_real_activity_and_times_survive_latest_window_and_display_filtering(self):
        first = self.report("cloud-a-1", 10, "first")
        first["notify_data"]["body"]["services"][0]["properties"].update(
            step_increment=3, is_sedentary=1, is_fall=0,
        )
        second = self.report("cloud-a-1", 20, "second")
        second["notify_data"]["body"]["services"][0]["properties"].update(
            step_increment=5, is_sedentary=0, is_fall=1,
        )
        for body in (first, second):
            self.assertEqual(self.processor.process(body), "accepted")
        latest = self.a.get("/api/devices/7/telemetry/latest").json["data"]
        self.assertEqual(latest["step_increment"], 5)
        self.assertEqual((latest["is_sedentary"], latest["is_fall"]), (0, 1))
        self.assertEqual(latest["reported_at"], "2026-10-12T03:40:18+00:00")
        self.assertEqual(latest["cloud_event_at"], "2026-10-12T03:40:18.976000+00:00")
        self.assertNotEqual(latest["received_at"], latest["reported_at"])
        window = self.a.get(
            "/api/devices/7/telemetry/window?filter=moving_average&filter_window=2",
        ).json["data"]["samples"]
        self.assertEqual([sample["accel_x"] for sample in window], [10, 15])
        self.assertEqual([sample["step_increment"] for sample in window], [3, 5])
        self.assertEqual([sample["is_fall"] for sample in window], [0, 1])
        self.assertEqual([sample["roll"] for sample in window], [62.73, 62.73])
        previews = self.a.get("/api/telemetry/reports").json["data"]["devices"]
        self.assertEqual(previews[0]["latest"], latest)
        self.assertEqual(previews[1]["reports"], [])
        self.assertEqual(self.b.get("/api/telemetry/reports").json["data"]["devices"][0]["reports"], [])


class ReportBufferTests(unittest.TestCase):
    def test_count_expiry_and_snapshots_cannot_mutate_stored_reports(self):
        buffer = ReportBuffer(retention_seconds=10, max_reports=2)
        received_at = datetime.now(timezone.utc)
        with patch("src.telemetry.reports.time.monotonic", return_value=0) as clock:
            for value in (1, 2, 3):
                buffer.append(7, {"value": value}, received_at)
            rows = buffer.snapshot(7)
            self.assertEqual([json.loads(row["raw"])["value"] for row in rows], [2, 3])
            rows[0]["raw"] = "modified"
            self.assertEqual(json.loads(buffer.snapshot(7)[0]["raw"])["value"], 2)
            clock.return_value = 10
            self.assertEqual(buffer.snapshot(7), [])

    def test_byte_limits_and_device_limits_bound_memory_and_mark_truncation(self):
        buffer = ReportBuffer(max_devices=2, max_message_bytes=32, max_total_bytes=64)
        received_at = datetime.now(timezone.utc)
        for device_id in (7, 8, 9):
            buffer.append(device_id, {"value": "中文" * 30}, received_at)
        self.assertEqual(buffer.snapshot(7), [])
        for device_id in (8, 9):
            row = buffer.snapshot(device_id)[0]
            self.assertTrue(row["truncated"])
            self.assertLessEqual(len(row["raw"].encode("utf-8")), 32)
        buffer.append(9, {"value": "x" * 100}, received_at)
        self.assertEqual(buffer.snapshot(8), [])
        buffer.clear(9)
        self.assertEqual(buffer.snapshot(9), [])
        buffer.append(7, {"value": 1}, received_at)
        self.assertEqual(len(buffer.snapshot(7)), 1)


if __name__ == "__main__":
    unittest.main()
