"""向量化模型封装（懒加载 + 单例）。

轻量模式：bge-small-zh-v1.5（512维，~100MB）
完整模式：bge-large-zh-v1.5（1024维，~1.3GB）

模式切换或模型加载失败时调用 unload_model() 重置。
"""
import os
import threading

_model = None
_model_lock = threading.Lock()
_model_path_loaded = None  # 当前槽位已加载的模型路径
#原先只有一个槽位，light / full 交替调用时会把 1.3GB 的大模型
# 反复卸掉又重装（每次几十秒）。改为按模型路径缓存，两个模式各驻留一份。
_models = {}
_MAX_CACHED_MODELS = 2


def unload_model():
    """卸载当前模型，释放内存"""
    global _model, _model_path_loaded
    with _model_lock:
        _model = None
        _model_path_loaded = None
        _models.clear()


def get_model(mode=None):
    """获取/加载指定模式对应的向量化模型。失败返回None。

    Args:
        mode: "light" / "full"；为 None 时使用当前模式（向后兼容）。
    """
    global _model, _model_path_loaded
    #改用 is_embedding_enabled()（只要求 AI 模式非 off）。
    # 原先调 is_ai_enabled()，它额外要求 LLM 后端非 off ——
    # 向量化根本不需要大模型，未配置后端时连索引都建不了。
    from .config import get_ai_mode, get_model_paths, is_embedding_enabled
    from . import deps

    if not is_embedding_enabled():
        return None

    if mode is None:
        mode = get_ai_mode()
    if mode == "full":
        path = get_model_paths()["full_embedding"]
    else:
        path = get_model_paths()["light_embedding"]

    # 已加载过该路径的模型，直接复用（light / full 各留一份，交替调用不再重装）
    _cached = _models.get(path)
    if _cached is not None:
        _model = _cached
        _model_path_loaded = path
        return _cached

    with _model_lock:
        _cached = _models.get(path)
        if _cached is not None:
            _model = _cached
            _model_path_loaded = path
            return _cached
        # 加载新模型
        ST = deps.get_sentence_transformers()
        if ST is None:
            print("⚠️ sentence-transformers未安装，无法加载向量模型")
            return None
        if not os.path.exists(path):
            print(f"⚠️ 向量模型不存在: {path}")
            return None
        try:
            print(f"⏳ 加载向量模型[{mode}]: {path}")
            # local_files_only=True：禁止联网拉取，确保离线/内网可用
            _model = ST.SentenceTransformer(path, device="cpu", local_files_only=True)
            _model_path_loaded = path
            # 超出上限时丢弃最早加载的那份，避免内存无上限增长
            while len(_models) >= _MAX_CACHED_MODELS:
                try:
                    _models.pop(next(iter(_models)), None)
                except Exception:
                    break
            _models[path] = _model
            print(f"✅ 向量模型[{mode}]加载完成，维度: {_model.get_sentence_embedding_dimension()}")
            return _model
        except Exception as e:
            print(f"❌ 向量模型[{mode}]加载失败: {e}")
            _model = None
            _model_path_loaded = None
            return None


def get_dim(mode=None):
    """获取指定模式模型的向量维度。"""
    m = get_model(mode)
    if m is not None:
        try:
            return m.get_sentence_embedding_dimension()
        except Exception:
            pass
    # 退化到配置默认值
    from .config import get_vector_dim
    if mode is None:
        return get_vector_dim()
    return 1024 if mode == "full" else 512


_SURROGATE_RE = None


def _sanitize_text(t) -> str:
    """把输入规整成 tokenizers 能接受的干净字符串。

    从 PDF 抽取的正文常混入两类致命字符：
      1) Unicode 代理项（如数学字母 U+D835，常是 lone surrogate）；
      2) Private Use Area 字符（Symbol 字体映射，如 \uf0ce）。
    Rust 版 tokenizers 遇到它们会直接抛
    "TextEncodeInput must be Union[TextInputSequence, ...]"，
    导致整批甚至整本书编码失败（实测 10 个分块里 3 个因此失败）。
    这里统一剔除；数学符号对语义检索影响很小。
    """
    global _SURROGATE_RE
    if not isinstance(t, str):
        t = "" if t is None else str(t)
    if _SURROGATE_RE is None:
        import re
        # 用 chr() 在运行时构造代理项区间，避免在源码里直接写 U+D800 转义
        # （那样会产生真实 surrogate 字面量，无法编码进 .pyc，导致模块无法编译）。
        _SURROGATE_RE = re.compile("[%s-%s]" % (chr(0xD800), chr(0xDFFF)))
    t = _SURROGATE_RE.sub("", t)
    # 兜底：丢弃任何无法用 utf-8 编码的字符
    return t.encode("utf-8", "ignore").decode("utf-8", "ignore")


def encode(texts, batch_size=32, mode=None):
    """批量向量化。返回 numpy ndarray 或 None。

    Args:
        texts: str 或 List[str]
        mode: 指定向量化模型所属模式（方案B：切换模式懒重建时用目标模式模型编码）
    Returns:
        numpy.ndarray of shape (N, dim) or None
    """
    import numpy as np
    m = get_model(mode)
    if m is None:
        return None
    if isinstance(texts, str):
        texts = [texts]
    if not texts:
        return np.zeros((0, get_dim(mode)), dtype="float32")

    # 清洗：tokenizers 对非字符串/None 会抛
    # "TextEncodeInput must be Union[TextInputSequence, ...]"，
    # 一旦抛出会让整本书的索引失败，所以先统一转字符串并剔除不可编码字符。
    cleaned = [_sanitize_text(t) for t in texts]

    try:
        vecs = m.encode(cleaned, batch_size=batch_size, show_progress_bar=False,
                        convert_to_numpy=True, normalize_embeddings=True)
        return vecs.astype("float32")
    except Exception as e:
        print(f"⚠️ 批量向量化失败({len(cleaned)} 条): {e}")
        # 降级：逐条编码。批量失败常见原因是单条异常文本把整批带崩，
        # 逐条处理可以保住其余分块，避免一本书因为一个坏块就完全无法检索。
        return _encode_one_by_one(m, cleaned)


def _encode_one_by_one(model, texts):
    """逐条编码；失败条目填零向量以保持数量对齐（零向量内积为 0，不会误命中）。"""
    import numpy as np
    dim = get_dim_from_model(model)
    out = []
    failed = 0
    for t in texts:
        try:
            v = model.encode([t], show_progress_bar=False,
                             convert_to_numpy=True, normalize_embeddings=True)
            out.append(np.asarray(v[0], dtype="float32"))
        except Exception as e:
            failed += 1
            out.append(np.zeros(dim, dtype="float32"))
            if failed <= 3:
                print(f"⚠️ 单条分块编码失败，已填零向量跳过: {str(e)[:80]}")
    if failed:
        print(f"⚠️ 逐条编码完成：{len(texts)} 条中 {failed} 条失败（已填零向量）")
    return np.vstack(out) if out else None


def get_dim_from_model(model):
    """安全获取模型维度，失败时退回配置默认值。"""
    try:
        return int(model.get_sentence_embedding_dimension())
    except Exception:
        from .config import get_vector_dim
        return get_vector_dim()


_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

def _maybe_bge_query_instruction(text):
    """BGE 系列官方建议：检索查询加指令前缀可提升召回。

    仅对 BGE 类嵌入模型生效，且仅作用于查询侧（encode_one）；建库侧用
    embedder.encode 时不加，保持语料原貌。其它嵌入模型原样返回，避免误伤。
    注意：查询空间因此改变，需随全量重建索引生效（否则新旧向量空间错位）。
    """
    try:
        from .config import get_current_embedding_model_name
        name = (get_current_embedding_model_name() or "").lower()
        if "bge" in name:
            return _BGE_QUERY_PREFIX + (text or "")
    except Exception:
        pass
    return text


def encode_one(text):
    """单条向量化（检索查询侧）。返回 shape=(dim,) 的 ndarray 或 None。

查询侧加 BGE 指令前缀（见 _maybe_bge_query_instruction），提升召回。
    """
    if not text:
        return None
    q = _maybe_bge_query_instruction(text)
    vecs = encode([q])
    if vecs is None or len(vecs) == 0:
        return None
    return vecs[0]
