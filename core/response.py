"""统一 API 返回格式。"""

from typing import Any, Dict, List, Optional
from flask import jsonify


class ApiResponse:
    """标准响应封装：{ code, msg, data }"""

    @staticmethod
    def build(code: int, msg: str = "", data: Any = None, **extra) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": code, "msg": msg, "data": data}
        if extra:
            payload.update(extra)
        return payload


def ok(data: Any = None, msg: str = "success", **extra):
    """成功响应，code=200"""
    return jsonify(ApiResponse.build(200, msg, data, **extra))


def fail(msg: str = "error", code: int = 400, data: Any = None, **extra):
    """失败响应，默认 code=400"""
    return jsonify(ApiResponse.build(code, msg, data, **extra))


def paginate(items: List[Any], page: int = 1, per_page: int = 20, msg: str = "success"):
    """列表分页响应。"""
    total = len(items) if items else 0
    page = max(1, int(page))
    per_page = max(1, min(200, int(per_page)))
    total_pages = (total + per_page - 1) // per_page if total else 0
    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end] if items else []
    return jsonify(ApiResponse.build(200, msg, {
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }))
