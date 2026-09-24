"""Book Library 图书管理模块。"""
import os
from flask import Blueprint

# ---- 路由前缀：/book ----
bp = Blueprint(
    "book_lib", __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
    static_url_path="/static/book_lib",
)

# ---- 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用） ----
# 配置入口：在「配置中心」聚合页中作为「图书馆」分组展示
config_entries = [
    {"name": "图书管理", "desc": "录入 / 编辑 / 删除 / 批量管理图书资料", "url": "/book/config"},
    {"name": "分类管理", "desc": "一级 / 二级分类的增删改与排序", "url": "/book/config#category"},
]

# 权限点：本模块拥有的权限，供用户管理页分组渲染 + db_base 聚合
PERMISSIONS = [
    ("file_upload", "上传文件"),
    ("book_add", "新增图书"),
    ("book_edit", "编辑图书"),
    ("book_delete", "删除图书"),
    ("book_batch_add", "批量新增图书"),
    ("book_batch_delete", "批量删除图书"),
    ("book_batch_move", "批量移动图书"),
    ("private_book", "私人图书"),
    ("c1_add", "新增一级分类"),
    ("c1_edit", "编辑一级分类"),
    ("c1_delete", "删除一级分类"),
    ("c1_move", "一级分类排序"),
    ("c2_add", "新增二级分类"),
    ("c2_edit", "编辑二级分类"),
    ("c2_delete", "删除二级分类"),
    ("c2_move", "二级分类排序"),
]

# AI 特性：本模块提供的 AI 能力（供「配置中心 - AI中心」分组展示）
ai_features = [
    {"key": "summary", "name": "自动摘要", "desc": "AI 读取图书后自动生成中文摘要"},
    {"key": "vector_search", "name": "语义检索", "desc": "基于 BGE 嵌入模型的 FAISS 向量检索"},
    {"key": "qa", "name": "智能问答", "desc": "读取多本书内容后回答用户问题"},
]

module_info = {
    "module_id": "book_lib",
    "display_name": "图书馆",
    "icon": "📚",
    "route_prefix": "/book",
    "url_default_endpoint": "index",
    "permission_required": None,  # 所有人可见，内部接口再做权限校验
    "description": "图书资料管理：浏览/搜索/借阅/笔记/收藏/分类管理",
    "version": "1.0.0",
    "blueprint": bp,
}

from . import routes  # noqa: E402

__all__ = ["module_info", "bp"]
