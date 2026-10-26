"""Validated AMQPS configuration; repr never exposes connection credentials."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from src.common.config import integer_setting
from src.telemetry.models import SIX_AXIS_SERVICE_ID


def setting(primary, alias=None, *, required=True):
    first = os.getenv(primary, "").strip()
    second = os.getenv(alias, "").strip() if alias else ""
    if first and second and first != second:
        raise ValueError(f"{primary} 与 {alias} 配置冲突")
    value = first or second
    if required and not value:
        raise ValueError(f"缺少 {primary}" + (f"（也可使用 {alias}）" if alias else ""))
    return value


@dataclass(frozen=True)
class AmqpConfig:
    host: str
    port: int
    queue: str
    access_key: str = field(repr=False)
    access_code: str = field(repr=False)
    instance_id: str = ""
    service_id: str = SIX_AXIS_SERVICE_ID
    ca_file: str = ""
    connect_timeout: int = 10
    ip_version: str = "4"

    @classmethod
    def from_env(cls):
        address = setting("AMQP_ACCESS")
        try:
            url = urlsplit(address if "://" in address else "//" + address)
            if (url.scheme not in ("", "amqps") or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.path not in ("", "/") or url.query or url.fragment
                    or not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname)):
                raise ValueError
            embedded_port = url.port
        except ValueError:
            raise ValueError("AMQP_ACCESS 应为域名或 amqps://域名:端口，不含凭证或路径") from None
        port = integer_setting(os.getenv("AMQP_PORT", str(embedded_port or 5671)),
                               "AMQP_PORT", maximum=65535)
        if embedded_port is not None and embedded_port != port:
            raise ValueError("AMQP_ACCESS 与 AMQP_PORT 的端口不一致")
        key = setting("AMQP_AK", "AMQP_ACCESS_KEY")
        code = setting("AMQP_CODE", "AMQP_ACCESS_CODE")
        instance = setting("AMQP_INSTANCE_ID", required=False)
        if any(c in key + instance for c in "|=\r\n"):
            raise ValueError("AMQP_AK 或 AMQP_INSTANCE_ID 含非法认证分隔符")
        ca_file = setting("AMQP_CA_FILE", required=False)
        if ca_file and not Path(ca_file).is_file():
            raise ValueError("AMQP_CA_FILE 指定的 CA 文件不存在")
        ip_version = os.getenv("AMQP_IP_VERSION", "4").strip()
        if ip_version not in ("4", "6", "auto"):
            raise ValueError("AMQP_IP_VERSION 必须是 4、6 或 auto")
        service_id = setting("AMQP_SERVICE_ID", required=False) or SIX_AXIS_SERVICE_ID
        if service_id != SIX_AXIS_SERVICE_ID:
            raise ValueError("AMQP_SERVICE_ID 必须为真实设备服务 six_axis，或留空使用默认值")
        return cls(url.hostname, port, setting("AMQP_QUEUE"), key, code, instance,
                   service_id, ca_file, ip_version=ip_version)

    @property
    def url(self):
        return f"amqps://{self.host}:{self.port}"

    def username(self, timestamp_ms):
        return (f"accessKey={self.access_key}|timestamp={timestamp_ms}"
                f"|instanceId={self.instance_id}")
