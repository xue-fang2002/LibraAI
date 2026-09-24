"""
工具箱统一入口（Hub）

设计原则：hub 收口
- 本模块只做「统一入口 + 分类归组 + 新拟态外壳」，不含任何业务功能；
- chart / office_tools / toolbox 三个既有模块的蓝图、路由、handler 全部保持原样，
  本模块通过链接/深链把用户导向它们，实现「侧栏只有一项工具箱、内部重新分类」；
- 后续 AI 工具助手也将挂在本模块下（Phase 3）。
"""
from flask import Blueprint
import os

bp = Blueprint(
    "toolbox_hub",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

module_info = {
    "module_id": "toolbox_hub",
    "display_name": "工具箱",
    "icon": "\U0001F9F0",
    "route_prefix": "/tools",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "工具箱统一入口：图表工具 / 文档处理 / 办公自动化 / 图片 / 文本 / 识别 / 系统",
    "version": "1.0.0",
    "blueprint": bp,
}

config_entries = []
PERMISSIONS = []
ai_features = []

from . import routes  # noqa: E402
