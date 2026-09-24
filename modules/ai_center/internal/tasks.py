"""AI 后台任务（单本书索引重建）。

设计：
- submit_rebuild_task(book_id) 同步完成一本书的：解析文件 -> 分块 -> 向量化 -> 写入索引 -> 摘要
- 涉密锁定（security_lock）开启时跳过正文解析
- ensure_scheduler_for_mode() 在当前架构下为无操作（索引由 API 直接触发，避免常驻线程占用内存）
"""
import os
import threading

_scheduler_stop = threading.Event()


def _calc_file_hash(path):
    """计算文件 MD5。部分图书上传时未写入 file_hash，
    会导致 OCR 缓存与增量索引判断双双失效，这里兜底补算。"""
    try:
        import hashlib
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def submit_rebuild_task(book_id, with_summary: bool = True):
    """索引单本图书（解析+分块+向量化[+摘要]）。返回 task id 字符串，失败返回 None。

    Args:
        with_summary: 是否顺带用大模型生成摘要。批量重建索引时建议传 False ——
                      7B 模型在 CPU 上每本书要几十秒，21 本书会拖到十几分钟，
                      足以让触发重建的 HTTP 请求超时（表现为「重建索引用不了」）。
                      摘要可后续用图书详情页的「重新生成摘要」单独触发。

    失败一律返回 None（不再把「向量化失败」伪装成成功），
    否则调用方统计不到真实成功率，索引会处于「看起来建过、实际查不到」的状态。
    """
    try:
        from core.db_base import (
            get_book_by_id, add_document_chunk, delete_document_chunks,
            delete_parent_chunks, update_book_ai_fields, clear_vector_mappings,
        )
        from . import parser, chunker, embedder, vector_store, summarizer
        from .config import is_security_locked, get_ai_mode
        from common.utils import resolve_file_path

        book = get_book_by_id(book_id)
        if not book:
            return None

        # 涉密锁定：跳过解析，仅标记
        if is_security_locked():
            update_book_ai_fields(book_id, summary="（涉密锁定，已跳过）")
            return f"skip:{book_id}"

        # 解析文件
        path = book.get("savepath") or book.get("path") or ""
        full, err = resolve_file_path(path) if path else (None, "no path")
        if (not full or not os.path.exists(full)) and book.get("savepath"):
            full = book.get("savepath")
        if not full or not os.path.exists(full):
            update_book_ai_fields(book_id, summary="（无文件，跳过）")
            return f"skip:{book_id}"

        # 文件哈希缺失时补算并回写，否则 OCR 缓存与增量判断都会失效
        file_hash = book.get("file_hash")
        if not file_hash and full and os.path.exists(full):
            file_hash = _calc_file_hash(full)
            if file_hash:
                try:
                    from core.db_base import update_book
                    update_book(book_id, file_hash=file_hash)
                except Exception:
                    pass

        # 按文档档位决定 OCR 页数：精确档整本全扫，公共/一般档只扫前若干页
        try:
            from .config import get_book_qa_mode, get_ocr_max_pages_for_mode
            qa_mode = get_book_qa_mode(book)
            ocr_pages = get_ocr_max_pages_for_mode(qa_mode)
        except Exception:
            qa_mode, ocr_pages = "general", 12
        # 传入 file_hash：扫描件走 OCR 时结果会按哈希缓存，同一文件不重复识别
        content = parser.extract_text_from_file(
            full, file_hash=file_hash, use_ocr=True, max_pages=ocr_pages
        )
        if not content or len(content) < 5:
            update_book_ai_fields(book_id, summary="（无文本内容，跳过）")
            return f"skip:{book_id}"

        # 分块（阶梯2：父子两级）
        parents, children, child_to_parent, child_sections, parent_sections = chunker.chunk_with_parents(content)
        if not children:
            update_book_ai_fields(book_id, summary="（空内容，跳过）")
            return f"skip:{book_id}"

        # 剥离页码标记；同时从原文位置提取页码与条款号（取不到则为 None，不报错）
        children_clean = [parser.strip_page_marks(c) for c in children]
        parents_clean = [parser.strip_page_marks(p) for p in parents]

        # 页码：块内没有标记时沿用上一个已知页码。
        # 分块常把页码标记切到相邻块，只取块内标记会导致约一半分块丢失页码。
        #旧数据的清除推迟到这里 —— 分块与向量化都已在内存中完成，
        # 此时才算「新索引一定建得出来」。原先删除放在最前面，一旦向量化或落盘
        # 失败，旧分块/映射已被删掉而新的又没写进去，这本书就永久失去索引，
        # 只能等下次全量重建兜回来。
        delete_document_chunks(book_id)
        delete_parent_chunks(book_id)
        clear_vector_mappings(book_id=book_id, mode=get_ai_mode())

        current_page = None
        for i, ch in enumerate(children):
            p = parser.extract_page(ch)
            if p is not None:
                current_page = p
            add_document_chunk(
                book_id, i, children_clean[i],
                parent_id=child_to_parent[i],
                parent_text=parents_clean[child_to_parent[i]],
                page=current_page,
                section=parser.extract_section(children_clean[i]),
                section_path=child_sections[i] if i < len(child_sections) else None,
            )

        # 向量化（仅子块入索引，父块用于回答时回退）
        # F1：embedding 文本前缀「书名 > 章节面包屑」，使向量本身携带最强区分信息
        #     （书名是全局检索时区分不同书的最强信号，此前只进词法层，向量无感知）。
        #     存储仍用干净文本 children_clean，向量用带前缀的 embed_texts。
        #在原有章节前缀基础上补书名（需随全量重建生效）。
        _book_name = (book.get("name") or "").strip()
        embed_texts = []
        for i, c in enumerate(children_clean):
            sp = child_sections[i] if i < len(child_sections) else None
            prefix = _book_name
            if sp:
                prefix = (_book_name + " > " + sp) if _book_name else sp
            embed_texts.append(("[" + prefix + "] " + c) if prefix else c)
        vecs = embedder.encode(embed_texts, batch_size=16)
        if vecs is None or len(vecs) == 0:
            # 不能静默跳过：这会让调用方以为成功，实际这本书根本检索不到
            print(f"❌ 索引任务失败 book={book_id}：向量化返回空（嵌入模型是否可加载？）")
            return None
        # 修正：批量编码失败时 embedder 会逐条降级，仍失败的条目填的是**零向量**而非 None，
        # 上面的 None 判断拦不住 —— 映射照写、marker 照刷，书显示「索引成功」却永远检索不到
        # （零向量内积恒为 0，永远排不进 top-k）。这里补一道范数校验：
        # 全零 = 实际失败，按失败处理；部分零 = 只影响该分块，告警并继续（保留原有可检索部分）。
        try:
            import numpy as _np
            _arr = _np.asarray(vecs, dtype="float32")
            _norms = _np.linalg.norm(_arr, axis=1) if _arr.ndim == 2 else _np.zeros(1, dtype="float32")
            _ok = int((_norms > 1e-6).sum())
            if _ok == 0:
                print(f"❌ 索引任务失败 book={book_id}：{len(_arr)} 条向量全为零向量（编码实际失败）")
                return None
            if _ok < len(_arr):
                print(f"⚠️ 索引任务 book={book_id}：{len(_arr)} 条中有 {len(_arr) - _ok} 条零向量（这部分将检索不到）")
        except Exception:
            pass
        written = vector_store.store(
            book_id, children_clean, vecs,
            child_parent_map=child_to_parent, parent_texts=parents_clean,
        )
        if not written:
            print(f"❌ 索引任务失败 book={book_id}：写入/落盘索引失败")
            return None
        # 刷新当前模式索引标记（数据指纹更新，避免切回时误判过期）
        try:
            vector_store.refresh_marker()
        except Exception:
            pass

        # 摘要（批量重建时可关闭，避免 LLM 拖垮整体耗时）
        if with_summary:
            try:
                summary = summarizer.summarize_text(content[:8000], book_id=book_id)
                update_book_ai_fields(book_id, summary=summary)
            except Exception as e:
                print(f"⚠️ 摘要生成失败: {e}")

        # 记录「已成功索引」及当时的文件哈希，供增量重建判断是否可跳过
        try:
            from core.db_base import mark_book_indexed
            # 修正：必须用上面补算得到的局部变量 file_hash。
            # 此前传的是 book.get("file_hash") —— book 字典在补算回写后并未刷新，
            # 若上传时没写过 file_hash，这里拿到的仍是 None，
            # indexed_hash 为空 → 增量重建每次都判定「需要重建」。
            mark_book_indexed(book_id, file_hash)
            #索引成功即清该书大纲缓存与全局 IDF 缓存，
            # 避免内容换了仍复用旧大纲/旧 IDF（_OUTLINE_CACHE 按 book_id、_IDF_CACHE 按全库分布）。
            try:
                from . import qa as _qa
                _qa.invalidate_book_caches(book_id)
            except Exception:
                pass
        except Exception:
            pass

        return f"task:{book_id}"
    except Exception as e:
        # 打印完整堆栈：只打印 str(e) 很难定位这类「单本失败」的根因
        import traceback
        print(f"⚠️ 索引任务失败 book={book_id}: {e}")
        traceback.print_exc()
        return None


def ensure_scheduler_for_mode():
    """按当前模式启动后台调度器。当前架构下索引由 API 直接触发，返回 None 即可。"""
    return None
