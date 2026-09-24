"""回归测试：
  ① 删除图书必须连带清理 document_chunks / ai_parent_chunks 正文；
  ② 书库清空后全量重建的收尾 empty_index() 必须把索引**落盘**为空且保持「就绪」，
     不能让 is_mode_ready() 永远返回「索引文件不存在」（否则反复触发懒重建）。

不依赖真实嵌入模型 —— 用文本哈希生成确定性伪向量即可验证存储/清理链路。

背景（实测数据）：全部图书删除后残留 130 行document_chunks、50 行 ai_parent_chunks、
faiss ntotal=157 而有效映射仅 3（孤儿率 98%），且空书库下点「全量重建」直接短路。
"""
import os
import sys
import json
import sqlite3
import tempfile
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import numpy as np

import core.db_base as db_base  # noqa
import modules.ai_center.internal.config as ai_config  # noqa
import modules.ai_center.internal.vector_store as vs  # noqa
import modules.ai_center.internal.embedder as emb  # noqa
import modules.book_lib.db_ops as db_ops  # noqa

_DIM = 512

SCHEMA = [
    "CREATE TABLE books (id TEXT PRIMARY KEY, name TEXT, path TEXT, savepath TEXT)",
    """CREATE TABLE document_chunks (
        book_id TEXT, chunk_index INTEGER, chunk_text TEXT,
        parent_id INTEGER, parent_text TEXT, section_path TEXT, page INTEGER, section TEXT)""",
    """CREATE TABLE ai_parent_chunks (
        book_id TEXT, parent_index INTEGER, parent_text TEXT)""",
    """CREATE TABLE ai_vector_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT, file_hash TEXT, chunk_index INTEGER,
        faiss_index INTEGER, chunk_text_preview TEXT,
        is_valid INTEGER DEFAULT 1, created_at TEXT,
        parent_id INTEGER, mode TEXT)""",
    "CREATE TABLE experience_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)",
    # delete_book 会顺手清的关联表，缺表会被 try/except 吞掉，但建出来更贴近真实
    "CREATE TABLE reading_records (book_id TEXT)",
    "CREATE TABLE notes (book_id TEXT)",
    "CREATE TABLE favorites (book_id TEXT)",
    "CREATE TABLE resource_permissions (resource_type TEXT, resource_id TEXT)",
]


def main():
    tmp = tempfile.mkdtemp(prefix="delbook_purge_")
    fails = []

    def check(cond, msg):
        print(("PASS" if cond else "FAIL"), "-", msg)
        if not cond:
            fails.append(msg)

    try:
        db_path = os.path.join(tmp, "test.db")
        conn = sqlite3.connect(db_path)
        for ddl in SCHEMA:
            conn.execute(ddl)
        conn.commit()
        conn.close()
        db_base.DB_PATH = db_path

        vs._VECTOR_DIR = os.path.join(tmp, "vector_data")
        os.makedirs(vs._VECTOR_DIR, exist_ok=True)
        vs.unload_index()

        def fake_encode(texts, *a, **k):
            texts = list(texts)
            out = np.zeros((len(texts), _DIM), dtype="float32")
            for i, t in enumerate(texts):
                h = abs(hash(t)) % 100000
                out[i, 0] = 0.1 + (h % 100) / 100.0
                out[i, 1] = 0.2 + ((h // 7) % 100) / 100.0
            return out

        emb.encode = fake_encode
        orig_mode = ai_config.get_ai_mode
        ai_config.get_ai_mode = lambda: "light"
        vs._current_mode = lambda: "light"

        def q1(sql, args=()):
            c = sqlite3.connect(db_path)
            try:
                return c.execute(sql, args).fetchone()[0]
            finally:
                c.close()

        def seed_book(bid, n=3):
            """造一本书 + 分块 + 父块 + 向量，模拟「上传并建索引」后的状态。"""
            c = sqlite3.connect(db_path)
            c.execute("INSERT INTO books (id, name, path, savepath) VALUES (?,?,?,?)",
                      (bid, "书名-" + bid, "", ""))
            for i in range(n):
                c.execute("INSERT INTO document_chunks (book_id, chunk_index, chunk_text, "
                          "parent_id, parent_text, section_path) VALUES (?,?,?,?,?,?)",
                          (bid, i, "正文-%s-%d" % (bid, i), 0, "父块文本", "第一章"))
            c.execute("INSERT INTO ai_parent_chunks (book_id, parent_index, parent_text) "
                      "VALUES (?,?,?)", (bid, 0, "父块文本"))
            c.commit()
            c.close()
            vs.store(bid, ["正文-%s-%d" % (bid, i) for i in range(n)],
                     fake_encode(["chunk%d" % i for i in range(n)]))

        # ================= ① 删书连带清正文 =================
        print("--- ① 删除图书的正文清理 ---")
        seed_book("b_keep", 2)
        seed_book("b_del", 4)
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='b_del'") == 4,
              "预置：待删书有 4 行分块")
        check(q1("SELECT COUNT(*) FROM ai_parent_chunks WHERE book_id='b_del'") == 1,
              "预置：待删书有 1 行父块")

        ok_del, msg = db_ops.delete_book("b_del")
        check(ok_del, "delete_book 返回成功（msg=%r）" % msg)
        check(q1("SELECT COUNT(*) FROM books WHERE id='b_del'") == 0,
              "① books 行已删除")
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='b_del'") == 0,
              "① document_chunks 正文已连带删除（修复前会残留 4 行）")
        check(q1("SELECT COUNT(*) FROM ai_parent_chunks WHERE book_id='b_del'") == 0,
              "① ai_parent_chunks 父块已连带删除（修复前会残留 1 行）")
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='b_keep'") == 2,
              "① 未删图书的分块不受影响（2 行保留）")

        # ================= ② 空书库收尾 empty_index =================
        print("--- ② 书库清空后的索引收尾 ---")
        db_ops.delete_book("b_keep")
        check(q1("SELECT COUNT(*) FROM books") == 0, "② 书库已空")
        check(q1("SELECT COUNT(*) FROM document_chunks") == 0,
              "② 全部删除后无分块残留（修复前实测残留 127 行）")

        # 故意制造历史残留，模拟修复前遗留的孤儿数据
        c = sqlite3.connect(db_path)
        c.execute("INSERT INTO document_chunks (book_id, chunk_index, chunk_text) "
                  "VALUES ('b_ghost', 0, '孤儿正文')")
        c.execute("INSERT INTO ai_parent_chunks (book_id, parent_index, parent_text) "
                  "VALUES ('b_ghost', 0, '孤儿父块')")
        c.execute("INSERT INTO ai_vector_mappings (book_id, chunk_index, faiss_index, "
                  "is_valid, mode) VALUES ('b_ghost', 0, 0, 0, 'light')")
        c.commit()
        c.close()
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='b_ghost'") == 1,
              "② 预置：1 行历史孤儿分块")

        purged, saved = vs.empty_index()
        check(purged >= 1, "② empty_index 清理了孤儿分块（purged=%d）" % purged)
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='b_ghost'") == 0,
              "② 孤儿分块已被清除")
        check(q1("SELECT COUNT(*) FROM ai_parent_chunks WHERE book_id='b_ghost'") == 0,
              "② 孤儿父块已被清除")

        idx_path = os.path.join(vs._VECTOR_DIR, "index_light.faiss")
        check(os.path.exists(idx_path),
              "② 空索引已**落盘**（关键：只删文件会让 is_mode_ready 永远 False）")
        if os.path.exists(idx_path):
            import faiss
            check(faiss.read_index(idx_path).ntotal == 0, "② 落盘索引 ntotal == 0")
        check(saved, "② empty_index 返回 saved=True")

        ready, reason = vs.is_mode_ready()
        check(ready, "② is_mode_ready() 为就绪（reason=%r）" % reason)
        faiss_total, valid, orphans = vs.get_index_drift()
        check(faiss_total == 0 and valid == 0 and orphans == 0,
              "② 索引无任何残留（faiss=%d, 映射=%d, 孤儿=%d）" % (faiss_total, valid, orphans))

        mk = json.load(open(os.path.join(vs._VECTOR_DIR, "index_light.meta.json"),
                            encoding="utf-8"))
        check(mk.get("book_count") == 0 and mk.get("chunk_count") == 0,
              "② marker 指纹归零：%s" % {k: mk.get(k) for k in ("book_count", "chunk_count")})

        # ================= ③ exp_ 经验帖不被误删 =================
        print("--- ③ 经验帖命名空间豁免 ---")
        c = sqlite3.connect(db_path)
        c.execute("INSERT INTO experience_posts (title) VALUES ('经验帖1')")
        c.execute("INSERT INTO document_chunks (book_id, chunk_index, chunk_text) "
                  "VALUES ('exp_1', 0, '经验正文')")
        c.commit()
        c.close()
        purged2, _ = vs.empty_index()
        check(q1("SELECT COUNT(*) FROM document_chunks WHERE book_id='exp_1'") == 1,
              "③ 经验帖分块未被 purge_orphan_chunks 误删（豁免 exp_ 铁律）")

        # ================= ④ 空书库 API 不再短路（静态契约） =================
        print("--- ④ 全量重建入口契约 ---")
        src = open(os.path.join(BASE, "modules", "config_center", "routes.py"),
                   encoding="utf-8").read()
        check("_rebuild_empty_library(admin_id, skipped_already)" in src,
              "④ rebuild_index 在空书库 + force 时走 _rebuild_empty_library（不再直接 return）")
        check("def _rebuild_empty_library(" in src, "④ _rebuild_empty_library 已定义")
        check("vector_store.empty_index()" in src, "④ 收尾调用 empty_index()")
        check("reindex_all_posts" in src, "④ 收尾补回经验帖索引（防静默丢向量）")

        ai_config.get_ai_mode = orig_mode
        vs.unload_index()

        print("\n结果：", "全部通过 ✅" if not fails else f"{len(fails)} 项失败 ❌")
        for f in fails:
            print("   ✗", f)
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
