"""Console and daily file logging for application modules."""

from __future__ import annotations

import logging
from datetime import datetime
import os
import threading
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import g, has_request_context, request, session
from flask.logging import default_handler

_CONFIG_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HTTP_LOGGER = logging.getLogger("src.http")
_LOG_TIMEZONE = ZoneInfo("Asia/Shanghai")


class DailyFileHandler(logging.FileHandler):
    """Append records to one file per Beijing calendar day, without size rotation."""

    def __init__(self, directory):
        self.directory = directory.resolve()
        super().__init__(self.path_for(time.time()), mode="a", encoding="utf-8", delay=True)

    def path_for(self, timestamp):
        date = datetime.fromtimestamp(timestamp, _LOG_TIMEZONE).strftime("%Y-%m-%d")
        return self.directory / f"app-{date}.log"

    def emit(self, record):
        # Handler.handle holds the logging handler lock, including this switch.
        # Use the record's date so a long-running process also changes files at midnight.
        path = str(self.path_for(record.created))
        if path != self.baseFilename:
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
                self.stream = None
            self.baseFilename = path
        super().emit(record)


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = "-"
        record.user_id = "-"
        if has_request_context():
            record.request_id = getattr(g, "request_id", "-")
            user = getattr(g, "authenticated_user", None)
            record.user_id = user["id"] if user else session.get("user_id", "-")
        return True


class UTCFormatter(logging.Formatter):
    converter = time.gmtime

    def formatException(self, exc_info):
        """Keep stack locations and error codes without arbitrary exception values."""
        exception = exc_info[1]
        parts = []
        seen = set()

        def append(error):
            if id(error) in seen:
                return
            seen.add(id(error))
            cause = error.__cause__ or (
                error.__context__ if not error.__suppress_context__ else None
            )
            if cause is not None:
                append(cause)
                parts.append("Caused the following exception:\n")
            parts.append("Traceback (most recent call last):\n")
            # Formatting traceback source lines can expose literal credentials.
            # Keep locations and function names, but omit code and local values.
            for frame in traceback.extract_tb(error.__traceback__):
                parts.append(
                    f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
                )
            name = f"{type(error).__module__}.{type(error).__qualname__}"
            errno = getattr(error, "errno", None)
            if type(errno) is int:
                name += f" errno={errno}"
            parts.append(name + "\n")

        append(exception)
        return "".join(parts).rstrip()


def configure_logging(app):
    """Configure only the src namespace; repeated app creation replaces our handlers.

    Runs on the same Beijing calendar day append to the same dated file.
    Third-party and Werkzeug loggers keep their own configuration.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    levels = {name: getattr(logging, name) for name in (
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    )}
    if level_name not in levels:
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    directory = Path(os.getenv("LOG_DIR", "logs"))
    if not directory.is_absolute():
        directory = _PROJECT_ROOT / directory
    formatter = UTCFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s "
        "pid=%(process)d request_id=%(request_id)s user_id=%(user_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    with _CONFIG_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = DailyFileHandler(directory)
        handlers = [logging.StreamHandler(), file_handler]
        logger = logging.getLogger("src")
        for handler in list(logger.handlers):
            if getattr(handler, "_iotda_owned", False):
                logger.removeHandler(handler)
                handler.close()
        for handler in handlers:
            handler._iotda_owned = True
            handler.setFormatter(formatter)
            handler.addFilter(RequestContextFilter())
            logger.addHandler(handler)
        logger.setLevel(levels[level_name])
        logger.propagate = False
        # Flask may have created its default handler before our namespace setup.
        if default_handler in app.logger.handlers:
            app.logger.removeHandler(default_handler)
        if not app.logger.name.startswith("src."):
            # Direct execution may name the Flask logger __main__.
            app.logger = logging.getLogger("src.app")
        app.logger.setLevel(logging.NOTSET)
        app.logger.propagate = True
        app.extensions["log_file"] = file_handler.baseFilename
    app.logger.info("event=logging.configured level=%s", level_name)


def log_request(response):
    """Log metadata only, using the route template instead of untrusted URL text."""
    logger = _HTTP_LOGGER
    level = (
        logging.ERROR if response.status_code >= 500 else
        logging.WARNING if response.status_code >= 400 else logging.INFO
    )
    if logger.isEnabledFor(level):
        now = time.monotonic()
        started = getattr(g, "request_started", now)
        route = request.url_rule.rule if request.url_rule else "<unmatched>"
        logger.log(
            level, "event=http.request method=%s route=%s status=%s duration_ms=%.1f",
            request.method, route, response.status_code,
            (now - started) * 1000,
        )
