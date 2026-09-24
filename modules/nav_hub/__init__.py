from flask import Blueprint
import os

bp = Blueprint("nav_hub", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"))

module_info = {
    "module_id": "nav_hub",
    "display_name": "网站导航",
    "icon": "🧭",
    "route_prefix": "/nav_hub",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "网站导航中心，支持分类管理与链接收藏",
    "version": "1.0.0",
    "blueprint": bp,
}

# 权限声明（管理导航需要此权限）
PERMISSIONS = [
    ("nav_manage", "导航管理"),
]

# 配置中心入口（可选）
config_entries = []

from . import routes   # noqa: E402
