"""Daily scheduling, durable counting and authenticated history queries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.auth_api import create_app
from src.telemetry.amqp import AmqpConsumer, NotificationProcessor
from src.telemetry.amqp_config import AmqpConfig
from src.telemetry.cloud import InvalidNotification, parse_notification
from src.telemetry.steps import BUSINESS_TIMEZONE, DailyStepHistory, MAX_STEP_COUNT
from test_reports import DeviceDatabase


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=BUSINESS_TIMEZONE)


def notification(*, increment=5, cloud="cloud-a-1", identity="first", reported_at="20261013T040000Z"):
    body = json.loads((Path(__file__).parent / "fixtures/iotda-six-axis-report.json").read_text())
    body["request_id"] = identity
    body["notify_data"]["header"]["device_id"] = cloud
    service = body["notify_data"]["body"]["services"][0]
    service["event_time"] = reported_at
    service["properties"]["step_increment"] = increment
    return body


class StepDatabase(DeviceDatabase):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.save_calls = 0
        self.failure = False
        self.after_commit = None

    def save_daily_steps(self, records):
        self.save_calls += 1
        if self.failure:
            raise RuntimeError("test database unavailable")
        for row in records:
            self.rows[row["user_id"], row["record_date"]] = row["steps"]
        if self.after_commit:
            self.after_commit()

    def get_daily_steps(self, user_id, start_date, end_date):
        return [{"user_id": owner, "record_date": day, "steps": steps}
                for (owner, day), steps in sorted(self.rows.items())
                if owner == user_id and start_date <= day <= end_date]


class DailyStepTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "steps.sqlite3"
        self.db = StepDatabase()
        self.history = DailyStepHistory(self.db, self.path)
        self.addCleanup(lambda: self.history.close())

    def record(self, *, owner=1, key="event-1", **kwargs):
        return self.history.record(owner, parse_notification(notification(**kwargs)).report, event_key=key)

    def test_writes_at_2200_combines_same_user_devices_and_keeps_users_separate(self):
        self.record(increment=5)
        self.record(key="event-2", increment=7, cloud="cloud-a-2")
        self.record(owner=2, key="event-3", increment=20, cloud="cloud-b-1")
        self.assertEqual(self.history.flush_due(at("2026-10-13T21:59:59")), 0)
        self.assertEqual(self.db.rows, {})
        self.assertEqual(self.history.flush_due(at("2026-10-13T22:00:00")), 2)
        self.assertEqual(self.db.rows, {(1, date(2026, 10, 13)): 12, (2, date(2026, 10, 13)): 20})
        self.assertEqual(self.history.flush_due(at("2026-10-13T22:00:30")), 0)

    def test_after_2200_increments_are_finalized_after_midnight_and_next_day_stays_separate(self):
        self.record(increment=5)
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.record(key="after-save", increment=4, reported_at="20261013T143000Z")
        self.assertEqual(self.history.flush_due(at("2026-10-13T22:30:45")), 0)
        self.record(key="next-day", increment=2, reported_at="20261013T160000Z")
        self.history.flush_due(at("2026-10-14T00:00:00"))
        self.assertEqual(self.db.rows, {(1, date(2026, 10, 13)): 9})
        self.history.flush_due(at("2026-10-14T22:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 14)], 2)

    def test_restart_recovers_day_and_deduplication_before_next_export(self):
        self.record(increment=5)
        self.history.close()
        self.history = DailyStepHistory(self.db, self.path)
        self.assertFalse(self.record(increment=5))
        self.record(key="second", increment=3)
        self.history.flush_due(at("2026-10-14T12:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 8)

    def test_failed_or_unknown_mysql_commit_retries_absolute_total(self):
        self.record(increment=5)
        self.db.failure = True
        with self.assertRaises(RuntimeError):
            self.history.flush_due(at("2026-10-13T22:00:00"))
        self.db.failure = False
        def unknown_commit():
            raise RuntimeError("test connection dropped after commit")
        self.db.after_commit = unknown_commit
        with self.assertRaises(RuntimeError):
            self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 5)
        self.db.after_commit = None
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 5)

    def test_increment_during_mysql_write_is_not_lost(self):
        self.record(increment=5)
        self.db.after_commit = lambda: self.record(key="during-write", increment=2)
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.db.after_commit = None
        self.history.flush_due(at("2026-10-14T00:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 7)

    def test_zero_reports_create_zero_day_and_missing_days_have_no_row(self):
        self.record(increment=0)
        self.record(increment=0)
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows, {(1, date(2026, 10, 13)): 0})

    def test_late_report_updates_its_reported_day(self):
        self.record(increment=5)
        self.history.flush_due(at("2026-10-14T00:00:00"))
        self.record(key="late", increment=3)
        self.history.flush_due(at("2026-10-14T12:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 8)

    def test_concurrent_duplicates_are_counted_once(self):
        report = parse_notification(notification()).report
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.history.record(1, report, event_key="same"), range(20)))
        self.assertEqual(sum(results), 1)
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 5)

    def test_overflow_rolls_back_event_marker_and_can_be_retried(self):
        self.record(increment=MAX_STEP_COUNT)
        with self.assertRaises(InvalidNotification):
            self.record(key="overflow", increment=1)
        self.assertTrue(self.record(owner=2, key="overflow", increment=1))

    def test_mysql_unsupported_report_date_is_rejected_before_recording(self):
        for reported_at in ("09990101T000000Z", "99991231T235959Z"):
            with self.subTest(reported_at=reported_at), self.assertRaises(InvalidNotification):
                self.record(reported_at=reported_at)
        self.assertEqual(self.history.flush_due(at("2026-10-14T12:00:00")), 0)

    def test_invalid_save_time_fails_and_scheduler_stops_promptly(self):
        for value in ("24:00", "9:00", "23:60", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DailyStepHistory(self.db, self.path, save_time=value)
        self.history.start()
        self.history.stop()
        self.assertFalse(self.history._thread.is_alive())


class StepIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {
            "APP_SECRET_KEY": "step-test-only", "LOG_DIR": directory.name,
            "DAILY_STEPS_STATE_PATH": str(Path(directory.name) / "steps.sqlite3"),
            "DAILY_STEPS_SAVE_TIME": "22:00",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.db = StepDatabase()
        self.app = create_app(self.db, device_verifier=lambda *_: True)
        self.history = self.app.extensions["step_history"]
        self.addCleanup(self.history.close)
        self.processor = NotificationProcessor(self.app.extensions["telemetry_service"])
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1)

    def test_reports_are_counted_for_actual_owner_and_survive_unbind(self):
        for cloud, identity, increment in (("cloud-a-1", "a1", 3), ("cloud-a-2", "a2", 4),
                                           ("cloud-b-1", "b1", 20)):
            body = notification(cloud=cloud, identity=identity, increment=increment)
            body["notify_data"]["body"]["services"][0]["properties"]["user_id"] = 2
            self.processor.process(body)
        self.assertEqual(self.client.delete("/api/devices/7").status_code, 200)
        self.assertEqual(self.processor.process(notification(identity="unbound")), "unbound_device")
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows, {(1, date(2026, 10, 13)): 7, (2, date(2026, 10, 13)): 20})

    def test_processor_restart_and_later_device_owner_do_not_recount_same_delivery(self):
        body = notification()
        self.processor.process(body)
        self.db.devices[7]["user_id"] = 2
        new_processor = NotificationProcessor(self.app.extensions["telemetry_service"])
        new_processor.process(body)
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows, {(1, date(2026, 10, 13)): 5})

    def test_local_write_failure_releases_message_without_ack(self):
        consumer = AmqpConsumer(AmqpConfig("iot.example.test", 5671, "queue", "ak", "code"),
                                self.app.extensions["telemetry_service"])
        receiver, message = Mock(), Mock(body=notification(), id="message")
        with patch.object(self.history, "record", side_effect=OSError("test disk failure")):
            with self.assertRaises(OSError):
                consumer.handle_message(receiver, message)
        receiver.release.assert_called_once_with(delivered=False)
        receiver.accept.assert_not_called()
        consumer.handle_message(receiver, message)
        receiver.accept.assert_called_once()
        self.history.flush_due(at("2026-10-13T22:00:00"))
        self.assertEqual(self.db.rows[1, date(2026, 10, 13)], 5)

    def test_history_query_is_authenticated_isolated_ordered_and_fills_missing_days(self):
        self.db.rows = {(1, date(2026, 10, 11)): 0, (1, date(2026, 10, 13)): 10,
                        (2, date(2026, 10, 12)): 999}
        result = self.client.get("/api/telemetry/steps/daily?start_date=2026-10-11&end_date=2026-10-13&user_id=2")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(result.json["data"]["user_id"], 1)
        self.assertEqual(result.json["data"]["points"], [
            {"date": "2026-10-11", "steps": 0, "has_data": True},
            {"date": "2026-10-12", "steps": None, "has_data": False},
            {"date": "2026-10-13", "steps": 10, "has_data": True},
        ])
        self.assertEqual(self.app.test_client().get("/api/telemetry/steps/daily").status_code, 401)

    def test_history_rejects_invalid_duplicate_reversed_and_oversized_ranges(self):
        for query in ("start_date=invalid", "start_date=2026-02-30", "start_date=20261013",
                      "start_date=2026-10-12&start_date=2026-10-13", "end_date=0001-01-01",
                      "start_date=2026-10-14&end_date=2026-10-13",
                      "start_date=2025-01-01&end_date=2026-10-13"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/telemetry/steps/daily?" + query).status_code, 400)


if __name__ == "__main__":
    unittest.main()
