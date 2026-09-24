"""权限校验装饰器 & 登录装饰器（供所有模块共用）。"""

from functools import wraps
from urllib.parse import urlparse
from flask import request, jsonify, redirect, url_for, g, session, render_template

from .db_base import (
    get_user_by_id, has_permission, get_user_permissions,
)
from .exceptions import NotLoginException, PermissionDeniedException, ModuleDisabledException
from .config_loader import is_module_enabled


# 统一的 session key（和 database.py / app.py 保持一致）
SESSION_USER_KEY = "user_id"


def _referer_same_origin(referer, host):
    """精确判定 Referer 与 Host 是否同源（替代脆弱的子串匹配）。

    - Host 头形如 host 或 host:port；
    - Referer 必须 http/https 且 netloc 与 Host 主机一致；
    - 端口缺省时按 80/443 默认端口对齐；
    - 允许 127.0.0.1 与 localhost 互认（本地开发常见）；
    - 反向代理场景下可在 config 配置 security.allowed_referer_origins
      （逗号分隔的 origin 白名单，如 https://app.example.com）放行公网来源，
      避免被误杀全部 POST。
    """
    if not host or not referer:
        return False
    try:
        rp = urlparse(referer)
    except Exception:
        return False
    if rp.scheme not in ("http", "https") or not rp.netloc:
        return False
    # 配置白名单优先（反向代理部署时放行公网 origin）
    allow = _allowed_referer_origins()
    if allow and referer.rstrip("/") in allow:
        return True
    r_host, _, r_port = rp.netloc.rpartition(":")
    if r_port and not r_port.isdigit():
        r_host, r_port = rp.netloc, ""
    h_host, _, h_port = host.rpartition(":")
    if h_port and not h_port.isdigit():
        h_host, h_port = host, ""
    if r_host != h_host:
        # 回环地址互认（含端口差异），其余主机不一致直接拒绝
        return {r_host, h_host} <= {"127.0.0.1", "localhost"}
    # 主机一致
    if {r_host, h_host} <= {"127.0.0.1", "localhost"}:
        return True  # 回环主机，端口差异也放行（兼容 Host 不带端口的开发场景）
    # 非回环：严格比对端口（含默认端口对齐）
    if not h_port and not r_port:
        return True
    if not h_port:
        return r_port in ("80", "443")
    if not r_port:
        return h_port in ("80", "443")
    return r_port == h_port


_ALLOWED_ORIGINS_CACHE = None


def _allowed_referer_origins():
    """读取反向代理场景下的 Referer 白名单（带缓存）。"""
    global _ALLOWED_ORIGINS_CACHE
    if _ALLOWED_ORIGINS_CACHE is not None:
        return _ALLOWED_ORIGINS_CACHE
    try:
        from .config_loader import get_config
        raw = get_config("security.allowed_referer_origins", "")
        if isinstance(raw, str) and raw.strip():
            _ALLOWED_ORIGINS_CACHE = {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}
        else:
            _ALLOWED_ORIGINS_CACHE = set()
    except Exception:
        _ALLOWED_ORIGINS_CACHE = set()
    return _ALLOWED_ORIGINS_CACHE


# ============ 全局上下文加载（建议在 app.before_request 中调用） ============
def load_global_user():
    """从 session 加载当前用户到 Flask.g。"""
    user_id = session.get(SESSION_USER_KEY)
    g.current_user = get_user_by_id(user_id) if user_id else None
    g.is_admin = bool(g.current_user and g.current_user.get("role") in ("admin", "superadmin"))

    # CSRF 防护：POST/PUT/DELETE 请求必须带 AJAX 头或同源 Referer
    if request.method in ("POST", "PUT", "DELETE") and not request.path.startswith("/login"):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        referer = request.headers.get("Referer", "")
        host = request.headers.get("Host", "")
        # C-18：精确同源判定，禁止子串匹配（旧逻辑 "://host" in referer
        # 可被 "://host.evil.com" 绕过，造成 CSRF 防护失效）。
        same_origin = _referer_same_origin(referer, host)
        if not is_ajax and not same_origin:
            return jsonify({"code": 403, "msg": "CSRF校验失败：缺少请求头"})
    return None


# ============ 统一登录提示（不跳转登录页） ============
def need_login_response(next_url=None):
    """未登录时的统一处理：提示而非跳转到登录页。
    - AJAX / JSON 请求：返回 401 JSON（前端据此弹提示），并带 need_login 标记。
    - 普通页面请求：渲染 need_login 提示页（停留在当前 URL，不自动跳转登录页）。
    """
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"code": 401, "msg": "请先登录后查看", "need_login": True})
    return render_template("need_login.html", next_url=next_url or request.path), 401


# ============ 装饰器 ============
def login_required(f):
    """要求登录（未登录：AJAX 返回401 JSON，页面显示登录提示而非跳转）。"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = g.get("current_user")
        if not user:
            return need_login_response(request.path)
        return f(*args, **kwargs)
    return wrapper


def permission_required(perm: str):
    """要求拥有某项权限。"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = g.get("current_user")
            if not user:
                return need_login_response(request.path)
            if not has_permission(user, perm):
                raise PermissionDeniedException()
            return f(*args, **kwargs)
        return wrapper
    return decorator


def module_enabled_required(module_id: str):
    """要求模块已在 modules.yaml 中启用。"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_module_enabled(module_id):
                raise ModuleDisabledException(module_id)
            return f(*args, **kwargs)
        return wrapper
    return decorator
