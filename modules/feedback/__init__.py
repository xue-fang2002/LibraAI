from flask import Blueprint
import os

# 注意：本模块的静态资源放项目根 static/feedback/（模板用 url_for('static', filename='feedback/xxx')）。
# 全局悬浮按钮片段在 templates/feedback/widget.html，由 layout_base.html 用
# {% include 'feedback/widget.html' %} 全局引入（仅登录用户可见）。
bp = Blueprint(
    "feedback",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

module_info = {
    "module_id": "feedback",
    "display_name": "反馈中心",
    "icon": "📣",
    "route_prefix": "/feedback",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "收集使用问题与改进建议，开发者据此优化",
    "version": "1.0.0",
    "blueprint": bp,
}

config_entries = []
ai_features = []

from . import routes  # noqa: E402
