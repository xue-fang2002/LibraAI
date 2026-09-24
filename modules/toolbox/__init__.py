"""
在线工具模块（toolbox）

归属：已收口进「工具箱」(toolbox_hub, /tools) —— 侧边栏不再单独显示本模块，
但 /toolbox 路由保留可直连，Hub 页面通过深链进入。
收口是纯导航层行为（见 core.plugin_scan.HUB_MERGED_MODULES / HUB_PARENT_MODULE），
不影响本模块的路由与业务逻辑。

注意：本模块除在线工具外，还承载 RPA 自动化框架（automation/），
被 workflow 模块与 ai_agent 复用，故外部引用较多。
"""
import os
from flask import Blueprint

bp = Blueprint("toolbox", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"))

module_info = {
    "module_id": "toolbox",
    # 显示名刻意不叫「工具箱」：那是 toolbox_hub（/tools，侧栏入口）的名字，
    # 两个模块同名会让配置中心 / 权限页 / 反馈分类里出现两个「工具箱」，无法分辨。
    # 本模块是「工具真正干活的地方」，故叫「在线工具」。
    "display_name": "在线工具",
    "icon": "🔧",
    "route_prefix": "/toolbox",
    "url_default_endpoint": "index",
    "permission_required": None,
    "description": "在线工具工作台：图片 / PDF / Office / 表格 / 文本 / 识别 / 计算换算",
    "version": "1.0.0",
    "blueprint": bp,
}
config_entries = []
PERMISSIONS = []
ai_features = []

from . import routes   # noqa: E402
