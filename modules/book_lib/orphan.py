"""孤儿文件扫描与回收。

背景（实测）：books 表已清空为 0 行，但 uploads/ 下仍残留 40 个文件共 273 MB，
任何删除操作都清不掉它们。根因是两步式上传：

    /api/upload_only  只把文件存盘、返回 path，**不写 books 表**
    /api/batch_add    后续才根据 path 建图书记录

只要中间断链（用户取消、刷新页面、入库失败、只上传了一部分），落盘的文件
就永远没有任何数据库行引用它 —— 而 delete_book 只能删"books 表里有记录"的
文件，于是这些文件成为永久孤儿。

本模块提供体系化的兜底：不依赖删除动作本身是否严谨，而是**反过来按磁盘事实
对账** —— 扫描上传根目录，凡不在 books 表引用集合内的文件即为孤儿。

安全约束：
  1. 待删路径必须解析在 UPLOAD_FOLDER 之内（防路径穿越）；
  2. 必须确实是孤儿（不在引用集合内）；
  3. mtime 必须早于 min_age_sec（默认 1 小时），保护正在上传/刚入库的文件；
  4. 跳过 `_` 前缀文件（项目惯例：元数据文件，如 _upload_order.json）。
"""
import os
import time

# 默认保护期：1 小时。刚上传但还没走完入库流程的文件不会被误判。
DEFAULT_MIN_AGE = 3600


def _upload_root():
    from common.utils import UPLOAD_FOLDER
    return os.path.abspath(UPLOAD_FOLDER)


def _norm(p):
    try:
        return os.path.normcase(os.path.abspath(p))
    except Exception:
        return None


def referenced_paths():
    """返回 books 表当前引用到的所有文件绝对路径集合（归一化）。

    books.path 可能是相对路径（uploads/books/...）或 __upload__ 前缀，
    books.savepath 是绝对路径；两者都收集。
    """
    refs = set()
    try:
        from core.db_base import get_db
        from common.utils import resolve_file_path
    except Exception:
        return refs

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT path, savepath FROM books")
        rows = cur.fetchall()
    except Exception:
        rows = []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for r in rows:
        vals = []
        try:
            vals = [r["path"], r["savepath"]]
        except Exception:
            try:
                vals = [r[0], r[1]]
            except Exception:
                vals = []
        for v in vals:
            if not v:
                continue
            # savepath 多为绝对路径，直接归一化；path 走统一解析
            n = _norm(v)
            if n and os.path.exists(v):
                refs.add(n)
                continue
            try:
                full, err = resolve_file_path(v)
                if full and not err:
                    n2 = _norm(full)
                    if n2:
                        refs.add(n2)
            except Exception:
                continue
    return refs


def scan_orphan_files(min_age_sec=DEFAULT_MIN_AGE):
    """扫描上传根目录，返回孤儿文件清单（只读，不删任何东西）。

    每项：{path, rel, name, size, mtime, mtime_str}
    """
    root = _upload_root()
    refs = referenced_paths()
    now = time.time()
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if fname.startswith("_"):
                continue
            full = os.path.join(dirpath, fname)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not st.st_size and False:
                continue
            if min_age_sec and (now - st.st_mtime) < min_age_sec:
                continue
            n = _norm(full)
            if not n or n in refs:
                continue
            try:
                rel = os.path.relpath(full, _upload_root()).replace("\\", "/")
            except ValueError:
                rel = fname
            out.append({
                "path": full,
                "rel": rel,
                "name": fname,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            })
    out.sort(key=lambda x: -x["size"])
    return out


def _prune_empty_dirs(root, stop_at):
    """删除因文件清空而变空的目录，一直清理到 stop_at（不含 stop_at）。"""
    stop = _norm(stop_at)
    removed = 0
    for dirpath, _dn, _fn in os.walk(root, topdown=False):
        if _norm(dirpath) == stop:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                removed += 1
        except OSError:
            pass
    return removed


def purge_orphan_files(paths, min_age_sec=DEFAULT_MIN_AGE, user_id=None, ip=None,
                       dry_run=False):
    """清理指定孤儿文件。

    只有同时满足「在上传根目录内 + 确实是孤儿 + 超过保护期」才删。
    返回 (deleted, failed, freed_bytes, skipped)。
    """
    root = _upload_root()
    root_n = _norm(root)
    refs = referenced_paths()
    now = time.time()
    deleted, failed, freed, skipped = 0, 0, 0, 0

    for p in paths or []:
        try:
            full = os.path.abspath(str(p))
        except Exception:
            failed += 1
            continue
        n = _norm(full)
        if not n:
            failed += 1
            continue
        # ① 必须落在上传根目录内
        if not (n == root_n or n.startswith(root_n + os.sep)):
            skipped += 1
            continue
        if not os.path.isfile(full):
            skipped += 1
            continue
        # ② 必须确实是孤儿
        if n in refs:
            skipped += 1
            continue
        # ③ 保护期内不删
        try:
            st = os.stat(full)
            if min_age_sec and (now - st.st_mtime) < min_age_sec:
                skipped += 1
                continue
            size = st.st_size
        except OSError:
            skipped += 1
            continue
        if dry_run:
            deleted += 1
            freed += size
            continue
        try:
            os.remove(full)
            deleted += 1
            freed += size
        except OSError:
            failed += 1

    if not dry_run and deleted:
        _prune_empty_dirs(root, root)
        try:
            from core.db_base import add_log
            add_log(user_id, "清理孤儿文件", target_type="file",
                    detail={"deleted": deleted, "failed": failed,
                            "freed_bytes": freed, "paths": [str(x) for x in (paths or [])][:50]},
                    ip=ip)
        except Exception:
            pass
    return deleted, failed, freed, skipped


def summary(items):
    total = sum(i["size"] for i in items)
    return {"count": len(items), "total_bytes": total,
            "total_mb": round(total / 1024 / 1024, 2)}
