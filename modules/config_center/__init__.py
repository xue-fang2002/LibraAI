"""配置中心模块（管理员专用，侧边栏末位）。

作为全站「管理聚合页」：
- 图书馆（图书管理 / 分类管理）—— 由各业务模块自报 config_entries 聚合
- AI 中心 —— AI 配置与状态
- 用户与权限 —— 用户管理 / 操作日志

用户管理 / 权限 / 日志 / AI 配置等原先散落在 book_lib 的接口，统一收敛到本模块，
避免业务模块承担「平台级管理」职责。仅管理员可见（permission_required=config_access，
且不在 DEFAULT_PUBLIC_MODULES 中）。
"""

import os
from flask import Blueprint

bp = Blueprint(
    "config_center", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
    static_url_path="/static/config_center",
)

module_info = {
    "module_id": "config_center",
    "display_name": "配置中心",
    "icon": "⚙️",
    "route_prefix": "/config",
    "url_default_endpoint": "index",
    "permission_required": "config_access",  # 仅管理员可见 / 可访问
    "description": "全站管理聚合：图书管理 / AI 中心 / 用户与权限",
    "version": "1.0.0",
    "blueprint": bp,
}

# ---- 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用） ----
# 注意：config_center 的子页（用户/日志/AI）由 index.html 的静态「AI 中心 / 用户与权限」
# 分组直接渲染，避免与动态聚合出现重复入口。book_lib 的 config_entries 则通过动态聚合
# 展示为「图书馆」分组，验证「模块自报 + 统一聚合」这一去中心化机制。
config_entries = []
PERMISSIONS = [
    ("config_access", "访问配置中心"),
    ("user_manage", "用户与权限管理"),
    ("view_all_records", "查看所有笔记/读后感"),
]
ai_features = []

from . import routes  # noqa: E402
