"""
工作自动化流程（私有 RPA）

设计：
- 本模块是「工作自动化」的**独立左侧导航入口**，带权限控制；
- 引擎（流程发现 / 运行 / SSE 进度）全部复用 modules.toolbox.automation，
  本模块不复制任何引擎代码，只在运行时懒调用（避免跨模块 import 触发 reload 陷阱）；
- 私有流程放在 modules.toolbox/automation/flows/（已被 .gitignore 忽略），
  后续新增流程只需在该目录放一个 .py，本模块自动发现、无需改代码。
"""
from flask import Blueprint
import os

bp = Blueprint(
    "workflow",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)

module_info = {
    "module_id": "workflow",
    "display_name": "工作自动化",
    "icon": "\U0001F3ED",
    "route_prefix": "/workflow",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "工作自动化（RPA）：把重复的页面操作脚本化并批量执行；流程文件放在 modules/toolbox/automation/flows/ 下（仓库只提供框架与示例）",
    "version": "1.0.0",
    "blueprint": bp,
}

config_entries = []
PERMISSIONS = []
ai_features = []

from . import routes  # noqa: E402
