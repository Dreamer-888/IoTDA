"""Start the local backend with configuration from the project .env file."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    """Load literal KEY=value settings without executing shell commands.

    Existing environment variables take precedence. Optional enclosing quotes
    are removed; variable expansion and multiline values are not supported.
    """
    if not path.exists():
        return
    settings = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f".env 第 {number} 行格式错误，应为 KEY=value")
        value = value.strip()
        if value.startswith(("'", '"')):
            closing = value.find(value[0], 1)
            suffix = value[closing + 1:].strip() if closing >= 1 else ""
            if closing < 1 or (suffix and not suffix.startswith("#")):
                raise ValueError(f".env 第 {number} 行引号未正确闭合")
            value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        settings[key] = value
    for key, value in settings.items():
        os.environ.setdefault(key, value)


def port_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须为整数") from exc
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口范围为 1–65535")
    return number


def wait_seconds(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("等待秒数必须为整数") from exc
    if not 1 <= number <= 300:
        raise argparse.ArgumentTypeError("等待秒数范围为 1–300")
    return number


def display_message(body, number: int) -> None:
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            pass
    rendered = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    received_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    print(f"\n[{received_at}] 第 {number} 条云端原始报文：")
    print(rendered[:16384])
    if len(rendered) > 16384:
        print("（显示内容已截断至 16384 字符）")
    # A successful display must be flushed before confirming the delivery.
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 IoT 后端开发服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=port_number, default=5000, help="监听端口，默认 5000")
    parser.add_argument("--debug", action="store_true", help="启用本地调试，不启用自动重载")
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument("--check-amqp", action="store_true",
                             help="检查 AMQPS 认证和队列连接，不消费消息或启动 Web 服务")
    diagnostics.add_argument("--peek-amqp", action="store_true",
                             help="持续接收并显示云端原始报文，按 Ctrl+C 停止；无需登录、设备绑定或数据库")
    parser.add_argument("--once", action="store_true",
                        help="配合 --peek-amqp，只预览一条报文后退出，并释放消息而不确认")
    parser.add_argument("--wait-seconds", type=wait_seconds, default=30,
                        help="无报文提示间隔秒数；--once 时为等待上限，默认 30，范围 1–300")
    args = parser.parse_args()
    if args.once and not args.peek_amqp:
        parser.error("--once 需要配合 --peek-amqp 使用")
    try:
        load_env(PROJECT_ROOT / ".env")
        from src.telemetry.amqp_config import AmqpConfig
        from src.telemetry.amqp import (
            AmqpConsumer, NoMessageReceived, check_connection, peek_message, watch_messages,
        )

        config = AmqpConfig.from_env()
        if args.check_amqp:
            try:
                check_connection(config)
            except Exception as error:
                print(f"AMQP 检查失败（{type(error).__name__}），请检查网络、接入地址、"
                      "凭证、队列、实例 ID 和 CA 信任配置。", file=sys.stderr)
                return 1
            print("AMQP TLS 校验、认证和队列连接成功；未消费消息。")
            return 0
        if args.peek_amqp:
            count = 0
            def show_message(body):
                nonlocal count
                display_message(body, count + 1)
                count += 1

            try:
                if args.once:
                    body = peek_message(
                        config, timeout=args.wait_seconds,
                        on_connected=lambda: print(
                            f"AMQP 已连接，等待云端报文（最多 {args.wait_seconds} 秒）……", flush=True),
                    )
                    show_message(body)
                    print("已发送释放请求，允许队列重新投递此消息。", flush=True)
                else:
                    print("持续显示模式：无需绑定设备；成功显示后确认队列消息。按 Ctrl+C 停止。", flush=True)
                    watch_messages(
                        config, show_message, timeout=args.wait_seconds,
                        on_connected=lambda: print("AMQP 已连接，持续等待云端报文……", flush=True),
                        on_idle=lambda: print(
                            f"最近 {args.wait_seconds} 秒未收到报文，继续等待……", flush=True),
                        on_reconnecting=lambda error_type, delay: print(
                            f"AMQP 连接中断或失败（{error_type}），{delay} 秒后重连……", flush=True),
                    )
            except NoMessageReceived:
                print("连接正常，但等待期间没有收到报文。请确认转发规则已启动、"
                      "目标队列正确，并让设备再次上报。", file=sys.stderr)
                return 2
            except KeyboardInterrupt:
                print(f"\n已停止接收，共显示 {count} 条报文。", flush=True)
                return 0
            except Exception as error:
                print(f"AMQP 报文预览失败（{type(error).__name__}），请检查网络、"
                      "接入配置和队列。", file=sys.stderr)
                return 1
            return 0
        from src.auth_api import create_app

        app = create_app()
        consumer = AmqpConsumer(config, app.extensions["telemetry_service"])
        app.extensions["amqp_consumer"] = consumer
        step_history = app.extensions["step_history"]
    except ImportError:
        print("依赖未安装完整，请执行：.venv/bin/python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 1
    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, terminate)
    try:
        step_history.start()
        consumer.start()
        # One process owns both the consumer and the in-memory sample buffer.
        app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.stop()
        app.extensions["live_hub"].close_all()
        step_history.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
