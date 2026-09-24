"""
图表工具模块（Chart Module）
提供 Excel/CSV 上传、多类型图表可视化功能

归属：已收口进「工具箱」(toolbox_hub, /tools) —— 侧边栏不再单独显示本模块，
但 /chart 路由保留可直连，Hub 页面通过深链进入。
收口是纯导航层行为（见 core.plugin_scan.HUB_MERGED_MODULES / HUB_PARENT_MODULE），
不影响本模块的路由与业务逻辑。
"""
from flask import Blueprint

# 创建 Blueprint 对象
bp = Blueprint(
    'chart',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/chart'
)

# 模块元信息（供菜单生成和路由注册使用）
module_info = {
    'module_id': 'chart',
    'display_name': '图表工具',
    'icon': '📊',
    'route_prefix': '/chart',
    'url_default_endpoint': 'index',
    'blueprint': bp,   # <--- 关键：必须显式指定 blueprint 对象
}

# 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用）
config_entries = []
PERMISSIONS = []
ai_features = []

# 导入路由（必须在 blueprint 定义之后）
from . import routes