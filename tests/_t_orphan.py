"""回归测试：孤儿文件扫描与回收（modules/book_lib/orphan.py）

覆盖：
  ① 被引用的文件不算孤儿；未引用的算；保护期内的不算；`_` 前缀跳过
  ② purge 只删孤儿，被引用的文件必须跳过（skipped）
  ③ dry_run 不得真的删除
  ④ 上传根目录之外的路径必须被拒绝（防穿越）
  ⑤ 清理后空目录被回收
  ⑥ delete_book 删完文件后同样回收空目录

不碰真实 book_manager.db 与真实 uploads/（全部指向 tempfile 临时目录）。
"""
import os
import sys
import time
import sqlite3
import tempfile
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import common.utils as U  # noqa
import core.db_base as db_base  # noqa
import modules.book_lib.db_ops as db_ops  # noqa
import modules.book_lib.orphan as orphan  # noqa

fails = []


def check(cond, msg):
    print(("PASS" if cond else "FAIL"), "-", msg)
    if not cond:
        fails.append(msg)


def main():
    tmp = tempfile.mkdtemp(prefix="orphan_")
    up = os.path.join(tmp, "uploads", "books")
    c1 = os.path.join(up, "cat1", "cat2")
    os.makedirs(c1, exist_ok=True)

    U.UPLOAD_FOLDER = up
    U.BASE_DIR = tmp

    # 用真实 books 表结构建临时库
    db_path = os.path.join(tmp, "t.db")
    real = sqlite3.connect(os.path.join(BASE, "book_manager.db"))
    ddl = real.execute("SELECT sql FROM sqlite_master WHERE name='books'").fetchone()[0]
    real.close()
    c = sqlite3.connect(db_path)
    c.execute(ddl)
    for t in ("CREATE TABLE reading_records (book_id TEXT)",
              "CREATE TABLE notes (book_id TEXT)",
              "CREATE TABLE favorites (book_id TEXT)",
              "CREATE TABLE resource_permissions (resource_type TEXT, resource_id TEXT)",
              "CREATE TABLE document_chunks (book_id TEXT)",
              "CREATE TABLE ai_parent_chunks (book_id TEXT)"):
        try:
            c.execute(t)
        except Exception:
            pass
    c.commit()
    c.close()
    db_base.DB_PATH = db_path

    def mk(name, age_days=30):
        p = os.path.join(c1, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x" * 2048)
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
        return p

    def seed(bid, path, savepath):
        cc = sqlite3.connect(db_path)
        cc.execute("INSERT INTO books (id,name,cat1,cat2,path,savepath) VALUES (?,?,?,?,?,?)",
                   (bid, "b-" + bid, "cat1", "cat2", path, savepath))
        cc.commit()
        cc.close()

    # ① 造四类文件
    used = mk("used.pdf")                       # 被 books 引用
    free = mk("free.pdf")                       # 无引用、很旧 → 孤儿
    fresh = mk("fresh.pdf", age_days=0)          # 无引用但刚创建 → 保护期内
    meta = mk("_meta.json")                     # 下划线前缀 → 跳过
    seed("b1", os.path.relpath(used, tmp).replace("\\", "/"), used)

    items = orphan.scan_orphan_files(min_age_sec=3600)
    rels = {i["name"] for i in items}
    print("\n--- ① 扫描结果 ---")
    for i in items:
        print("   孤儿:", i["rel"], i["size"], i["mtime_str"])
    check("used.pdf" not in rels, "① 被引用的文件不算孤儿")
    check("free.pdf" in rels, "① 无引用的旧文件算孤儿")
    check("fresh.pdf" not in rels, "① 保护期内的新文件不算孤儿")
    check("_meta.json" not in rels, "① `_` 前缀文件被跳过")
    check(orphan.summary(items)["count"] == 1, "① 孤儿数为 1")

    # ② dry_run 不能删
    print("\n--- ② dry_run 预演 ---")
    d, f, freed, s = orphan.purge_orphan_files([free], dry_run=True)
    check(os.path.exists(free), "② dry_run 不实际删除文件")
    check(d == 1 and freed == 2048, "② dry_run 正确返回预演结果 (deleted=%d freed=%d)" % (d, freed))

    # ③ 真删：只删孤儿
    print("\n--- ③ 实际清理 ---")
    outside = os.path.join(tmp, "outside.txt")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("y" * 512)
    d, f, freed, s = orphan.purge_orphan_files(
        [free, used, outside], user_id="u_test", ip="127.0.0.1")
    print("   deleted=%d failed=%d skipped=%d freed=%d" % (d, f, s, freed))
    check(not os.path.exists(free), "③ 孤儿文件已删除")
    check(os.path.exists(used), "③ 被引用文件未被删除（skipped）")
    check(os.path.exists(outside), "③ 根目录外路径被拒绝（防穿越）")
    check(d == 1 and s == 2, "③ 计数正确：删 1、跳 2")

    # ④ 已被引用后不再是孤儿
    print("\n--- ④ 入库后不再算孤儿 ---")
    check(len(orphan.scan_orphan_files(min_age_sec=3600)) == 0, "④ 清理后无孤儿（剩余均被引用/受保护）")

    # ⑤ 删书后空目录回收（用独立子目录，避免与仍被引用的 used.pdf 混在一起）
    print("\n--- ⑤ delete_book 回收空目录 ---")
    c2 = os.path.join(up, "catX", "catY")
    os.makedirs(c2, exist_ok=True)
    p2 = os.path.join(c2, "book2.pdf")
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write("z" * 1024)
    seed("b2", os.path.relpath(p2, tmp).replace("\\", "/"), p2)
    ok_del, msg = db_ops.delete_book("b2")
    check(ok_del, "⑤ delete_book 成功 (msg=%r)" % msg)
    check(not os.path.exists(p2), "⑤ 删书同时删掉了磁盘文件")
    check(not os.path.isdir(c2), "⑤ 变空的分类目录 catX/catY 被回收")
    check(os.path.isdir(c1), "⑤ 仍有引用文件的目录未被误删")
    check(os.path.exists(used), "⑤ 被引用的 used.pdf 依然完好")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("失败项: %d" % len(fails))
    for x in fails:
        print("  -", x)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
