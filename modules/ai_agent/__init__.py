"""AI 任务中心模块（ai_agent）。

定位：专门处理「问答以外」的任务 —— 自然语言描述需求，由 AI 选出能力、
生成计划，再按能力声明的执行方式（原地执行 / 带参跳转 / 只给链接）落地。

与首页 AI 问答的分工：
- 首页（homepage）：一问一答的流式对话
- 本模块：有输入、有参数、有产物的任务

文件划分（刻意拆细，避免重蹈 handlers.py 千行单文件的覆辙）：
  registry.py    能力注册表：连接器的注册与查询
  store.py       任务持久化：建表 / 状态机 / CRUD
  planner.py     意图路由与计划生成（LLM 调用都收敛在这里）
  routes.py      只放路由，业务逻辑一律下沉
  connectors/    每个业务模块一个连接器文件，声明「能做什么、怎么执行」
"""
from flask import Blueprint
import os

bp = Blueprint("ai_agent", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"))

module_info = {
    "module_id": "ai_agent",
    "display_name": "AI 任务",
    "icon": "⚡",
    "route_prefix": "/agent",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "用一句话下达任务：AI 自动选择工具箱/办公/图表里的能力并生成执行计划",
    "version": "0.1.0",
    "blueprint": bp,
}

# 本模块不额外声明权限点（沿用各业务模块自己的权限校验）
PERMISSIONS = []

config_entries = []

from . import routes   # noqa: E402

# 进程启动恢复：async 任务跑在进程内后台线程里，进程被杀（如服务器重启、
# 宿主 shell 崩溃）后线程消失但任务永远停在 running，前端会一直转圈且无法重跑。
# 模块加载在 create_app 插件扫描期执行，每次进程启动恰好一次 —— 在这里把
# 遗留的 running 任务统一标为 failed。表尚不存在时静默跳过。
from . import store as _store  # noqa: E402
_store.recover_orphan_running()
