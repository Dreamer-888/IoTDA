"""Durable rising-edge events in the existing local working journal."""

from datetime import datetime, timedelta, timezone
import uuid

from src.common.time import format_utc

ALERT_RETENTION_SECONDS = 7 * 24 * 60 * 60
FLAGS = (("is_fall", "fall"), ("is_sedentary", "sedentary"))


class AlertJournal:
    """Caller owns the SQLite transaction and lock; never stores raw reports."""

    def __init__(self, connection):
        self.connection = connection
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS alert_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_states (
                device_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                is_fall INTEGER NOT NULL,
                is_sedentary INTEGER NOT NULL,
                reported_at TEXT NOT NULL,
                cloud_event_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_deliveries (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS alert_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                source_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                device_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                UNIQUE(source_key, type)
            );
            CREATE INDEX IF NOT EXISTS idx_alert_events_owner
                ON alert_events(user_id, sequence);
        """)
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO alert_metadata VALUES ('store_id', ?)",
                (uuid.uuid4().hex,),
            )
        self.store_id = connection.execute(
            "SELECT value FROM alert_metadata WHERE key='store_id'"
        ).fetchone()[0]
        self._since_cleanup = 0

    def observe(self, user_id, device_id, report, received_at, event_key=None):
        source_key = event_key or uuid.uuid4().hex
        inserted = self.connection.execute(
            "INSERT OR IGNORE INTO alert_deliveries(source_key) VALUES (?)", (source_key,),
        )
        if not inserted.rowcount:
            return []
        self._since_cleanup += 1
        if self._since_cleanup >= 512:
            self._since_cleanup = 0
            self.connection.execute(
                "DELETE FROM alert_deliveries WHERE sequence < ("
                "SELECT sequence FROM alert_deliveries ORDER BY sequence DESC LIMIT 1 OFFSET 49999)",
            )
        previous = self.connection.execute(
            "SELECT * FROM alert_states WHERE device_id=?", (device_id,),
        ).fetchone()
        reported_at = format_utc(report.reported_at)
        cloud_event_at = format_utc(report.cloud_event_at)
        if previous and previous["user_id"] == user_id:
            if (reported_at, cloud_event_at) < (previous["reported_at"], previous["cloud_event_at"]):
                return []
        else:
            previous = None
        events = []
        for flag, kind in FLAGS:
            if report.values[flag] == 1 and (previous is None or previous[flag] == 0):
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO alert_events "
                    "(event_id, source_key, user_id, device_id, type, occurred_at, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, source_key, user_id, device_id, kind,
                     reported_at, format_utc(received_at)),
                )
                if cursor.rowcount:
                    row = self.connection.execute(
                        "SELECT * FROM alert_events WHERE sequence=?", (cursor.lastrowid,),
                    ).fetchone()
                    events.append(self.public_event(row))
        self.connection.execute(
            "INSERT INTO alert_states VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET user_id=excluded.user_id, "
            "is_fall=excluded.is_fall, is_sedentary=excluded.is_sedentary, "
            "reported_at=excluded.reported_at, cloud_event_at=excluded.cloud_event_at",
            (device_id, user_id, report.values["is_fall"], report.values["is_sedentary"],
             reported_at, cloud_event_at),
        )
        if events:
            cutoff = format_utc(received_at - timedelta(seconds=ALERT_RETENTION_SECONDS))
            self.connection.execute("DELETE FROM alert_events WHERE received_at < ?", (cutoff,))
        return events

    @staticmethod
    def public_event(row):
        return {key: row[key] for key in (
            "sequence", "event_id", "device_id", "type", "occurred_at", "received_at",
        )}

    def read(self, user_id, device_ids, *, after=0, limit=100):
        cutoff = format_utc(datetime.now(timezone.utc) - timedelta(seconds=ALERT_RETENTION_SECONDS))
        maximum = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM alert_events"
        ).fetchone()[0]
        after = min(after, maximum)
        rows = []
        if device_ids:
            placeholders = ",".join("?" for _ in device_ids)
            rows = self.connection.execute(
                f"SELECT * FROM alert_events WHERE user_id=? AND device_id IN ({placeholders}) "
                "AND sequence > ? AND received_at >= ? ORDER BY sequence LIMIT ?",
                (user_id, *device_ids, after, cutoff, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        events = [self.public_event(row) for row in rows[:limit]]
        return {"user_id": user_id, "store_id": self.store_id, "events": events,
                "cursor": events[-1]["sequence"] if has_more else maximum,
                "has_more": has_more, "retention_seconds": ALERT_RETENTION_SECONDS}

    def clear_device(self, device_id):
        self.connection.execute("DELETE FROM alert_states WHERE device_id=?", (device_id,))
