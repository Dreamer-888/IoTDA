"""Raw statistics and optional display smoothing for six-axis samples."""

from collections import deque
import math

from src.telemetry.models import AXES


def finite_or_none(value):
    return value if math.isfinite(value) else None


def mean(values):
    """Scale before summing to avoid overflow with finite large inputs."""
    scale = max(abs(value) for value in values)
    if scale == 0:
        return 0.0
    normalized = math.fsum(value / scale for value in values) / len(values)
    return max(-1.0, min(1.0, normalized)) * scale


def describe_sample(sample):
    return add_magnitudes(sample.to_dict())


def add_magnitudes(data):
    data["accel_magnitude"] = finite_or_none(math.hypot(*(data[name] for name in AXES[:3])))
    data["gyro_magnitude"] = finite_or_none(math.hypot(*(data[name] for name in AXES[3:])))
    return data


def _finite_sum(values):
    try:
        return finite_or_none(math.fsum(values))
    except OverflowError:
        return None


class StatisticsService:
    @staticmethod
    def summarize(samples):
        metrics = {}
        for name in AXES:
            if not samples:
                metrics[name] = {"min": None, "max": None, "avg": None, "range": None}
                continue
            values = [getattr(sample, name) for sample in samples]
            low, high = min(values), max(values)
            metrics[name] = {
                "min": low, "max": high, "avg": mean(values),
                "range": finite_or_none(high - low),
            }
        return {"count": len(samples), "metrics": metrics}

    @staticmethod
    def smooth(samples, window_size):
        """Causal moving average for display; stored samples stay unchanged."""
        if type(window_size) is not int or not 1 <= window_size <= 50:
            raise ValueError("window_size must be an integer from 1 to 50")
        if window_size == 1:
            return [describe_sample(sample) for sample in samples]
        recent = deque(maxlen=window_size)
        totals = [0.0] * len(AXES)
        result = []
        for index, sample in enumerate(samples):
            row = tuple(getattr(sample, name) for name in AXES)
            removed = recent[0] if len(recent) == window_size else None
            recent.append(row)
            data = sample.to_dict()
            for axis, name in enumerate(AXES):
                total = totals[axis]
                if total is None or index % 128 == 0:
                    total = _finite_sum(item[axis] for item in recent)
                else:
                    previous = total
                    outgoing = removed[axis] if removed else 0.0
                    total = _finite_sum((previous, row[axis], -outgoing))
                    if total is not None and abs(total) < 1e-10 * max(
                        abs(previous), abs(row[axis]), abs(outgoing)
                    ):
                        # Recompute after severe cancellation to preserve small
                        # values when a much larger sample leaves the window.
                        total = _finite_sum(item[axis] for item in recent)
                totals[axis] = total
                # Normal sensor values use constant-cost rolling sums. Extreme
                # finite inputs fall back to scaled means rather than infinity.
                data[name] = (total / len(recent) if total is not None else
                              mean([item[axis] for item in recent]))
            result.append(add_magnitudes(data))
        return result
