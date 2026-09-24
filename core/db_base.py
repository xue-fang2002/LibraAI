"""
数据库基础封装。
"""

import os
import secrets
import sqlite3
import time
import json
import copy
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from werkzeug.security import generate_password_hash, check_password_hash

from .config_loader import get_config
from .logger import get_logger

logger = get_logger("db_base")

# flask.g 连接追踪（#12 请求级安全网）：在请求上下文内打开的连接，
# 即使某函数忘记 close（异常提前返回），也会在 teardown 时统一关闭。
try:
    from flask import g as _flask_g, has_app_context as _has_app_context
except Exception:  # pragma: no cover - 独立脚本场景
    _flask_g = None

    def _has_app_context():
        return False

# ============ 基础路径 ============
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_FILENAME = get_config("database.path", "book_manager.db")
DB_PATH = os.path.join(_BASE_DIR, _DB_FILENAME)

# ============ 分类布局缓存（保持原逻辑） ============
_layout_cache: Dict[str, Any] = {}
_layout_cache_lock = threading.Lock()
_LAYOUT_CACHE_TTL = 60

_log_lock = threading.Lock()


# ============ 基础连接 ============
def get_db() -> sqlite3.Connection:
    """获取 SQLite 连接（默认启用 WAL + 外键）。

    在请求上下文内打开的连接会被登记到 flask.g，由 teardown 兜底关闭（#12），
    即使调用方忘记 close 也不泄漏。
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    if get_config("database.wal_mode", True):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
    busy = int(get_config("database.busy_timeout", 5000))
    try:
        conn.execute(f"PRAGMA busy_timeout={busy}")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    # 登记连接，供请求级 teardown 统一关闭（重复关闭无害）
    if _has_app_context() and _flask_g is not None:
        _flask_g.setdefault("_open_conns", []).append(conn)
    return conn


def close_request_connections(exc=None):
    """请求结束兜底关闭本请求内打开的所有数据库连接（#12）。

    由 app.py 的 teardown_appcontext 注册；与函数内显式 close 不冲突
    （sqlite3 连接重复 close 为幂等 no-op）。
    """
    if not _has_app_context() or _flask_g is None:
        return
    conns = getattr(_flask_g, "_open_conns", None)
    if not conns:
        return
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    _flask_g._open_conns = []


def _request_cache(key, loader, deepcopy=False):
    """请求级缓存（#22/#23）：同一请求内重复调用 loader 只执行一次。

    - 无请求上下文（脚本/后台任务）时直查 loader，不缓存。
    - deepcopy=True 时对返回值做深拷贝，避免调用方就地修改污染后续复用（如 list_books_tree 给图书 dict 加字段）。
    """
    if not _has_app_context() or _flask_g is None:
        val = loader()
        return copy.deepcopy(val) if deepcopy else val
    cache = _flask_g.setdefault("_req_cache", {})
    if key not in cache:
        cache[key] = loader()
    val = cache[key]
    return copy.deepcopy(val) if deepcopy else val


# ============ 通用 SQL 助手（供业务模块直接使用） ============
def db_execute(sql, params=()):
    """执行写操作（INSERT/UPDATE/DELETE/DDL）。返回 lastrowid（INSERT 自增主键）或 rowcount。

    #12：try/finally 确保连接无论成功失败都关闭；#18：异常记录 traceback 后上抛，
    由调用方（路由层 module_exception_guard）统一转换为错误响应，不再静默吞掉。
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        rid = cur.lastrowid
        return rid if rid else cur.rowcount
    except Exception:
        logger.exception("[db_execute] SQL 执行失败")
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_query(sql, params=()):
    """执行查询，返回 list[dict]（行以字典形式返回，键为列名）。

    #12/#18：同 db_execute，连接必关、异常上抛（详见 db_execute 说明）。
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.exception("[db_query] SQL 查询失败")
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def db_query_one(sql, params=()):
    """执行查询，返回单条 dict 或 None。#12/#18：连接必关、异常上抛。"""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        r = cur.fetchone()
        return dict(r) if r else None
    except Exception:
        logger.exception("[db_query_one] SQL 查询失败")
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_db_version(cursor) -> int:
    try:
        cursor.execute("PRAGMA user_version")
        return cursor.fetchone()[0] or 0
    except Exception:
        return 0


def set_db_version(cursor, version: int):
    cursor.execute(f"PRAGMA user_version = {int(version)}")


def _add_column_if_missing(cursor, table: str, column: str, col_def: str):
    """安全添加字段：若字段已存在则跳过。"""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cursor.fetchall()]
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        # 新增列后让表结构缓存失效，避免 _table_columns 的模块级缓存（早于本次
        # 加列被填充）导致 update_book 等动态列拼接把新列静默跳过（如 books.pc1/pc2）。
        try:
            _table_cols_cache.pop(table, None)
        except Exception:
            pass


# ============ 数据库初始化 & 增量迁移 ============
def init_db():
    """
    初始化数据库：创建所有核心表（用户、日志、图书、分类、AI相关）。
    增量迁移基于 PRAGMA user_version，不会破坏存量数据。
    """
    conn = get_db()
    cursor = conn.cursor()

    # ---- 用户表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        account TEXT UNIQUE NOT NULL,
        name TEXT,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        permissions TEXT DEFAULT '[]',
        is_active INTEGER DEFAULT 1,
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        last_login_at TEXT,
        last_login_ip TEXT
    )
    """)
    _add_column_if_missing(cursor, "users", "permissions", "TEXT DEFAULT '[]'")
    _add_column_if_missing(cursor, "users", "is_active", "INTEGER DEFAULT 1")
    _add_column_if_missing(cursor, "users", "created_by", "TEXT")
    _add_column_if_missing(cursor, "users", "last_login_at", "TEXT")
    _add_column_if_missing(cursor, "users", "last_login_ip", "TEXT")

    # ---- 图书表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS books (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        cat1 TEXT,
        cat2 TEXT,
        path TEXT,
        upload_by TEXT,
        file_hash TEXT,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    _add_column_if_missing(cursor, "books", "file_hash", "TEXT")
    _add_column_if_missing(cursor, "books", "sort_order", "INTEGER DEFAULT 0")
    # owner_id：图书归属人（上传者用户 id）。私人图书馆功能依赖此字段判断"我的私有图书"，
    # 并在设为私有时作为 resource_permissions.allowed_users 的唯一授权人。
    _add_column_if_missing(cursor, "books", "owner_id", "TEXT")
    # indexed_hash：上次成功建立索引时的文件哈希，用于「增量重建」判断
    # （文件没变且已索引过就跳过，避免每次重建都把全部书推倒重来）
    _add_column_if_missing(cursor, "books", "indexed_hash", "TEXT")
    # qa_mode：单本回答档位覆盖（public/general/precise），为空表示继承分类配置
    _add_column_if_missing(cursor, "books", "qa_mode", "TEXT")
    # pc1/pc2：个人分类（私人图书馆）。与 cat1/cat2（全局分类）二选一，
    # pc1 非空 = 挂在个人分类下，只在「我的私有图书」显示，不进全局分类树。
    # 必须在 _init_db 阶段就加列，避免被 _table_columns 的模块级缓存（早于
    # 懒加载 _ensure_book_extra_cols）漏掉，导致 update_book(bid, pc1=, pc2=) 静默跳过。
    _add_column_if_missing(cursor, "books", "pc1", "TEXT")
    _add_column_if_missing(cursor, "books", "pc2", "TEXT")
    # 迁移：把「已经成功建立过索引」的书的哈希基线补上，
    # 否则新增 indexed_hash 字段后第一次增量重建仍会把它们全部重做一遍。
    # 条件带 indexed_hash 为空，重复执行安全。
    try:
        cursor.execute("""
            UPDATE books SET indexed_hash = file_hash
            WHERE ai_status = 'done'
              AND file_hash IS NOT NULL AND file_hash <> ''
              AND (indexed_hash IS NULL OR indexed_hash = '')
              AND EXISTS (SELECT 1 FROM document_chunks d WHERE d.book_id = books.id)
        """)
    except Exception:
        pass

    # ---- 分类表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        parent_id TEXT,
        sort_order INTEGER DEFAULT 0,
        color TEXT
    )
    """)
    _add_column_if_missing(cursor, "categories", "color", "TEXT")

    # ---- 个人分类表（私人图书馆专用：与全局 categories 完全隔离，不进共享分类树） ----
    # 设计要点：个人分类单独建表而非给 categories 加 owner_id，
    # 避免 get_all_categories()（11 个调用点、无 owner 过滤）把个人分类漏进全局树。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS private_categories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        parent_id TEXT,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pcat_user ON private_categories(user_id)")
    except Exception:
        pass

    # ---- 操作日志表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        action TEXT,
        target_type TEXT,
        target_id TEXT,
        detail TEXT,
        ip TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # ---- 阅读记录表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reading_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        status TEXT DEFAULT 'reading',
        progress INTEGER,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, book_id)
    )
    """)
    _add_column_if_missing(cursor, "reading_records", "progress", "INTEGER")

    # ---- 笔记表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        content TEXT,
        is_public INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # ---- 读后感表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        content TEXT,
        rating INTEGER DEFAULT 5,
        is_public INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # ---- 收藏表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, book_id)
    )
    """)

    # ---- 资源权限表（图书/分类级别权限，支持指定用户） ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS resource_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_type TEXT NOT NULL DEFAULT 'book',
        resource_id TEXT NOT NULL,
        min_level TEXT NOT NULL DEFAULT 'public',
        allowed_users TEXT,
        UNIQUE(resource_type, resource_id)
    )
    """)
    _add_column_if_missing(cursor, "resource_permissions", "allowed_users", "TEXT")

    # ---- 模块访问权限表（控制某模块对各用户的可见级别，全局设置） ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS module_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module_id TEXT NOT NULL,
        min_level TEXT NOT NULL DEFAULT 'public',
        allowed_users TEXT,
        UNIQUE(module_id)
    )
    """)

    # ---- AI向量映射表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_vector_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT,
        file_hash TEXT,
        chunk_index INTEGER,
        faiss_index INTEGER,
        chunk_text_preview TEXT,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    _add_column_if_missing(cursor, "ai_vector_mappings", "is_valid", "INTEGER DEFAULT 1")
    _add_column_if_missing(cursor, "ai_vector_mappings", "chunk_text_preview", "TEXT")
    _add_column_if_missing(cursor, "ai_vector_mappings", "parent_id", "INTEGER")
    # 索引方案B：按运行模式（light/full）区分索引归属
    _add_column_if_missing(cursor, "ai_vector_mappings", "mode", "TEXT")
    try:
        from modules.ai_center.internal.config import get_ai_mode
        _default_mode = get_ai_mode()
    except Exception:
        _default_mode = "light"
    try:
        cursor.execute(
            "UPDATE ai_vector_mappings SET mode=? WHERE mode IS NULL OR mode=''",
            (_default_mode,),
        )
    except Exception:
        pass

    # ---- AI父块表（阶梯2：父子分块检索，父块用于回答时回退上下文） ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_parent_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT,
        parent_index INTEGER,
        parent_text TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    _add_column_if_missing(cursor, "ai_parent_chunks", "parent_text", "TEXT")

    # ---- 文档分块表（存分块原文；阶梯2 增加 parent_id/parent_text 列） ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS document_chunks (
        book_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
        chunk_text TEXT, content TEXT NOT NULL,
        parent_id INTEGER, parent_text TEXT,
        UNIQUE(book_id, chunk_index))""")
    _add_column_if_missing(cursor, "document_chunks", "parent_id", "INTEGER")
    _add_column_if_missing(cursor, "document_chunks", "parent_text", "TEXT")
    # 来源定位：page=页码，section=条款号（如 "5.2" / "第3条"）。
    # 两者都可能取不到（纯文本、无页码文档），展示时必须容错。
    _add_column_if_missing(cursor, "document_chunks", "page", "INTEGER")
    _add_column_if_missing(cursor, "document_chunks", "section", "TEXT")
    # F1：章节面包屑（如 "一、总则 > 1.1 适用范围 > 1.1.1 报备时限"），
    # 由 chunker 计算，供召回层按「书名>章>节>正文」分层加权。
    _add_column_if_missing(cursor, "document_chunks", "section_path", "TEXT")

    # ---- AI异步任务表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_tasks (
        id TEXT PRIMARY KEY,
        task_type TEXT,
        book_id TEXT,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        message TEXT,
        result TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    _add_column_if_missing(cursor, "ai_tasks", "progress", "INTEGER DEFAULT 0")
    # 兼容历史库：ai_tasks 可能由旧版本建表（含 payload/total/error/user_id/
    # started_at/finished_at，却缺 book_id/message）。因为用了 CREATE TABLE IF NOT EXISTS，
    # 旧表不会被重建，导致 create_ai_task（写 book_id）/ update_ai_task（写 message）
    # 一直插入失败、任务记录写不进去。这里双向补齐，两种结构都能正常工作。
    for _col, _def in (
        ("book_id", "TEXT"),
        ("message", "TEXT"),
        ("payload", "TEXT"),
        ("total", "INTEGER"),
        ("error", "TEXT"),
        ("user_id", "TEXT"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
    ):
        _add_column_if_missing(cursor, "ai_tasks", _col, _def)

    # ---- 问答会话历史表（服务端存储；此前塞 Flask 签名 Cookie，长答案轻松超
    # 4KB 上限被静默丢弃，历史无感丢失，且每请求背几 KB Cookie）----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_histories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope TEXT DEFAULT 'homepage',
        role TEXT NOT NULL,
        content TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chathist_user ON chat_histories(user_id, id)")
    except Exception:
        pass

    # ---- OCR 结果缓存表（按文件哈希复用，避免同一文件重复 OCR）----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ocr_cache (
        file_hash TEXT PRIMARY KEY,
        text TEXT,
        pages INTEGER DEFAULT 0,
        engine TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # ---- AI日志表 ----
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        action TEXT,
        target_type TEXT,
        target_id TEXT,
        detail TEXT,
        ip TEXT,
        mode TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """)
    _add_column_if_missing(cursor, "ai_logs", "mode", "TEXT")

    conn.commit()

    # 版本号（供后续增量迁移用）
    current = get_db_version(cursor)
    if current < 1:
        set_db_version(cursor, 1)
        conn.commit()

    conn.close()


def _resolve_admin_init_password() -> str:
    """决定首次初始化 admin 账号时使用的密码。

    优先级：
      1. 环境变量 ``ADMIN_INIT_PASSWORD`` —— 容器化 / 自动化部署时显式注入；
      2. 否则**随机生成** —— 开源项目不再内置任何公开已知的固定口令，
         避免"部署即可被默认密码登录"的风险。

    注意：本函数只在 admin 账号不存在（首次初始化）时被调用，
    已有账号永远不会被重置或覆盖。
    """
    env_pwd = (os.environ.get("ADMIN_INIT_PASSWORD") or "").strip()
    if env_pwd:
        return env_pwd
    return secrets.token_urlsafe(9)


def _persist_admin_init_password(plain_password: str) -> None:
    """把随机生成的初始密码落盘到 config/.init_admin_pwd（仅首次初始化时）。

    随机密码若只打印一次就刷屏消失，管理员会彻底无法登录，所以必须留一份。
    该文件已在 .gitignore 中，且按 600 权限写入。
    """
    try:
        cfg_dir = os.path.join(_BASE_DIR, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        path = os.path.join(cfg_dir, ".init_admin_pwd")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, plain_password.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        logger.warning("[INIT] 初始管理员密码已写入 %s （该文件仅本地保存，勿外传）", path)
    except Exception as e:
        logger.warning("[INIT] 初始密码落盘失败（请留意控制台输出）：%s", e)


def create_default_admin() -> Optional[str]:
    """确保 admin 默认账号存在。

    返回：
        - ``None``：admin 已存在，本次未做任何改动；
        - ``str``：本次新建 admin 时使用的明文密码（仅首次初始化会出现），
          调用方应立刻打印提示，提醒使用者登录后修改。
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE account='admin'")
    if cur.fetchone():
        conn.close()
        return None

    plain_password = _resolve_admin_init_password()
    pwd_hash = generate_password_hash(plain_password)

    import uuid as _uuid
    admin_id = "u_" + _uuid.uuid4().hex[:12]
    cur.execute(
        "INSERT INTO users (id, account, name, password_hash, role, permissions) VALUES (?,?,?,?,?,?)",
        (admin_id, "admin", "超级管理员", pwd_hash, "superadmin",
         '["admin","book_add","book_edit","book_delete","book_batch_delete","book_batch_move","book_batch_add","c1_add","c1_edit","c1_delete","c1_move","c2_add","c2_edit","c2_delete","c2_move","user_manage","file_upload","config_access","view_all_records","ai_config","ai_qa"]'),
    )
    conn.commit()
    conn.close()

    if not (os.environ.get("ADMIN_INIT_PASSWORD") or "").strip():
        _persist_admin_init_password(plain_password)
    return plain_password


# ============ 密码 & 用户查询（属于全局基础能力） ============
def verify_password(user_row: Dict[str, Any], plain_password: str) -> bool:
    if not user_row or not plain_password:
        return False
    try:
        return check_password_hash(user_row["password_hash"], plain_password)
    except Exception:
        return False


# 注：get_user_by_id / get_user_by_account 的「带装饰（_decorate_user_row）」版本
# 定义在文件末尾（模块加载后生效），此处不重复定义，避免死代码歧义。

ALL_PERMISSIONS = [
    ("admin", "超级管理员（全部权限）"),
    ("config_access", "访问配置中心"),
    ("file_upload", "上传文件"),
    ("book_add", "新增图书"),
    ("book_edit", "编辑图书"),
    ("book_delete", "删除图书"),
    ("book_batch_add", "批量新增图书"),
    ("book_batch_delete", "批量删除图书"),
    ("book_batch_move", "批量移动图书"),
    ("c1_add", "新增一级分类"),
    ("c1_edit", "编辑一级分类"),
    ("c1_delete", "删除一级分类"),
    ("c1_move", "一级分类排序"),
    ("c2_add", "新增二级分类"),
    ("c2_edit", "编辑二级分类"),
    ("c2_delete", "删除二级分类"),
    ("c2_move", "二级分类排序"),
    ("user_manage", "用户与权限管理"),
    ("view_all_records", "查看所有笔记/读后感"),
    ("ai_config", "AI配置管理"),
    ("ai_qa", "AI问答使用"),
]


def refresh_module_permissions():
    """
    从各模块 __init__.py 自报的 PERMISSIONS 聚合权限点，并入 ALL_PERMISSIONS。
    这样新增业务模块时，只需在模块 __init__.py 声明权限点，无需改动本文件。
    在 app 启动时（模块注册完成后）调用一次。失败时回退到内置基线，不影响系统运行。
    """
    global ALL_PERMISSIONS, ALL_PERMISSION_KEYS
    try:
        from .plugin_scan import get_all_permissions
        mod_perms = get_all_permissions() or []
        existing = {p[0] for p in ALL_PERMISSIONS}
        merged = [list(p) for p in ALL_PERMISSIONS]
        for key, name in mod_perms:
            if key in existing:
                continue
            merged.append([key, name])
            existing.add(key)
        ALL_PERMISSIONS = merged
        ALL_PERMISSION_KEYS = [p[0] for p in ALL_PERMISSIONS]
    except Exception as e:
        logger.warning(f"[db_base] 模块权限聚合失败，沿用内置基线: {e}")


def has_permission(user, perm: str) -> bool:
    if not user:
        return False
    if user.get("role") in ("admin", "superadmin"):
        return True
    try:
        import json as _json
        perms = user.get("permissions") or "[]"
        if isinstance(perms, str):
            perms = _json.loads(perms)
        return perm in list(perms)
    except Exception:
        return False


def get_user_permissions(user):
    if not user:
        return []
    if user.get("role") in ("admin", "superadmin"):
        return [p[0] for p in ALL_PERMISSIONS]
    try:
        import json as _json
        perms = user.get("permissions") or "[]"
        if isinstance(perms, str):
            perms = _json.loads(perms)
        return list(perms)
    except Exception:
        return []


def update_last_login(user_id, ip=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE users SET last_login_at=?, last_login_ip=? WHERE id=?",
            (now, ip, user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ============ 操作日志（全局基础能力） ============
def add_log(user_id, action, target_type=None, target_id=None, detail=None, ip=None):
    try:
        with _log_lock:
            conn = get_db()
            cur = conn.cursor()
            import json as _json
            detail_str = _json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else (str(detail) if detail is not None else None)
            cur.execute(
                "INSERT INTO logs (user_id, action, target_type, target_id, detail, ip) VALUES (?,?,?,?,?,?)",
                (user_id, action, target_type, target_id, detail_str, ip),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def add_ai_log(user_id, action, target_type=None, target_id=None, detail=None, ip=None, mode=None):
    try:
        with _log_lock:
            conn = get_db()
            cur = conn.cursor()
            import json as _json
            detail_str = _json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else (str(detail) if detail is not None else None)
            cur.execute(
                "INSERT INTO ai_logs (user_id, action, target_type, target_id, detail, ip, mode) VALUES (?,?,?,?,?,?,?)",
                (user_id, action, target_type, target_id, detail_str, ip, mode),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def get_ai_logs(per_page=50, offset=0):
    """查询 AI 操作日志（分页，新到旧）。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM ai_logs")
        total = cur.fetchone()["c"] or 0
        cur.execute(
            "SELECT al.*, u.account as user_account, u.name as user_name "
            "FROM ai_logs al LEFT JOIN users u ON al.user_id=u.id "
            "ORDER BY al.id DESC LIMIT ? OFFSET ?",
            (int(per_page), int(offset)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"total": total, "logs": rows}
    except Exception as e:
        logger.exception("[get_ai_logs] 读取 AI 日志失败")
        return {"total": 0, "logs": []}


def get_logs(per_page=50, offset=0):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM logs")
        total = cur.fetchone()["c"] or 0
        cur.execute(
            "SELECT l.*, u.account as user_account, u.name as user_name "
            "FROM logs l LEFT JOIN users u ON l.user_id=u.id "
            "ORDER BY l.id DESC LIMIT ? OFFSET ?",
            (int(per_page), int(offset)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"total": total, "logs": rows}
    except Exception as e:
        logger.exception("[get_logs] 读取日志失败")
        return {"total": 0, "logs": []}


# ============ 用户 & 权限 DAL（全局基础能力，供 system_config 模块复用） ============
def get_all_users():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


import uuid as _uuid_mod


def _uid(prefix="u"):
    return prefix + "_" + _uuid_mod.uuid4().hex[:12]


def add_user(account, name, password, role="user", permissions=None, created_by=None):
    conn = get_db()
    cur = conn.cursor()
    user_id = _uid()
    import json as _json
    perm_json = _json.dumps(list(permissions or []), ensure_ascii=False)
    cur.execute(
        "INSERT INTO users (id, account, name, password_hash, role, permissions, created_by) VALUES (?,?,?,?,?,?,?)",
        (user_id, account, name or account, generate_password_hash(password), role, perm_json, created_by),
    )
    conn.commit()
    conn.close()
    return user_id


def update_user_password(user_id, new_password):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), user_id),
    )
    conn.commit()
    conn.close()


def update_user_name(user_id, new_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET name=? WHERE id=?", (new_name, user_id))
    conn.commit()
    conn.close()


def update_user_permissions(user_id, permissions):
    import json as _json
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET permissions=? WHERE id=?",
        (_json.dumps(list(permissions or []), ensure_ascii=False), user_id),
    )
    conn.commit()
    conn.close()


def toggle_user_active(user_id, is_active: bool):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET is_active=? WHERE id=?",
        (1 if is_active else 0, user_id),
    )
    conn.commit()
    conn.close()


# ============ 资源权限（图书/分类级别的全局能力，支持三层级 + 指定用户） ============
def _load_category_id_maps():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, name, parent_id FROM categories")
        rows = cur.fetchall(); conn.close()
        c1, c2 = {}, {}
        for r in rows:
            if r["parent_id"]:
                c2[r["name"]] = r["id"]
            else:
                c1[r["name"]] = r["id"]
        return c1, c2
    except Exception:
        logger.exception("[_get_category_id_maps] 读取分类映射失败")
        return {}, {}


def _get_category_id_maps():
    """返回 {一级分类名: id} 与 {二级分类名: id}。请求级缓存（#23）。"""
    return _request_cache("category_id_maps", _load_category_id_maps)


def set_resource_permission(resource_id, level, resource_type="book", allowed_users=None):
    conn = get_db(); cur = conn.cursor()
    au = None
    if level == "selected" and allowed_users:
        try:
            au = json.dumps([str(u) for u in allowed_users], ensure_ascii=False)
        except Exception:
            au = None
    cur.execute(
        "INSERT OR REPLACE INTO resource_permissions "
        "(resource_type, resource_id, min_level, allowed_users) VALUES (?,?,?,?)",
        (resource_type, resource_id, level, au),
    )
    conn.commit(); conn.close()


def delete_resource_permission(resource_id, resource_type="book"):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "DELETE FROM resource_permissions WHERE resource_type=? AND resource_id=?",
        (resource_type, resource_id),
    )
    conn.commit(); conn.close()


def get_all_resource_permissions():
    """兼容旧调用：返回 {type:id: level}。"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT resource_type, resource_id, min_level FROM resource_permissions")
    rows = cur.fetchall(); conn.close()
    return {f"{r['resource_type']}:{r['resource_id']}": r["min_level"] for r in rows}


def _load_resource_permission_details():
    """返回完整权限明细列表（含指定用户）。"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT resource_type, resource_id, min_level, allowed_users FROM resource_permissions")
    rows = cur.fetchall(); conn.close()
    out = []
    for r in rows:
        au = []
        if r["allowed_users"]:
            try:
                au = json.loads(r["allowed_users"])
            except Exception:
                au = []
        out.append({
            "resource_type": r["resource_type"],
            "resource_id": r["resource_id"],
            "permission_level": r["min_level"],
            "allowed_users": au,
        })
    return out


def get_all_resource_permission_details():
    """返回完整权限明细列表（含指定用户）。请求级缓存（#23）。"""
    return _request_cache("resource_permission_details", _load_resource_permission_details)


def get_resource_permission_detail(resource_type, resource_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT resource_type, resource_id, min_level, allowed_users "
        "FROM resource_permissions WHERE resource_type=? AND resource_id=?",
        (resource_type, resource_id),
    )
    r = cur.fetchone(); conn.close()
    if not r:
        return None
    au = []
    if r["allowed_users"]:
        try:
            au = json.loads(r["allowed_users"])
        except Exception:
            au = []
    return {
        "resource_type": r["resource_type"],
        "resource_id": r["resource_id"],
        "permission_level": r["min_level"],
        "allowed_users": au,
    }


def _collect_permission_candidates(book, detail_map, c1_map, c2_map):
    """收集某书生效的权限候选（图书/二级分类/一级分类）。"""
    cands = []
    d = detail_map.get(("book", book.get("id")))
    if d:
        cands.append(d)
    if book.get("cat2") and book["cat2"] in c2_map:
        d = detail_map.get(("cat2", c2_map[book["cat2"]]))
        if d:
            cands.append(d)
    if book.get("cat1") and book["cat1"] in c1_map:
        d = detail_map.get(("cat1", c1_map[book["cat1"]]))
        if d:
            cands.append(d)
    return cands


def get_book_effective_level(book):
    """返回该书生效的访问级别标签（取图书/二级分类/一级分类中最严格者）。"""
    if not book:
        return "public"
    try:
        details = get_all_resource_permission_details()
        detail_map = {(d["resource_type"], d["resource_id"]): d for d in details}
        c1, c2 = _get_category_id_maps()
        cands = _collect_permission_candidates(book, detail_map, c1, c2)
        if not cands:
            return "public"
        levels = [c["permission_level"] for c in cands]
        # 取最严格的级别（admin > selected > login > public）
        severity = {"public": 0, "login": 1, "selected": 2, "admin": 3}
        return max(levels, key=lambda lv: severity.get(lv, 0))
    except Exception:
        return "public"


def user_can_view_book(book, user):
    """综合图书/分类权限，判断用户是否可见该书。"""
    if not book:
        return True
    try:
        details = get_all_resource_permission_details()
        detail_map = {(d["resource_type"], d["resource_id"]): d for d in details}
        c1, c2 = _get_category_id_maps()
        is_admin = bool(user and user.get("role") in ("admin", "superadmin"))
        cands = _collect_permission_candidates(book, detail_map, c1, c2)
        if not cands:
            return True  # 默认公开
        has_admin = any(c["permission_level"] == "admin" for c in cands)
        has_login = any(c["permission_level"] == "login" for c in cands)
        has_selected = any(c["permission_level"] == "selected" for c in cands)
        allowed = set()
        for c in cands:
            if c["permission_level"] == "selected":
                allowed.update(c.get("allowed_users") or [])
        if has_admin and not is_admin:
            return False
        if has_selected:
            if is_admin:
                return True
            if not user:
                return False
            return str(user.get("id")) in {str(x) for x in allowed}
        if has_login:
            return bool(user)
        return True
    except Exception:
        return True


def get_effective_permission(book):
    """兼容旧调用：返回生效级别标签。"""
    return get_book_effective_level(book)


def filter_books_by_permission(books, user):
    if not books:
        return []
    try:
        c1, c2 = _get_category_id_maps()
        details = get_all_resource_permission_details()
        detail_map = {(d["resource_type"], d["resource_id"]): d for d in details}
        is_admin = bool(user and user.get("role") in ("admin", "superadmin"))
        result = []
        for b in books:
            cands = _collect_permission_candidates(b, detail_map, c1, c2)
            if not cands:
                result.append(b)
                continue
            has_admin = any(c["permission_level"] == "admin" for c in cands)
            has_login = any(c["permission_level"] == "login" for c in cands)
            has_selected = any(c["permission_level"] == "selected" for c in cands)
            allowed = set()
            for c in cands:
                if c["permission_level"] == "selected":
                    allowed.update(c.get("allowed_users") or [])
            if has_admin and not is_admin:
                continue
            if has_selected:
                if is_admin:
                    result.append(b)
                    continue
                if user and str(user.get("id")) in {str(x) for x in allowed}:
                    result.append(b)
                continue
            if has_login:
                if user:
                    result.append(b)
                continue
            result.append(b)
        return result
    except Exception:
        return books


# ============ 模块访问权限（控制侧边栏模块的可见级别） ============
def set_module_access(module_id, level, allowed_users=None):
    """设置某模块的全局访问级别：public/login/admin/selected。"""
    try:
        if level not in ("public", "login", "admin", "selected"):
            level = "public"
        au = None
        if level == "selected" and allowed_users:
            au = json.dumps([str(u) for u in allowed_users], ensure_ascii=False)
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO module_access (module_id, min_level, allowed_users)
               VALUES (?,?,?)
               ON CONFLICT(module_id) DO UPDATE SET min_level=excluded.min_level,
               allowed_users=excluded.allowed_users""",
            (module_id, level, au),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_module_access(module_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM module_access WHERE module_id=?", (module_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_module_access_map():
    """返回 {module_id: {'min_level':..., 'allowed_users':[...]}}，未设置的模块默认 public。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT module_id, min_level, allowed_users FROM module_access")
        rows = cur.fetchall()
        conn.close()
        out = {}
        for r in rows:
            au = []
            if r["allowed_users"]:
                try:
                    au = json.loads(r["allowed_users"])
                except Exception:
                    au = []
            out[r["module_id"]] = {"min_level": r["min_level"] or "public", "allowed_users": au}
        return out
    except Exception:
        return {}


def user_can_access_module(user, module_id):
    """判断某用户能否访问该模块（侧边栏/路由可见性）。默认 public=可见。"""
    try:
        mp = get_module_access_map().get(module_id)
        if not mp:
            return True  # 未设置 → 默认公开
        level = mp["min_level"]
        if level == "public":
            return True
        if level == "login":
            return bool(user)
        if level == "admin":
            return bool(user and user.get("role") in ("admin", "superadmin"))
        if level == "selected":
            if not user:
                return False
            return str(user.get("id")) in {str(x) for x in (mp.get("allowed_users") or [])}
        return True
    except Exception:
        return True


# ============ AI向量失效（全局基础能力，供图书删除/分类删除时调用） ============
def invalidate_vector_mappings_by_book(book_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE ai_vector_mappings SET is_valid=0 WHERE book_id=?", (book_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def clean_orphan_vectors():
    """清理失效向量：删除已不存在图书的映射行，并清除 is_valid=0 的脏数据。
    返回 {deleted_orphan, deleted_invalid}。失败返回空结果而非抛异常。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        # 孤儿向量：book_id 不在 books 表
        # P0：必须豁免 exp_% 命名空间 —— 经验帖以 book_id="exp_<id>" 写入
        # ai_vector_mappings / document_chunks，但 books 表没有对应行。
        # 不加这条会把经验帖映射全部误删（与 purge_orphan_chunks 同一条铁律）。
        cur.execute(
            "DELETE FROM ai_vector_mappings WHERE book_id IS NOT NULL "
            "AND book_id NOT LIKE 'exp_%' "
            "AND book_id NOT IN (SELECT id FROM books)"
        )
        deleted_orphan = cur.rowcount
        # 失效向量：is_valid=0（已被 invalidate_vector_mappings_by_book 标记）
        cur.execute("DELETE FROM ai_vector_mappings WHERE is_valid=0")
        deleted_invalid = cur.rowcount
        conn.commit()
        conn.close()
        return {"deleted_orphan": deleted_orphan, "deleted_invalid": deleted_invalid}
    except Exception as e:
        return {"deleted_orphan": 0, "deleted_invalid": 0, "error": str(e)}


def reset_running_ai_tasks():
    """启动时调用：将运行中/挂起的任务重置为失败状态。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_tasks SET status='failed', message='服务重启，任务中断' WHERE status IN ('pending','running')"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ============================================================
# 图书 DAL（基础SQL查询，属于全局数据库层；业务层逻辑放 book_lib.db_ops）
# ============================================================
def add_document_chunk(book_id, chunk_index, chunk_text, file_hash=None,
                       parent_id=None, parent_text=None, page=None, section=None,
                       section_path=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO document_chunks "
            "(book_id, chunk_index, chunk_text, content, parent_id, parent_text, page, section, section_path) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (book_id, int(chunk_index), chunk_text, chunk_text,
             int(parent_id) if parent_id is not None else None,
             parent_text,
             int(page) if page not in (None, "") else None,
             (str(section)[:64] if section else None),
             (str(section_path)[:256] if section_path else None)),
        )
        conn.commit()
        conn.close()
    except Exception:
        # 表不存在时静默忽略（首次运行可能还没迁移）
        try:
            conn = get_db()
            conn.execute("""CREATE TABLE IF NOT EXISTS document_chunks (
                book_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                chunk_text TEXT, content TEXT NOT NULL,
                parent_id INTEGER, parent_text TEXT,
                UNIQUE(book_id, chunk_index))""")
            conn.commit()
            conn.close()
        except Exception:
            pass


def delete_document_chunks(book_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM document_chunks WHERE book_id=?", (book_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_document_chunks(book_id=None, limit=5000):
    try:
        conn = get_db()
        cur = conn.cursor()
        if book_id:
            cur.execute(
                "SELECT * FROM document_chunks WHERE book_id=? ORDER BY chunk_index LIMIT ?",
                (book_id, int(limit)),
            )
        else:
            cur.execute(
                "SELECT * FROM document_chunks ORDER BY book_id, chunk_index LIMIT ?",
                (int(limit),),
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ---------- AI父块（阶梯2：父子分块检索） ----------
def add_parent_chunks(book_id, parent_texts):
    """覆盖写入某本书的父块（按列表下标作为 parent_index）。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_parent_chunks WHERE book_id=?", (book_id,))
        for i, pt in enumerate(parent_texts or []):
            if not pt:
                continue
            cur.execute(
                "INSERT INTO ai_parent_chunks (book_id, parent_index, parent_text) VALUES (?,?,?)",
                (book_id, i, pt),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_parent_chunk_text(book_id, parent_index):
    """读取某父块文本；若父块表缺失，回退到 document_chunks.parent_text 缓存。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT parent_text FROM ai_parent_chunks WHERE book_id=? AND parent_index=?",
            (book_id, int(parent_index)),
        )
        row = cur.fetchone()
        if row and row["parent_text"]:
            conn.close()
            return row["parent_text"]
        # 回退：从 document_chunks 缓存取（任意一条匹配 parent_id 的行）
        cur.execute(
            "SELECT parent_text FROM document_chunks WHERE book_id=? AND parent_id=? "
            "AND parent_text IS NOT NULL LIMIT 1",
            (book_id, int(parent_index)),
        )
        row2 = cur.fetchone()
        conn.close()
        return row2["parent_text"] if row2 and row2["parent_text"] else ""
    except Exception:
        return ""


def delete_parent_chunks(book_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_parent_chunks WHERE book_id=?", (book_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------- AI向量映射 ----------
def add_vector_mapping(book_id, file_hash, chunk_index, faiss_index, preview=None, parent_id=None, mode=None):
    """写入一条向量映射；返回是否成功。

    ⚠️ 原来是裸 `except: pass`：写入失败（磁盘满、锁冲突、表缺列）时调用方
    vector_store.store() 完全感知不到，照样返回 len(chunks) 让上层判定"索引成功"，
    结果书显示已索引、实际检索为空，且日志里一个字都没有。
    现在改为记录日志并返回 False（调用方当前仍忽略返回值，属既有行为，
    未擅自改成中断写入，避免影响全量重建的稳定性）。
    """
    try:
        if mode is None:
            try:
                from modules.ai_center.internal.config import get_ai_mode
                mode = get_ai_mode()
            except Exception:
                mode = "light"
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_vector_mappings "
            "(book_id, file_hash, chunk_index, faiss_index, chunk_text_preview, parent_id, mode) "
            "VALUES (?,?,?,?,?,?,?)",
            (book_id, file_hash, int(chunk_index), int(faiss_index),
             (preview or "")[:300], int(parent_id) if parent_id is not None else None, mode),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        try:
            logger.exception("写入向量映射失败 book=%s chunk=%s: %s", book_id, chunk_index, e)
        except Exception:
            print(f"⚠️ 写入向量映射失败 book={book_id} chunk={chunk_index}: {e}")
        return False


def get_max_faiss_idx(mode=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        if mode:
            cur.execute(
                "SELECT COALESCE(MAX(faiss_index), -1) AS m FROM ai_vector_mappings "
                "WHERE is_valid=1 AND mode=?",
                (mode,),
            )
        else:
            cur.execute("SELECT COALESCE(MAX(faiss_index), -1) AS m FROM ai_vector_mappings WHERE is_valid=1")
        r = cur.fetchone()
        conn.close()
        return int(r["m"]) if r else -1
    except Exception:
        return -1


def get_valid_vector_mappings(mode=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        if mode:
            cur.execute(
                "SELECT * FROM ai_vector_mappings WHERE is_valid=1 AND mode=? ORDER BY faiss_index",
                (mode,),
            )
        else:
            cur.execute("SELECT * FROM ai_vector_mappings WHERE is_valid=1 ORDER BY faiss_index")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def clear_vector_mappings(book_id=None, mode=None):
    """清空向量映射。可按 book_id 或 mode 单独过滤，二者皆空则全部清空。

    索引方案B：重建某一模式时只清该 mode 的行，不动另一模式的索引数据。
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        if book_id and mode:
            cur.execute("DELETE FROM ai_vector_mappings WHERE book_id=? AND mode=?", (book_id, mode))
        elif book_id:
            cur.execute("DELETE FROM ai_vector_mappings WHERE book_id=?", (book_id,))
        elif mode:
            cur.execute("DELETE FROM ai_vector_mappings WHERE mode=?", (mode,))
        else:
            cur.execute("DELETE FROM ai_vector_mappings")
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------- AI 异步任务 ----------
def create_ai_task(task_id, task_type, book_id=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_tasks (id, task_type, book_id, status, progress) VALUES (?,?,?,'pending',0)",
            (task_id, task_type, book_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.exception("[create_ai_task] 创建 AI 任务失败")


def update_ai_task(task_id, status=None, progress=None, message=None, result=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        sets, args = [], []
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if progress is not None:
            sets.append("progress=?")
            args.append(int(progress))
        if message is not None:
            sets.append("message=?")
            args.append(message)
        if result is not None:
            import json as _json
            sets.append("result=?")
            args.append(_json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result))
        if not sets:
            conn.close()
            return
        sets.append("updated_at=datetime('now','localtime')")
        args.append(task_id)
        cur.execute(f"UPDATE ai_tasks SET {', '.join(sets)} WHERE id=?", tuple(args))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_ocr_cache(file_hash):
    """按文件哈希读取已缓存的 OCR 文本；无缓存返回 None。

    OCR 很慢（150 DPI 下每页约 10 秒），同一个文件绝不应该识别第二次。
    """
    if not file_hash:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT text FROM ocr_cache WHERE file_hash=?", (file_hash,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def save_ocr_cache(file_hash, text, pages=0, engine=None):
    """保存 OCR 结果，供后续复用。"""
    if not file_hash or not text:
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO ocr_cache (file_hash, text, pages, engine) VALUES (?,?,?,?)",
            (file_hash, text, int(pages or 0), engine or ""),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        logger.exception("[save_ocr_cache] 保存 OCR 缓存失败")
        return False


def get_ai_tasks(status=None, limit=100, offset=0):
    try:
        conn = get_db()
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM ai_tasks WHERE status=? ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                (status, int(limit), int(offset)),
            )
        else:
            cur.execute(
                "SELECT * FROM ai_tasks ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_ai_task(task_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM ai_tasks WHERE id=?", (task_id,))
        r = cur.fetchone()
        conn.close()
        return dict(r) if r else None
    except Exception:
        return None


# ---------- 图书 / 分类 ----------
def get_book_by_id(book_id):
    if not book_id:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
        r = cur.fetchone()
        conn.close()
        return dict(r) if r else None
    except Exception:
        return None


def _load_all_books():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM books ORDER BY cat1, cat2, sort_order, name")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        logger.exception("[get_all_books] 读取图书列表失败")
        return []


def get_all_books():
    """返回全部图书（dict 列表）。请求级缓存（#22/#23）；deepcopy=True 防止调用方就地修改污染复用。"""
    return _request_cache("all_books", _load_all_books, deepcopy=True)


def get_books_need_indexing(force=False):
    """返回需要（重新）建立索引的图书，供「增量重建」使用。

    force=True  → 返回全部图书（全量重建）
    force=False → 只返回以下情况的书：
      1) 没有任何分块（从未索引过）；
      2) ai_status 不是 done（上次失败或待处理）；
      3) 文件哈希与上次成功索引时记录的不同（文件被替换过）。

    这样已索引且文件未变的书会被跳过，避免每次重建都推倒重来
    （接了 OCR 后尤其重要，否则每本扫描件都要重跑几十分钟）。
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM books ORDER BY cat1, cat2, sort_order, name")
        books = [dict(r) for r in cur.fetchall()]
        if force:
            conn.close()
            return books
        # 一次性聚合各书分块数，避免逐本书 COUNT(*) 的 N+1 查询
        try:
            cur.execute("SELECT book_id, COUNT(*) FROM document_chunks GROUP BY book_id")
            chunk_counts = {r[0]: (r[1] or 0) for r in cur.fetchall()}
        except Exception:
            chunk_counts = {}
        need = []
        for b in books:
            bid = b.get("id")
            if not bid:
                continue
            cnt = chunk_counts.get(bid, 0)
            if cnt == 0 or (b.get("ai_status") or "") != "done":
                need.append(b)
                continue
            fh = b.get("file_hash") or ""
            ih = b.get("indexed_hash") or ""
            # 文件被替换过（有哈希且记录的哈希不同）→ 需要重建
            if fh and fh != ih:
                need.append(b)
                continue
        conn.close()
        return need
    except Exception:
        logger.exception("[get_books_need_indexing] 判断待索引图书失败")
        return []


def mark_book_indexed(book_id, file_hash=None):
    """标记某本书已成功建立索引，记录当时的文件哈希供增量判断。"""
    if not book_id:
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        if file_hash:
            cur.execute(
                "UPDATE books SET indexed_hash=?, ai_status='done' WHERE id=?",
                (file_hash, book_id),
            )
        else:
            cur.execute("UPDATE books SET ai_status='done' WHERE id=?", (book_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        logger.exception("[mark_book_indexed] 标记索引完成失败")
        return False


_table_cols_cache: Dict[str, set] = {}

def _table_columns(table: str):
    """返回表的真实列名集合（连接级无，模块级缓存）。读失败返回 None。"""
    if table not in _table_cols_cache:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            _table_cols_cache[table] = {row[1] for row in cur.fetchall()}
            conn.close()
        except Exception:
            return None
    return _table_cols_cache[table]

import re as _re_dyn
_IDENT_RE = _re_dyn.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def update_book(book_id, **fields):
    """更新图书字段。列名动态拼接前先过白名单（表结构真实列），
    白名单不可用时退化为标识符正则过滤，双重防线防 SQL 列名注入。"""
    if not book_id or not fields:
        return
    try:
        allowed = _table_columns("books")
        conn = get_db()
        cur = conn.cursor()
        cols, vals = [], []
        for k, v in fields.items():
            if allowed is not None:
                if k not in allowed:
                    continue
            elif not _IDENT_RE.match(str(k)):
                continue
            cols.append(f"{k}=?")
            vals.append(v)
        if not cols:
            conn.close()
            return
        vals.append(book_id)
        cur.execute(f"UPDATE books SET {', '.join(cols)} WHERE id=?", tuple(vals))
        conn.commit()
        conn.close()
    except Exception:
        pass


def move_book_order(book_id, direction):
    """移动图书顺序（在所属 cat1+cat2 分组内上下移动），返回 True/False"""
    if not book_id or direction not in ("up", "down"):
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT cat1, cat2, sort_order FROM books WHERE id=?", (book_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False
        cat1, cat2, current_order = row["cat1"], row["cat2"], row["sort_order"] or 0
        if direction == "up":
            cur.execute(
                "SELECT id, sort_order FROM books WHERE cat1=? AND cat2=? AND sort_order<? "
                "ORDER BY sort_order DESC LIMIT 1", (cat1, cat2, current_order))
        else:
            cur.execute(
                "SELECT id, sort_order FROM books WHERE cat1=? AND cat2=? AND sort_order>? "
                "ORDER BY sort_order ASC LIMIT 1", (cat1, cat2, current_order))
        neighbor = cur.fetchone()
        if not neighbor:
            conn.close()
            return False
        cur.execute("UPDATE books SET sort_order=? WHERE id=?", (neighbor["sort_order"], book_id))
        cur.execute("UPDATE books SET sort_order=? WHERE id=?", (current_order, neighbor["id"]))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def move_book_to_edge(book_id, direction):
    """将图书置顶或置底（direction: top / bottom），返回 True/False"""
    if not book_id or direction not in ("top", "bottom"):
        return False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT cat1, cat2, sort_order FROM books WHERE id=?", (book_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False
        cat1, cat2, current_order = row["cat1"], row["cat2"], row["sort_order"] or 0
        if direction == "top":
            cur.execute("SELECT COALESCE(MIN(sort_order), 0) FROM books WHERE cat1=? AND cat2=?", (cat1, cat2))
            min_ord = cur.fetchone()[0]
            if min_ord == current_order:
                conn.close()
                return True
            if min_ord == 0:
                cur.execute(
                    "UPDATE books SET sort_order=sort_order+1 WHERE cat1=? AND cat2=? AND id!=?",
                    (cat1, cat2, book_id))
                new_order = 0
            else:
                new_order = min_ord - 1
            cur.execute("UPDATE books SET sort_order=? WHERE id=?", (new_order, book_id))
        else:
            cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM books WHERE cat1=? AND cat2=?", (cat1, cat2))
            max_ord = cur.fetchone()[0]
            cur.execute("UPDATE books SET sort_order=? WHERE id=?", (max_ord + 1, book_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def update_book_ai_fields(book_id, summary=None, tags=None, ai_review=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cols, vals = [], []
        if summary is not None:
            cols.append("summary=?")
            vals.append(summary)
        if tags is not None:
            cols.append("tags=?")
            vals.append(tags)
        if ai_review is not None:
            cols.append("ai_review=?")
            vals.append(ai_review)
        if not cols:
            conn.close()
            return
        vals.append(book_id)
        cur.execute(f"UPDATE books SET {', '.join(cols)} WHERE id=?", tuple(vals))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_all_categories():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM categories ORDER BY parent_id, sort_order, name")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ---------- 用户阅读/笔记/读后感 ----------
def get_user_notes(user_id=None, book_id=None, include_public=False):
    try:
        conn = get_db()
        cur = conn.cursor()
        sql = "SELECT n.*, u.account AS user_account, u.name AS user_name, b.name AS book_name " \
              "FROM notes n LEFT JOIN users u ON n.user_id=u.id LEFT JOIN books b ON n.book_id=b.id WHERE 1=1"
        args = []
        if user_id:
            sql += " AND n.user_id=?"
            args.append(user_id)
        if book_id:
            sql += " AND n.book_id=?"
            args.append(book_id)
        if not include_public and not user_id:
            sql += " AND n.is_public=1"
        sql += " ORDER BY n.id DESC"
        cur.execute(sql, tuple(args))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_user_reviews(user_id=None, book_id=None, include_public=False):
    try:
        conn = get_db()
        cur = conn.cursor()
        sql = "SELECT r.*, u.account AS user_account, u.name AS user_name, b.name AS book_name " \
              "FROM reviews r LEFT JOIN users u ON r.user_id=u.id LEFT JOIN books b ON r.book_id=b.id WHERE 1=1"
        args = []
        if user_id:
            sql += " AND r.user_id=?"
            args.append(user_id)
        if book_id:
            sql += " AND r.book_id=?"
            args.append(book_id)
        if not include_public and not user_id:
            sql += " AND r.is_public=1"
        sql += " ORDER BY r.id DESC"
        cur.execute(sql, tuple(args))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_user_reading_history(user_id=None, book_id=None, limit=100):
    try:
        conn = get_db()
        cur = conn.cursor()
        sql = "SELECT r.*, r.updated_at AS last_read_at, b.name AS book_name, b.cat1, b.cat2, u.account AS user_account FROM reading_records r " \
              "LEFT JOIN books b ON r.book_id=b.id LEFT JOIN users u ON r.user_id=u.id WHERE 1=1"
        args = []
        if user_id:
            sql += " AND r.user_id=?"
            args.append(user_id)
        if book_id:
            sql += " AND r.book_id=?"
            args.append(book_id)
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(int(limit))
        cur.execute(sql, tuple(args))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ---------- 问答会话历史（服务端存储） ----------
def get_chat_history(user_id, max_rounds=10, scope="homepage"):
    """返回该用户最近 max_rounds 轮问答历史（[
        {"role":..., "content":...}] ，时间正序）。"""
    if not user_id:
        return []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content FROM (SELECT * FROM chat_histories WHERE user_id=? AND scope=? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (user_id, scope, int(max_rounds) * 2),
        )
        rows = [{"role": r[0], "content": r[1] or ""} for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []

def append_chat_history(user_id, role, content, max_rounds=20, scope="homepage"):
    """追加一条历史，并按 max_rounds 保留最新若干轮（裁掉更早的）。"""
    if not user_id or not role:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_histories (user_id, scope, role, content) VALUES (?,?,?,?)",
            (user_id, scope, role, content or ""),
        )
        cur.execute(
            "DELETE FROM chat_histories WHERE user_id=? AND scope=? AND id NOT IN "
            "(SELECT id FROM chat_histories WHERE user_id=? AND scope=? ORDER BY id DESC LIMIT ?)",
            (user_id, scope, user_id, scope, int(max_rounds) * 2),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("[append_chat_history] 写入问答历史失败")

def clear_chat_history(user_id, scope="homepage"):
    if not user_id:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_histories WHERE user_id=? AND scope=?", (user_id, scope))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ---------- 收藏 ----------
def get_user_favorites(user_id=None, book_id=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        sql = "SELECT f.*, b.name AS book_name, b.cat1, b.cat2, b.path AS book_path " \
              "FROM favorites f LEFT JOIN books b ON f.book_id=b.id WHERE 1=1"
        args = []
        if user_id:
            sql += " AND f.user_id=?"
            args.append(user_id)
        if book_id:
            sql += " AND f.book_id=?"
            args.append(book_id)
        sql += " ORDER BY f.id DESC"
        cur.execute(sql, tuple(args))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ============================================================
# 兼容层 & helpers（app.py / 路由层引用）
# ============================================================

# 函数别名（保持 app.py 中习惯的命名）
init_db_once = init_db

def ensure_tables_and_columns():
    """幂等地确保所有核心表/字段存在。"""
    try:
        init_db()
    except Exception as e:
        import traceback; traceback.print_exc()
        raise

_get_conn = get_db  # 历史兼容别名


# 纯权限 key 列表（给模板/前端渲染权限点 chips 用）
ALL_PERMISSION_KEYS = [p[0] for p in ALL_PERMISSIONS]


def _decorate_user_row(user_dict):
    """给从 users 表查出的字典补充前端/逻辑需要的派生字段。"""
    if not user_dict:
        return None
    import json as _json
    # inactive 派生字段：is_active = 0 则视为 inactive
    user_dict["inactive"] = not bool(user_dict.get("is_active", 1))
    # permissions_json：解析后的权限点列表
    raw = user_dict.get("permissions") or "[]"
    try:
        if isinstance(raw, str):
            user_dict["permissions_json"] = _json.loads(raw)
        else:
            user_dict["permissions_json"] = list(raw) if raw else []
    except Exception:
        user_dict["permissions_json"] = []
    return user_dict


def get_all_users():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = [_decorate_user_row(dict(r)) for r in cur.fetchall()]
    conn.close()
    return rows


def get_user_by_id(user_id):
    if not user_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return _decorate_user_row(dict(row)) if row else None


def get_user_by_account(account):
    if not account:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE account=?", (account,))
    row = cur.fetchone()
    conn.close()
    return _decorate_user_row(dict(row)) if row else None


def build_current_user(user_id):
    """用于 before_request：根据 session 里的 uid 构造 g.current_user。"""
    u = get_user_by_id(user_id)
    return u

