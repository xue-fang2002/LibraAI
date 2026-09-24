"""Homepage 首页模块（AI智能问答）。"""
import os
from flask import Blueprint

# ---- 路由前缀：/home （首页模块） ----
# 注意：本模块的静态资源走项目根 static/{css,js,vendor}/，模板用的是
# url_for('static', ...)（app 级 endpoint），蓝图自己的 static_folder 用不上。
#删除了空的 modules/homepage/static/，连带移除下面两个参数。
bp = Blueprint(
    "homepage", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

# ---- 模块元数据（供 core.plugin_scan 读取） ----
module_info = {
    "module_id": "homepage",
    "display_name": "AI问答",
    "icon": "💬",
    "route_prefix": "/home",
    "url_default_endpoint": "index",
    "permission_required": None,  # 所有人可见
    "description": "首页：全局AI智能问答模块",
    "version": "1.0.0",
    "blueprint": bp,
}

# 导入路由模块（让装饰器生效）
from . import routes  # noqa: E402  (延后导入避免循环)

# ---- 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用） ----
config_entries = []
PERMISSIONS = []
ai_features = [
    {"key": "global_chat", "name": "全局智能问答", "desc": "首页 AI 问答，不限具体图书"},
]

__all__ = ["module_info", "bp"]
