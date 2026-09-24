"""向量存储：Faiss 索引 + SQLite ai_vector_mappings 映射表（方案B：按运行模式分索引）。

设计：
- 每种运行模式（light / full）维护各自独立的 Faiss 索引文件：
    vector_data/index_light.faiss  (512维, bge-small)
    vector_data/index_full.faiss   (1024维, bge-large)
  切换模式即切换文件，无需每次全量重建；目标模式索引缺失/过期时按需后台懒重建。
- 向量 -> 图书/分块 的映射存于 ai_vector_mappings 表，并用 mode 列区分归属，
  faiss_index 在每个 mode 内各自从 0 编号（避免两套向量行号打架）。
- 分块正文 / 父块正文与模式无关，由 document_chunks / ai_parent_chunks 共用。
- 索引文件同目录放置 marker（index_{mode}.meta.json）记录模型名+维度+数据指纹，
  启动/切换时据此判断索引是否过期（修「切了模式忘了重建」的坑）。
"""
import os
import json
import threading
from datetime import datetime

from . import deps, embedder
from .config import (
    get_ai_mode, get_model_paths,
    LIGHT_EMBEDDING_DIM, FULL_EMBEDDING_DIM,
)
from core.db_base import (
    add_vector_mapping, get_valid_vector_mappings,
    clear_vector_mappings, get_document_chunks,
    add_parent_chunks, get_parent_chunk_text,
)

_VECTOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vector_data"
)
os.makedirs(_VECTOR_DIR, exist_ok=True)

_INDEX_CACHE = {}          # mode -> (index, dim)
_INDEX_LOCK = threading.Lock()

# 后台懒重建去重锁
_REBUILD_INFLIGHT = set()
_REBUILD_LOCK = threading.Lock()

# P0-7：全局重建互斥锁（可重入，rebuild_all 内部会再次调用 store）
# 站内存在三条互不感知的重建路径（懒重建线程 / 配置中心批量线程 / 上传时同步触发的单本重建），
# 并发时会出现「两线程算出同一 start → faiss_index 重复映射互相覆盖」「add 与 os.remove 交错」。
# 用一把全局锁把「算槽位 → add → 写映射 → 落盘」与「全量重建」串行化。
_REBUILD_MUTEX = threading.RLock()
_REBUILD_MUTEX_TIMEOUT = 300   # store() 等待锁的上限（秒）；超时宁可本次失败也不破坏一致性

# P0-1：孤儿槽位整理阈值。单本重建只删映射、不删向量（IndexFlatIP 无法定点删除），
# 长期累积会留下大量无映射的孤儿向量（实测曾达 80%），既浪费检索坑位又掩盖漂移问题。
_AUTO_COMPACT = True           # 置 False 可关闭自动整理
_COMPACT_MIN_NTOTAL = 200      # 索引太小不值得整理
_COMPACT_ORPHAN_RATIO = 0.5    # 孤儿占比超过该比例则触发后台整理重建

#健康检查「告警线」，比上面的自动整理线更低，用于提前预警。
# 两级设计：>=30% 在面板告警（提示可做全量重建）；>=50% 由 _AUTO_COMPACT 自动整理。
# 注意：孤儿只占 faiss 搜索坑位、不返回内容（检索时查不到映射即跳过，已删书不会泄漏），
# 因此告警是「性能/召回质量」信号，不是数据安全问题。
_ORPHAN_WARN_RATIO = 0.30

# 旧索引一次性迁移标记
_MIGRATED = False


# ============================================================
# 模式相关工具
# ============================================================
def _current_mode():
    return get_ai_mode()


def _dim_for_mode(mode):
    return FULL_EMBEDDING_DIM if mode == "full" else LIGHT_EMBEDDING_DIM


def _model_name_for(mode):
    paths = get_model_paths()
    return paths["full_embedding"] if mode == "full" else paths["light_embedding"]


def _index_path(mode):
    if mode is None:
        mode = _current_mode()
    return os.path.join(_VECTOR_DIR, f"index_{mode}.faiss")


def _marker_path(mode):
    if mode is None:
        mode = _current_mode()
    return os.path.join(_VECTOR_DIR, f"index_{mode}.meta.json")


# ============================================================
# 索引加载 / 保存 / 迁移
# ============================================================
def unload_index(mode=None):
    """卸载缓存的索引。mode=None 时清空全部（模式切换时调用）。"""
    global _INDEX_CACHE
    with _INDEX_LOCK:
        if mode is None:
            _INDEX_CACHE.clear()
        else:
            _INDEX_CACHE.pop(mode, None)


def _build_index(dim):
    faiss = deps.get_faiss()
    if faiss is None:
        return None
    return faiss.IndexFlatIP(dim)


def _migrate_legacy_index_once():
    """把旧版单文件索引 index_global.faiss 迁移到当前模式的索引文件（仅一次）。"""
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    try:
        legacy = os.path.join(_VECTOR_DIR, "index_global.faiss")
        if os.path.exists(legacy):
            mode = _current_mode()
            target = _index_path(mode)
            if not os.path.exists(target):
                os.rename(legacy, target)
                try:
                    b, c = _index_fingerprint()
                    _write_marker(mode, b, c)
                except Exception:
                    pass
                print(f"🔄 旧索引已迁移至 {target}")
    except Exception as e:
        print(f"⚠️ 旧索引迁移失败: {e}")


def get_index(mode=None):
    """获取/加载指定模式的 Faiss 索引（懒加载 + 单例缓存）。"""
    faiss = deps.get_faiss()
    if faiss is None:
        return None
    if mode is None:
        mode = _current_mode()
    if mode in _INDEX_CACHE:
        return _INDEX_CACHE[mode][0]
    with _INDEX_LOCK:
        if mode in _INDEX_CACHE:
            return _INDEX_CACHE[mode][0]
        _migrate_legacy_index_once()
        dim = _dim_for_mode(mode)
        path = _index_path(mode)
        if os.path.exists(path):
            try:
                idx = faiss.read_index(path)
                _INDEX_CACHE[mode] = (idx, idx.d)
                return idx
            except Exception as e:
                # ⚠️ 读失败（文件损坏/写入半途被杀）时**绝不能**拿空索引顶替并缓存：
                #  1) is_mode_ready() 只看 marker，marker 没动 → 仍判定"就绪"，
                #     ensure_mode_ready 永不触发重建；
                #  2) 之后任何一次 store() 的 _save_index 都会把这份 ntotal=0 的
                #     空索引覆盖写回磁盘，其余全部图书的向量永久消失，
                #     而映射行还在库里 → 检索静默无召回、日志只有一行 print。
                # 正确做法：损坏文件改名留档 + 清掉 marker，让上层走正常重建。
                print(f"⚠️ 索引[{mode}]读取失败（文件损坏）：{e}")
                try:
                    import traceback as _tb
                    _tb.print_exc()
                except Exception:
                    pass
                try:
                    os.replace(path, path + ".corrupt")
                    print(f"🧯 已隔离损坏索引：{path} -> {path}.corrupt")
                except Exception:
                    pass
                try:
                    mp = _marker_path(mode)
                    if os.path.exists(mp):
                        os.remove(mp)
                except Exception:
                    pass
        idx = _build_index(dim)
        _INDEX_CACHE[mode] = (idx, dim)
        return idx


def _save_index(mode=None):
    faiss = deps.get_faiss()
    if faiss is None:
        return False
    if mode is None:
        mode = _current_mode()
    if mode not in _INDEX_CACHE:
        return False
    try:
        faiss.write_index(_INDEX_CACHE[mode][0], _index_path(mode))
        return True
    except Exception as e:
        # stdout 在后台服务里被缓冲看不到，落盘失败必须写文件才能定位
        print(f"⚠️ 索引[{mode}]保存失败: {e}")
        try:
            import datetime as _dt
            import traceback as _tb
            open(os.path.join(os.path.dirname(_index_path(mode)), "_save_err.log"), "a",
                 encoding="utf-8").write(
                "[%s] 索引[%s]保存失败: %s\n%s\n" % (_dt.datetime.now(), mode, e, _tb.format_exc()))
        except Exception:
            pass
        return False


# ============================================================
# 写入
# ============================================================
def _rollback_mappings(mode, start_idx, end_idx, book_id=None):
    """回滚本次写入的向量映射行（只在 store() 落盘失败时调用）。"""
    try:
        from core.db_base import get_db
        conn = get_db()
        cur = conn.cursor()
        if book_id:
            cur.execute(
                "DELETE FROM ai_vector_mappings "
                "WHERE mode=? AND book_id=? AND faiss_index>=? AND faiss_index<?",
                (mode, book_id, int(start_idx), int(end_idx)))
        else:
            cur.execute(
                "DELETE FROM ai_vector_mappings "
                "WHERE mode=? AND faiss_index>=? AND faiss_index<?",
                (mode, int(start_idx), int(end_idx)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ 回滚向量映射失败: {e}")
        return False


def store(book_id, chunks, embeddings, mode=None,
          child_parent_map=None, parent_texts=None):
    """将一本书的分块与向量写入指定模式的索引。

    Args:
        book_id: 图书ID
        chunks: List[str] 分块文本（与 embeddings 一一对应）
        embeddings: numpy ndarray shape=(N, dim)
        mode: 目标模式；None 用当前模式
        child_parent_map: Optional[List[int]] 子块所属父块下标（阶梯2）
        parent_texts: Optional[List[str]] 父块文本列表，提供则写入 ai_parent_chunks
    Returns:
        实际写入的向量数

        写入槽位统一由 idx.ntotal 推导 —— 这是向量**真实的追加位置**。
        原实现用 get_max_faiss_idx(mode)+1（映射表最大有效号），两者只有在全量重建后才相等：
        单本重建只 DELETE 映射行、不删 Faiss 向量（IndexFlatIP 无法定点删除），
        当被重建的书占据索引尾部时 max(有效)+1 < ntotal，
        新映射会指向装着**旧内容**的向量槽位，新向量反而成为无映射孤儿。
        文件未变时旧向量≈新向量「碰巧能用」，一旦替换文件就会用旧向量服务新文本，静默答错。
    """
    import numpy as np
    if mode is None:
        mode = _current_mode()
    if embeddings is None or len(embeddings) == 0:
        return 0
    # P0-7：与 rebuild_all 共用一把可重入锁，保证「算槽位 → add → 写映射 → 落盘」原子化
    if not _REBUILD_MUTEX.acquire(timeout=_REBUILD_MUTEX_TIMEOUT):
        print(f"⚠️ 索引[{mode}]写入等待重建锁超时（{_REBUILD_MUTEX_TIMEOUT}s），本次写入放弃")
        return 0
    try:
        # 阶梯2：写入父块表（覆盖式，与模式无关）
        if parent_texts is not None:
            add_parent_chunks(book_id, parent_texts)
        idx = get_index(mode)
        if idx is None:
            return 0
        # 关键：槽位取自 Faiss 实际追加位置，而非映射表最大编号
        start = idx.ntotal
        embs = np.asarray(embeddings, dtype="float32")
        try:
            idx.add(embs)
        except Exception as e:
            print(f"⚠️ Faiss add失败: {e}")
            return 0
        for i, ch in enumerate(chunks):
            pid = None
            if child_parent_map is not None and i < len(child_parent_map):
                pid = child_parent_map[i]
            add_vector_mapping(
                book_id=book_id,
                file_hash=None,
                chunk_index=i,
                faiss_index=start + i,
                preview=(ch or "")[:120],
                parent_id=pid,
                mode=mode,
            )
        if not _save_index(mode):
            #原先只 return 0 不回滚 —— 但此时映射已提交、内存索引已 add，
            # 唯独磁盘文件没变。进程重启后映射指向的槽位在磁盘索引里根本不存在，
            # 检索要么越界要么张冠李戴。必须把映射回滚掉。
            print(f"⚠️ 索引[{mode}]落盘失败，回滚本次写入的 {len(chunks)} 条映射")
            _rollback_mappings(mode, start, start + len(chunks), book_id)
            # IndexFlatIP 无法定点删除已 add 的向量，只能丢弃内存中的脏索引；
            # 下次访问会从磁盘重新加载，从而回到落盘前的一致状态。
            try:
                unload_index()
            except Exception as e:
                print(f"⚠️ 卸载脏索引失败: {e}")
            return 0
        return len(chunks)
    finally:
        _REBUILD_MUTEX.release()


# ============================================================
# 检索
# ============================================================
def _expand_child(cache, ci, context_window):
    """子块级上下文扩展（Task2 行为，作为无父块时的兜底）。返回 (content, start, end)。"""
    if context_window and context_window > 0:
        start = max(0, ci - context_window)
        end = ci + context_window + 1
        ctx_parts = [cache.get(i, "") for i in range(start, end)]
        content = "\n".join(p for p in ctx_parts if p)
        return content, start, end
    return cache.get(ci, ""), ci, ci


def query(book_id, embedding, top_k=5, context_window=1, max_per_book=None,
          use_parent=True, mode=None):
    """向量检索。

    Args:
        book_id: 限定图书ID；为 None 时跨全部图书检索
        embedding: shape=(dim,) ndarray
        top_k: 返回结果上限
        context_window: 命中块的上下文扩展窗口（仅子块无父块关联时生效）
        max_per_book: 单本书最多采纳块数；None 时：
                      - 指定 book_id（单书问答）→ 取满 top_k
                      - book_id 为 None（全局检索）→ 每书至多 3 块
        use_parent: 是否启用父子归并（阶梯2）
        mode: 检索所用索引模式；None 用当前模式
    Returns:
        List[dict] 字段: book_id, chunk_index, parent_id, content, score,
                          original_chunk, context_start, context_end,
                          page, section, section_path

此前「关闭父块」分支不返回 section_path，
        而所有调用方都传 use_parent=False（qa.py 三处），导致章节面包屑在
        实际检索路径中被整体丢弃 —— qa._rerank_by_lexical 的 _W_CAT/_W_SUB
        分层加权恒为 0，_pack_sources 也没有面包屑可用。现两个分支统一返回。
    """
    import numpy as np
    if mode is None:
        mode = _current_mode()
    idx = get_index(mode)
    if idx is None or idx.ntotal == 0:
        return []
    vec = np.asarray(embedding, dtype="float32")
    if vec.ndim == 1:
        vec = vec.reshape(1, -1)
    # 单书问答时扩大候选范围，避免全局 top-k 截断后漏掉本书相关块
    #全局检索放大系数 4 → 20。索引里存在大量孤儿向量（ai_vector_mappings
    # 中 is_valid=0 的行），它们仍占着 faiss 的搜索坑位 —— 实测 index_full 共 1290 条
    # 向量而有效映射仅 258 条，孤儿占比约 80%。系数 4 时 k=80 个坑里只剩约 21 个有效
    # 候选，相关内容根本排不进来。放宽到 20（k=400）后有效候选约 79 条。
    k = min(top_k * (16 if book_id is not None else 20), idx.ntotal)
    if k <= 0:
        return []
    # B-12：search 必须与 store 的 add 互斥（Faiss 非线程安全）。
    # 复用 add/rebuild 共用的 _REBUILD_MUTEX，保证索引读写不并发。
    try:
        with _REBUILD_MUTEX:
            scores, indices = idx.search(vec, k)
    except Exception as e:
        print(f"⚠️ Faiss search失败: {e}")
        return []
    faiss_idxs = indices[0].tolist()
    scores_list = scores[0].tolist()

    maps = {m["faiss_index"]: m for m in get_valid_vector_mappings(mode=mode)}
    content_cache = {}
    parent_cache = {}
    # 分块的来源定位信息（页码 / 条款号），与正文分开缓存
    meta_cache = {}

    # 决定每本书最多采纳块数
    cap = max_per_book
    if cap is None:
        #全局检索每书上限 3 → 12。实测《电气运维部晋升调薪管理制度》
        # 17 个 chunk 中「1. Tujuan 目的」段余弦 0.5067 排第 5，被 cap=3 硬截
        # （第 3 名 0.5211，仅差 0.014），模型只能看到封面样板文字，于是自由发挥
        # 出「规范晋升调薪流程」这类万能句。名额放宽后，由二阶 cross-encoder
        # 精排收敛（相关命中 ce≈6~8，无关 ce≈-3~-1），不会引入噪声。
        cap = top_k if book_id is not None else 12

    def load_cache(bid):
        if bid not in content_cache:
            rows = get_document_chunks(bid)
            content_cache[bid] = {
                c["chunk_index"]: c["chunk_text"] for c in rows
            }
            meta_cache[bid] = {
                c["chunk_index"]: {
                    "page": c.get("page"),
                    "section": c.get("section"),
                    "section_path": c.get("section_path"),
                }
                for c in rows
            }
        return content_cache[bid]

    def source_meta(bid, ci):
        """取该块的页码/条款号/章节面包屑；没有则返回空值（不报错）。"""
        try:
            m = (meta_cache.get(bid) or {}).get(ci) or {}
            return m.get("page"), (m.get("section") or ""), (m.get("section_path") or "")
        except Exception:
            return None, "", ""

    def load_parent(bid, pid):
        key = (bid, pid)
        if key not in parent_cache:
            parent_cache[key] = get_parent_chunk_text(bid, pid)
        return parent_cache[key]

    # 1) 收集子块候选（应用每书上限）
    child_hits = []
    per_book_count = {}
    for fi, sc in zip(faiss_idxs, scores_list):
        if fi < 0:
            continue
        m = maps.get(fi)
        if not m:
            continue
        bid = m["book_id"]
        if book_id is not None and str(bid) != str(book_id):
            continue
        if per_book_count.get(bid, 0) >= cap:
            continue
        per_book_count[bid] = per_book_count.get(bid, 0) + 1
        child_hits.append({
            "book_id": bid,
            "chunk_index": m["chunk_index"],
            "parent_id": m.get("parent_id"),
            "score": float(sc),
        })

    # 2) 归并：启用父块则按 (book_id, parent_id) 归并，回退返回父块文本
    results = []
    if use_parent:
        groups = {}  # key -> 该组内最高分命中
        for h in child_hits:
            pid = h.get("parent_id")
            # 无父块关联：以 (book_id, 子块下标) 作为独立组，保证降级时不丢子块
            key = (h["book_id"], "p", pid) if pid is not None else (h["book_id"], "c", h["chunk_index"])
            if key not in groups or h["score"] > groups[key]["score"]:
                groups[key] = h
        ordered = sorted(groups.values(), key=lambda x: x["score"], reverse=True)
        for h in ordered[:top_k]:
            bid = h["book_id"]
            ci = h["chunk_index"]
            pid = h.get("parent_id")
            cache = load_cache(bid)
            original = cache.get(ci, "")
            if pid is not None:
                ptext = load_parent(bid, pid)
                if ptext:
                    content = ptext
                    context_start = context_end = None
                else:
                    content, context_start, context_end = _expand_child(cache, ci, context_window)
            else:
                content, context_start, context_end = _expand_child(cache, ci, context_window)
            page_no, section_no, section_path_no = source_meta(bid, ci)
            results.append({
                "book_id": bid,
                "chunk_index": ci,
                "parent_id": pid,
                "content": content,
                "original_chunk": original,
                "context_start": context_start,
                "context_end": context_end,
                "score": h["score"],
                "page": page_no,
                "section": section_no,
                "section_path": section_path_no,
            })
    else:
        # 关闭父块：纯子块 + 上下文（Task2 行为）
        for h in child_hits[:top_k]:
            bid = h["book_id"]
            ci = h["chunk_index"]
            cache = load_cache(bid)
            original = cache.get(ci, "")
            content, context_start, context_end = _expand_child(cache, ci, context_window)
            page_no, section_no, sp = source_meta(bid, ci)
            results.append({
                "book_id": bid,
                "chunk_index": ci,
                "parent_id": h.get("parent_id"),
                "content": content,
                "original_chunk": original,
                "context_start": context_start,
                "context_end": context_end,
                "score": h["score"],
                "page": page_no,
                "section": section_no,
                # 修正：原先这里把 source_meta 的第三个返回值丢给了 _sp 而没写入结果，
                # 章节面包屑因此在整条检索链路上丢失，与父块分支行为不一致。
                "section_path": sp,
            })
    return results


# ============================================================
# 索引健康检查 / 懒重建
# ============================================================
def _index_fingerprint():
    """返回 (图书数, 分块数) 作为 document_chunks 指纹，用于判断索引是否过期。

    只统计 books 表中仍然存在的图书：图书被删除后若分块残留（孤儿数据），
    会污染指纹，并让重建去向量化大量无效文本。
    实测曾出现 20 个 book_id 里 16 个是孤儿、占 16322 个分块中的 16290 个，
    直接导致 rebuild_all 去编码上万个无效分块，重建跑不完。
    """
    try:
        from core.db_base import get_db
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT c.book_id), COUNT(*) "
            "FROM document_chunks c INNER JOIN books b ON b.id = c.book_id"
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return 0, 0
        return row[0] or 0, row[1] or 0
    except Exception:
        return 0, 0


def purge_orphan_chunks():
    """清理孤儿索引数据：books 表中已不存在、却仍留在索引表里的分块。

    图书被删除时若未连带清理 document_chunks / ai_parent_chunks /
    ai_vector_mappings，这些残留会：
      1) 污染 _index_fingerprint，使索引被误判为「需要重建/已就绪」；
      2) 让 rebuild_all 去向量化上万条无效文本，重建长时间跑不完。

必须排除经验帖命名空间 book_id LIKE 'exp_%'。
    经验帖（modules/experience_hub）以 book_id = "exp_<id>" 写入同一批索引表，
    但 books 表里并没有对应行 —— 不排除的话，每次全量/批量重建都会把
    所有经验帖的分块和向量映射删掉，帖子在作者重新编辑前永久不可检索。
    返回删除的分块数。
    """
    try:
        from core.db_base import get_db
        conn = get_db()
        cur = conn.cursor()
        _orphan_where = "book_id NOT IN (SELECT id FROM books) AND book_id NOT LIKE 'exp_%'"
        cur.execute(f"SELECT COUNT(*) FROM document_chunks WHERE {_orphan_where}")
        n = cur.fetchone()[0] or 0
        if n:
            cur.execute(f"DELETE FROM document_chunks WHERE {_orphan_where}")
            for tbl in ("ai_parent_chunks", "ai_vector_mappings"):
                try:
                    cur.execute(f"DELETE FROM {tbl} WHERE {_orphan_where}")
                except Exception:
                    pass
            conn.commit()
            print(f"🧹 已清理孤儿索引数据：{n} 个分块（对应图书已不存在；已保留 exp_ 经验帖）")
        conn.close()
        return n
    except Exception as e:
        print(f"⚠️ 清理孤儿索引数据失败: {e}")
        return 0


def _read_marker(mode):
    p = _marker_path(mode)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_marker(mode, book_count, chunk_count):
    p = _marker_path(mode)
    data = {
        "mode": mode,
        "model_name": _model_name_for(mode),
        "dim": _dim_for_mode(mode),
        "book_count": book_count,
        "chunk_count": chunk_count,
        "built_at": datetime.now().isoformat(),
    }
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 索引标记写入失败: {e}")


def is_mode_ready(mode=None):
    """判断指定模式的索引是否就绪（文件/模型/维度/数据指纹均匹配）。"""
    if mode is None:
        mode = _current_mode()
    path = _index_path(mode)
    if not os.path.exists(path):
        return False, "索引文件不存在"
    marker = _read_marker(mode)
    if marker is None:
        return False, "索引标记缺失"
    if marker.get("dim") != _dim_for_mode(mode):
        return False, "向量维度不匹配"
    if marker.get("model_name") != _model_name_for(mode):
        return False, "embedding 模型不匹配"
    b_now, c_now = _index_fingerprint()
    if marker.get("book_count") != b_now or marker.get("chunk_count") != c_now:
        return False, "数据已变更，需重建"
    return True, "就绪"


def ensure_mode_ready(mode=None, lazy=True):
    """切换模式时调用：目标模式就绪则瞬时返回；否则按需后台懒重建。

    返回 (ready: bool, msg: str)
    """
    if mode is None:
        mode = _current_mode()
    ready, reason = is_mode_ready(mode)
    if ready:
        return True, "就绪"
    if not lazy:
        return False, reason
    with _REBUILD_LOCK:
        if mode in _REBUILD_INFLIGHT:
            return False, "重建进行中"
        _REBUILD_INFLIGHT.add(mode)
    t = threading.Thread(target=_lazy_rebuild_worker, args=(mode,), daemon=True)
    t.start()
    return False, f"索引缺失/过期（{reason}），已后台开始重建"


def _lazy_rebuild_worker(mode):
    try:
        print(f"⏳ 后台懒重建索引[{mode}] 开始")
        rebuild_all(mode=mode)
        print(f"✅ 后台懒重建索引[{mode}] 完成")
    except Exception as e:
        print(f"❌ 后台懒重建索引[{mode}] 失败: {e}")
    finally:
        with _REBUILD_LOCK:
            _REBUILD_INFLIGHT.discard(mode)


def refresh_marker(mode=None):
    """重建/单本索引后刷新当前模式的索引标记（数据指纹），避免被误判过期。

    P0-1：单本重建会在 Faiss 里留下无映射的孤儿向量，这里顺带检查一次漂移程度，
    超过阈值就后台触发一次整理重建，让索引自愈（否则孤儿只增不减）。
    """
    if mode is None:
        mode = _current_mode()
    b, c = _index_fingerprint()
    _write_marker(mode, b, c)
    try:
        schedule_compaction_if_needed(mode)
    except Exception:
        pass


def get_index_drift(mode=None):
    """返回 (faiss 向量总数, 有效映射数, 孤儿槽位数)。用于判断索引漂移程度。"""
    if mode is None:
        mode = _current_mode()
    valid = len(get_valid_vector_mappings(mode=mode))
    idx = get_index(mode)
    faiss_total = idx.ntotal if idx else 0
    return faiss_total, valid, max(0, faiss_total - valid)


def needs_compaction(mode=None):
    """孤儿槽位是否多到需要整理重建（全量重建会顺带把孤儿清掉）。"""
    faiss_total, _valid, orphans = get_index_drift(mode)
    if faiss_total < _COMPACT_MIN_NTOTAL:
        return False
    return orphans > faiss_total * _COMPACT_ORPHAN_RATIO


def schedule_compaction_if_needed(mode=None):
    """孤儿过多时后台触发一次全量重建。已在重建中的模式不会重复触发。"""
    if not _AUTO_COMPACT:
        return False
    if mode is None:
        mode = _current_mode()
    if not needs_compaction(mode):
        return False
    with _REBUILD_LOCK:
        if mode in _REBUILD_INFLIGHT:
            return False
        _REBUILD_INFLIGHT.add(mode)
    faiss_total, valid, orphans = get_index_drift(mode)
    print(f"🧹 索引[{mode}] 孤儿槽位 {orphans}/{faiss_total}（有效映射 {valid}），后台开始整理重建")
    t = threading.Thread(target=_compact_worker, args=(mode,), daemon=True)
    t.start()
    return True


def _compact_worker(mode):
    try:
        rebuild_all(mode=mode)
        print(f"✅ 索引[{mode}] 整理重建完成")
    except Exception as e:
        print(f"❌ 索引[{mode}] 整理重建失败: {e}")
    finally:
        with _REBUILD_LOCK:
            _REBUILD_INFLIGHT.discard(mode)


def get_stats():
    """返回当前模式索引统计（字段名与前端一致）。"""
    mode = _current_mode()
    maps = get_valid_vector_mappings(mode=mode)
    total = len(maps)
    books = len({m["book_id"] for m in maps})
    idx = get_index(mode)
    ready, reason = is_mode_ready(mode)
    faiss_total = idx.ntotal if idx else 0
    # P0-1：暴露真实的孤儿槽位数（无映射的向量）。此前 invalid 恒为 0，
    # 索引漂移、孤儿堆积这类问题在面板上完全不可见。
    orphans = max(0, faiss_total - total)
    #暴露孤儿率与告警标记，供配置中心健康检查展示。
    orphan_rate = (orphans / faiss_total) if faiss_total > 0 else 0.0
    return {
        "mode": mode,
        "model_name": _model_name_for(mode),
        "total": total,
        "valid": total,
        "invalid": 0,
        "books": books,
        "faiss_total": faiss_total,
        "orphans": orphans,
        "orphan_rate": round(orphan_rate, 4),
        "orphan_warning": orphan_rate >= _ORPHAN_WARN_RATIO,
        "orphan_threshold": _ORPHAN_WARN_RATIO,
        "index_file": _index_path(mode),
        "index_file_exists": os.path.exists(_index_path(mode)),
        "ready": ready,
        "ready_reason": reason,
    }


def reset_index(mode=None):
    """清空指定模式的索引（映射表 + 磁盘文件 + 内存缓存），使其从 ntotal=0 开始重建。

    背景：配置中心「全量重建」走的是 _rebuild_worker 逐本
    trigger_rebuild_for_book 路径 —— 只删该书映射、store() 从 idx.ntotal 追加，
    因此**上一轮的全部向量都会变成孤儿**。实测：重建前 ntotal=80/映射=80，
    重建后 ntotal=164/映射=84（孤儿 80，占 48.8%），且映射 faiss_index 起始为 80，
    证明是追加到旧索引尾部。孤儿虽不返回内容，但会挤占 faiss 搜索坑位，每重建一次翻倍。

    本函数把原本只存在于 _rebuild_all_locked 内部的清空逻辑抽出来复用，
    供「全量重建」在逐本重建前调用一次。
    """
    if mode is None:
        mode = _current_mode()
    clear_vector_mappings(mode=mode)
    path = _index_path(mode)
    # 删磁盘文件（失败仅警告，不致命）+ 丢弃缓存 + 置入全新空索引。
    # 即使旧磁盘文件因文件锁删不掉，store() 末尾 _save_index 也会覆盖写盘，
    # 最终落盘仍是干净的「仅本次重建向量」。
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"⚠️ 删除旧索引文件失败（将强制重置内存索引）：{e}")
    try:
        fa = deps.get_faiss()
        if fa is not None:
            dim = _dim_for_mode(mode)
            with _INDEX_LOCK:
                _INDEX_CACHE[mode] = (fa.IndexFlatIP(dim), dim)
        else:
            with _INDEX_LOCK:
                _INDEX_CACHE.pop(mode, None)
    except Exception as e:
        print(f"⚠️ 重置内存索引失败：{e}（回退到删缓存逻辑）")
        with _INDEX_LOCK:
            _INDEX_CACHE.pop(mode, None)


def empty_index(mode=None):
    """清空指定模式的索引并**落盘为空索引**（ntotal=0）+ 匹配的 marker。

    与 reset_index() 的关键区别：reset_index 只删磁盘文件 + 清内存缓存，磁盘上不留文件，
    于是 is_mode_ready() 会永远返回「索引文件不存在」，ensure_mode_ready() 每次调用都再起
    一个空重建线程。本函数用于「书库已清空」的收尾：purge 孤儿分块 → reset → **把空索引
    写盘** → 刷新 marker，让索引停在「就绪且为空」的稳定终态。

    ⚠️ 调用方若还有 exp_ 经验帖，必须在之后自行调用 experience_hub 的
    reindex_all_posts() 补回向量 —— reset_index 清的是整个 mode 的映射，
    经验帖与图书共用同一索引（这条铁律见 _t_rebuild_exp.py）。

    Returns:
        (purged_chunks, saved) —— 清理的孤儿分块数、空索引是否成功落盘
    """
    if mode is None:
        mode = _current_mode()
    purged = purge_orphan_chunks()
    reset_index(mode)
    # reset_index 已把内存缓存换成全新的空 IndexFlatIP，必须落盘：
    # 否则磁盘无文件 → is_mode_ready() 恒 False → 反复触发懒重建。
    saved = _save_index(mode)
    refresh_marker(mode)
    return purged, bool(saved)


def rebuild_all(mode=None):
    """全量重建指定模式的索引：从 document_chunks 表读取所有分块重新向量化。
    会一并恢复父子关系（document_chunks 中缓存的 parent_id/parent_text），保证自愈。
    仅清空/重建目标模式，不影响另一模式的索引。
    返回 (总条数, 成功图书数)。

    P0-7：内部持有全局重建锁，与 store() 互斥，
    避免懒重建线程 / 配置中心批量线程 / 上传单本重建并发把索引与映射写乱。
    """
    if mode is None:
        mode = _current_mode()
    if not _REBUILD_MUTEX.acquire(timeout=_REBUILD_MUTEX_TIMEOUT):
        print(f"⚠️ 索引[{mode}]全量重建等待锁超时（{_REBUILD_MUTEX_TIMEOUT}s），跳过本次重建")
        return 0, 0
    try:
        return _rebuild_all_locked(mode)
    finally:
        _REBUILD_MUTEX.release()


def _rebuild_all_locked(mode):
    """rebuild_all 的真实实现。调用方必须已持有 _REBUILD_MUTEX。

    锁是可重入的（RLock），函数内部调用的 store() 会再次获取同一把锁，不会死锁。
    """
    if mode is None:
        mode = _current_mode()
    # 先清理孤儿分块（图书已删除但分块残留），否则重建会去向量化上万条无效文本
    purge_orphan_chunks()
    # 清空逻辑已抽成 reset_index() 复用（说明见其 docstring）
    reset_index(mode)

    from core.db_base import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT book_id, chunk_index, chunk_text, parent_id, parent_text, section_path "
                "FROM document_chunks ORDER BY book_id, chunk_index")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not rows:
        _write_marker(mode, 0, 0)
        return 0, 0
    by_book = {}
    for r in rows:
        by_book.setdefault(r["book_id"], []).append(r)
    success = 0
    for bid, recs in by_book.items():
        # F1：embedding 文本前缀「书名 > 章节面包屑」
        #     必须与 tasks.submit_rebuild_task 完全一致 —— 否则「单本索引带书名、
        #     全量重建不带书名」，两侧向量空间不一致，检索行为会随重建方式漂移。
        _bn = ""
        try:
            from core.db_base import get_book_by_id as _gbi
            _bn = ((_gbi(bid) or {}).get("name") or "").strip()
        except Exception:
            _bn = ""
        texts = []
        clean_texts = []
        for r in recs:
            sp = r.get("section_path") or ""
            txt = r["chunk_text"] or ""
            clean_texts.append(txt)
            prefix = (_bn + " > " + sp) if (_bn and sp) else (sp or _bn)
            texts.append(("[" + prefix + "] " + txt) if prefix else txt)
        parent_map = [r["parent_id"] for r in recs]
        # 从缓存重建父块文本列表（按下标对齐）
        pmap = {}
        for r in recs:
            pid = r["parent_id"]
            pt = r.get("parent_text")
            if pid is not None and pt:
                pmap[int(pid)] = pt
        parent_texts = [pmap.get(i, "") for i in range(max(pmap.keys()) + 1)] if pmap else None
        # 关键：用目标模式的模型编码（方案B 懒重建时 mode != 当前模式）
        vecs = embedder.encode(texts, batch_size=16, mode=mode)
        if vecs is None or len(vecs) == 0:
            print(f"⚠️ 重建索引：图书 {bid} 向量化失败（嵌入模型是否可加载？），跳过")
            continue
        # 注意：store 的 chunks 参数只用于写 preview 字段（前 120 字符），不写正文，
        # 这里传干净文本，避免 preview 里混入「[书名 > 章节]」前缀。
        written = store(bid, clean_texts, vecs, mode=mode,
                        child_parent_map=parent_map, parent_texts=parent_texts)
        if written:
            success += 1
        else:
            print(f"⚠️ 重建索引：图书 {bid} 写入索引失败，跳过")
    # 写入标记（含数据指纹）
    b, c = _index_fingerprint()
    if success > 0:
        _write_marker(mode, b, c)
    else:
        # 关键：一本都没成功时绝不能写 marker，否则会造成「索引就绪」的假象 ——
        # marker 记录了 21 本书/452 分块，faiss 却为空或根本不存在，
        # is_mode_ready() 因此返回 True，检索恒为空且永远不会再触发重建。
        try:
            p = _marker_path(mode)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        print(f"❌ 重建索引[{mode}] 失败：{len(by_book)} 本书全部向量化/写入失败")
    return len(rows), success
