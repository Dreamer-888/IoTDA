"""Shared integer validation for database, telemetry, and logging settings."""

import os


def integer_setting(value, name, minimum=1, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < minimum or (maximum is not None and number > maximum):
        bounds = f"{minimum} to {maximum}" if maximum is not None else f"at least {minimum}"
        raise ValueError(f"{name} must be {bounds}")
    return number


def positive_env_setting(name, default):
    return integer_setting(os.getenv(name, str(default)), name)
