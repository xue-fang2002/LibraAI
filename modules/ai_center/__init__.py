"""
AI 中心模块（全局 AI 服务开关 / 能力聚合）。

设计：
- 这是一个「纯元数据 + 共享服务」模块：没有独立页面，也不在侧边栏出现
  （已在 core.plugin_scan.NON_NAV_MODULES 中排除）。
- 真正的 AI 能力通过 core.ai_interface 暴露给 book_lib / homepage 等业务模块，
  业务模块只依赖 core.ai_interface，不直接 import 本模块的 internal 实现，
  做到故障隔离：AI 不可用 / 模型未配置时，图书等基础功能不受影响。
- internal/ 内部实现采用「优雅降级」：模块可正常导入，未配置模型时各函数返回
  清晰的错误信息，而不是让整个应用崩溃。后续接入真实模型只需替换 internal 内实现。
"""

module_info = {
    "module_id": "ai_center",
    "display_name": "AI 中心",
    "icon": "🤖",
    "route_prefix": "/ai",
    "url_default_endpoint": "index",
    "permission_required": "ai_config",
    "description": "全局 AI 能力（摘要 / 检索 / 问答）开关与配置",
    "version": "1.0.0",
    "blueprint": None,  # 纯元数据模块：AI 能力经 core.ai_interface 暴露，无独立蓝图
}

# ---- 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用） ----
config_entries = []
PERMISSIONS = [
    ("ai_config", "AI配置管理"),
    ("ai_qa", "AI问答使用"),
]
ai_features = [
    {"key": "summary", "name": "自动摘要", "desc": "AI 读取图书后自动生成中文摘要"},
    {"key": "vector_search", "name": "语义检索", "desc": "基于 BGE 嵌入模型的 FAISS 向量检索"},
    {"key": "qa", "name": "智能问答", "desc": "读取多本书内容后回答用户问题"},
]
