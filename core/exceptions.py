"""全局异常处理与自定义业务异常。"""

import traceback
from functools import wraps
from flask import jsonify, request, current_app, g

from .response import ApiResponse


class AppException(Exception):
    """业务层自定义异常，可被全局处理器自动捕获并返回友好消息。"""

    def __init__(self, message: str, code: int = 400, data=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


class ModuleDisabledException(AppException):
    """访问已被禁用的模块时抛出。"""

    def __init__(self, module_id: str = ""):
        super().__init__(f"模块[{module_id}]已禁用", 404)


class PermissionDeniedException(AppException):
    def __init__(self, message: str = "权限不足"):
        super().__init__(message, 403)


class NotLoginException(AppException):
    def __init__(self, message: str = "请先登录"):
        super().__init__(message, 401)


def global_exception_handler(app):
    """注册 Flask 全局异常处理器。"""

    @app.errorhandler(AppException)
    def _handle_app_exception(e: AppException):
        # AJAX/JSON 请求返回 JSON
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ApiResponse.build(e.code, e.message, e.data)), e.code
        # 普通页面：交给上层处理（或简单返回文字）
        return e.message, e.code

    @app.errorhandler(404)
    def _handle_404(e):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ApiResponse.build(404, "资源不存在")), 404
        return "页面不存在", 404

    @app.errorhandler(403)
    def _handle_403(e):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ApiResponse.build(403, "权限不足")), 403
        return "权限不足", 403

    @app.errorhandler(Exception)
    def _handle_uncaught(e: Exception):
        # 单个模块异常不导致整个服务崩溃
        tb = traceback.format_exc()
        current_app.logger.error("[UncaughtException]\n%s", tb)
        try:
            user = getattr(g, "current_user", None)
            uid = user.get("id") if user else None
            # 尝试写操作日志（如果数据库模块可用）
            try:
                from core.db_base import add_log
                add_log(uid, "uncaught_exception", detail={
                    "path": request.path,
                    "method": request.method,
                    "err": str(e)[:500],
                })
            except Exception:
                pass
        except Exception:
            pass
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ApiResponse.build(500, "服务器内部错误", {
                "err": str(e) if current_app.debug else None
            })), 500
        return ("服务器内部错误" + (f": {str(e)}" if current_app.debug else "")), 500


def module_exception_guard(module_id: str):
    """
    模块路由装饰器：捕获该蓝图下的所有异常，保证单个模块故障不扩散。
    用法：
        @bp.route("/xxx")
        @module_exception_guard("book_lib")
        def xxx(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except AppException:
                raise
            except Exception as e:
                import traceback as _tb
                _tb.print_exc()
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    # 异常原文只在 debug 下外泄（与上面全局兜底第 82 行保持一致）。
                    # 生产环境把 str(e) 直接回给浏览器会泄露服务器绝对路径、
                    # 数据库表名/列名与依赖版本 —— 详情已由 print_exc 落日志。
                    try:
                        _debug = bool(current_app.debug)
                    except Exception:
                        _debug = False
                    return jsonify(ApiResponse.build(
                        500,
                        f"[{module_id}]模块异常" + (f": {str(e)}" if _debug else "，请查看服务端日志"),
                        {"err": str(e)} if _debug else None,
                    )), 500
                raise
        return wrapper
    return decorator
