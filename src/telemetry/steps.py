"""Durable daily step accumulation and scheduled MySQL snapshots."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from zoneinfo import ZoneInfo

from src.telemetry.cloud import InvalidNotification
from src.telemetry.alerts import AlertJournal

logger = logging.getLogger(__name__)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_STEP_COUNT = (1 << 63) - 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DailyStepHistory:
    """Commit increments locally before AMQP ACK; export totals at 22:00.

    A user/day has one absolute MySQL total. Export revisions make an unknown
    MySQL commit safe to retry and preserve increments arriving during export.
    Finished days are also reconciled after midnight, restart or late delivery.
    This is a working journal, not the public history database; keep the file
    and use one backend process, as with the existing AMQP consumer.
    """

    def __init__(self, database, state_path, *, save_time="22:00", clock=None):
        if not isinstance(save_time, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", save_time):
            raise ValueError("DAILY_STEPS_SAVE_TIME must use HH:MM")
        self.database = database
        self.save_time = time.fromisoformat(save_time)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._export_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._connection = sqlite3.connect(self.state_path, timeout=5, check_same_thread=False)
        try:
            self.state_path.chmod(0o600)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS step_events (
                    event_key TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS day_totals (
                    user_id INTEGER NOT NULL,
                    record_date TEXT NOT NULL,
                    steps INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    exported_revision INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, record_date)
                );
            """)
            self.alerts = AlertJournal(self._connection)
        except Exception:
            self._connection.close()
            raise

    @classmethod
    def from_env(cls, database):
        value = os.getenv("DAILY_STEPS_STATE_PATH", "data/steps.sqlite3")
        if not value.strip():
            raise ValueError("DAILY_STEPS_STATE_PATH must not be empty")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return cls(database, path, save_time=os.getenv("DAILY_STEPS_SAVE_TIME", "22:00"))

    def record(self, user_id, report, *, event_key=None):
        """Atomically deduplicate a positive increment and update its owner's day."""
        if type(user_id) is not int or not 1 <= user_id <= MAX_STEP_COUNT:
            raise ValueError("step owner must be a positive integer")
        increment = report.values["step_increment"]
        if type(increment) is not int or not 0 <= increment <= MAX_STEP_COUNT:
            raise InvalidNotification("step_increment 超出支持的整数范围")
        try:
            record_date = report.reported_at.astimezone(BUSINESS_TIMEZONE).date()
        except OverflowError as exc:
            raise InvalidNotification("设备上报日期超出历史表支持的范围") from exc
        if record_date.year < 1000:
            raise InvalidNotification("设备上报日期超出历史表支持的范围")
        day = record_date.isoformat()
        with self._lock, self._connection:
            if increment == 0:
                # Persist evidence for a zero-step day without retaining every
                # high-frequency zero-step notification or changing its total.
                result = self._connection.execute(
                    "INSERT INTO day_totals (user_id, record_date, steps) VALUES (?, ?, 0) "
                    "ON CONFLICT (user_id, record_date) DO NOTHING", (user_id, day),
                )
                return result.rowcount > 0
            # Without a stable message ID, two equal increments may be two
            # legitimate reports. Do not deduplicate them by value or timestamp.
            if event_key is None:
                logger.warning("event=steps.message_without_identity user_id=%s", user_id)
                event_key = uuid.uuid4().hex
            inserted = self._connection.execute(
                "INSERT INTO step_events (event_key) VALUES (?) ON CONFLICT DO NOTHING",
                (event_key,),
            )
            if inserted.rowcount == 0:
                return False
            previous = self._connection.execute(
                "SELECT steps FROM day_totals WHERE user_id = ? AND record_date = ?",
                (user_id, day),
            ).fetchone()
            if increment + (previous["steps"] if previous else 0) > MAX_STEP_COUNT:
                raise InvalidNotification("当天步数累计超出支持的整数范围")
            self._connection.execute(
                "INSERT INTO day_totals (user_id, record_date, steps) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id, record_date) DO UPDATE SET "
                "steps = steps + excluded.steps, revision = revision + 1",
                (user_id, day, increment),
            )
            return True

    def flush_due(self, now=None):
        """Save the day's first scheduled snapshot and any dirty finished days."""
        local = (now or self.clock()).astimezone(BUSINESS_TIMEZONE)
        today = local.date().isoformat()
        due_today = int(local.time() >= self.save_time)
        with self._export_lock:
            with self._lock:
                pending = self._connection.execute(
                    "SELECT user_id, record_date, steps, revision FROM day_totals "
                    "WHERE revision > exported_revision AND "
                    "(record_date < ? OR (record_date = ? AND ? = 1 AND exported_revision = 0)) "
                    "ORDER BY record_date, user_id LIMIT 200", (today, today, due_today),
                ).fetchall()
            if not pending:
                return 0
            records = [{"user_id": row["user_id"], "steps": row["steps"],
                        "record_date": date.fromisoformat(row["record_date"])} for row in pending]
            self.database.save_daily_steps(records)
            with self._lock, self._connection:
                self._connection.executemany(
                    "UPDATE day_totals SET exported_revision = ? "
                    "WHERE user_id = ? AND record_date = ?",
                    [(row["revision"], row["user_id"], row["record_date"]) for row in pending],
                )
            logger.info("event=steps.daily_saved records=%s timezone=Asia/Shanghai", len(records))
            return len(records)

    def record_alerts(self, user_id, device_id, report, received_at, *, event_key=None):
        with self._lock, self._connection:
            return self.alerts.observe(user_id, device_id, report, received_at, event_key)

    def read_alerts(self, user_id, device_ids, *, after=0, limit=100):
        with self._lock:
            return self.alerts.read(user_id, device_ids, after=after, limit=limit)

    def clear_alert_state(self, device_id):
        with self._lock, self._connection:
            self.alerts.clear_device(device_id)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("步数定时任务不能重复启动")
        self._thread = threading.Thread(target=self._run, name="iotda-daily-steps", daemon=True)
        self._thread.start()

    def _run(self):
        logger.info("event=steps.scheduler_started save_time=%s timezone=Asia/Shanghai", self.save_time)
        while not self._stop.is_set():
            try:
                self.flush_due()
            except Exception:
                logger.exception("event=steps.daily_save_failed")
            local = self.clock().astimezone(BUSINESS_TIMEZONE)
            scheduled = datetime.combine(local.date(), self.save_time, BUSINESS_TIMEZONE)
            if scheduled <= local:
                scheduled = datetime.combine(local.date() + timedelta(days=1), time(), BUSINESS_TIMEZONE)
            # Reconcile late reports/failures every 30s and wake at the exact
            # scheduled minute or midnight rather than sleeping a full day.
            self._stop.wait(max(0.05, min(30, (scheduled - local).total_seconds())))
        logger.info("event=steps.scheduler_stopped")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning("event=steps.scheduler_stop_timeout")

    def close(self):
        self.stop()
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            self._connection.close()
