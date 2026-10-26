"""Flask controller for the user-management API."""

from __future__ import annotations

import logging
import hashlib
import uuid

from flask import Blueprint, current_app, request, session

from src.common.http import json_payload, require_user, response
from src.common.errors import ServiceError
from src.user.dto import LoginDTO, UserDTO
from src.user.service import AuthService
from src.user.models import User
from src.user.vo import UserLoginVO, UserVO

logger = logging.getLogger(__name__)


class AuthController:
    """Translate HTTP requests to AuthService calls and standard R responses."""

    def __init__(self, service: AuthService):
        self.service = service
        self.blueprint = Blueprint("user_management", __name__)
        self.blueprint.add_url_rule(
            "/api/auth/register", view_func=self.register, methods=["POST"]
        )
        self.blueprint.add_url_rule(
            "/api/auth/login", view_func=self.login, methods=["POST"]
        )
        self.blueprint.add_url_rule(
            "/api/auth/me", view_func=self.current_user, methods=["GET"]
        )
        self.blueprint.add_url_rule(
            "/api/auth/logout", view_func=self.logout, methods=["POST"]
        )
        self.blueprint.add_url_rule(
            "/api/users/<int:user_id>",
            view_func=self.get_user_info,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/users/<int:user_id>",
            view_func=self.update_user_info,
            methods=["PATCH"],
        )

    def register(self):
        user_info = UserDTO.from_payload(json_payload())
        user = self.service.register(user_info)
        logger.info("event=user.registered account_id=%s", user.id)
        return response(
            201, "注册成功", UserVO.from_user(user)
        )

    def login(self):
        try:
            credentials = LoginDTO.from_payload(json_payload())
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        user = self.service.login(credentials)
        hub = current_app.extensions.get("live_hub")
        if hub and session.get("live_session_id"):
            hub.revoke(session_id=session["live_session_id"])
        session.clear()
        session["user_id"] = user.id
        session["session_version"] = user.session_version
        session["live_session_id"] = uuid.uuid4().hex
        session.permanent = True
        logger.info("event=user.logged_in account_id=%s", user.id)
        return response(200, "登录成功", UserLoginVO(user=UserVO.from_user(user)))

    def current_user(self):
        record = require_user(self.service.database)
        if not isinstance(session.get("live_session_id"), str):
            # Concurrent tabs restoring the same legacy cookie must receive
            # the same connection ID, so logout can revoke all of its sockets.
            cookie = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"], "")
            session["live_session_id"] = "legacy-" + hashlib.sha256(cookie.encode()).hexdigest()
        return response(200, "查询成功", UserVO.from_user(User.from_record(record)))

    def get_user_info(self, user_id: int):
        record = require_user(self.service.database)
        if record["id"] != user_id:
            raise ServiceError("无权查看该用户信息", 403)
        return response(200, "查询成功", UserVO.from_user(User.from_record(record)))

    def update_user_info(self, user_id: int):
        record = require_user(self.service.database)
        if record["id"] != user_id:
            raise ServiceError("无权修改该用户信息", 403)
        user_info = UserDTO.from_payload(json_payload())
        changed = self.service.update_user_info(
            user_id, user_info, expected_version=record["session_version"],
            current_user=record,
        )
        logger.info(
            "event=user.updated account_id=%s changed=%s password_changed=%s",
            user_id, changed, user_info.password is not None and changed,
        )
        if user_info.password is not None and changed:
            current_app.extensions["live_hub"].revoke(user_id=user_id)
            session.clear()
            message = "密码已修改，请重新登录"
        else:
            message = "用户信息更新成功" if changed else "用户信息未发生变化"
        return response(200, "请求处理完成", message)

    def logout(self):
        user_id = session.get("user_id")
        if session.get("live_session_id"):
            current_app.extensions["live_hub"].revoke(session_id=session["live_session_id"])
        session.clear()
        logger.info("event=user.logged_out account_id=%s", user_id)
        return response(200, "已退出登录")
