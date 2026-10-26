"""Response VOs shared by the user controller and frontend API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.user.models import User
from src.common.time import format_utc
from src.common.response import R


@dataclass(slots=True)
class UserVO:
    id: int
    username: str
    email: str | None
    created_at: str | None
    updated_at: str | None

    @classmethod
    def from_user(cls, user: User) -> "UserVO":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            created_at=format_utc(user.created_at),
            updated_at=format_utc(user.updated_at),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class UserLoginVO:
    user: UserVO
    authenticated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"user": self.user.to_dict(), "authenticated": self.authenticated}

