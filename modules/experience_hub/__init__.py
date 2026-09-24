from flask import Blueprint
import os

# 注意：本模块的静态资源**不在这里**，而是放在项目根 static/experience_hub/。
# 模板写的是 url_for('static', filename='experience_hub/style.css') —— 这是 **app 级**
# endpoint，永远指向项目根 static/，蓝图自己的 static_folder 根本用不上。
#删除了 modules/experience_hub/static/（Aug-24 的历史副本，从未被加载过，
# 且比现行版旧），连带移除下面两个参数，避免留下指向已删目录的悬空配置。
bp = Blueprint(
    "experience_hub",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

module_info = {
    "module_id": "experience_hub",
    "display_name": "笔记本",
    "icon": "💡",
    "route_prefix": "/experience",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "社区经验分享：留下你的解法，点亮别人的困境",
    "version": "1.0.0",
    "blueprint": bp,
}

# 经验社区的操作（发布/点赞/收藏/删除）仅要求登录即可，无需逐项授权
# （routes.py 路由只用 @login_required）。如需精细化控制，恢复下方声明并在对应路由加 @permission_required。
config_entries = []
ai_features = []

# 重要：注册路由
from . import routes  # noqa: E402