"""
办公工具模块（office_tools）

归属：已收口进「工具箱」(toolbox_hub, /tools) —— 侧边栏不再单独显示本模块，
但 /office_tools 路由保留可直连，Hub 页面通过深链进入。
收口是纯导航层行为（见 core.plugin_scan.HUB_MERGED_MODULES / HUB_PARENT_MODULE），
不影响本模块的路由与业务逻辑。
"""
from flask import Blueprint
import os

bp = Blueprint("office_tools", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"))

module_info = {
    "module_id": "office_tools",
    "display_name": "办公工具",
    "icon": "🛠️",
    "route_prefix": "/office_tools",
    "url_default_endpoint": "index",
    "description": "全能办公批量处理工具（文件夹/文件/Excel分表/重命名/转PDF等）",
    "version": "1.0.0",
    "blueprint": bp,
}

config_entries = []

from . import routes
