"""
Book Library 图书模块 - 业务层数据库访问（db_ops）。

仅包含与图书业务相关的高级DAL函数；基础表结构/基础查询在 core.db_base 中。
"""
import os
import sys
import uuid as _uuid
import logging as _logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

_log = _logging.getLogger(__name__)

from core.db_base import (
    get_db,
    add_log,
    invalidate_vector_mappings_by_book,
    clean_orphan_vectors,
    delete_document_chunks,
    delete_parent_chunks,
    set_resource_permission,
    delete_resource_permission,
    get_all_resource_permissions,
    get_all_users,
    get_book_by_id,
    user_can_view_book,
    get_book_effective_level,
    filter_books_by_permission,
)

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(os.path.dirname(BASE_DIR))  # 修正到项目根

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "books")
ALLOWED_EXTENSIONS = {'pdf','doc','docx','xls','xlsx','ppt','pptx','zip','rar','7z','txt','csv','log',
                      'jpg','jpeg','png','gif','bmp','mp4','avi','mov','dwg','dxf','scl','awl','l5x','step','stp'}
MAX_FILE_SIZE = 50 * 1024 * 1024


def _uid(prefix="b"):
    return prefix + "_" + _uuid.uuid4().hex[:12]


# ============================================================
# 图书 CRUD
# ============================================================
def add_book(name, cat1, cat2, path, upload_by=None, file_hash=None,
             savepath=None, file_ext=None, file_size=None, owner_id=None):
    book_id = _uid("b")
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 探测可用列（老库可能是 upload_at 而非 created_at）
    try:
        cols = {r[1] for r in cur.execute("PRAGMA table_info(books)").fetchall()}
    except Exception:
        cols = set()
    time_col = "created_at" if "created_at" in cols else ("upload_at" if "upload_at" in cols else None)
    has_save = "savepath" in cols
    has_fext = "file_ext" in cols
    has_fsize = "file_size" in cols
    try:
        if time_col and has_save and has_fext and has_fsize:
            cur.execute(
                "INSERT INTO books (id, name, cat1, cat2, path, upload_by, file_hash, sort_order, "
                f"{time_col}, savepath, file_ext, file_size) VALUES (?,?,?,?,?,?,?,0,?,?,?,?)",
                (book_id, name, cat1, cat2, path, upload_by, file_hash, now,
                 savepath, file_ext, file_size),
            )
        elif time_col:
            cur.execute(
                "INSERT INTO books (id, name, cat1, cat2, path, upload_by, file_hash, sort_order, "
                f"{time_col}) VALUES (?,?,?,?,?,?,?,0,?)",
                (book_id, name, cat1, cat2, path, upload_by, file_hash, now),
            )
        else:
            cur.execute(
                "INSERT INTO books (id, name, cat1, cat2, path, upload_by, file_hash, sort_order) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (book_id, name, cat1, cat2, path, upload_by, file_hash),
            )
    except sqlite3.OperationalError:
        # 最终兜底：仅写核心列
        cur.execute(
            "INSERT INTO books (id, name, cat1, cat2, path, upload_by) VALUES (?,?,?,?,?,?)",
            (book_id, name, cat1, cat2, path, upload_by),
        )
    conn.commit()
    # 写入归属人（私人图书馆功能依赖 owner_id）
    if owner_id:
        try:
            cur.execute("UPDATE books SET owner_id=? WHERE id=?", (owner_id, book_id))
            conn.commit()
        except Exception:
            pass
    conn.close()
    return book_id


import sqlite3  # noqa: E402 延后导入避免循环依赖


def _book_has_col(col: str) -> bool:
    try:
        conn = get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(books)").fetchall()]
        conn.close()
        return col in cols
    except Exception:
        return False


def _ensure_book_extra_cols():
    """懒加载：确保 books 表有 savepath/file_ext/file_size/summary/tags 等扩展字段。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        for col, ddl in [
            ("savepath", "TEXT"), ("file_ext", "TEXT"), ("file_size", "INTEGER"),
            ("summary", "TEXT"), ("tags", "TEXT"), ("ai_review", "TEXT"),
            ("created_at", "TEXT"), ("owner_id", "TEXT"),
            # 个人分类（private_categories 表）：与 cat1/cat2（全局分类）二选一。
            # pc1 非空 = 挂在个人分类下 → 只在「我的私有图书」显示，不进全局分类树。
            ("pc1", "TEXT"), ("pc2", "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE books ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception:
        pass


def backfill_book_owner_id():
    """历史数据回填：owner_id 为空的旧书，用 upload_by 反查上传者用户 id。

    - upload_by 命中某用户 account → owner_id = 该用户 id
    - upload_by 本身就是某用户 id → 直接采用
    - 都查不到 → 保持 NULL（视为无归属人）
    幂等：仅更新 owner_id 为空且 upload_by 非空的书。
    """
    try:
        users = get_all_users() or []
        account_to_id = {}
        id_set = set()
        for u in users:
            acc = (u.get("account") or "").strip()
            uid = u.get("id")
            if acc:
                account_to_id[acc] = uid
            if uid:
                id_set.add(str(uid))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, upload_by, owner_id FROM books WHERE owner_id IS NULL AND upload_by IS NOT NULL AND upload_by <> ''")
        rows = cur.fetchall()
        for r in rows:
            bid, upload_by, _ = r["id"], r["upload_by"], r["owner_id"]
            owner = None
            if upload_by in account_to_id:
                owner = account_to_id[upload_by]
            elif str(upload_by) in id_set:
                owner = upload_by
            if owner:
                try:
                    cur.execute("UPDATE books SET owner_id=? WHERE id=?", (owner, bid))
                except Exception:
                    pass
        conn.commit()
        conn.close()
    except Exception:
        pass


def set_book_visibility(book_id, visibility, owner_id=None):
    """设置图书可见性：private=仅 owner+管理员可见（selected+allowed_users=[owner]）；
    public=清除私人权限（回到分类/默认公开）。"""
    if visibility == "private":
        if not owner_id:
            b = get_book_by_id(book_id)
            owner_id = (b or {}).get("owner_id") if b else None
        if owner_id:
            set_resource_permission(book_id, "selected", "book", [str(owner_id)])
        else:
            # 无归属人时仍置 selected（仅管理员可见），避免权限真空
            set_resource_permission(book_id, "selected", "book", None)
    else:
        delete_resource_permission(book_id, "book")


def get_my_books(user_id, private_only=False):
    """返回某用户的图书。private_only=True 时仅返回其私有（selected）图书。"""
    from core.db_base import get_all_books
    if not user_id:
        return []
    books = get_all_books() or []
    mine = [b for b in books if str(b.get("owner_id") or "") == str(user_id)]
    if private_only:
        mine = [b for b in mine if get_book_effective_level(b) == "selected"]
    mine.sort(key=lambda b: (b.get("cat1") or "", b.get("cat2") or "", b.get("name") or ""))
    return mine


# ============================================================
# 个人分类（private_categories）
# 与全局 categories 完全隔离：不进共享分类树，仅本人可见/可用。
# ============================================================
def get_private_categories(user_id):
    """返回某用户的个人分类（含一级与二级），按 sort_order/name 排序。"""
    if not user_id:
        return []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM private_categories WHERE user_id=? ORDER BY sort_order, name",
            (str(user_id),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _pcat_sibling_count(cur, user_id, parent_id):
    cur.execute(
        "SELECT COUNT(*) FROM private_categories WHERE user_id=? AND COALESCE(parent_id,'')=?",
        (str(user_id), str(parent_id or "")),
    )
    return cur.fetchone()[0]


def add_private_category(user_id, name, parent_id=None):
    """新增个人分类。重名检查只在同一用户、同一父级范围内（不与全局分类互相抢名）。"""
    if not user_id:
        return None, "参数不完整"
    name = (str(name) or "").strip()[:30]
    if not name:
        return None, "分类名称不能为空"
    try:
        import uuid as _uuid
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM private_categories WHERE user_id=? AND name=? AND COALESCE(parent_id,'')=?",
            (str(user_id), name, str(parent_id or "")),
        )
        if cur.fetchone():
            conn.close()
            return None, "分类名称已存在"
        cid = "pc_" + _uuid.uuid4().hex[:12]
        cur.execute(
            "INSERT INTO private_categories (id, user_id, name, parent_id, sort_order) VALUES (?,?,?,?,?)",
            (cid, str(user_id), name, parent_id, _pcat_sibling_count(cur, user_id, parent_id)),
        )
        conn.commit()
        conn.close()
        return cid, None
    except Exception as e:
        return None, str(e)


def rename_private_category(cat_id, user_id, name):
    """个人分类改名。仅本人可改。"""
    if not cat_id or not user_id:
        return False, "参数不完整"
    name = (str(name) or "").strip()[:30]
    if not name:
        return False, "分类名称不能为空"
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE private_categories SET name=? WHERE id=? AND user_id=?",
            (name, cat_id, str(user_id)),
        )
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0, None if n > 0 else "分类不存在"
    except Exception as e:
        return False, str(e)


def delete_private_category(cat_id, user_id):
    """删除个人分类，连带删除其下的二级分类。仅本人可删。
    注：挂在被删分类下的书不会删除，仅失去 pc1/pc2 归属（仍在私有图书页列出）。"""
    if not cat_id or not user_id:
        return False, "参数不完整"
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM private_categories WHERE (id=? OR parent_id=?) AND user_id=?",
            (cat_id, cat_id, str(user_id)),
        )
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0, None if n > 0 else "分类不存在"
    except Exception as e:
        return False, str(e)


def ensure_schema():
    _ensure_book_extra_cols()
    backfill_book_owner_id()


def delete_book(book_id):
    """删除图书（记录+文件+资源权限）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return False, "图书不存在"
    row = dict(r)
    ok_del, msg = True, ""
    try:
        # 删除数据库记录
        cur.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()
        # 删相关阅读记录/笔记/收藏/向量/文件
        for table, sql in [
            ("reading_records", "DELETE FROM reading_records WHERE book_id=?"),
            ("notes", "DELETE FROM notes WHERE book_id=?"),
            ("favorites", "DELETE FROM favorites WHERE book_id=?"),
        ]:
            try:
                cur.execute(sql, (book_id,))
                conn.commit()
            except Exception:
                pass
        try:
            cur.execute("DELETE FROM resource_permissions WHERE resource_type='book' AND resource_id=?", (book_id,))
            conn.commit()
        except Exception:
            pass
        try:
            invalidate_vector_mappings_by_book(book_id)
        except Exception:
            pass
        #同步物理清理失效映射行，避免 ai_vector_mappings 长期膨胀。
        # 说明：faiss（IndexFlatIP）无法定点删除向量，清理映射后这些向量成为孤儿槽位，
        # 检索时因查不到映射被直接跳过（不会返回已删内容），仅占搜索坑位；
        # 彻底清零孤儿需做一次全量重建（vector_store.rebuild_all），
        # 因此**不需要每删一本书就全量重建**，按孤儿率阈值定期处理即可。
        try:
            clean_orphan_vectors()
        except Exception:
            pass
        #删书必须连带删除正文分块与父块。
        # 原实现只失效/清理 ai_vector_mappings，document_chunks 与 ai_parent_chunks
        # 的正文行**永久残留**：实测全部图书删除后仍留下 127 行分块 + 50 行父块，
        # 既污染 _index_fingerprint 的重建工作量，又让「全量重建」在空书库场景下失效
        # （见 config_center/routes.py 的空列表短路）。
        # 顺序：紧接 clean_orphan_vectors 之后，语义上「先失效映射、再清正文」。
        for _fn in (delete_document_chunks, delete_parent_chunks):
            try:
                _fn(book_id)
            except Exception:
                pass
        # 删实体文件
        #原先 os.remove 失败一律 `except: pass`，删不掉照样返回成功，
        # 磁盘残留完全无从察觉（本次 273 MB 孤儿正是这样悄悄攒出来的）。
        # 改为失败即告警日志，让"记录删了文件还在"这件事可观测。
        # 只处理 savepath（绝对路径）。path 是相对路径，若在这里直接
        # os.path.exists 会按**进程 CWD** 解析，与下面 resolve_file_path
        # （按 BASE_DIR）语义不一致 —— 换个启动目录就删不掉且毫无提示。
        # 相对 path 统一交给下方的 resolve_file_path。
        p = row.get("savepath")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception as e:
                _log.warning("删书[%s] 文件删除失败: %s (%s)", book_id, p, e)
        # 旧的 __upload__ 前缀路径也尝试解析清理
        try:
            from common.utils import resolve_file_path
            path_val = row.get("path") or ""
            full, _ = resolve_file_path(path_val)
            if full and os.path.exists(full):
                try: os.remove(full)
                except Exception as e:
                    _log.warning("删书[%s] 文件删除失败(解析路径): %s (%s)", book_id, full, e)
        except Exception:
            pass
        # 删完顺手回收因之变空的分类目录，避免留下层层空文件夹
        try:
            from common.utils import UPLOAD_FOLDER
            from modules.book_lib.orphan import _prune_empty_dirs
            _root = os.path.abspath(UPLOAD_FOLDER)
            _prune_empty_dirs(_root, _root)
        except Exception:
            pass
    except Exception as e:
        ok_del, msg = False, str(e)
    conn.close()
    return ok_del, msg


def list_books_tree(user) -> Dict[str, Any]:
    """按分类分组返回图书树 + 权限过滤。"""
    from core.db_base import get_all_books, get_all_categories
    ensure_schema()
    all_books = get_all_books()
    all_books = filter_books_by_permission(all_books, user)
    cats = get_all_categories()
    cat1_list = [c for c in cats if not c.get("parent_id")]
    cat1_list.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    cat1_order = [c.get("name") for c in cat1_list]
    cat2_map: Dict[str, Any] = {}
    for c in cats:
        if c.get("parent_id"):
            parent_name = None
            for pc in cats:
                if str(pc.get("id")) == str(c.get("parent_id")):
                    parent_name = pc.get("name")
                    break
            if parent_name:
                cat2_map.setdefault(parent_name, []).append(c)
    for k, v in cat2_map.items():
        v.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    result: Dict[str, Any] = {}
    # ========== 分类分组 ==========
    for b in all_books:
        # 挂在「个人分类」(pc1) 下的书属于私人图书馆，不进全局分类树（对任何人都不显示，含本人）
        if (b.get("pc1") or "").strip():
            continue
        c1 = b.get("cat1") or "未分类"
        c2 = b.get("cat2") or "未分类"
        # 用统一的权限判定函数（user_can_view_book 已正确处理 selected 指定用户 / 登录 / 公开 / 管理员），
        # 避免扁平 perm_map 丢失 allowed_users 导致 owner 的私有书"看得见打不开"。
        b["effective_permission"] = get_book_effective_level(b)
        b["can_view"] = user_can_view_book(b, user)
        b["can_download"] = b["can_view"]
        result.setdefault(c1, {})
        result[c1].setdefault(c2, [])
        result[c1][c2].append(b)
    # ========== 排序（按分类顺序，二级分类按 cat2_map 中的顺序） ==========
    sorted_result = {}
    for c1 in cat1_order:
        if c1 in result:
            sub = result[c1]
            ordered_sub = {}
            c2_list = cat2_map.get(c1, [])
            c2_order = [c.get("name") for c in c2_list]
            for c2n in c2_order:
                if c2n in sub: ordered_sub[c2n] = sub[c2n]
            for k, v in sub.items():
                if k not in ordered_sub: ordered_sub[k] = v
            sorted_result[c1] = ordered_sub
    for remain_c1 in [k for k in result.keys() if k not in sorted_result]:
        sorted_result[remain_c1] = result[remain_c1]
    # 统计
    total_books = len(all_books)
    total_cats = sum(1 for k, sub in sorted_result.items() for _ in sub.keys())
    return {
        "tree": sorted_result,
        "cat1_list": cat1_list,
        "cat2_map": cat2_map,
        "total_books": total_books,
        "total_categories": total_cats,
    }


def batch_move_books(book_ids, new_cat1, new_cat2):
    if not book_ids:
        return 0
    conn = get_db()
    cur = conn.cursor()
    affected = 0
    for bid in book_ids:
        try:
            cur.execute("UPDATE books SET cat1=?, cat2=? WHERE id=?", (new_cat1, new_cat2, bid))
            affected += cur.rowcount
        except Exception:
            continue
    conn.commit()
    conn.close()
    return affected


def batch_delete_books(book_ids, user_id=None, ip=None):
    if not book_ids:
        return 0, 0
    ok_count = 0
    fail_count = 0
    for bid in book_ids:
        ok, _ = delete_book(bid)
        if ok: ok_count += 1
        else: fail_count += 1
    try:
        add_log(user_id, "批量删除图书", target_type="books",
                detail={"ids": list(book_ids), "ok": ok_count, "fail": fail_count}, ip=ip)
    except Exception:
        pass
    return ok_count, fail_count


# ============================================================
# 分类 CRUD（由 system_config 中的 config_access 权限管理）
# ============================================================
def _cuid():
    return "c_" + _uuid.uuid4().hex[:8]


def add_category(name, parent_id=None, sort_order=0, color=None):
    conn = get_db()
    cur = conn.cursor()
    cid = _cuid()
    cur.execute(
        "INSERT INTO categories (id, name, parent_id, sort_order, color) VALUES (?,?,?,?,?)",
        (cid, name, parent_id, int(sort_order), color),
    )
    conn.commit()
    conn.close()
    return cid


def update_category(cat_id, **fields):
    if not fields: return
    conn = get_db()
    cur = conn.cursor()
    cols, vals = [], []
    for k, v in fields.items():
        if k in ("name", "parent_id", "sort_order", "color"):
            cols.append(f"{k}=?")
            vals.append(v)
    if not cols:
        conn.close()
        return
    vals.append(cat_id)
    cur.execute(f"UPDATE categories SET {', '.join(cols)} WHERE id=?", tuple(vals))
    conn.commit()
    conn.close()


def delete_category(cat_id, user=None, ip=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE id=?", (cat_id,))
    cat = cur.fetchone()
    if not cat:
        conn.close()
        return False
    cat = dict(cat)
    cname = cat.get("name")
    parent_id = cat.get("parent_id")
    # 如果是一级分类，清理所有二级分类，图书移到未分类
    if not parent_id:
        cur.execute("SELECT id FROM categories WHERE parent_id=?", (cat_id,))
        sub_cats = [r[0] for r in cur.fetchall()]
        # 图书归未分类
        cur.execute("UPDATE books SET cat1='未分类', cat2='未分类' WHERE cat1=?", (cname,))
        for scid in sub_cats:
            cur.execute("DELETE FROM categories WHERE id=?", (scid,))
        cur.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    else:
        # 二级分类：把该分类下的图书归到「<父分类>/未分类」下
        cur.execute("SELECT name FROM categories WHERE id=?", (parent_id,))
        p_row = cur.fetchone()
        parent_name = p_row[0] if p_row else "未分类"
        cur.execute("UPDATE books SET cat1=?, cat2='未分类' WHERE cat1=? AND cat2=?",
                    (parent_name, parent_name, cname))
        cur.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()
    try:
        from core.db_base import add_log as _add_log
        uid = user.get("id") if user else None
        _add_log(uid, "删除分类", target_type="category", target_id=cat_id,
                 detail={"name": cname}, ip=ip)
    except Exception:
        pass
    return True


def move_category(cat_id, direction="up"):
    """上下移动分类（调整 sort_order）。"""
    from core.db_base import get_all_categories
    cats = get_all_categories()
    cat = next((c for c in cats if str(c.get("id")) == str(cat_id)), None)
    if not cat: return
    parent = cat.get("parent_id")
    siblings = [c for c in cats if (c.get("parent_id") or None) == (parent or None)]
    siblings.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    try:
        idx = next(i for i, c in enumerate(siblings) if str(c.get("id")) == str(cat_id))
    except StopIteration:
        return
    target = idx - 1 if direction == "up" else idx + 1
    if 0 <= target < len(siblings):
        c1, c2 = siblings[idx], siblings[target]
        o1, o2 = c1.get("sort_order") or 0, c2.get("sort_order") or 0
        update_category(c1.get("id"), sort_order=o2)
        update_category(c2.get("id"), sort_order=o1)


def ensure_default_categories():
    """确保至少存在未分类。"""
    from core.db_base import get_all_categories
    cats = get_all_categories()
    names = [c.get("name") for c in cats]
    conn = get_db()
    cur = conn.cursor()
    if "未分类" not in names:
        cid = _cuid()
        cur.execute(
            "INSERT INTO categories (id, name, parent_id, sort_order, color) VALUES (?,?,?,0,?)",
            (cid, "未分类", None, "#e5e7eb"),
        )
    conn.commit()
    conn.close()


# ============================================================
# 阅读/笔记/收藏/读后感 业务接口
# ============================================================
def upsert_reading(user_id, book_id, status="reading", progress=None):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "INSERT INTO reading_records (user_id, book_id, status, progress, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(user_id, book_id) DO UPDATE SET status=excluded.status, progress=excluded.progress, updated_at=?",
        (user_id, book_id, status, progress, now, now),
    )
    conn.commit()
    conn.close()


def add_note(user_id, book_id, content, is_public=True):
    if not content or not content.strip():
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes (user_id, book_id, content, is_public) VALUES (?,?,?,?)",
        (user_id, book_id, content.strip(), 1 if is_public else 0),
    )
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    return nid


def add_review(user_id, book_id, content, rating=5, is_public=True):
    if not content or not content.strip():
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reviews (user_id, book_id, content, rating, is_public) VALUES (?,?,?,?,?)",
        (user_id, book_id, content.strip(), max(1, min(5, int(rating))), 1 if is_public else 0),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def toggle_favorite(user_id, book_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM favorites WHERE user_id=? AND book_id=?", (user_id, book_id))
    exist = cur.fetchone()
    if exist:
        cur.execute("DELETE FROM favorites WHERE id=?", (exist[0],))
        conn.commit()
        conn.close()
        return False
    cur.execute("INSERT INTO favorites (user_id, book_id) VALUES (?,?)", (user_id, book_id))
    conn.commit()
    conn.close()
    return True


# ============================================================
# 统计 / 分类-布局缓存（保留原实现）
# ============================================================
import json as _json
import threading as _threading

_layout_cache: Dict[str, Any] = {}
_layout_lock = _threading.Lock()
_LAYOUT_CACHE_TTL = 60


def get_categories_layout_cache_key(c1_order):
    try:
        return "layout_" + str(_json.dumps(c1_order or [], ensure_ascii=False))[:32]
    except Exception:
        return "layout_default"


def get_cached_categories_layout(key: str, user) -> Optional[Dict[str, Any]]:
    with _layout_lock:
        item = _layout_cache.get(key)
        if not item: return None
        import time as _t
        if _t.time() - item.get("ts", 0) > _LAYOUT_CACHE_TTL:
            _layout_cache.pop(key, None)
            return None
        # 即使命中，也要对结果做当前用户的权限过滤
        tree = item.get("data") or {}
        filtered = {}
        for c1, sub in tree.items():
            filtered[c1] = {}
            for c2, book_list in sub.items():
                # 使用统一权限判定，正确处理 selected 指定用户（含 owner）/ 登录 / 公开 / 管理员
                ok_list = [b for b in book_list if user_can_view_book(b, user)]
                if ok_list: filtered[c1][c2] = ok_list
            if not filtered[c1]: filtered.pop(c1)
        return {
            "tree": filtered,
            "cat1_list": item.get("cat1_list", []),
            "cat2_map": item.get("cat2_map", {}),
            "total_books": sum(len(lst) for sub in filtered.values() for lst in sub.values()),
            "total_categories": sum(1 for sub in filtered.values() for _ in sub),
        }


def set_cached_categories_layout(key: str, data: Dict[str, Any]):
    with _layout_lock:
        import time as _t
        _layout_cache[key] = {"ts": _t.time(), **data}


def clear_categories_layout_cache():
    with _layout_lock:
        _layout_cache.clear()
