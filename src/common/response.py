"""Standard response envelope shared by all modules."""

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class R(Generic[T]):
    code: int
    message: str
    data: T | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.data.to_dict() if hasattr(self.data, "to_dict") else self.data
        return {"code": self.code, "message": self.message, "data": data}
