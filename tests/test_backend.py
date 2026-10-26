"""Frontend simulation and opt-in isolated MySQL integration tests.

Run: IOTDA_TEST_MYSQL=1 .venv/bin/python -B -m unittest discover -s tests -v
Only a randomly named temporary database is written and removed.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask
from itsdangerous import TimestampSigner

from src.auth_api import create_app
from src.DBcontrol import DBControl, DatabasePoolExhaustedError, DatabaseUnavailableError
from src.common.logging import configure_logging
from src.devices.binding import PreallocatedDeviceVerifier
from src.user.dto import UserDTO
from src.user.service import AuthService, UserServiceError

PASSWORD = "simulation-password-123"


class LoggingTests(unittest.TestCase):
    def test_exception_does_not_leak_literal_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LOG_DIR": directory, "LOG_LEVEL": "DEBUG",
        }):
            app = Flask("logging_simulation")
            configure_logging(app)
            try:
                raise ValueError("literal-password-should-not-appear")
            except ValueError:
                app.logger.exception("event=simulation.failure")
            content = Path(app.extensions["log_file"]).read_text()
            self.assertNotIn("literal-password-should-not-appear", content)
            self.assertIn("ValueError", content)
            self.assertIn("test_exception_does_not_leak_literal_secret", content)

    def test_same_day_restarts_append_to_one_file_without_size_rotation(self):
        timestamp = datetime(2026, 10, 11, 6, tzinfo=timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LOG_DIR": directory, "LOG_LEVEL": "INFO", "LOG_MAX_BYTES": "512",
            "LOG_BACKUP_COUNT": "2",
        }), patch("src.common.logging.time.time", return_value=timestamp):
            app = Flask("logging_simulation")
            with patch("src.common.logging.os.getpid", return_value=1001):
                configure_logging(app)
                app.logger.info("event=first_run")
            with patch("src.common.logging.os.getpid", return_value=2002):
                configure_logging(app)
                app.logger.info("event=second_run")
            logger = logging.getLogger("src")
            self.assertEqual(sum(getattr(h, "_iotda_owned", False) for h in logger.handlers), 2)
            app.logger.info("event=large_record padding=%s", "x" * 1000)
            path = Path(app.extensions["log_file"])
            self.assertEqual(path.name, "app-2026-10-11.log")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertGreater(path.stat().st_size, 512)
            content = path.read_text(encoding="utf-8")
            self.assertEqual(content.count("event=first_run"), 1)
            self.assertEqual(content.count("event=second_run"), 1)
            self.assertIn("pid=1001", content)
            self.assertIn("pid=2002", content)

    def test_running_process_switches_files_at_beijing_midnight(self):
        before_midnight = datetime(2026, 10, 11, 15, 59, 59, tzinfo=timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "LOG_DIR": directory, "LOG_LEVEL": "INFO",
        }), patch("src.common.logging.time.time", return_value=before_midnight) as clock:
            app = Flask("logging_simulation")
            configure_logging(app)
            app.logger.info("event=before_midnight")
            clock.return_value = before_midnight + 2
            app.logger.info("event=after_midnight")
            first = Path(directory) / "app-2026-10-11.log"
            second = Path(directory) / "app-2026-10-12.log"
            self.assertEqual(set(Path(directory).iterdir()), {first, second})
            first_content = first.read_text(encoding="utf-8")
            second_content = second.read_text(encoding="utf-8")
            self.assertIn("event=before_midnight", first_content)
            self.assertNotIn("event=after_midnight", first_content)
            self.assertIn("event=after_midnight", second_content)
            self.assertNotIn("event=before_midnight", second_content)
            # Restart on the next day must append to the file already opened at midnight.
            configure_logging(app)
            app.logger.info("event=next_day_restart")
            self.assertIn("event=next_day_restart", second.read_text(encoding="utf-8"))
            self.assertIn("event=after_midnight", second.read_text(encoding="utf-8"))


@unittest.skipUnless(os.getenv("IOTDA_TEST_MYSQL") == "1", "requires opt-in local MySQL")
class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mysql.connector
        cls.directory = tempfile.TemporaryDirectory()
        cls.environment = patch.dict(os.environ, {
            "APP_SECRET_KEY": secrets.token_hex(32), "LOG_DIR": cls.directory.name,
            "LOG_LEVEL": "DEBUG",
            "FRONTEND_ORIGIN": "http://localhost:5173",
            "SESSION_COOKIE_SECURE": "false", "SESSION_COOKIE_SAMESITE": "Lax",
            "DAILY_STEPS_STATE_PATH": str(Path(cls.directory.name) / "steps.sqlite3"),
        })
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        cls.addClassCleanup(cls.directory.cleanup)
        # Some execution environments adjust wall time during a short run.
        # Keep cookie signing time monotonic without changing backend validation.
        signing_epoch = time.time()
        signing_started = time.monotonic()
        cls.signing_clock = patch.object(
            TimestampSigner, "get_timestamp",
            lambda self: int(signing_epoch + time.monotonic() - signing_started),
        )
        cls.signing_clock.start()
        cls.addClassCleanup(cls.signing_clock.stop)
        cls.admin = mysql.connector.connect(
            user="root", unix_socket="/var/run/mysqld/mysqld.sock", autocommit=True
        )
        cls.addClassCleanup(cls.admin.close)
        cls.schema = "iotda_simulation_" + secrets.token_hex(8)
        cls.original = cls.fingerprint()
        with cls.admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{cls.schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        cls.addClassCleanup(cls.remove_schema)
        with cls.admin.cursor() as cursor:
            cursor.execute(f"USE `{cls.schema}`")
            for statement in Path("mysql_schema.sql").read_text().split(";"):
                start = statement.find("CREATE TABLE IF NOT EXISTS")
                if start >= 0:
                    cursor.execute(statement[start:])
        cls.db = DBControl(user="root", unix_socket="/var/run/mysqld/mysqld.sock",
                           database=cls.schema, pool_size=3)
        cls.verifier = PreallocatedDeviceVerifier({})
        cls.app = create_app(cls.db, device_verifier=cls.verifier)
        cls.addClassCleanup(cls.app.extensions["step_history"].close)

    @classmethod
    def fingerprint(cls):
        with cls.admin.cursor() as cursor:
            rows = []
            for table in ("users", "devices"):
                cursor.execute(f"SELECT * FROM iot_visualization.{table} ORDER BY id")
                rows.extend(cursor.fetchall())
            cursor.execute("SELECT TABLE_NAME, AUTO_INCREMENT FROM information_schema.tables "
                           "WHERE table_schema='iot_visualization' ORDER BY TABLE_NAME")
            rows.extend(cursor.fetchall())
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    @classmethod
    def remove_schema(cls):
        with cls.admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{cls.schema}`")
        if cls.original != cls.fingerprint():
            raise AssertionError("Existing project data or ID sequences changed")
        print("Temporary database removed; existing project data and ID sequences unchanged.")

    def setUp(self):
        with self.admin.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{self.schema}`.daily_steps")
            cursor.execute(f"DELETE FROM `{self.schema}`.devices")
            cursor.execute(f"DELETE FROM `{self.schema}`.users")
        self.verifier.allocations.clear()
        self.client = self.app.test_client()

    def call(self, method, path, status, *, client=None, **kwargs):
        result = (client or self.client).open(path, method=method, **kwargs)
        self.assertEqual(result.status_code, status, result.get_json())
        if status != 204:
            self.assertTrue(result.is_json)
            self.assertEqual(result.get_json()["code"], status)
        self.assertTrue(result.headers.get("X-Request-ID"))
        return result

    def register(self, username="alice", *, client=None, email=None):
        return self.call("POST", "/api/auth/register", 201, client=client,
                         json={"username": username, "password": PASSWORD, "email": email}).json["data"]

    def login(self, username="alice", *, client=None, password=PASSWORD):
        return self.call("POST", "/api/auth/login", 200, client=client,
                         json={"username": username, "password": password})

    def account(self):
        user = self.register()
        self.login()
        return user

    def test_daily_steps_mysql_upsert_is_idempotent_and_queries_are_user_scoped(self):
        first, second = self.register("step-first"), self.register("step-second")
        first_day, second_day = date(2026, 10, 12), date(2026, 10, 13)
        records = [
            {"user_id": first["id"], "steps": 10, "record_date": first_day},
            {"user_id": first["id"], "steps": 0, "record_date": second_day},
            {"user_id": second["id"], "steps": 99, "record_date": first_day},
        ]
        self.db.save_daily_steps(records)
        self.db.save_daily_steps(records)
        records[0]["steps"] = 13
        self.db.save_daily_steps(records[:1])
        rows = self.db.get_daily_steps(first["id"], first_day, second_day)
        self.assertEqual([(row["record_date"], row["steps"]) for row in rows],
                         [(first_day, 13), (second_day, 0)])
        with self.assertRaises(Exception):
            self.db.save_daily_steps([
                {"user_id": first["id"], "steps": 100, "record_date": first_day},
                {"user_id": 999999999, "steps": 1, "record_date": first_day},
            ])
        self.assertEqual(self.db.get_daily_steps(first["id"], first_day, first_day)[0]["steps"], 13)

    def bind(self, user_id):
        self.verifier.allocations["cloud-device-001"] = {
            "user_id": user_id, "cloud_product_id": "product-001"
        }
        return self.call("POST", "/api/devices", 201, json={
            "cloud_device_id": "cloud-device-001", "cloud_product_id": "product-001",
            "display_name": "六轴传感器", "user_id": 999999,
        }).json["data"]

    def test_registration_login_and_query(self):
        user = self.register(email=" Alice@Example.com ")
        self.assertEqual(user["email"], "alice@example.com")
        self.assertTrue(user["created_at"].endswith("+00:00"))
        self.assertNotIn("password_hash", user)
        self.assertNotIn("session_version", user)
        self.call("GET", "/api/auth/me", 401)
        result = self.login()
        cookie = result.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertTrue(result.json["data"]["authenticated"])
        self.call("GET", "/api/auth/me", 200)
        self.call("GET", f"/api/users/{user['id']}", 200)
        self.call("GET", f"/api/users/{user['id'] + 999}", 403)

    def test_duplicate_registration(self):
        self.register(email="a@example.com")
        for body in ({"username": "alice", "email": None},
                     {"username": "bob", "email": "A@EXAMPLE.COM"}):
            self.call("POST", "/api/auth/register", 409, json={**body, "password": PASSWORD})

    def test_bad_requests(self):
        for body in ([], None, {}, {"username": True, "password": PASSWORD},
                     {"username": "alice", "password": "short"},
                     {"username": "alice", "password": PASSWORD, "email": "invalid"}):
            with self.subTest(body=body):
                self.call("POST", "/api/auth/register", 400, json=body)
        self.call("POST", "/api/auth/register", 400, data="{", content_type="application/json")

    def test_wrong_credentials(self):
        self.register()
        for username, password in (("alice", "wrong"), ("missing", PASSWORD)):
            self.call("POST", "/api/auth/login", 401, json={"username": username, "password": password})
        self.call("POST", "/api/auth/login", 400, json={"username": "alice", "password": "x" * 129})

    def test_profile_changes(self):
        user = self.account()
        path = f"/api/users/{user['id']}"
        with patch.object(self.db, "get_user_by_id", wraps=self.db.get_user_by_id) as getter:
            self.call("PATCH", path, 200, json={"username": "alice_new", "email": "new@example.com"})
            self.assertEqual(getter.call_count, 1)
        self.call("PATCH", path, 200, json={"email": None})
        self.assertIsNone(self.call("GET", "/api/auth/me", 200).json["data"]["email"])
        self.call("PATCH", path, 400, json={})
        self.call("PATCH", path, 400, json={"password": "new-password-123"})
        self.call("PATCH", path, 401, json={"password": "new-password-123", "current_password": "wrong"})

    def test_password_invalidates_all_sessions(self):
        user = self.account()
        second = self.app.test_client()
        self.login(client=second)
        self.call("PATCH", f"/api/users/{user['id']}", 200,
                  json={"password": "new-password-123", "current_password": PASSWORD})
        for client in (self.client, second):
            self.call("GET", "/api/auth/me", 401, client=client)
            self.call("GET", "/api/devices", 401, client=client)
        self.call("POST", "/api/auth/login", 401, json={"username": "alice", "password": PASSWORD})
        self.login(password="new-password-123")
        self.assertEqual(self.db.get_user_session(user["id"])["session_version"], 2)

    def test_logout_and_deleted_account(self):
        user = self.account()
        self.call("POST", "/api/auth/logout", 200)
        self.call("GET", "/api/auth/me", 401)
        self.login()
        self.db.delete_user(user["id"])
        self.call("GET", "/api/devices", 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_invalid_signed_session_fields(self):
        for user_id, version in ((True, 1), ("1", 1), (1, None), (1, True), (-1, 1)):
            with self.client.session_transaction() as session:
                session["user_id"] = user_id
                session["session_version"] = version
            self.call("GET", "/api/auth/me", 401)

    def test_device_lifecycle(self):
        user = self.account()
        device = self.bind(user["id"])
        self.assertEqual(device["user_id"], user["id"])
        self.assertIsNone(device["hardware_model"])
        self.assertTrue(device["created_at"].endswith("+00:00"))
        self.assertEqual(len(self.call("GET", "/api/devices", 200).json["data"]), 1)
        path = f"/api/devices/{device['id']}"
        self.call("PATCH", path, 200, json={"display_name": "新名称"})
        result = self.call("PATCH", path, 200, json={"display_name": "新名称"})
        self.assertIn("未发生变化", result.json["data"])
        self.call("PATCH", path, 400, json={"display_name": " "})
        self.call("DELETE", path, 200)
        self.call("DELETE", path, 404)
        self.call("PATCH", path, 404, json={"display_name": "another"})
        self.assertEqual(self.call("GET", "/api/devices", 200).json["data"], [])

    def test_device_ownership_and_duplicate(self):
        user = self.account()
        device = self.bind(user["id"])
        payload = {"cloud_device_id": "cloud-device-001", "cloud_product_id": "product-001", "display_name": "test"}
        self.call("POST", "/api/devices", 409, json=payload)
        other = self.app.test_client()
        self.register("bob", client=other)
        self.login("bob", client=other)
        self.call("POST", "/api/devices", 403, client=other, json=payload)
        self.call("PATCH", f"/api/devices/{device['id']}", 404, client=other, json={"display_name": "stolen"})
        self.call("DELETE", f"/api/devices/{device['id']}", 404, client=other)
        self.assertEqual(self.call("GET", "/api/devices", 200, client=other).json["data"], [])

    def test_unallocated_and_bad_product(self):
        user = self.account()
        payload = {"cloud_device_id": "unknown", "cloud_product_id": "wrong", "display_name": "sensor"}
        self.call("POST", "/api/devices", 403, json=payload)
        self.verifier.allocations["unknown"] = {"user_id": user["id"], "cloud_product_id": "correct"}
        self.call("POST", "/api/devices", 403, json=payload)

    def test_device_auth_reads_minimal_fields(self):
        self.account()
        with patch.object(self.db, "get_user_by_id", side_effect=AssertionError("unnecessary profile query")):
            self.call("GET", "/api/devices", 200)

    def test_http_errors_and_cors(self):
        self.call("GET", "/missing", 404)
        result = self.call("GET", "/api/auth/login", 405)
        self.assertIn("POST", result.headers["Allow"])
        self.call("POST", "/api/auth/register", 413, data='{"x":"' + "x" * 20000 + '"}', content_type="application/json")
        origin = "http://localhost:5173"
        result = self.call("OPTIONS", "/api/devices", 204, headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        self.assertEqual(result.headers["Access-Control-Allow-Origin"], origin)
        self.assertEqual(result.headers["Access-Control-Allow-Credentials"], "true")
        result = self.call("GET", "/api/auth/me", 401, headers={"Origin": "http://untrusted.example"})
        self.assertNotIn("Access-Control-Allow-Origin", result.headers)

    def test_pool_exhaustion_and_recovery(self):
        self.account()
        held = [self.db._connect() for _ in range(3)]
        try:
            self.call("GET", "/api/auth/me", 503)
        finally:
            for connection in held:
                connection.close()
        self.call("GET", "/api/auth/me", 200)

    def test_database_fault_mapping(self):
        self.account()
        for error, expected in ((DatabaseUnavailableError("private-message"), 503),
                                (DatabasePoolExhaustedError("private-message"), 503),
                                (RuntimeError("private-message"), 500)):
            with patch.object(self.db, "get_user_by_id", side_effect=error):
                result = self.call("GET", "/api/auth/me", expected)
                self.assertNotIn("private-message", result.get_data(as_text=True))

    def test_concurrent_duplicate_registration(self):
        barrier = threading.Barrier(2)
        def register():
            client = self.app.test_client()
            barrier.wait(timeout=10)
            return client.post("/api/auth/register", json={"username": "racing", "password": PASSWORD}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: register(), range(2)))
        self.assertEqual(sorted(results), [201, 409])

    def test_concurrent_password_change(self):
        user = self.account()
        current = self.db.get_user_by_id(user["id"])
        barrier = threading.Barrier(2)
        def update(number):
            barrier.wait(timeout=10)
            try:
                return AuthService(self.db).update_user_info(
                    user["id"], UserDTO(password=f"new-password-{number}", current_password=PASSWORD),
                    current_user=current, expected_version=1,
                )
            except UserServiceError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, range(2)))
        self.assertCountEqual(results, [True, 409])
        self.assertEqual(self.db.get_user_session(user["id"])["session_version"], 2)

    def test_log_correlation_and_no_request_secrets(self):
        self.account()
        response = self.call("GET", "/api/devices", 200)
        content = Path(self.app.extensions["log_file"]).read_text()
        self.assertIn(response.headers["X-Request-ID"], content)
        self.assertIn("event=user.registered", content)
        self.assertIn("event=user.logged_in", content)
        self.assertNotIn(PASSWORD, content)


if __name__ == "__main__":
    unittest.main()
