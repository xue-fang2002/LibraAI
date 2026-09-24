import sqlite3
from flask import render_template, request, session
from modules.feedback import bp
from core.auth import login_required
from core.response import ok, fail
from core.exceptions import module_exception_guard
from core.db_base import get_db

TYPE_CHOICES = ("bug", "suggestion", "idea")
STATUS_CHOICES = ("pending", "acknowledged", "in_progress", "resolved", "closed")
PRIORITY_CHOICES = ("low", "medium", "high")

# ---------- 数据表（模块自建，幂等创建） ----------
#原先直接在模块 import 期执行 init_tables()：
#   ① 那时没有 app context，get_db() 的连接拿不到请求级 teardown 回收；
#   ② 连接用完从不 close；
#   ③ 失败被 `except: pass` 吞掉 —— 之后所有反馈操作一律 500，日志毫无线索。
# 改为在请求上下文内惰性建表（before_request），失败记日志，连接 finally 关闭。
_TABLES_READY = False


def init_tables():
    """幂等建表，返回是否成功。"""
    global _TABLES_READY
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL DEFAULT 'suggestion',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            module TEXT,
            attachment TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            dev_reply TEXT,
            dev_reply_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        conn.commit()
        _TABLES_READY = True
        return True
    except Exception as e:
        try:
            import logging as _logging
            _logging.getLogger(__name__).warning("feedback 建表失败: %s", e)
        except Exception:
            pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@bp.before_request
def _ensure_feedback_tables():
    """建表放在请求上下文内执行（模块 import 期没有 app context）。"""
    if not _TABLES_READY:
        init_tables()

# ---------- 辅助：当前用户 / 管理员判定 ----------
def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    # role 必须查出：管理员判定要用
    cur.execute(
        "SELECT id, account, name AS nickname, role FROM users WHERE id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None

def is_admin_user(user) -> bool:
    return bool(user and user.get("role") in ("admin", "superadmin"))

def get_item(item_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.*, u.account as author_account, u.name as author_nickname
        FROM feedback_items f
        JOIN users u ON f.user_id = u.id
        WHERE f.id = ?
        """,
        (item_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None

# ---------- 页面路由 ----------
@bp.route("/")
@module_exception_guard("feedback")
def index():
    """反馈中心主页：我的反馈列表 + 提交入口"""
    user = get_current_user()
    return render_template(
        "feedback/index.html",
        current_module_id="feedback",
        current_user=user,
    )

@bp.route("/admin")
@module_exception_guard("feedback")
@login_required
def admin_page():
    """管理全部反馈（仅管理员）"""
    user = get_current_user()
    if not is_admin_user(user):
        return "仅管理员可访问", 403
    return render_template(
        "feedback/admin.html",
        current_module_id="feedback",
        current_user=user,
    )

@bp.route("/<int:item_id>")
@module_exception_guard("feedback")
def detail(item_id):
    """反馈详情：作者本人或管理员可见"""
    user = get_current_user()
    item = get_item(item_id)
    if not item:
        return "反馈不存在", 404
    if not is_admin_user(user) and (not user or str(user.get("id")) != str(item.get("user_id"))):
        return "反馈不存在", 404
    is_admin = is_admin_user(user)
    return render_template(
        "feedback/detail.html",
        current_module_id="feedback",
        item=item,
        current_user=user,
        is_admin=is_admin,
    )

# ---------- JSON API ----------
@bp.route("/api/create", methods=["POST"])
@module_exception_guard("feedback")
@login_required
def api_create():
    """提交反馈（普通用户）"""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    ftype = data.get("type", "suggestion")
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    module = (data.get("module") or "").strip() or None
    if ftype not in TYPE_CHOICES:
        ftype = "suggestion"
    if not title or not content:
        return fail("标题和详细描述不能为空", code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO feedback_items (user_id, type, title, content, module) VALUES (?, ?, ?, ?, ?)",
        (user["id"], ftype, title, content, module),
    )
    conn.commit()
    return ok(data={"id": cur.lastrowid}, msg="反馈已提交，感谢！")

@bp.route("/api/list")
@module_exception_guard("feedback")
@login_required
def api_list():
    """我的反馈列表（仅本人）"""
    user = get_current_user()
    page = request.args.get("page", 1, type=int) or 1
    per_page = 15
    offset = (page - 1) * per_page
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM feedback_items WHERE user_id = ?", (user["id"],))
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT * FROM feedback_items WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user["id"], per_page, offset),
    )
    items = [dict(r) for r in cur.fetchall()]
    return ok(data={"items": items, "total": total, "page": page, "per_page": per_page})

@bp.route("/api/admin")
@module_exception_guard("feedback")
@login_required
def api_admin():
    """管理列表（仅管理员，支持 status/type/module 筛选 + 分页）"""
    user = get_current_user()
    if not is_admin_user(user):
        return fail("仅管理员可访问", code=403)
    page = request.args.get("page", 1, type=int) or 1
    per_page = 15
    offset = (page - 1) * per_page
    status = request.args.get("status", "").strip()
    ftype = request.args.get("type", "").strip()
    module = request.args.get("module", "").strip()
    params = []
    where = []
    if status:
        where.append("f.status = ?")
        params.append(status)
    if ftype:
        where.append("f.type = ?")
        params.append(ftype)
    if module:
        where.append("f.module = ?")
        params.append(module)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM feedback_items f {where_sql}", params)
    total = cur.fetchone()[0]
    # 待处理优先，其次按 id 倒序
    cur.execute(
        f"""
        SELECT f.*, u.account as author_account, u.name as author_nickname
        FROM feedback_items f
        JOIN users u ON f.user_id = u.id
        {where_sql}
        ORDER BY (CASE f.status WHEN 'pending' THEN 0 WHEN 'acknowledged' THEN 1
                                 WHEN 'in_progress' THEN 2 WHEN 'resolved' THEN 3 ELSE 4 END),
                 f.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    )
    items = [dict(r) for r in cur.fetchall()]
    return ok(data={"items": items, "total": total, "page": page, "per_page": per_page})

@bp.route("/api/<int:item_id>/reply", methods=["POST"])
@module_exception_guard("feedback")
@login_required
def api_reply(item_id):
    """开发者回复（仅管理员），回复后自动置为已受理"""
    user = get_current_user()
    if not is_admin_user(user):
        return fail("仅管理员可回复", code=403)
    item = get_item(item_id)
    if not item:
        return fail("反馈不存在", code=404)
    data = request.get_json(silent=True) or {}
    reply = (data.get("dev_reply") or "").strip()
    if not reply:
        return fail("回复内容不能为空", code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE feedback_items SET dev_reply=?, dev_reply_at=CURRENT_TIMESTAMP, status='acknowledged', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (reply, item_id),
    )
    conn.commit()
    return ok(msg="已回复")

@bp.route("/api/<int:item_id>/status", methods=["POST"])
@module_exception_guard("feedback")
@login_required
def api_status(item_id):
    """状态流转（仅管理员）"""
    user = get_current_user()
    if not is_admin_user(user):
        return fail("仅管理员可操作", code=403)
    item = get_item(item_id)
    if not item:
        return fail("反馈不存在", code=404)
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in STATUS_CHOICES:
        return fail("非法状态", code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE feedback_items SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, item_id),
    )
    conn.commit()
    return ok(msg="状态已更新")

@bp.route("/api/<int:item_id>/priority", methods=["POST"])
@module_exception_guard("feedback")
@login_required
def api_priority(item_id):
    """优先级调整（仅管理员）"""
    user = get_current_user()
    if not is_admin_user(user):
        return fail("仅管理员可操作", code=403)
    item = get_item(item_id)
    if not item:
        return fail("反馈不存在", code=404)
    data = request.get_json(silent=True) or {}
    pri = data.get("priority", "")
    if pri not in PRIORITY_CHOICES:
        return fail("非法优先级", code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE feedback_items SET priority=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (pri, item_id),
    )
    conn.commit()
    return ok(msg="优先级已更新")
