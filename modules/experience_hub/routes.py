import json
import sqlite3
import threading
import time
from flask import render_template, request, jsonify, session, g
from modules.experience_hub import bp
from core.auth import login_required, need_login_response
from core.response import ok, fail
from core.exceptions import module_exception_guard
from core.db_base import get_db, add_document_chunk, delete_document_chunks, clear_vector_mappings


# ---------- 发布幂等：并发同内容防重复 ----------
# 同一用户 + 完全相同 (标题/分类/正文) 的发布，在窗口期内只允许落库一次。
# 仅 SELECT 再 INSERT 在并发下存在竞态（多请求同时查到"无重复"然后都插入），
# 故用「按内容哈希分桶的锁」把相同 payload 串行化：第一个请求插入并记下 id，
# 其余请求在锁内复查时即可命中刚提交的记录，直接返回已有 id，不再新建。
_CREATE_LOCK_GUARD = threading.Lock()
_CREATE_LOCKS = {}        # key -> threading.Lock
_CREATE_LOCK_TS = {}     # key -> 最近一次访问时间戳（用于过期清理，避免无限增长）
_CREATE_DEDUP_WINDOW = 10  # 秒：慢速（非并发）的连点/重试也在此窗口内去重


def _acquire_create_lock(key):
    """获取（必要时创建）该 payload 对应的排他锁，顺带清理过期桶。"""
    now = time.time()
    with _CREATE_LOCK_GUARD:
        # 清理超过窗口 5 倍（50s）未访问的桶，防止内存随发帖量无限增长
        expired = [k for k, t in _CREATE_LOCK_TS.items() if now - t > _CREATE_DEDUP_WINDOW * 5]
        for k in expired:
            _CREATE_LOCKS.pop(k, None)
            _CREATE_LOCK_TS.pop(k, None)
        if key not in _CREATE_LOCKS:
            _CREATE_LOCKS[key] = threading.Lock()
        _CREATE_LOCK_TS[key] = now
        return _CREATE_LOCKS[key]

# ---------- 辅助函数：初始化数据表（首次运行自动创建） ----------
def _ensure_column(cursor, table: str, column: str, col_def: str):
    """幂等加列。

    刻意不复用 core.db_base._add_column_if_missing：experience_posts 是经验社区自建表，
    不在 core 的 init_db 里创建。若在表还不存在时于 init_db 中调用该函数，
    PRAGMA table_info 返回空列表会误判"字段缺失"并执行 ALTER，进而抛错中断整个 init_db。
    因此加列只在本地 init_tables 里、CREATE TABLE 之后执行，表必然已存在。
    """
    try:
        cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return
    if column not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except Exception:
            pass


def init_tables():
    conn = get_db()
    cursor = conn.cursor()
    # 经验主表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS experience_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            likes INTEGER DEFAULT 0,
            collects INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # 用户点赞关联表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_likes (
            user_id INTEGER,
            post_id INTEGER,
            PRIMARY KEY (user_id, post_id)
        )
    """)
    # 用户收藏关联表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_collects (
            user_id INTEGER,
            post_id INTEGER,
            PRIMARY KEY (user_id, post_id)
        )
    """)
    # is_public：1=公开（默认，存量老帖自动继承为公开），0=私有（仅作者本人可见）
    _ensure_column(cursor, "experience_posts", "is_public", "INTEGER DEFAULT 1")
    conn.commit()

# 尝试初始化（忽略已存在）
try:
    init_tables()
except Exception:
    pass

# ---------- 辅助：获取当前用户信息 ----------
def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    # role 必须查出来：可见性判定要用它识别管理员（is_admin_user）。
    # 早期这行只查 id/account/name，导致 HTTP 层永远拿不到 role，
    # 管理员在页面上被当成普通用户，看不到别人的私有帖。
    cur.execute(
        "SELECT id, account, name AS nickname, role FROM users WHERE id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None

def get_post_detail(post_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.*, u.account as author_account, u.name as author_nickname
        FROM experience_posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    """, (post_id,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(row)


# ---------- 可见性判定（公开 / 私有） ----------
def is_private_post(post) -> bool:
    """是否为私有帖。

    只有显式 is_public=0 才算私有；NULL / 1 一律按公开处理。
    加列时带 DEFAULT 1，SQLite 对存量行读取时返回该默认值，所以老帖天然是公开的；
    NULL 兜底为公开，是为了兼容任何手工改库留下的空值，符合「默认公开」的产品语义。
    """
    if not post:
        return False
    v = post.get("is_public")
    return v is not None and int(v) == 0


def is_admin_user(user) -> bool:
    """是否为管理员。与 core.auth.load_global_user 的判定保持一致（admin / superadmin）。"""
    return bool(user and user.get("role") in ("admin", "superadmin"))


def post_visible_to(post, user) -> bool:
    """经验帖对当前用户是否可见 —— 全模块唯一的可见性判定入口。

    私有帖仅**作者本人**与**管理员**可见；其他登录用户和未登录访客一律不可见。

    列表、详情、相似经验召回、热门标签、AI 问答都必须调用本函数，
    否则会出现「页面上藏起来了、AI 问答里照样能读出来」的泄漏。

    ⚠️ 注意：本函数只负责 Python 侧的逐条判定。列表与标签的 SQL 层
    （api_list / api_hot_tags）另有可见性条件，改这里的规则时必须同步改那两处，
    否则管理员会出现「详情页能打开、列表里却搜不到」的错位。
    """
    if not post:
        return False
    if not is_private_post(post):
        return True
    if not user:
        return False
    if is_admin_user(user):
        return True
    uid = user.get("id")
    return uid is not None and str(uid) == str(post.get("user_id"))


# ---------- 经验帖向量索引（让「保守思维」全局问答也能检索经验内容） ----------
# 每篇帖用独立命名空间 book_id = "exp_<id>"，避免 query() 按 book_id 去重时只回一篇。
EXP_NS_PREFIX = "exp_"

def _post_namespace(post_id):
    return f"{EXP_NS_PREFIX}{post_id}"

def index_post(post):
    """将一篇经验帖索引进全局向量库（保守问答可检索）。任何异常静默，不影响主流程。

    返回 bool：成功写入向量与映射返回 True；正文为空 / 向量化失败 / 抛异常均返回 False。
    返回值是给 reindex_all_posts() 统计成功失败用的；单篇发帖/编辑的调用方忽略即可。
    """
    try:
        from modules.ai_center.internal import vector_store, embedder
        ns = _post_namespace(post["id"])
        # 编辑场景先清旧索引，避免脏数据/重复
        clear_vector_mappings(ns)
        delete_document_chunks(ns)
        text = "\n".join(filter(None, [
            post.get("title") or "",
            post.get("tags") or "",
            post.get("content") or "",
        ])).strip()
        if not text:
            return False
        add_document_chunk(ns, 0, text)          # 始终存正文（重建索引的源头，先于向量化）
        vecs = embedder.encode([text])
        if vecs is None or len(vecs) == 0:
            return False
        vector_store.store(ns, [text], vecs)     # 写向量 + 映射
        return True
    except Exception as e:
        print(f"⚠️ 经验帖索引失败(post={post.get('id')}): {e}")
        return False

def unindex_post(post_id):
    """删除一篇经验帖的向量索引。"""
    try:
        ns = _post_namespace(post_id)
        clear_vector_mappings(ns)
        delete_document_chunks(ns)
    except Exception as e:
        print(f"⚠️ 经验帖反索引失败(post={post_id}): {e}")

def reindex_all_posts():
    """把全部经验帖按当前嵌入模型重新灌入向量索引，返回 (总数, 成功数, 失败数)。

    为什么需要它：
    经验帖与图书**共用同一套** faiss 索引和 ai_vector_mappings 表，只是 book_id 用
    "exp_<id>" 命名空间区分。而配置中心「全量重建」会调 reset_index() 清空该 mode 的
    **全部**映射（clear_vector_mappings 只按 mode 过滤，不过滤 book_id），随后又只
    遍历 books 表重建图书 —— 经验帖不在 books 表里，于是向量被清空且无人补回，
    保守问答从此检索不到任何经验内容，且没有任何报错提示。

    按「谁清空谁负责补回」的原则，全量重建后调用本函数即可恢复。

    实现要点：
    - 复用 index_post()，与单篇发帖/编辑走完全相同的「清旧 → 存 chunks → 向量化 → 写映射」
      链路，不另起一套索引逻辑，避免两侧行为漂移。
    - 单篇失败不影响其余帖子（index_post 内部吞异常并返回 False）。
    - 返回三元组，供调用方（配置中心重建任务 / 后台接口）写日志与回显。

    返回：(total, ok, failed)
    """
    total = ok = failed = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM experience_posts ORDER BY id")
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"⚠️ 经验帖全量重建：读取帖子列表失败：{e}")
        return 0, 0, 0
    for pid in ids:
        post = get_post_detail(pid)
        if not post:
            failed += 1
            continue
        total += 1
        if index_post(post):
            ok += 1
        else:
            failed += 1
    print(f"🔄 经验帖全量重建完成：共 {total} 篇，成功 {ok}，失败 {failed}")
    return total, ok, failed

# ---------- 页面路由（公开可看，登录可写） ----------
@bp.route("/")
@module_exception_guard("experience_hub")
def index():
    """首页：经验流 + 侧边栏"""
    user = get_current_user()
    return render_template(
        "experience_hub/index.html",
        current_module_id="experience_hub",
        current_user=user
    )

@bp.route("/<int:post_id>")
@module_exception_guard("experience_hub")
def detail(post_id):
    """详情页"""
    post = get_post_detail(post_id)
    if not post:
        return "经验不存在", 404
    user = get_current_user()
    # 私有帖仅作者本人可见。对无权限者一律返回 404 而非 403：
    # 403 等于告诉对方「这篇帖子确实存在」，本身就是信息泄漏。
    if not post_visible_to(post, user):
        return "经验不存在", 404
    # 检查当前用户是否已点赞/收藏（前端用于高亮按钮）
    is_liked = False
    is_collected = False
    if user:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM user_likes WHERE user_id=? AND post_id=?", (user['id'], post_id))
        is_liked = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM user_collects WHERE user_id=? AND post_id=?", (user['id'], post_id))
        is_collected = cur.fetchone() is not None

    return render_template(
        "experience_hub/detail.html",
        current_module_id="experience_hub",
        post=post,
        current_user=user,
        is_liked=is_liked,
        is_collected=is_collected
    )

@bp.route("/new")
@module_exception_guard("experience_hub")
@login_required
def new_post():
    """发布页面"""
    return render_template(
        "experience_hub/form.html",
        current_module_id="experience_hub",
        post=None,
        is_edit=False
    )

@bp.route("/<int:post_id>/edit")
@module_exception_guard("experience_hub")
@login_required
def edit_post(post_id):
    """编辑页面"""
    user = get_current_user()
    post = get_post_detail(post_id)
    if not post:
        return "经验不存在", 404
    if post['user_id'] != user['id']:
        return "无权编辑此内容", 403
    return render_template(
        "experience_hub/form.html",
        current_module_id="experience_hub",
        post=post,
        is_edit=True
    )

# ---------- JSON API（数据接口） ----------
@bp.route("/api/list")
@module_exception_guard("experience_hub")
def api_list():
    """获取经验列表（公开），支持分类/标签筛选 & 分页"""
    page = request.args.get('page', 1, type=int) or 1
    per_page = 10
    offset = (page - 1) * per_page
    category = request.args.get('category', '').strip()
    tag = request.args.get('tag', '').strip()

    conn = get_db()
    cur = conn.cursor()
    params = []
    where_clauses = []

    if category:
        where_clauses.append("p.category = ?")
        params.append(category)
    if tag:
        where_clauses.append("p.tags LIKE ?")
        params.append(f"%{tag}%")

    # 可见性：私有帖仅作者本人与管理员可见。
    # COALESCE 兜底 is_public 为 NULL 的老数据（按公开处理，与 is_private_post 保持一致）。
    # 注意 user_id 必须带表前缀 p. —— 下面 data_sql JOIN 了 users，两表都有 user_id，
    # 不加前缀 SQLite 会报 ambiguous column name。
    # 管理员不加任何可见性条件（可见全部），与 post_visible_to 的判定保持一致。
    user = get_current_user()
    if is_admin_user(user):
        pass
    elif user:
        where_clauses.append("(COALESCE(p.is_public, 1) = 1 OR p.user_id = ?)")
        params.append(user["id"])
    else:
        where_clauses.append("COALESCE(p.is_public, 1) = 1")

    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = "WHERE " + where_sql

    # 查询总数
    count_sql = f"SELECT COUNT(*) FROM experience_posts p {where_sql}"
    cur.execute(count_sql, params)
    total = cur.fetchone()[0]

    # 查询数据（按收藏数降序排序，弱化时间，突出优质内容）
    data_sql = f"""
        SELECT p.*, u.account as author_account, u.name as author_nickname
        FROM experience_posts p
        JOIN users u ON p.user_id = u.id
        {where_sql}
        ORDER BY collects DESC, likes DESC, id DESC
        LIMIT ? OFFSET ?
    """
    cur.execute(data_sql, params + [per_page, offset])
    rows = cur.fetchall()
    posts = [dict(row) for row in rows]

    # 附加当前用户的互动状态（若已登录）
    if user:
        user_id = user['id']
        ids = [p['id'] for p in posts]
        if ids:
            placeholders = ','.join(['?'] * len(ids))
            cur.execute(f"SELECT post_id FROM user_likes WHERE user_id=? AND post_id IN ({placeholders})", [user_id] + ids)
            liked_set = {row[0] for row in cur.fetchall()}
            cur.execute(f"SELECT post_id FROM user_collects WHERE user_id=? AND post_id IN ({placeholders})", [user_id] + ids)
            collect_set = {row[0] for row in cur.fetchall()}
            for p in posts:
                p['is_liked'] = p['id'] in liked_set
                p['is_collected'] = p['id'] in collect_set
    else:
        for p in posts:
            p['is_liked'] = False
            p['is_collected'] = False

    return ok(data={
        "posts": posts,
        "total": total,
        "page": page,
        "per_page": per_page
    })

@bp.route("/api/create", methods=["POST"])
@module_exception_guard("experience_hub")
@login_required
def api_create():
    """发布新经验"""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    category = data.get('category', '').strip()
    content = data.get('content', '').strip()
    tags = data.get('tags', '').strip()
    # 未传时默认公开；前端 checkbox 传布尔值，这里统一落库成 1/0
    is_public = 1 if data.get('is_public', True) else 0

    if not title or not content or not category:
        return fail("标题、分类和内容不能为空", code=400)

    conn = get_db()
    cursor = conn.cursor()

    # 幂等防护：同一用户、完全相同的 标题/分类/正文，窗口期内只认第一份，
    # 避免「提交卡顿后连点 / 多标签页 / 网络重试」产生多条一模一样的经验贴。
    # 用按内容分桶的锁把相同 payload 串行化，消除 SELECT-再-INSERT 的并发竞态。
    dedup_key = (user['id'], title, category, content)
    with _acquire_create_lock(dedup_key):
        cursor.execute(
            "SELECT id FROM experience_posts "
            "WHERE user_id=? AND title=? AND category=? AND content=? "
            "AND created_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT 1",
            (user['id'], title, category, content, f"-{_CREATE_DEDUP_WINDOW} seconds")
        )
        dup = cursor.fetchone()
        if dup:
            return ok(data={"id": dup['id']}, msg="已发布，请勿重复提交")

        cursor.execute(
            "INSERT INTO experience_posts (user_id, title, category, content, tags, is_public) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user['id'], title, category, content, tags, is_public)
        )
        conn.commit()
    # 同步索引进全局向量库，使「保守思维」问答可检索本帖
    post = get_post_detail(cursor.lastrowid)
    if post:
        index_post(post)
    return ok(data={"id": cursor.lastrowid}, msg="发布成功！")

@bp.route("/api/<int:post_id>/update", methods=["PUT"])
@module_exception_guard("experience_hub")
@login_required
def api_update(post_id):
    """更新经验"""
    user = get_current_user()
    post = get_post_detail(post_id)
    if not post or post['user_id'] != user['id']:
        return fail("无权修改或内容不存在", code=403)

    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    category = data.get('category', '').strip()
    content = data.get('content', '').strip()
    tags = data.get('tags', '').strip()

    # 未传时默认公开；前端 checkbox 传布尔值，这里统一落库成 1/0
    is_public = 1 if data.get('is_public', True) else 0

    if not title or not content or not category:
        return fail("标题、分类和内容不能为空", code=400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE experience_posts SET title=?, category=?, content=?, tags=?, is_public=? WHERE id=?",
        (title, category, content, tags, is_public, post_id)
    )
    conn.commit()
    # 同步更新索引，使「保守思维」问答检索到最新内容
    post = get_post_detail(post_id)
    if post:
        index_post(post)
    return ok(data={"id": post_id}, msg="更新成功！")

@bp.route("/api/<int:post_id>/like", methods=["POST"])
@module_exception_guard("experience_hub")
@login_required
def api_like(post_id):
    """点赞/取消点赞（切换，事务化防并发计数漂移）"""
    user = get_current_user()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM user_likes WHERE user_id=? AND post_id=?", (user['id'], post_id))
        exists = cur.fetchone()
        if exists:
            cur.execute("DELETE FROM user_likes WHERE user_id=? AND post_id=?", (user['id'], post_id))
            cur.execute("UPDATE experience_posts SET likes = MAX(0, likes - 1) WHERE id=?", (post_id,))
            action, msg = "unliked", "已取消点赞"
        else:
            cur.execute("INSERT OR IGNORE INTO user_likes (user_id, post_id) VALUES (?, ?)", (user['id'], post_id))
            if cur.rowcount > 0:
                cur.execute("UPDATE experience_posts SET likes = likes + 1 WHERE id=?", (post_id,))
            action, msg = "liked", "点赞成功！"
        conn.commit()
    except Exception:
        conn.rollback()
        return fail("操作失败，请重试", code=500)
    finally:
        conn.close()
    return ok(data={"action": action, "likes": get_post_detail(post_id)['likes']}, msg=msg)

@bp.route("/api/<int:post_id>/collect", methods=["POST"])
@module_exception_guard("experience_hub")
@login_required
def api_collect(post_id):
    """收藏/取消收藏（切换，事务化防并发计数漂移）"""
    user = get_current_user()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM user_collects WHERE user_id=? AND post_id=?", (user['id'], post_id))
        exists = cur.fetchone()
        if exists:
            cur.execute("DELETE FROM user_collects WHERE user_id=? AND post_id=?", (user['id'], post_id))
            cur.execute("UPDATE experience_posts SET collects = MAX(0, collects - 1) WHERE id=?", (post_id,))
            action, msg = "uncollected", "已取消收藏"
        else:
            cur.execute("INSERT OR IGNORE INTO user_collects (user_id, post_id) VALUES (?, ?)", (user['id'], post_id))
            if cur.rowcount > 0:
                cur.execute("UPDATE experience_posts SET collects = collects + 1 WHERE id=?", (post_id,))
            action, msg = "collected", "收藏成功！"
        conn.commit()
    except Exception:
        conn.rollback()
        return fail("操作失败，请重试", code=500)
    finally:
        conn.close()
    return ok(data={"action": action, "collects": get_post_detail(post_id)['collects']}, msg=msg)

@bp.route("/api/<int:post_id>/delete", methods=["DELETE"])
@module_exception_guard("experience_hub")
@login_required
def api_delete(post_id):
    """删除经验（仅作者本人）"""
    user = get_current_user()
    post = get_post_detail(post_id)
    if not post or post['user_id'] != user['id']:
        return fail("无权删除或内容不存在", code=403)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM experience_posts WHERE id=?", (post_id,))
    cur.execute("DELETE FROM user_likes WHERE post_id=?", (post_id,))
    cur.execute("DELETE FROM user_collects WHERE post_id=?", (post_id,))
    conn.commit()
    # 同步移除索引
    unindex_post(post_id)
    return ok(msg="删除成功！")

@bp.route("/api/hot_tags")
@module_exception_guard("experience_hub")
def api_hot_tags():
    """获取热门标签（用于侧边栏）

    只统计当前用户可见的帖子（管理员可见全部，故统计全部）。
    标签名本身也是内容 —— 若把私有帖的标签一起统计，
    陌生人从标签云就能反推出私人帖子的主题和数量。
    """
    user = get_current_user()
    conn = get_db()
    cur = conn.cursor()
    if is_admin_user(user):
        cur.execute("SELECT tags FROM experience_posts")
    elif user:
        cur.execute(
            "SELECT tags FROM experience_posts "
            "WHERE COALESCE(is_public, 1) = 1 OR user_id = ?",
            (user["id"],)
        )
    else:
        cur.execute("SELECT tags FROM experience_posts WHERE COALESCE(is_public, 1) = 1")
    rows = cur.fetchall()
    tag_count = {}
    for row in rows:
        tags = row[0] or ""
        for t in tags.split(','):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)[:10]
    return ok(data=[{"name": k, "count": v} for k, v in sorted_tags])


@bp.route("/api/reindex", methods=["POST"])
@login_required
@module_exception_guard("experience_hub")
def api_reindex():
    """重建全部经验帖的向量索引（仅管理员）。

    正常发帖 / 编辑 / 删除会自动同步索引，本接口只在两类场景需要用：
    1) 配置中心执行「全量重建」后 —— 共享索引被整体清空，经验帖需重新灌入
       （全量重建内部已自动调用 reindex_all_posts，此按钮是给「单独重刷」用的）；
    2) 更换嵌入模型、或怀疑经验帖索引异常时，按当前模型全量重刷一遍。

    只重建经验帖，不碰图书索引 —— 图书全量重建耗时长，没必要为了经验帖陪跑。
    """
    u = g.get("current_user")
    if not u or u.get("role") not in ("admin", "superadmin"):
        return fail("仅管理员可重建经验索引", code=403)

    total, ok_cnt, failed = reindex_all_posts()
    if total == 0:
        return ok(data={"total": 0, "ok": 0, "failed": 0}, msg="暂无经验帖需要索引")
    msg = f"已重建 {ok_cnt}/{total} 篇经验帖索引" + (f"，失败 {failed} 篇" if failed else "")
    return ok(data={"total": total, "ok": ok_cnt, "failed": failed}, msg=msg)


@bp.route("/api/<int:post_id>/related")
@module_exception_guard("experience_hub")
def api_related(post_id):
    """相似经验：基于向量相似度，在 exp_ 命名空间内召回 top-N（纯检索，不依赖 LLM）。"""
    user = get_current_user()
    post = get_post_detail(post_id)
    if not post:
        return fail("经验不存在", code=404)
    # 源帖若为别人的私有帖，连"它存在"这一点都不该被探测到
    if not post_visible_to(post, user):
        return fail("经验不存在", code=404)
    try:
        from modules.ai_center.internal import vector_store, embedder
        text = "\n".join(filter(None, [
            post.get("title") or "",
            post.get("tags") or "",
            post.get("content") or "",
        ])).strip()
        if not text:
            return ok(data={"posts": []})
        vecs = embedder.encode([text])
        if vecs is None or len(vecs) == 0:
            return ok(data={"posts": []})
        vec = vecs[0]
        # 跨全部命名空间检索，再筛出 exp_ 命名空间（即经验帖）
        hits = vector_store.query(None, vec, top_k=12, use_parent=False)
        seen = set()
        out = []
        for h in hits:
            ns = h.get("book_id") or ""
            if not ns.startswith("exp_"):
                continue
            try:
                pid = int(ns.split("_", 1)[1])
            except Exception:
                continue
            if pid == post_id or pid in seen:
                continue
            seen.add(pid)
            p = get_post_detail(pid)
            if not p:
                continue
            # 向量召回只认语义相似度，天然会跨过私密边界，
            # 必须在这里按可见性过滤，否则私有帖的标题+摘要会被陌生人看到
            if not post_visible_to(p, user):
                continue
            excerpt = p.get("content") or ""
            excerpt = (excerpt[:90] + "…") if len(excerpt) > 90 else excerpt
            out.append({
                "id": p["id"],
                "title": p.get("title"),
                "category": p.get("category"),
                "author": p.get("author_nickname") or p.get("author_account"),
                "excerpt": excerpt,
                "score": round(float(h.get("score", 0.0)), 4),
            })
            if len(out) >= 5:
                break
        return ok(data={"posts": out})
    except Exception as e:
        print("⚠️ 相似经验召回失败(post=%s): %s" % (post_id, e))
        return ok(data={"posts": []})