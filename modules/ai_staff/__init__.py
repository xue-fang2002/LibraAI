"""AI 员工模块（ai_staff）。

定位：卡片墙 + 可扩展的对话式 AI 员工。
首页 `/staff/` 是一面员工卡片墙（配置驱动，见 staffs.py）；
点卡片进 `/staff/<id>` 与该员工对话。

首个员工是「运维助手」——能读项目文件、检索代码、只读查库、分析日志，
并给出带证据（文件路径 + 行号 / 真实 SQL 结果）的结论。

与相邻模块的分工：
- homepage（AI 问答）：基于已索引资料的文档问答
- ai_agent（AI 任务）：一句话触发工具箱里某个具体能力，单轮执行
- 本模块：多轮「提问 → 查 → 再查 → 总结」的排查链路

⚠️ 铁律：跨模块调用（ai_center 的 LLM、db_base 的日志）一律**函数内惰性 import**，
绝不顶层 import 兄弟模块包，否则会触发 plugin_scan 的 reload 陷阱导致路由全丢。

文件划分：
  staffs.py  员工花名册（配置化：新增员工只改 yaml，不动模板/路由）
  guard.py   安全护栏：路径白名单 / SQL 只读校验 / 审计
  tools.py   只读工具实现（列目录 / 读文件 / 代码检索 / 查库 / 日志）
  engine.py  ReAct 编排（唯一调 LLM 处）+ 任务状态机（内存）
  routes.py  只放路由与 SSE
"""
from flask import Blueprint
import os

bp = Blueprint(
    "ai_staff",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

module_info = {
    "module_id": "ai_staff",
    "display_name": "AI 员工",
    "icon": "\U0001F9D1",
    "route_prefix": "/staff",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "卡片墙式 AI 员工：点卡片进入对话，首个员工「运维助手」可检索代码、只读查库、分析日志",
    "version": "0.1.0",
    "blueprint": bp,
}

# 本模块不额外声明权限点：可见性由 modules.yaml 的 public + module_access（默认 admin）控制
PERMISSIONS = []
config_entries = []
ai_features = []

from . import routes  # noqa: E402
