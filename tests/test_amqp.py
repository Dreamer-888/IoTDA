"""Offline AMQP ingestion, delivery semantics and frontend integration tests."""

from copy import deepcopy
from dataclasses import replace
import io
import json
import os
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from proton import SSLDomain, Timeout
from proton.reactor import AtLeastOnce

from src.auth_api import create_app
from src.common.errors import ServiceError
from src.telemetry.amqp import (
    AmqpConsumer, NoMessageReceived, NotificationProcessor, check_connection, connect,
    connection_url, peek_message, tls_domain, watch_messages,
)
from src.telemetry.amqp_config import AmqpConfig
from src.telemetry.cloud import InvalidNotification, parse_notification
from src.telemetry.models import PROPERTY_FIELDS

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def payload():
    return json.loads((FIXTURES / "iotda-six-axis-report.json").read_text())


def config():
    return AmqpConfig("iot.example.test", 5671, "sensor_data_queue", "key-secret", "code-secret")


class ConfigurationTests(unittest.TestCase):
    def test_current_env_names_and_no_secret_repr(self):
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "iot.example.test", "AMQP_QUEUE": "sensor_data_queue",
            "AMQP_AK": "key-secret", "AMQP_CODE": "code-secret",
        }, clear=True):
            result = AmqpConfig.from_env()
        self.assertEqual(result.url, "amqps://iot.example.test:5671")
        self.assertEqual(result.service_id, "six_axis")
        self.assertNotIn("key-secret", repr(result))
        self.assertNotIn("code-secret", repr(result))

    def test_aliases_and_secure_url(self):
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "amqps://iot.example.test:5671", "AMQP_QUEUE": "queue",
            "AMQP_ACCESS_KEY": "ak", "AMQP_ACCESS_CODE": "code",
        }, clear=True):
            self.assertEqual(AmqpConfig.from_env().access_key, "ak")
            for address in ("http://iot.example.test", "amqp://iot.example.test",
                            "amqps://secret:secret@host", "host/path", "host?secret=1",
                            "host:bad", "host:0", "host:5672"):
                with self.subTest(address=address), patch.dict(os.environ, {
                    "AMQP_ACCESS": address, "AMQP_PORT": "5671",
                }), self.assertRaises(ValueError):
                    AmqpConfig.from_env()

    def test_real_service_is_default_and_legacy_service_fails_before_connecting(self):
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "host", "AMQP_QUEUE": "queue",
            "AMQP_AK": "ak", "AMQP_CODE": "code",
        }, clear=True):
            for service in ("", "six_axis"):
                with self.subTest(service=service), patch.dict(os.environ, {"AMQP_SERVICE_ID": service}):
                    self.assertEqual(AmqpConfig.from_env().service_id, "six_axis")
            with patch.dict(os.environ, {"AMQP_SERVICE_ID": "IMU"}), self.assertRaisesRegex(ValueError, "six_axis"):
                AmqpConfig.from_env()

    def test_missing_and_conflicting_credentials(self):
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "host", "AMQP_QUEUE": "queue", "AMQP_AK": "ak",
        }, clear=True), self.assertRaisesRegex(ValueError, "AMQP_CODE"):
            AmqpConfig.from_env()
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "host", "AMQP_QUEUE": "queue", "AMQP_AK": "ak",
            "AMQP_ACCESS_KEY": "different", "AMQP_CODE": "code",
        }, clear=True), self.assertRaisesRegex(ValueError, "配置冲突"):
            AmqpConfig.from_env()

    def test_tls_verifies_hostname_and_reconnect_refreshes_timestamp(self):
        with patch("src.telemetry.amqp.SSLDomain") as domain_type:
            tls_domain(config())
            domain_type.return_value.set_peer_authentication.assert_called_once_with(
                domain_type.VERIFY_PEER_NAME)
        domain = tls_domain(config())
        self.assertIsInstance(domain, SSLDomain)
        with patch("src.telemetry.amqp.BlockingConnection") as factory, \
                patch("src.telemetry.amqp.connection_url", return_value="amqps://192.0.2.1:5671"), \
                patch("src.telemetry.amqp.time.time", side_effect=[1000, 2000]):
            connect(config(), domain)
            connect(config(), domain)
        first, second = factory.call_args_list
        self.assertIn("timestamp=1000000", first.kwargs["user"])
        self.assertIn("timestamp=2000000", second.kwargs["user"])
        self.assertFalse(first.kwargs["reconnect"])
        self.assertEqual(first.kwargs["sni"], "iot.example.test")
        self.assertEqual(first.kwargs["virtual_host"], "default")
        self.assertEqual(first.args[0], "amqps://192.0.2.1:5671")

    def test_ip_family_resolution_and_retry_rotation(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 5671))
                   for ip in ("192.0.2.1", "192.0.2.2")]
        with patch("src.telemetry.amqp.socket.getaddrinfo", return_value=answers) as resolve:
            self.assertEqual(connection_url(config(), 0), "amqps://192.0.2.1:5671")
            self.assertEqual(connection_url(config(), 1), "amqps://192.0.2.2:5671")
            self.assertEqual(connection_url(config(), 2), "amqps://192.0.2.1:5671")
        resolve.assert_called_with("iot.example.test", 5671, socket.AF_INET, socket.SOCK_STREAM)
        with patch("src.telemetry.amqp.socket.getaddrinfo") as resolve:
            self.assertEqual(connection_url(replace(config(), ip_version="auto")), config().url)
            resolve.assert_not_called()
        with patch("src.telemetry.amqp.socket.getaddrinfo", return_value=[
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 5671, 0, 0)),
        ]):
            self.assertEqual(connection_url(replace(config(), ip_version="6")),
                             "amqps://[2001:db8::1]:5671")

    def test_check_does_not_consume_and_closes_on_failure(self):
        connection = Mock()
        with patch("src.telemetry.amqp.connect", return_value=connection):
            check_connection(config())
        self.assertEqual(connection.create_receiver.call_args.args, ("sensor_data_queue",))
        self.assertEqual(connection.create_receiver.call_args.kwargs["credit"], 0)
        self.assertIsInstance(connection.create_receiver.call_args.kwargs["options"], AtLeastOnce)
        connection.create_receiver.return_value.receive.assert_not_called()
        connection.close.assert_called_once()
        connection.reset_mock()
        connection.create_receiver.side_effect = RuntimeError("queue unavailable")
        with patch("src.telemetry.amqp.connect", return_value=connection), self.assertRaises(RuntimeError):
            check_connection(config())
        connection.close.assert_called_once()


class NotificationTests(unittest.TestCase):
    def test_property_formats_and_cloud_identity(self):
        body = payload()
        properties = body["notify_data"]["body"]["services"][0]["properties"]
        properties.update(cloud_device_id="spoofed", user_id=999, roll=12)
        for encoded in (body, json.dumps(body), json.dumps(body).encode()):
            report = parse_notification(encoded).report
            self.assertEqual(report.cloud_device_id, "6ab50bab4e7fed229661d66e_24D7EBEF0B50")
            self.assertEqual(report.cloud_product_id, "6ab50bab4e7fed229661d66e")
            self.assertEqual(report.values["accel_z"], 4.48)
            self.assertEqual(report.values["roll"], 12)
            self.assertEqual(report.values["yaw"], -48.14)
            self.assertEqual(report.values["step_increment"], 0)
            self.assertEqual(report.values["is_sedentary"], 0)
            self.assertEqual(report.values["is_fall"], 0)
            self.assertEqual(report.reported_at.isoformat(), "2026-10-12T03:40:18+00:00")
            self.assertEqual(report.cloud_event_at.isoformat(), "2026-10-12T03:40:18.976000+00:00")

    def test_legacy_message_and_imu_service_are_not_ingested(self):
        body = payload()
        body["resource"] = "device.message"
        self.assertIsNone(parse_notification(body))
        body = payload()
        body["notify_data"]["body"]["services"][0]["service_id"] = "IMU"
        self.assertIsNone(parse_notification(body))

    def test_invalid_data_and_old_envelope(self):
        for value in ("not-json", [], b"\xff", {"cloud_device_id": "old", "data": {}},
                      " " * (1024 * 1024 + 1)):
            with self.subTest(kind=type(value)), self.assertRaises(InvalidNotification):
                parse_notification(value)
        for value in (True, None, "1.2", float("nan"), float("inf")):
            body = payload()
            body["notify_data"]["body"]["services"][0]["properties"]["accel_x"] = value
            with self.subTest(value=value), self.assertRaises(InvalidNotification):
                parse_notification(body)

    def test_every_real_property_is_required(self):
        for field in PROPERTY_FIELDS:
            body = payload()
            del body["notify_data"]["body"]["services"][0]["properties"][field]
            with self.subTest(field=field), self.assertRaises(InvalidNotification):
                parse_notification(body)

    def test_activity_values_preserve_integer_types_and_reject_invalid_values(self):
        for field, invalid in (
            ("step_increment", (-1, 1.5, "2", True, None)),
            ("is_sedentary", (-1, 2, 0.0, "0", False, None)),
            ("is_fall", (-1, 2, 1.0, "1", True, None)),
        ):
            for value in invalid:
                body = payload()
                body["notify_data"]["body"]["services"][0]["properties"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(InvalidNotification):
                    parse_notification(body)
        body = payload()
        body["notify_data"]["body"]["services"][0]["properties"].update(
            step_increment=7, is_sedentary=1, is_fall=1,
        )
        values = parse_notification(body).report.values
        for field, expected in (("step_increment", 7), ("is_sedentary", 1), ("is_fall", 1)):
            self.assertEqual(values[field], expected)
            self.assertIs(type(values[field]), int)

    def test_real_time_fields_and_identity_are_required_and_validated(self):
        for field in ("device_id", "product_id"):
            for value in (None, "", 123):
                body = payload()
                body["notify_data"]["header"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(InvalidNotification):
                    parse_notification(body)
        for field, invalid in (
            ("event_time", (None, "20260230T034018Z", "20261012T034018")),
            ("event_time_ms", (None, "2026-02-30T03:40:18.976Z", "2026-10-12T03:40:18.976")),
        ):
            for value in invalid:
                body = payload()
                body[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(InvalidNotification):
                    parse_notification(body)
        body = payload()
        body["notify_data"]["body"]["services"][0]["event_time"] = "not-a-time"
        with self.assertRaises(InvalidNotification):
            parse_notification(body)

    def test_service_selection_prevents_ambiguous_samples(self):
        body = payload()
        services = body["notify_data"]["body"]["services"]
        services.append(deepcopy(services[0]))
        services[1]["service_id"] = "other"
        self.assertIsNotNone(parse_notification(body))
        self.assertIsNotNone(parse_notification(body, service_id="six_axis"))
        services[1]["service_id"] = "six_axis"
        with self.assertRaises(InvalidNotification):
            parse_notification(body)
        with self.assertRaises(ValueError):
            parse_notification(body, service_id="IMU")
        self.assertIsNone(parse_notification({"resource": "device", "event": "update"}))


class PreviewTests(unittest.TestCase):
    def test_original_body_released_without_business_validation(self):
        connection = Mock()
        receiver = connection.create_receiver.return_value
        receiver.receive.return_value = SimpleNamespace(body="not-six-axis-json")
        connected = Mock()
        with patch("src.telemetry.amqp.connect", return_value=connection), \
                patch.object(NotificationProcessor, "process") as process:
            self.assertEqual(peek_message(config(), timeout=60, on_connected=connected),
                             "not-six-axis-json")
        connected.assert_called_once()
        self.assertEqual(connection.create_receiver.call_args.kwargs["credit"], 0)
        receiver.receive.assert_called_once_with(timeout=60)
        receiver.release.assert_called_once_with(delivered=False)
        receiver.accept.assert_not_called()
        receiver.reject.assert_not_called()
        process.assert_not_called()
        connection.close.assert_called_once()

    def test_idle_timeout_distinct_from_connection_timeout_and_closes(self):
        connection = Mock()
        receiver = connection.create_receiver.return_value
        receiver.receive.side_effect = Timeout("idle")
        with patch("src.telemetry.amqp.connect", return_value=connection), \
                self.assertRaises(NoMessageReceived):
            peek_message(config())
        receiver.release.assert_not_called()
        connection.close.assert_called_once()
        with patch("src.telemetry.amqp.connect", side_effect=Timeout("connect")), \
                self.assertRaises(Timeout):
            peek_message(config())

    def test_cli_needs_only_amqp_and_never_creates_web_or_database(self):
        import run
        output = io.StringIO()
        with patch.dict(os.environ, {
            "AMQP_ACCESS": "iot.example.test", "AMQP_QUEUE": "queue",
            "AMQP_AK": "key-secret", "AMQP_CODE": "code-secret",
        }, clear=True), patch.object(run, "load_env"), \
                patch("sys.argv", ["run.py", "--peek-amqp", "--once", "--wait-seconds", "60"]), \
                patch("sys.stdout", output), patch("src.auth_api.create_app") as create, \
                patch("src.telemetry.amqp.peek_message", return_value=json.dumps(payload()).encode()) as peek:
            self.assertEqual(run.main(), 0)
        create.assert_not_called()
        self.assertEqual(peek.call_args.kwargs["timeout"], 60)
        self.assertIn('"device_id": "6ab50bab4e7fed229661d66e_24D7EBEF0B50"', output.getvalue())
        self.assertNotIn("code-secret", output.getvalue())

    def test_cli_timeout_reports_no_message_instead_of_connection_failure(self):
        import run
        output = io.StringIO()
        with patch.object(run, "load_env"), \
                patch("sys.argv", ["run.py", "--peek-amqp", "--once"]), \
                patch("sys.stderr", output), \
                patch("src.telemetry.amqp_config.AmqpConfig.from_env", return_value=config()), \
                patch("src.telemetry.amqp.peek_message", side_effect=NoMessageReceived()):
            self.assertEqual(run.main(), 2)
        self.assertIn("等待期间没有收到报文", output.getvalue())


class ContinuousPreviewTests(unittest.TestCase):
    def test_displays_multiple_messages_before_ack_and_continues_after_idle(self):
        connection = Mock()
        receiver = connection.create_receiver.return_value
        receiver.receive.side_effect = [
            SimpleNamespace(body="first"), Timeout("idle"),
            SimpleNamespace(body={"unexpected": "second"}), KeyboardInterrupt(),
        ]
        order = []
        show = Mock(side_effect=lambda body: order.append(("display", body)))
        receiver.accept.side_effect = lambda: order.append(("ack", None))
        idle, connected = Mock(), Mock()
        with patch("src.telemetry.amqp.connect", return_value=connection), \
                patch.object(NotificationProcessor, "process") as process, \
                self.assertRaises(KeyboardInterrupt):
            watch_messages(config(), show, timeout=60, on_idle=idle, on_connected=connected)
        self.assertEqual(order, [
            ("display", "first"), ("ack", None),
            ("display", {"unexpected": "second"}), ("ack", None),
        ])
        connected.assert_called_once()
        idle.assert_called_once()
        self.assertEqual(connection.create_receiver.call_args.kwargs["credit"], 0)
        self.assertEqual(receiver.receive.call_count, 4)
        receiver.receive.assert_called_with(timeout=60)
        receiver.release.assert_not_called()
        process.assert_not_called()
        connection.close.assert_called_once()

    def test_disconnect_closes_then_reconnects_without_exposing_exception_text(self):
        first, second = Mock(), Mock()
        first.create_receiver.return_value.receive.side_effect = RuntimeError("code-secret")
        second.create_receiver.return_value.receive.side_effect = [
            SimpleNamespace(body="new-message"), KeyboardInterrupt(),
        ]
        retry, connected, show = Mock(), Mock(), Mock()
        with patch("src.telemetry.amqp.connect", side_effect=[first, second]) as connect_mock, \
                patch("src.telemetry.amqp.time.sleep") as sleep, \
                self.assertRaises(KeyboardInterrupt):
            watch_messages(config(), show, on_reconnecting=retry, on_connected=connected)
        first.close.assert_called_once()
        second.close.assert_called_once()
        sleep.assert_called_once_with(1)
        retry.assert_called_once_with("RuntimeError", 1)
        self.assertEqual(connected.call_count, 2)
        self.assertEqual([call.kwargs["attempt"] for call in connect_mock.call_args_list], [0, 1])
        show.assert_called_once_with("new-message")
        second.create_receiver.return_value.accept.assert_called_once()

    def test_connection_failures_use_bounded_backoff(self):
        retry = Mock()
        with patch("src.telemetry.amqp.connect", side_effect=RuntimeError("code-secret")), \
                patch("src.telemetry.amqp.time.sleep", side_effect=[None] * 6 + [KeyboardInterrupt()]), \
                self.assertRaises(KeyboardInterrupt):
            watch_messages(config(), Mock(), on_reconnecting=retry)
        self.assertEqual([call.args for call in retry.call_args_list], [
            ("RuntimeError", delay) for delay in (1, 2, 4, 8, 16, 30, 30)
        ])

    def test_display_failure_releases_message_and_does_not_ack_or_retry(self):
        connection = Mock()
        receiver = connection.create_receiver.return_value
        receiver.receive.return_value = SimpleNamespace(body="original")
        with patch("src.telemetry.amqp.connect", return_value=connection), \
                patch("src.telemetry.amqp.time.sleep") as sleep, \
                self.assertRaises(BrokenPipeError):
            watch_messages(config(), Mock(side_effect=BrokenPipeError()))
        receiver.accept.assert_not_called()
        receiver.release.assert_called_once_with(delivered=False)
        connection.close.assert_called_once()
        sleep.assert_not_called()

    def test_default_cli_streams_without_web_database_and_stops_cleanly(self):
        import run
        output = io.StringIO()
        def stream(_config, on_message, **kwargs):
            kwargs["on_connected"]()
            on_message(json.dumps(payload()).encode())
            kwargs["on_idle"]()
            on_message("not-six-axis-json")
            raise KeyboardInterrupt()

        with patch.dict(os.environ, {
            "AMQP_ACCESS": "iot.example.test", "AMQP_QUEUE": "queue",
            "AMQP_AK": "key-secret", "AMQP_CODE": "code-secret",
        }, clear=True), patch.object(run, "load_env"), \
                patch("sys.argv", ["run.py", "--peek-amqp", "--wait-seconds", "60"]), \
                patch("sys.stdout", output), patch("src.auth_api.create_app") as create, \
                patch("src.telemetry.amqp.peek_message") as peek, \
                patch("src.telemetry.amqp.watch_messages", side_effect=stream) as watch:
            self.assertEqual(run.main(), 0)
        create.assert_not_called()
        peek.assert_not_called()
        self.assertEqual(watch.call_args.kwargs["timeout"], 60)
        text = output.getvalue()
        self.assertIn("第 1 条云端原始报文", text)
        self.assertIn("第 2 条云端原始报文", text)
        self.assertIn("最近 60 秒未收到报文，继续等待", text)
        self.assertIn("共显示 2 条报文", text)
        self.assertNotIn("code-secret", text)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.receive.return_value = {"accepted": True}
        self.consumer = AmqpConsumer(config(), self.service)
        self.receiver = Mock()
        self.message = SimpleNamespace(body=payload(), id="message-id")

    def test_ack_after_processing_and_duplicate_suppression(self):
        order = []
        self.service.receive.side_effect = lambda report, **kwargs: order.append("store") or {"accepted": True}
        self.receiver.accept.side_effect = lambda: order.append("ack")
        self.consumer.handle_message(self.receiver, self.message)
        self.consumer.handle_message(self.receiver, self.message)
        self.assertEqual(order, ["store", "ack", "ack"])

    def test_invalid_rejected_but_transient_failure_released(self):
        self.message.body = "bad"
        self.consumer.handle_message(self.receiver, self.message)
        self.receiver.reject.assert_called_once()
        self.service.receive.assert_not_called()
        self.receiver.accept.assert_not_called()
        self.message.body = payload()
        self.service.receive.side_effect = ServiceError("buffer full", 503)
        with self.assertRaises(ServiceError):
            self.consumer.handle_message(self.receiver, self.message)
        self.receiver.release.assert_called_once_with(delivered=False)
        self.service.receive.side_effect = None
        self.consumer.handle_message(self.receiver, self.message)
        self.assertEqual(self.service.receive.call_count, 2)
        self.receiver.accept.assert_called_once()

    def test_unbound_and_unrelated_events_acknowledged(self):
        self.service.receive.return_value = {"accepted": False}
        self.consumer.handle_message(self.receiver, self.message)
        self.message.body = {"resource": "device", "event": "update"}
        self.consumer.handle_message(self.receiver, self.message)
        self.assertEqual(self.receiver.accept.call_count, 2)
        self.assertEqual(self.service.receive.call_count, 1)

    def test_dedup_expiry_capacity_and_changed_values(self):
        processor = NotificationProcessor(self.service, dedup_limit=1, dedup_seconds=5)
        body = payload()
        with patch("src.telemetry.amqp.time.monotonic", return_value=0):
            processor.process(body)
            self.assertEqual(processor.process(body), "duplicate")
        with patch("src.telemetry.amqp.time.monotonic", return_value=6):
            self.assertEqual(processor.process(body), "accepted")
            body["notify_data"]["body"]["services"][0]["properties"]["accel_x"] = 99
            self.assertEqual(processor.process(body), "accepted")
            self.assertEqual(len(processor._processed), 1)

    def test_activity_or_time_changes_are_not_mistaken_for_identical_samples(self):
        processor = NotificationProcessor(self.service)
        body = payload()
        self.assertEqual(processor.process(body), "accepted")
        self.assertEqual(processor.process(body), "duplicate")
        properties = body["notify_data"]["body"]["services"][0]["properties"]
        for field, value in (("step_increment", 3), ("is_sedentary", 1), ("is_fall", 1)):
            properties[field] = value
            self.assertEqual(processor.process(body), "accepted")
            self.assertEqual(processor.process(body), "duplicate")
        body["event_time_ms"] = "2026-10-12T03:40:18.977Z"
        self.assertEqual(processor.process(body), "accepted")
        body["notify_data"]["body"]["services"][0]["event_time"] = "20261012T034019Z"
        self.assertEqual(processor.process(body), "accepted")
        self.assertEqual(self.service.receive.call_count, 6)

    def test_legacy_message_is_acknowledged_without_processing(self):
        self.message.body = {"resource": "device.message", "event": "report"}
        self.consumer.handle_message(self.receiver, self.message)
        self.receiver.accept.assert_called_once()
        self.service.receive.assert_not_called()
        self.receiver.reject.assert_not_called()

    def test_disconnect_retries_without_logging_credentials(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = lambda delay: setattr(stop.is_set, "return_value", True)
        self.consumer._stop = stop
        with patch("src.telemetry.amqp.connect", side_effect=RuntimeError("code-secret")), \
                self.assertLogs("src.telemetry.amqp", level="INFO") as logs:
            self.consumer.run()
        stop.wait.assert_called_once_with(1)
        self.assertNotIn("code-secret", " ".join(logs.output))

    def test_receive_timeout_and_shutdown_close_connection(self):
        connection = Mock()
        receiver = connection.create_receiver.return_value
        entered = threading.Event()
        def stop_on_receive(**kwargs):
            entered.set()
            self.consumer._stop.set()
            raise Timeout("idle")
        receiver.receive.side_effect = stop_on_receive
        with patch("src.telemetry.amqp.connect", return_value=connection):
            self.consumer.start()
            self.assertTrue(entered.wait(2))
            self.consumer.stop()
        self.assertFalse(self.consumer._thread.is_alive())
        connection.close.assert_called_once()


class FrontendIntegrationTests(unittest.TestCase):
    def test_amqp_data_available_to_owner_and_http_ingestion_removed(self):
        database = Mock()
        database.get_device_identity_by_cloud_id.return_value = {"id": 7, "user_id": 1}
        database.get_device_for_user.side_effect = lambda user, device: (
            {"id": 7, "user_id": 1, "last_seen_at": None} if (user, device) == (1, 7) else None
        )
        database.get_user_session.side_effect = lambda user: {"id": user, "session_version": 1}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "APP_SECRET_KEY": "test-only-secret", "LOG_DIR": directory,
            "DAILY_STEPS_STATE_PATH": str(Path(directory) / "steps.sqlite3"),
        }):
            app = create_app(database, device_verifier=lambda *_: True)
            self.addCleanup(app.extensions["step_history"].close)
            client = app.test_client()
            processor = NotificationProcessor(app.extensions["telemetry_service"])
            self.assertEqual(processor.process(payload()), "accepted")
            self.assertEqual(client.get("/api/devices/7/telemetry/latest").status_code, 401)
            with client.session_transaction() as session:
                session.update(user_id=1, session_version=1)
            latest = client.get("/api/devices/7/telemetry/latest")
            self.assertEqual(latest.status_code, 200)
            self.assertEqual(latest.json["data"]["accel_z"], 4.48)
            self.assertEqual(client.get("/api/devices/7/telemetry/summary").json["data"]["count"], 1)
            self.assertEqual(client.get("/api/devices/7/telemetry/status").json["data"]["status"], "online")
            self.assertEqual(client.post("/api/telemetry/report", json=payload()).status_code, 404)
            with client.session_transaction() as session:
                session.update(user_id=2, session_version=1)
            self.assertEqual(client.get("/api/devices/7/telemetry/latest").status_code, 404)
            self.assertNotIn("amqp_consumer", app.extensions)


if __name__ == "__main__":
    unittest.main()
