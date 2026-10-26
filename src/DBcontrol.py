"""MySQL access layer for the IoT posture visualization application.

The module persists users, device ownership metadata and daily step totals.
High-frequency sensor samples remain in the application's in-memory buffer.

Install the MySQL driver with ``pip install mysql-connector-python`` before
using :class:`DBControl`.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator, Mapping

from src.common.config import integer_setting as _integer_setting

logger = logging.getLogger(__name__)

_USER_COLUMNS = "id, username, email, password_hash, session_version, created_at, updated_at"
_DEVICE_COLUMNS = (
    "id, user_id, cloud_device_id, cloud_product_id, display_name, "
    "hardware_model, sample_interval_ms, last_seen_at, created_at, updated_at"
)


class DatabaseUnavailableError(RuntimeError):
    """Raised for database transport failures or connection timeouts."""


def _is_connection_failure(error: Exception) -> bool:
    from mysql.connector import errors

    timeout_types = tuple(
        cls for name in ("ReadTimeoutError", "WriteTimeoutError")
        if (cls := getattr(errors, name, None)) is not None
    )
    if isinstance(error, timeout_types):
        return True
    if not isinstance(error, (errors.InterfaceError, errors.OperationalError)):
        return False
    return (
        getattr(error, "errno", None) in {None, -1, 2002, 2003, 2005, 2006, 2013, 2055, 4031}
        or str(getattr(error, "sqlstate", "")).startswith("08")
    )


class DatabasePoolExhaustedError(RuntimeError):
    """Raised when all pooled database connections are currently checked out."""


class DBControl:
    """Small, parameterized MySQL data-access layer.

    Pass connection settings as keyword arguments or a mapping. If omitted,
    ``from_env`` reads ``DB_HOST``, ``DB_PORT``, ``DB_USER``, ``DB_PASSWORD``,
    and ``DB_NAME`` from the environment. Set ``DB_SOCKET`` to use a local
    Unix socket instead of TCP. A bounded connection pool is initialized on
    the first database operation. Each operation borrows its own connection,
    so connections are not shared between request threads.

    The corresponding MySQL tables must already exist; this class does not
    create or migrate the schema.
    """

    def __init__(self, config: Mapping[str, Any] | None = None, **kwargs: Any):
        settings = dict(config or {})
        settings.update(kwargs)

        self._config = {
            "host": settings.get("host", "127.0.0.1"),
            "port": _integer_setting(settings.get("port", 3306), "DB_PORT", 1, 65535),
            "user": settings.get("user"),
            "password": settings.get("password"),
            "database": settings.get("database", settings.get("db")),
            "charset": settings.get("charset", "utf8mb4"),
            "connection_timeout": _integer_setting(
                settings.get("connection_timeout", 5), "DB_CONNECT_TIMEOUT", 1
            ),
        }
        self._pool_size = _integer_setting(settings.get("pool_size", 5), "DB_POOL_SIZE", 1, 32)
        self._pool_name = settings.get(
            "pool_name", f"iotda_{os.getpid()}_{id(self):x}"
        )
        self._pool = None
        self._pool_lock = threading.Lock()
        if settings.get("unix_socket"):
            self._config["unix_socket"] = settings["unix_socket"]
        if not self._config["user"]:
            raise ValueError("MySQL user is required")
        if self._config["database"] is None:
            raise ValueError("MySQL database is required")

    @classmethod
    def from_env(cls, prefix: str = "DB_") -> "DBControl":
        """Build a database client from environment variables."""
        return cls(
            host=os.getenv(f"{prefix}HOST", "127.0.0.1"),
            port=os.getenv(f"{prefix}PORT", "3306"),
            user=os.getenv(f"{prefix}USER"),
            password=os.getenv(f"{prefix}PASSWORD"),
            database=os.getenv(f"{prefix}NAME"),
            unix_socket=os.getenv(f"{prefix}SOCKET"),
            pool_size=os.getenv(f"{prefix}POOL_SIZE", "5"),
            connection_timeout=os.getenv(f"{prefix}CONNECT_TIMEOUT", "5"),
        )

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    try:
                        from mysql.connector.pooling import MySQLConnectionPool
                    except ImportError as exc:
                        raise RuntimeError(
                            "mysql-connector-python is required: "
                            "pip install mysql-connector-python"
                        ) from exc

                    self._pool = MySQLConnectionPool(
                        pool_name=self._pool_name,
                        pool_size=self._pool_size,
                        pool_reset_session=True,
                        **self._config,
                        autocommit=False,
                        time_zone="+00:00",
                    )
                    logger.debug("event=database.pool_initialized size=%s", self._pool_size)
        return self._pool

    def _connect(self):
        try:
            from mysql.connector.errors import PoolError
        except ImportError as exc:
            raise RuntimeError(
                "mysql-connector-python is required: "
                "pip install mysql-connector-python"
            ) from exc

        try:
            return self._get_pool().get_connection()
        except PoolError as exc:
            raise DatabasePoolExhaustedError(
                "all database connections are currently in use"
            ) from exc
        except Exception as exc:
            if _is_connection_failure(exc):
                raise DatabaseUnavailableError("database connection unavailable") from exc
            raise

    @contextmanager
    def _cursor(self, *, write: bool = False) -> Iterator[Any]:
        """Yield a dictionary cursor and close/commit/rollback the connection."""
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            yield cursor
            if write:
                connection.commit()
            else:
                connection.rollback()
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                logger.warning("Database rollback failed", exc_info=True)
            if _is_connection_failure(exc):
                raise DatabaseUnavailableError("database operation interrupted") from exc
            raise
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    logger.warning("Database cursor cleanup failed", exc_info=True)
            try:
                connection.close()
            except Exception:
                logger.warning("Database connection return failed", exc_info=True)

    # User operations -----------------------------------------------------

    @staticmethod
    def _insert_user(cursor, username, password_hash, email) -> int:
        cursor.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
            (username, email, password_hash),
        )
        return int(cursor.lastrowid)

    def create_user(
        self, username: str, password_hash: str, email: str | None = None
    ) -> int:
        """Insert an account and return its generated ID.

        Hash passwords in the application layer before calling this method.
        MySQL uniqueness errors for username/email are allowed to propagate.
        """
        with self._cursor(write=True) as cursor:
            return self._insert_user(cursor, username, password_hash, email)

    def create_user_and_get(
        self, username: str, password_hash: str, email: str | None = None
    ) -> dict[str, Any]:
        """Insert an account and read it back in the same transaction."""
        with self._cursor(write=True) as cursor:
            user_id = self._insert_user(cursor, username, password_hash, email)
            cursor.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",
                (user_id,),
            )
            record = cursor.fetchone()
            if record is None:
                raise RuntimeError("inserted user could not be read in transaction")
            return record

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",
                (user_id,),
            )
            return cursor.fetchone()

    def get_user_session(self, user_id: int) -> dict[str, Any] | None:
        """Read only the fields needed to validate a device API session."""
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT id, session_version FROM users WHERE id = %s", (user_id,)
            )
            return cursor.fetchone()

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Return the account row, including password_hash for login checks."""
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE username = %s",
                (username,),
            )
            return cursor.fetchone()

    def update_user(
        self,
        user_id: int,
        *,
        username: str | None = None,
        email: str | None = None,
        password_hash: str | None = None,
    ) -> bool:
        """Update supplied account fields; return whether a row was changed."""
        changes = {
            "username": username,
            "email": email,
            "password_hash": password_hash,
        }
        fields = [(name, value) for name, value in changes.items() if value is not None]
        if not fields:
            return False

        return self.update_user_fields(user_id, dict(fields))

    def update_user_fields(
        self, user_id: int, changes: Mapping[str, Any], *,
        expected_version: int | None = None,
        expected_password_hash: str | None = None,
    ) -> bool:
        """Update fields atomically, optionally checking the observed credentials.

        Password changes increment the session version in the same statement.
        A failed comparison leaves every supplied field unchanged.
        """
        allowed_fields = {"username", "email", "password_hash"}
        if not changes:
            return False
        if not set(changes).issubset(allowed_fields):
            raise ValueError("unsupported users field")

        assignments = ", ".join(f"{name} = %s" for name in changes)
        if "password_hash" in changes:
            assignments += ", session_version = session_version + 1"
        values = list(changes.values())
        values.append(user_id)
        condition = "id = %s"
        if expected_version is not None:
            condition += " AND session_version = %s"
            values.append(expected_version)
        if expected_password_hash is not None:
            condition += " AND BINARY password_hash = BINARY %s"
            values.append(expected_password_hash)
        with self._cursor(write=True) as cursor:
            cursor.execute(
                f"UPDATE users SET {assignments} WHERE {condition}", tuple(values)
            )
            return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        """Delete an account if its foreign-key constraints permit it."""
        with self._cursor(write=True) as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cursor.rowcount > 0

    # Device operations ---------------------------------------------------

    @staticmethod
    def _insert_device(
        cursor, user_id, cloud_device_id, cloud_product_id, display_name, hardware_model
    ) -> int:
        cursor.execute(
            "INSERT INTO devices (user_id, cloud_device_id, cloud_product_id, "
            "display_name, hardware_model) VALUES (%s, %s, %s, %s, %s)",
            (user_id, cloud_device_id, cloud_product_id, display_name, hardware_model),
        )
        return int(cursor.lastrowid)

    def bind_device(
        self,
        user_id: int,
        cloud_device_id: str,
        cloud_product_id: str,
        display_name: str,
        hardware_model: str | None = None,
    ) -> int:
        """Bind a verified cloud device to a user and return its internal ID."""
        with self._cursor(write=True) as cursor:
            return self._insert_device(
                cursor, user_id, cloud_device_id, cloud_product_id,
                display_name, hardware_model,
            )

    def bind_device_and_get(
        self, user_id: int, cloud_device_id: str, cloud_product_id: str,
        display_name: str, hardware_model: str | None = None,
    ) -> dict[str, Any]:
        """Create and read an ownership record in one transaction."""
        with self._cursor(write=True) as cursor:
            device_id = self._insert_device(
                cursor, user_id, cloud_device_id, cloud_product_id,
                display_name, hardware_model,
            )
            cursor.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE id = %s AND user_id = %s",
                (device_id, user_id),
            )
            record = cursor.fetchone()
            if record is None:
                raise RuntimeError("inserted device could not be read in transaction")
            return record

    def list_devices_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE user_id = %s ORDER BY id",
                (user_id,),
            )
            return cursor.fetchall()

    def get_device_for_user(
        self, user_id: int, device_id: int
    ) -> dict[str, Any] | None:
        """Fetch a device only when it belongs to the supplied logged-in user."""
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE id = %s AND user_id = %s",
                (device_id, user_id),
            )
            return cursor.fetchone()

    def get_device_by_cloud_id(self, cloud_device_id: str) -> dict[str, Any] | None:
        """Resolve an incoming cloud message to its owned device record."""
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE cloud_device_id = %s",
                (cloud_device_id,),
            )
            return cursor.fetchone()

    def get_device_identity_by_cloud_id(self, cloud_device_id: str) -> dict[str, Any] | None:
        """Resolve the device and owner for samples and durable step counting."""
        with self._cursor() as cursor:
            cursor.execute("SELECT id, user_id FROM devices WHERE cloud_device_id = %s", (cloud_device_id,))
            return cursor.fetchone()

    def rename_device(
        self, user_id: int, device_id: int, display_name: str
    ) -> bool | None:
        """Rename using one transaction; None means missing or not owned.

        Lock the owned row briefly so unbinding cannot race the name check.
        False means the requested name already matches the stored value.
        """
        with self._cursor(write=True) as cursor:
            cursor.execute(
                "SELECT display_name FROM devices WHERE id = %s AND user_id = %s FOR UPDATE",
                (device_id, user_id),
            )
            record = cursor.fetchone()
            if record is None:
                return None
            if record["display_name"] == display_name:
                return False
            cursor.execute(
                "UPDATE devices SET display_name = %s "
                "WHERE id = %s AND user_id = %s",
                (display_name, device_id, user_id),
            )
            return cursor.rowcount > 0

    def delete_device(self, user_id: int, device_id: int) -> bool:
        """Delete a device only when it belongs to the supplied user."""
        with self._cursor(write=True) as cursor:
            cursor.execute(
                "DELETE FROM devices WHERE id = %s AND user_id = %s",
                (device_id, user_id),
            )
            return cursor.rowcount > 0

    def update_confirmed_sample_interval(
        self, user_id: int, device_id: int, sample_interval_ms: int
    ) -> bool:
        """Save a sampling interval only after device execution is confirmed."""
        if isinstance(sample_interval_ms, bool) or not isinstance(sample_interval_ms, int):
            raise TypeError("sample_interval_ms must be an integer")
        if sample_interval_ms <= 0:
            raise ValueError("sample_interval_ms must be greater than zero")

        with self._cursor(write=True) as cursor:
            cursor.execute(
                "UPDATE devices SET sample_interval_ms = %s "
                "WHERE id = %s AND user_id = %s",
                (sample_interval_ms, device_id, user_id),
            )
            return cursor.rowcount > 0

    def touch_last_seen(self, cloud_device_id: str) -> bool:
        """Record a valid report time at most once per minute per device.

        Call only after validating the cloud message's source and payload. The
        method does not persist sensor samples and returns False for an unknown
        device or when the one-minute write interval has not elapsed.
        """
        with self._cursor(write=True) as cursor:
            cursor.execute(
                "UPDATE devices SET last_seen_at = UTC_TIMESTAMP(3) "
                "WHERE cloud_device_id = %s AND "
                "(last_seen_at IS NULL OR last_seen_at <= "
                "UTC_TIMESTAMP(3) - INTERVAL 60 SECOND)",
                (cloud_device_id,),
            )
            return cursor.rowcount > 0
    # Daily step history --------------------------------------------------

    def save_daily_steps(self, records: list[dict[str, Any]]) -> None:
        """Replace daily totals in one transaction; retrying never adds twice."""
        if not records:
            return
        values = []
        for record in records:
            user_id, steps = record["user_id"], record["steps"]
            record_date = record["record_date"]
            if type(user_id) is not int or user_id < 1:
                raise ValueError("user_id must be a positive integer")
            if type(steps) is not int or not 0 <= steps <= (1 << 63) - 1:
                raise ValueError("steps exceeds the supported integer range")
            if type(record_date) is not date or record_date.year < 1000:
                raise ValueError("record_date must be a MySQL-supported date")
            values.append((user_id, steps, record_date, steps))
        with self._cursor(write=True) as cursor:
            # A bound UPDATE parameter is outside VALUES; execute separately
            # to avoid the driver's executemany INSERT rewriting assumptions.
            for parameters in values:
                cursor.execute(
                    "INSERT INTO daily_steps (user_id, steps, record_date) "
                    "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE steps = %s",
                    parameters,
                )

    def get_daily_steps(self, user_id: int, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Return only the requested user's persisted, ordered daily totals."""
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT user_id, steps, record_date FROM daily_steps "
                "WHERE user_id = %s AND record_date BETWEEN %s AND %s "
                "ORDER BY record_date",
                (user_id, start_date, end_date),
            )
            return cursor.fetchall()


__all__ = ["DBControl"]
