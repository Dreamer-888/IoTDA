"""User domain model used by the user-management service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class User:
    id: int
    username: str
    email: str | None
    password_hash: str = field(repr=False)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    session_version: int = 1

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "User":
        return cls(
            id=int(record["id"]),
            username=record["username"],
            email=record.get("email"),
            password_hash=record["password_hash"],
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
            session_version=int(record["session_version"]),
        )
