"""Thread-safe bounded sample windows; age is measured with monotonic time."""

from collections import deque
import threading
import time

from src.common.errors import ServiceError
from src.common.config import positive_env_setting as positive_setting


class SensorBuffer:
    def __init__(self, *, retention_seconds=300, max_samples=6000, max_devices=128):
        for value in (retention_seconds, max_samples, max_devices):
            if type(value) is not int or value < 1:
                raise ValueError("buffer limits must be positive integers")
        self.retention_seconds = retention_seconds
        self.max_samples = max_samples
        self.max_devices = max_devices
        self._queues = {}
        self._lock = threading.Lock()
        self._sweep_interval = min(30, retention_seconds)
        self._last_sweep = float("-inf")

    @classmethod
    def from_env(cls):
        return cls(
            retention_seconds=positive_setting("TELEMETRY_RETENTION_SECONDS", 300),
            max_samples=positive_setting("TELEMETRY_MAX_SAMPLES", 6000),
            max_devices=positive_setting("TELEMETRY_MAX_DEVICES", 128),
        )

    def _prune_device(self, device_id, now):
        queue = self._queues.get(device_id)
        if queue is not None:
            cutoff = now - self.retention_seconds
            while queue and queue[0][0] <= cutoff:
                queue.popleft()
            if not queue:
                del self._queues[device_id]
                return None
        return queue

    def _sweep(self, now):
        for device_id in list(self._queues):
            self._prune_device(device_id, now)
        self._last_sweep = now

    def _prepare_queue(self, device_id, now):
        if now - self._last_sweep >= self._sweep_interval:
            self._sweep(now)
        queue = self._prune_device(device_id, now)
        if queue is None and len(self._queues) >= self.max_devices:
            self._sweep(now)
            if len(self._queues) >= self.max_devices:
                raise ServiceError("传感数据缓冲已满，请稍后重试", 503)
        return queue

    def ensure_capacity(self, device_id):
        """Check capacity before a report updates database metadata."""
        with self._lock:
            self._prepare_queue(device_id, time.monotonic())

    def append(self, sample):
        with self._lock:
            now = time.monotonic()
            queue = self._prepare_queue(sample.device_id, now)
            if queue is None:
                queue = self._queues[sample.device_id] = deque(maxlen=self.max_samples)
            queue.append((now, sample))

    def latest(self, device_id):
        sample, _age = self.latest_with_age(device_id)
        return sample

    def latest_with_age(self, device_id):
        with self._lock:
            now = time.monotonic()
            queue = self._prune_device(device_id, now)
            if not queue:
                return None, None
            stamp, sample = queue[-1]
            return sample, max(0.0, now - stamp)

    def window(self, device_id, seconds):
        self.validate_window(seconds)
        with self._lock:
            now = time.monotonic()
            queue = self._prune_device(device_id, now)
            # Walk newest first and stop once the requested window is exceeded.
            # Return an immutable-sample snapshot in ascending arrival order.
            samples = []
            cutoff = now - seconds
            for stamp, sample in reversed(queue or ()):
                if stamp <= cutoff:
                    break
                samples.append(sample)
            samples.reverse()
            return samples

    def validate_window(self, seconds):
        if type(seconds) is not int or not 1 <= seconds <= self.retention_seconds:
            raise ServiceError(f"seconds 必须是 1 到 {self.retention_seconds} 的整数")

    def clear(self, device_id):
        with self._lock:
            self._queues.pop(device_id, None)
