"""Request DTOs for user registration, login, and profile updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class UserDTO:
    username: str | None = None
    email: str | None = None
    password: str | None = field(default=None, repr=False)
    current_password: str | None = field(default=None, repr=False)
    email_provided: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UserDTO":
        return cls(
            username=payload.get("username"),
            email=payload.get("email"),
            password=payload.get("password"),
            current_password=payload.get("current_password"),
            email_provided="email" in payload,
        )


@dataclass(slots=True)
class LoginDTO:
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LoginDTO":
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("username 和 password 必须是字符串")
        return cls(username=username, password=password)
