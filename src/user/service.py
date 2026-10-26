"""Business logic for user account operations."""

from __future__ import annotations

import re
from typing import Any, Mapping, NoReturn

from werkzeug.security import check_password_hash, generate_password_hash

from src.DBcontrol import DBControl
from src.user.dto import LoginDTO, UserDTO
from src.user.models import User
from src.common.errors import ServiceError

_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class UserServiceError(ServiceError):
    """User account validation or conflict error."""


class AuthService:
    """Validate account operations and delegate persistence to DBControl."""

    def __init__(self, database: DBControl):
        self.database = database

    @staticmethod
    def _normalize_username(username: Any) -> str:
        if not isinstance(username, str):
            raise UserServiceError("username 必须是字符串")
        value = username.strip()
        if not 1 <= len(value) <= 64:
            raise UserServiceError("username 长度必须为 1 到 64 个字符")
        return value

    @staticmethod
    def _validate_password(password: Any) -> str:
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise UserServiceError("password 长度必须为 8 到 128 个字符")
        return password

    @staticmethod
    def _normalize_email(email: Any) -> str | None:
        if email is None:
            return None
        if not isinstance(email, str):
            raise UserServiceError("email 必须是字符串或 null")
        value = email.strip().casefold()
        if not value:
            return None
        if len(value) > 254 or not _EMAIL_PATTERN.fullmatch(value):
            raise UserServiceError("email 格式不正确")
        return value

    @staticmethod
    def _handle_duplicate(error: Exception) -> NoReturn:
        if getattr(error, "errno", None) == 1062:
            raise UserServiceError("用户名或邮箱已被注册", 409) from error
        raise error

    def register(self, user_info: UserDTO) -> User:
        username = self._normalize_username(user_info.username)
        password = self._validate_password(user_info.password)
        email = self._normalize_email(user_info.email)
        password_hash = generate_password_hash(password)

        try:
            record = self.database.create_user_and_get(
                username, password_hash, email
            )
        except Exception as exc:
            self._handle_duplicate(exc)
        return User.from_record(record)

    def login(self, credentials: LoginDTO) -> User:
        username = self._normalize_username(credentials.username)
        password = credentials.password
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise UserServiceError("password 长度必须为 1 到 128 个字符")

        record = self.database.get_user_by_username(username)
        if record is None or not check_password_hash(record["password_hash"], password):
            raise UserServiceError("用户名或密码错误", 401)
        return User.from_record(record)

    def get_user_info(self, user_id: int) -> User:
        record = self.database.get_user_by_id(user_id)
        if record is None:
            raise UserServiceError("用户不存在", 404)
        return User.from_record(record)

    def update_user_info(
        self, user_id: int, user_info: UserDTO, *,
        expected_version: int | None = None,
        current_user: Mapping[str, Any] | None = None,
    ) -> bool:
        """Reuse a trusted record read during authentication, keeping atomic guards."""
        current = (
            current_user if current_user is not None
            else self.database.get_user_by_id(user_id)
        )
        if current is None:
            raise UserServiceError("用户不存在", 404)
        if current["id"] != user_id:
            raise UserServiceError("无权修改该用户信息", 403)
        if expected_version is not None and current["session_version"] != expected_version:
            raise UserServiceError("登录状态已失效，请重新登录", 401)

        changes: dict[str, Any] = {}

        if user_info.username is not None:
            changes["username"] = self._normalize_username(user_info.username)
        if user_info.email_provided:
            changes["email"] = self._normalize_email(user_info.email)
        if user_info.password is not None:
            new_password = self._validate_password(user_info.password)
            if not isinstance(user_info.current_password, str):
                raise UserServiceError("修改密码时必须提供 current_password")
            if not check_password_hash(
                current["password_hash"], user_info.current_password
            ):
                raise UserServiceError("当前密码错误", 401)
            changes["password_hash"] = generate_password_hash(new_password)

        if not changes:
            raise UserServiceError("至少提供一个需要修改的字段")

        try:
            changed = self.database.update_user_fields(
                user_id, changes,
                expected_version=current["session_version"],
                expected_password_hash=current["password_hash"]
                if "password_hash" in changes else None,
            )
        except Exception as exc:
            self._handle_duplicate(exc)
        if not changed:
            latest = self.database.get_user_session(user_id)
            if latest is None:
                raise UserServiceError("用户不存在", 404)
            if "password_hash" in changes or latest["session_version"] != current["session_version"]:
                raise UserServiceError("账号已被其他请求修改，请重新登录后操作", 409)
        return changed
