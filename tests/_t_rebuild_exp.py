"""回归测试：配置中心「全量重建」不可静默清除经验帖(exp_)向量。

不依赖真实嵌入模型 —— embedder.encode 用「文本哈希」生成确定性非零伪向量，
足以验证 映射层(guard) 与 faiss 槽位一致性。

证明两点：
- 场景A（复现原 BUG）：reset_index + 仅重建图书(not 补 exp)
  => exp_ 命名空间的向量映射被清空且无人补回（原「静默丢失」）。
- 场景B（验证 FIX）：reset_index + 重建图书 + 重建 exp（reindex_all_posts 做的事）
  => exp_ 向量映射被补回，query 能召回经验帖。
"""
import os
import sys
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

_DIM = 512


def main():
    tmp = tempfile.mkdtemp(prefix="rebuild_exp_")
    try:
        # 1) 临时 DB（仅建映射表，足够验证映射层）
        db_path = os.path.join(tmp, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
        CREATE TABLE ai_vector_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT, file_hash TEXT, chunk_index INTEGER,
            faiss_index INTEGER, chunk_text_preview TEXT,
            is_valid INTEGER DEFAULT 1, created_at TEXT,
            parent_id INTEGER, mode TEXT
        )
        """)
        conn.commit()
        conn.close()
        db_base.DB_PATH = db_path

        # 2) 临时 faiss 目录
        faiss_dir = os.path.join(tmp, "vector_data")
        os.makedirs(faiss_dir, exist_ok=True)
        vs._VECTOR_DIR = faiss_dir

        # 3) 确定性伪向量
        def fake_encode(texts, *a, **k):
            texts = list(texts)
            out = np.zeros((len(texts), _DIM), dtype="float32")
            for i, t in enumerate(texts):
                h = abs(hash(t)) % 100000
                out[i, 0] = 0.1 + (h % 100) / 100.0
                out[i, 1] = 0.2 + ((h // 7) % 100) / 100.0
            return out

        emb.encode = fake_encode

        # 强制 light 模式（dim=512）。
        # 注意：vector_store._current_mode 在 import 时已绑定原 get_ai_mode 对象，
        # 仅改 ai_config.get_ai_mode 模块属性对其无效，必须直接替换它；
        # ai_config.get_ai_mode 也要改（db_base.add_vector_mapping 在函数内动态 import）。
        orig_mode = ai_config.get_ai_mode
        ai_config.get_ai_mode = lambda: "light"
        vs._current_mode = lambda: "light"

        def store_ns(ns, n):
            texts = [f"{ns} chunk {i}" for i in range(n)]
            return vs.store(ns, texts, emb.encode(texts))

        def ns_set():
            return {m["book_id"] for m in vs.get_valid_vector_mappings(mode="light")}

        fails = []

        def check(cond, msg):
            print(("PASS" if cond else "FAIL"), "-", msg)
            if not cond:
                fails.append(msg)

        # ---- 预置：2 本书 + 2 篇经验帖，各 2 分块 ----
        store_ns("book_a", 2)
        store_ns("book_b", 2)
        store_ns("exp_1", 2)
        store_ns("exp_2", 2)
        check(ns_set() == {"book_a", "book_b", "exp_1", "exp_2"},
              "预置：4 个命名空间均在映射中")

        # ---- 场景A：复现原 BUG（reset + 仅图书重建）----
        vs.reset_index()
        store_ns("book_a", 2)
        store_ns("book_b", 2)
        check("book_a" in ns_set() and "book_b" in ns_set(),
              "场景A：图书向量已重建")
        check("exp_1" not in ns_set() and "exp_2" not in ns_set(),
              "场景A：经验帖向量被清空（复现 BUG：仅靠图书循环会丢 exp_）")

        # ---- 场景B：验证 FIX（reset + 图书重建 + 经验帖补回）----
        vs.reset_index()
        store_ns("book_a", 2)
        store_ns("book_b", 2)
        store_ns("exp_1", 2)   # 模拟 reindex_all_posts() 内部 index_post -> store
        store_ns("exp_2", 2)
        check(ns_set() == {"book_a", "book_b", "exp_1", "exp_2"},
              "场景B：全量重建后经验帖向量被补回（FIX 生效）")

        # ---- 额外：query 能召回经验帖向量 ----
        q = emb.encode(["exp_1 chunk 0"])
        hits = vs.query(None, q[0], top_k=5)
        check(any(h["book_id"].startswith("exp_") for h in hits),
              "场景B：query 跨命名空间能召回经验帖向量")

        ai_config.get_ai_mode = orig_mode

        print("\n结果：", "全部通过 ✅" if not fails else f"{len(fails)} 项失败 ❌")
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
