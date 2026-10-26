"""Shared session validation and JSON responses."""

from flask import g, jsonify, request, session

from src.common.errors import ServiceError
from src.common.response import R


def response(code, message, data=None):
    return jsonify(R(code, message, data).to_dict()), code


def json_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ServiceError("请求体必须是 JSON 对象")
    return payload


def require_user(database, *, include_profile=True):
    """Validate the signed session against the current database version."""
    if hasattr(g, "authenticated_user") and (
        not include_profile or "password_hash" in g.authenticated_user
    ):
        return g.authenticated_user
    user_id = session.get("user_id")
    version = session.get("session_version")
    if type(user_id) is not int or user_id <= 0:
        session.clear()
        raise ServiceError("尚未登录", 401)
    if type(version) is not int or version < 1:
        session.clear()
        raise ServiceError("登录状态已失效，请重新登录", 401)
    record = (
        database.get_user_by_id(user_id) if include_profile
        else database.get_user_session(user_id)
    )
    if record is None or record["session_version"] != version:
        session.clear()
        raise ServiceError("登录状态已失效，请重新登录", 401)
    g.authenticated_user = record
    return record
