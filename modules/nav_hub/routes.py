"""
网站导航模块 (nav_hub)
======================
- 公开访问：所有人可查看导航列表与分类
- 管理权限：需 nav_manage 权限方可增删改导航项与分类
- 风格：新拟态 Neumorphism + 轻度毛玻璃

数据库表（首次访问自动创建）：
  nav_categories  - 分类表
  nav_items       - 导航项表

注意：
  本模块假设 core.db_base 提供以下通用函数：
    - db_execute(sql, params=()) -> int (lastrowid / rowcount)
    - db_query(sql, params=()) -> list[dict]
    - db_query_one(sql, params=()) -> dict | None
  若实际项目 API 不同，请调整 routes.py 中的数据库调用。
"""

from flask import render_template, request, g
from modules.nav_hub import bp
from core.auth import login_required, permission_required, need_login_response
from core.response import ok, fail
from core.exceptions import module_exception_guard
from core.db_base import db_execute, db_query, db_query_one, has_permission

# ============================================================
# 辅助函数
# ============================================================

def _can_manage():
    """检查当前用户是否有导航管理权限（与 permission_required('nav_manage') 保持一致）"""
    user = getattr(g, 'current_user', None)
    if not user:
        return False
    # admin / superadmin 自动拥有全部权限；普通用户需显式拥有 nav_manage 权限
    return has_permission(user, 'nav_manage')


# ============================================================
# 数据库初始化（首次加载时自动建表）
# ============================================================

#导航首页是公开页面，原先每次访问 index() 都执行两条 DDL ——
# 匿名用户每刷一次就触发一次写锁，可被轻易放大。加进程内短路标志，只在首次真正建表。
_TABLES_READY = False


def _init_tables():
    """自动创建导航模块所需数据表（幂等，进程内只真正执行一次）"""
    global _TABLES_READY
    if _TABLES_READY:
        return
    db_execute("""
        CREATE TABLE IF NOT EXISTS nav_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS nav_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            type TEXT DEFAULT 'web',
            category_id INTEGER,
            description TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            is_hot INTEGER DEFAULT 0,
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES nav_categories(id)
        )
    """)
    _TABLES_READY = True


# ============================================================
# 页面路由
# ============================================================

@bp.route("/")
@module_exception_guard("nav_hub")
def index():
    """导航首页 - 公开访问"""
    _init_tables()
    return render_template("nav_hub/index.html",
                           current_module_id="nav_hub",
                           can_manage=_can_manage())


@bp.route("/manage/categories")
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def manage_categories():
    """分类管理页 - 需 nav_manage 权限"""
    return render_template("nav_hub/manage_categories.html",
                           current_module_id="nav_hub",
                           can_manage=True)


# ============================================================
# 分类 API
# ============================================================

@bp.route("/api/categories", methods=["GET"])
@module_exception_guard("nav_hub")
def api_categories_list():
    """获取所有分类（公开）"""
    rows = db_query(
        "SELECT id, name, icon, sort_order FROM nav_categories ORDER BY sort_order ASC, id ASC"
    )
    return ok(data={"list": rows})


@bp.route("/api/categories", methods=["POST"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_category_create():
    """创建分类（需权限）"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    icon = (data.get("icon") or "").strip()
    sort_order = data.get("sort_order", 0)

    if not name:
        return fail("分类名称不能为空", code=400)

    # 检查重名
    exist = db_query_one("SELECT id FROM nav_categories WHERE name = ?", (name,))
    if exist:
        return fail("分类名称已存在", code=400)

    last_id = db_execute(
        "INSERT INTO nav_categories (name, icon, sort_order) VALUES (?, ?, ?)",
        (name, icon, sort_order)
    )
    return ok(data={"id": last_id, "name": name, "icon": icon, "sort_order": sort_order})


@bp.route("/api/categories/<int:cid>", methods=["PUT"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_category_update(cid):
    """更新分类（需权限）"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    icon = (data.get("icon") or "").strip()
    sort_order = data.get("sort_order", 0)

    if not name:
        return fail("分类名称不能为空", code=400)

    # 检查重名（排除自己）
    exist = db_query_one(
        "SELECT id FROM nav_categories WHERE name = ? AND id != ?",
        (name, cid)
    )
    if exist:
        return fail("分类名称已存在", code=400)

    db_execute(
        "UPDATE nav_categories SET name = ?, icon = ?, sort_order = ? WHERE id = ?",
        (name, icon, sort_order, cid)
    )
    return ok(data={"id": cid, "name": name, "icon": icon, "sort_order": sort_order})


@bp.route("/api/categories/<int:cid>", methods=["DELETE"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_category_delete(cid):
    """删除分类（需权限）- 同时清空该分类下的导航项"""
    # 检查分类是否存在
    cat = db_query_one("SELECT id FROM nav_categories WHERE id = ?", (cid,))
    if not cat:
        return fail("分类不存在", code=404)

    # 删除该分类下的所有导航项
    db_execute("DELETE FROM nav_items WHERE category_id = ?", (cid,))
    # 删除分类
    db_execute("DELETE FROM nav_categories WHERE id = ?", (cid,))
    return ok(data={"id": cid})


# ============================================================
# 导航项 API
# ============================================================

@bp.route("/api/items", methods=["GET"])
@module_exception_guard("nav_hub")
def api_items_list():
    """获取导航项列表（公开）- 支持按分类筛选"""
    category_id = request.args.get("category_id", type=int)
    keyword = (request.args.get("keyword") or "").strip()

    sql = """
        SELECT i.id, i.name, i.url, i.type, i.category_id,
               i.description, i.sort_order, i.is_hot, i.tags,
               c.name as category_name, c.icon as category_icon
        FROM nav_items i
        LEFT JOIN nav_categories c ON i.category_id = c.id
        WHERE 1=1
    """
    params = []

    if category_id:
        sql += " AND i.category_id = ?"
        params.append(category_id)

    if keyword:
        sql += " AND (i.name LIKE ? OR i.description LIKE ? OR i.tags LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like])

    sql += " ORDER BY i.sort_order ASC, i.id ASC"

    rows = db_query(sql, tuple(params))
    return ok(data={"list": rows})


@bp.route("/api/items", methods=["POST"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_item_create():
    """创建导航项（需权限）"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    item_type = (data.get("type") or "web").strip().lower()
    category_id = data.get("category_id")
    description = (data.get("description") or "").strip()
    sort_order = data.get("sort_order", 0)
    is_hot = 1 if data.get("is_hot") else 0
    tags = (data.get("tags") or "").strip()

    if not name:
        return fail("名称不能为空", code=400)
    if not url:
        return fail("链接地址不能为空", code=400)
    if item_type not in ("web", "doc"):
        return fail("类型只能是 web 或 doc", code=400)

    # 检查分类是否存在
    if category_id:
        cat = db_query_one("SELECT id FROM nav_categories WHERE id = ?", (category_id,))
        if not cat:
            return fail("所属分类不存在", code=400)

    last_id = db_execute("""
        INSERT INTO nav_items (name, url, type, category_id, description, sort_order, is_hot, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, url, item_type, category_id, description, sort_order, is_hot, tags))

    return ok(data={
        "id": last_id, "name": name, "url": url, "type": item_type,
        "category_id": category_id, "description": description,
        "sort_order": sort_order, "is_hot": is_hot, "tags": tags
    })


@bp.route("/api/items/<int:iid>", methods=["PUT"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_item_update(iid):
    """更新导航项（需权限）"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    item_type = (data.get("type") or "web").strip().lower()
    category_id = data.get("category_id")
    description = (data.get("description") or "").strip()
    sort_order = data.get("sort_order", 0)
    is_hot = 1 if data.get("is_hot") else 0
    tags = (data.get("tags") or "").strip()

    if not name:
        return fail("名称不能为空", code=400)
    if not url:
        return fail("链接地址不能为空", code=400)
    if item_type not in ("web", "doc"):
        return fail("类型只能是 web 或 doc", code=400)

    # 检查分类是否存在
    if category_id:
        cat = db_query_one("SELECT id FROM nav_categories WHERE id = ?", (category_id,))
        if not cat:
            return fail("所属分类不存在", code=400)

    db_execute("""
        UPDATE nav_items
        SET name = ?, url = ?, type = ?, category_id = ?,
            description = ?, sort_order = ?, is_hot = ?, tags = ?,
            updated_at = datetime('now')
        WHERE id = ?
    """, (name, url, item_type, category_id, description, sort_order, is_hot, tags, iid))

    return ok(data={
        "id": iid, "name": name, "url": url, "type": item_type,
        "category_id": category_id, "description": description,
        "sort_order": sort_order, "is_hot": is_hot, "tags": tags
    })


@bp.route("/api/items/<int:iid>", methods=["DELETE"])
@module_exception_guard("nav_hub")
@login_required
@permission_required("nav_manage")
def api_item_delete(iid):
    """删除导航项（需权限）"""
    item = db_query_one("SELECT id FROM nav_items WHERE id = ?", (iid,))
    if not item:
        return fail("导航项不存在", code=404)

    db_execute("DELETE FROM nav_items WHERE id = ?", (iid,))
    return ok(data={"id": iid})
