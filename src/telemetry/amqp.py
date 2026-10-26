"""AMQP 1.0 consumer sharing the Flask process's telemetry service.

All Proton objects belong to the worker thread. Polling receive timeouts allow
shutdown without calling the non-thread-safe Proton API from another thread.
"""

from collections import OrderedDict
import hashlib
import json
import logging
import socket
import ssl
import threading
import time

from proton import SSLDomain, SSLException, Timeout
from proton.reactor import AtLeastOnce
from proton.utils import BlockingConnection

from src.telemetry.cloud import InvalidNotification, parse_notification

logger = logging.getLogger(__name__)


def tls_domain(config):
    try:
        domain = SSLDomain(SSLDomain.MODE_CLIENT)
        ca_file = config.ca_file or ssl.get_default_verify_paths().cafile
        if ca_file:
            domain.set_trusted_ca_db(ca_file)
        domain.set_peer_authentication(SSLDomain.VERIFY_PEER_NAME)
    except SSLException:
        raise RuntimeError(
            "AMQP TLS 初始化失败：请检查 CA 文件；若 Proton 缺少 TLS 支持，"
            "请安装 libssl-dev/pkg-config 后按 README 重新编译依赖"
        ) from None
    return domain


def connection_url(config, attempt=0):
    if config.ip_version == "auto":
        return config.url
    # Proton may wait indefinitely on a blackholed first IPv6 address rather
    # than try IPv4. Resolve the chosen family afresh and rotate its addresses
    # across retries. TLS SNI/peer verification must STILL use the cloud domain.
    family = socket.AF_INET if config.ip_version == "4" else socket.AF_INET6
    addresses = list(dict.fromkeys(
        item[4][0] for item in socket.getaddrinfo(
            config.host, config.port, family, socket.SOCK_STREAM,
        )
    ))
    if not addresses:
        raise OSError("AMQP 域名没有所选 IP 协议的地址")
    address = addresses[attempt % len(addresses)]
    if ":" in address:
        address = f"[{address}]"
    return f"amqps://{address}:{config.port}"


def connect(config, domain, *, attempt=0):
    # Rebuild time-dependent SASL credentials on EVERY reconnect.
    return BlockingConnection(
        connection_url(config, attempt), timeout=config.connect_timeout, ssl_domain=domain,
        heartbeat=30, reconnect=False, sasl_enabled=True, allowed_mechs="PLAIN",
        user=config.username(int(time.time() * 1000)), password=config.access_code,
        virtual_host="default", sni=config.host,
    )


def close_connection(connection):
    if connection is not None:
        try:
            connection.close()
        except Exception:
            # Remote errors can contain credentials or message content.
            logger.warning("event=amqp.close_failed")


def check_connection(config):
    """Verify TLS, credentials and receiver attachment without consuming data."""
    connection = None
    try:
        connection = connect(config, tls_domain(config))
        connection.create_receiver(config.queue, credit=0, options=AtLeastOnce())
    finally:
        close_connection(connection)


class NoMessageReceived(TimeoutError):
    """The receiver attached successfully but no message arrived in time."""


def peek_message(config, *, timeout=30, on_connected=None):
    """Receive one original body without business processing or accepting it.

    Zero initial credit disables prefetch; receive grants credit for one message.
    Release requests redelivery so this diagnostic does not accept queued data.
    """
    connection = None
    try:
        connection = connect(config, tls_domain(config))
        receiver = connection.create_receiver(config.queue, credit=0, options=AtLeastOnce())
        if on_connected is not None:
            on_connected()
        try:
            message = receiver.receive(timeout=timeout)
        except Timeout:
            raise NoMessageReceived() from None
        try:
            return message.body
        finally:
            receiver.release(delivered=False)
    finally:
        close_connection(connection)


def watch_messages(config, on_message, *, timeout=30, on_connected=None,
                   on_idle=None, on_reconnecting=None):
    """Display original messages continuously, accepting only after display.

    Idle timeouts keep the same connection open. Network failures reconnect
    with backoff; display failures propagate and leave the message unaccepted.
    Callbacks run on this thread, and KeyboardInterrupt closes the connection.
    """
    domain = tls_domain(config)
    delay = 1
    attempt = 0
    while True:
        connection = None
        try:
            try:
                connection = connect(config, domain, attempt=attempt)
                receiver = connection.create_receiver(config.queue, credit=0, options=AtLeastOnce())
            except Exception as error:
                error_type = type(error).__name__
            else:
                if on_connected is not None:
                    on_connected()
                while True:
                    try:
                        message = receiver.receive(timeout=timeout)
                    except Timeout:
                        if on_idle is not None:
                            on_idle()
                        continue
                    except Exception as error:
                        error_type = type(error).__name__
                        break
                    try:
                        on_message(message.body)
                    except BaseException:
                        try:
                            receiver.release(delivered=False)
                        except Exception:
                            logger.warning("event=amqp.preview_release_failed")
                        raise
                    try:
                        receiver.accept()
                    except Exception as error:
                        error_type = type(error).__name__
                        break
                    delay = 1
        finally:
            close_connection(connection)
        if on_reconnecting is not None:
            on_reconnecting(error_type, delay)
        time.sleep(delay)
        delay = min(delay * 2, 30)
        attempt += 1


class NotificationProcessor:
    def __init__(self, service, *, service_id="", dedup_limit=4096, dedup_seconds=300):
        self.service = service
        self.service_id = service_id
        self.dedup_limit = dedup_limit
        self.dedup_seconds = dedup_seconds
        self._processed = OrderedDict()

    def process(self, body, message_id=None):
        notification = parse_notification(body, service_id=self.service_id)
        if notification is None:
            return "ignored_event"
        report = notification.report
        identity = notification.request_id
        if identity is None and message_id is not None:
            identity = str(message_id)
        now = time.monotonic()
        while self._processed:
            _key, recorded = next(iter(self._processed.items()))
            if now - recorded < self.dedup_seconds:
                break
            self._processed.popitem(last=False)
        key = None
        if identity:
            # Include every real property and both cloud times so a step or
            # activity change cannot be lost when axes and request ID match.
            # Never retain raw messages or IDs in the deduplication cache.
            key = hashlib.sha256(json.dumps(
                [identity, report.cloud_device_id, report.cloud_product_id,
                 report.reported_at.isoformat(), report.cloud_event_at.isoformat(),
                 report.values], sort_keys=True,
            ).encode()).digest()
            if key in self._processed:
                return "duplicate"
        result = self.service.receive(
            report, raw_notification=notification.payload,
            event_key=key.hex() if key is not None else None,
        )
        if not result["accepted"]:
            return "unbound_device"
        if key is not None:
            self._processed[key] = now
            while len(self._processed) > self.dedup_limit:
                self._processed.popitem(last=False)
        return "accepted"


class AmqpConsumer:
    def __init__(self, config, service):
        self.config = config
        self.processor = NotificationProcessor(service, service_id=config.service_id)
        self._stop = threading.Event()
        self._thread = None
        # Fail synchronously if the local TLS trust configuration is invalid.
        self._tls = tls_domain(config)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("AMQP 接收器不能重复启动")
        self._thread = threading.Thread(target=self.run, name="iotda-amqp", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.config.connect_timeout * 3 + 2)
            if self._thread.is_alive():
                logger.warning("event=amqp.stop_timeout")

    def handle_message(self, receiver, message):
        try:
            outcome = self.processor.process(message.body, message.id)
        except InvalidNotification as exc:
            # Our validation messages contain field names only, never values.
            logger.warning("event=amqp.message_rejected reason=%s", exc)
            receiver.reject()
            return
        except Exception:
            # Transient database/buffer failures must NOT acknowledge success.
            # End this connection and back off to prevent a hot redelivery loop.
            receiver.release(delivered=False)
            raise
        receiver.accept()
        if outcome == "accepted":
            logger.debug("event=amqp.message_accepted")
        else:
            logger.info("event=amqp.message_ignored reason=%s", outcome)

    def run(self):
        delay = 1
        attempt = 0
        logger.info("event=amqp.started")
        while not self._stop.is_set():
            connection = None
            ready_at = None
            try:
                connection = connect(self.config, self._tls, attempt=attempt)
                if self._stop.is_set():
                    break
                receiver = connection.create_receiver(
                    self.config.queue, credit=1, options=AtLeastOnce(),
                )
                ready_at = time.monotonic()
                logger.info("event=amqp.connected")
                while not self._stop.is_set():
                    try:
                        message = receiver.receive(timeout=1)
                    except Timeout:
                        continue
                    self.handle_message(receiver, message)
            except Exception as exc:
                if not self._stop.is_set():
                    logger.warning("event=amqp.disconnected error_type=%s", type(exc).__name__)
            finally:
                close_connection(connection)
            if self._stop.is_set():
                break
            if ready_at is not None and time.monotonic() - ready_at >= 60:
                delay = 1
            logger.info("event=amqp.reconnecting delay_seconds=%s", delay)
            self._stop.wait(delay)
            delay = min(delay * 2, 30)
            attempt += 1
        logger.info("event=amqp.stopped")
