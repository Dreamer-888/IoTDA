"""Bounded, thread-safe previews of validated cloud reports, grouped by device."""

from collections import OrderedDict, deque
import json
import threading
import time

from src.common.time import format_utc


class ReportBuffer:
    def __init__(self, *, retention_seconds=300, max_reports=50, max_devices=128,
                 max_message_bytes=16384, max_total_bytes=8 * 1024 * 1024):
        for value in (retention_seconds, max_reports, max_devices,
                      max_message_bytes, max_total_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("report buffer limits must be positive integers")
        self.retention_seconds = retention_seconds
        self.max_reports = max_reports
        self.max_devices = max_devices
        self.max_message_bytes = min(max_message_bytes, max_total_bytes)
        self.max_total_bytes = max_total_bytes
        self._queues = OrderedDict()
        self._bytes = 0
        self._sequence = 0
        self._lock = threading.Lock()

    def _pop(self, device_id):
        queue = self._queues[device_id]
        self._bytes -= queue.popleft()[2]
        if not queue:
            del self._queues[device_id]

    def _prune(self, now):
        cutoff = now - self.retention_seconds
        for device_id in list(self._queues):
            while device_id in self._queues and self._queues[device_id][0][0] <= cutoff:
                self._pop(device_id)

    def append(self, device_id, payload, received_at):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        truncated = len(encoded) > self.max_message_bytes
        raw = encoded[:self.max_message_bytes].decode("utf-8", errors="ignore")
        size = len(raw.encode("utf-8"))
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if device_id in self._queues and len(self._queues[device_id]) >= self.max_reports:
                self._pop(device_id)
            if device_id not in self._queues and len(self._queues) >= self.max_devices:
                oldest_device = next(iter(self._queues))
                while oldest_device in self._queues:
                    self._pop(oldest_device)
            while self._bytes + size > self.max_total_bytes and self._queues:
                oldest_device = min(self._queues, key=lambda key: self._queues[key][0][0])
                self._pop(oldest_device)
            self._sequence += 1
            record = {
                "sequence": self._sequence, "device_id": device_id,
                "received_at": format_utc(received_at), "raw": raw, "truncated": truncated,
            }
            self._queues.setdefault(device_id, deque()).append((now, record, size))
            self._queues.move_to_end(device_id)
            self._bytes += size

    def snapshot(self, device_id):
        with self._lock:
            self._prune(time.monotonic())
            return [record.copy() for _stamp, record, _size in self._queues.get(device_id, ())]

    def clear(self, device_id):
        with self._lock:
            while device_id in self._queues:
                self._pop(device_id)
