"""AI 后端配置读取（优雅降级 + 依赖探测）。

本模块不依赖任何重型三方库：在没有安装 AI 依赖的环境里，
探测函数返回 installed=False，UI 据此提示用户先安装对应依赖，
而不是抛异常崩溃。

配置来源：core.ai_config_store.read_ai_config()（读 config/modules.yaml 的 ai_center 块）。
"""

import os
from contextvars import ContextVar

# 依赖清单：模块名 -> 中文说明
_DEPENDENCIES = {
    "torch": "PyTorch（深度学习框架，本地大模型/向量化基础）",
    "sentence_transformers": "Sentence-Transformers（文本向量化，transformers 后端）",
    "faiss": "FAISS（向量检索索引）",
    "llama_cpp": "llama-cpp-python（本地 GGUF 大模型推理）",
    "openai": "OpenAI SDK（DashScope / Ollama 兼容接口调用）",
    "requests": "requests（HTTP 客户端，DashScope 调用）",
    "yaml": "PyYAML（配置文件解析）",
}

# 向量维度（须与所选模型一致）
LIGHT_EMBEDDING_DIM = 512
FULL_EMBEDDING_DIM = 1024

_BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    )
)


def _safe_import(module_name):
    """尝试导入模块，失败返回 None（不抛异常）。"""
    try:
        return __import__(module_name)
    except Exception:
        return None


def _read_cfg():
    try:
        from core.ai_config_store import read_ai_config
        return read_ai_config()
    except Exception:
        return {}


def detect_dependencies():
    """探测 AI 相关 Python 依赖是否已安装，返回列表。"""
    result = []
    for mod, desc in _DEPENDENCIES.items():
        result.append({
            "name": mod,
            "desc": desc,
            "installed": _safe_import(mod) is not None,
        })
    return result


def get_available_backends():
    """返回当前环境真正可运行的后端（依赖已装即可配置）。"""
    avail = []
    if _safe_import("sentence_transformers") and _safe_import("faiss"):
        avail.append("transformers")
    if _safe_import("llama_cpp"):
        avail.append("llama_cpp")
    # ollama / dashscope / vllm 是**远程 HTTP 接口**，本项目的实现全部走 requests
    # （见 qa.py 的 _ollama_* / _dashscope_* / _vllm_*），与 openai SDK 无关。
    # 原先按 openai 是否安装来判定：环境里没装 openai 时这三个后端会被错误剔除，
    # 配置页看不到它们，且 qa._candidate_backends 的降级候选里也不再有云端兜底。
    if _safe_import("requests"):
        avail.append("ollama")
        avail.append("dashscope")
        avail.append("vllm")
    return avail


def get_model_status():
    """探测默认模型路径是否存在，供 UI 提示。"""
    try:
        from core.ai_config_store import read_ai_config
        cfg = read_ai_config()
        paths = cfg.get("model_paths", {}) or {}
    except Exception:
        paths = {}
    out = {}
    for key, rel in paths.items():
        if not rel:
            out[key] = {"path": "", "exists": False}
            continue
        full = rel if os.path.isabs(rel) else os.path.join(_BASE_DIR, rel)
        out[key] = {"path": full, "exists": os.path.exists(full)}
    return out


def get_config():
    """返回当前 AI 运行配置摘要（供调试 / 状态展示）。"""
    try:
        from core.ai_config_store import read_ai_config
        cfg = read_ai_config()
    except Exception:
        cfg = {}
    deps = detect_dependencies()
    backends = get_available_backends()
    chosen = (cfg.get("backend") or "off")
    ready = bool(chosen and chosen != "off" and chosen in backends)
    return {
        "backends": backends,
        "dependencies": deps,
        "model_status": get_model_status(),
        "config": cfg,
        "ready": ready,
        "note": "" if ready else "AI 后端未完全就绪：请在下方选择推理后端并填写模型路径 / 密钥后启用。",
    }


# ============================================================
# 以下函数为 qa / embedder / vector_store / tasks 提供配置读取
# ============================================================
def get_model_paths():
    """返回已配置的绝对模型路径。"""
    cfg = _read_cfg()
    mp = cfg.get("model_paths", {}) or {}
    base = _BASE_DIR

    def _abs(p):
        return p if os.path.isabs(p) else os.path.join(base, p)

    return {
        "light_embedding": _abs(mp.get("light_emb") or "models/bge-small-zh-v1.5"),
        "full_embedding": _abs(mp.get("full_emb") or "models/bge-large-zh-v1.5"),
        "llm": _abs(mp.get("local_llm") or "models/LFM2.5-2.6B-Q4_K_M.gguf"),
        #二阶重排模型路径。原先在 reranker.py 里硬编码，
        # 配置中心改不了。未配置时回落到原默认目录（见 reranker._resolve_model_dir）。
        "reranker": _abs(mp.get("reranker") or "models/bge-reranker-v2-m3"),
    }


def get_llm_backend():
    """获取当前 LLM 后端类型：off / llama_cpp / ollama / dashscope / transformers。"""
    return (_read_cfg().get("backend") or "off").lower()


def _resolve_backend_model_path(cfg, backend_key):
    """本地后端的模型路径：优先用「后端专属配置」里的 model_path，否则用全局模型路径。

配置中心切换后端时可给 llama_cpp / transformers 单独存一个
    model_path（set_ai_backend 会写成 cfg[backend]["model_path"]），
    但 get_llm_backend_config 原先从不读它，只用 model_paths.local_llm，
    导致该配置项填了也不生效。

    安全约束：后端专属 model_path **只有在确实指向可用模型时才采用** ——
    配置中心该字段的默认值是目录 "models/"（并非模型文件），
    直接采用会让模型加载失败。取不到有效路径时回落到 model_paths["llm"]
    （即本次改动之前的行为）。
    """
    p = ((cfg.get(backend_key) or {}).get("model_path") or "").strip()
    if p:
        ap = p if os.path.isabs(p) else os.path.join(_BASE_DIR, p)
        if os.path.isfile(ap):
            return ap
        # transformers 常用「HF 权重目录」形式
        if os.path.isdir(ap) and os.path.exists(os.path.join(ap, "config.json")):
            return ap
    return get_model_paths()["llm"]


def get_backend_config(backend_key=None):
    """获取**指定后端**的完整加载配置（供各后端加载器使用）。

    与原 get_llm_backend_config() 的区别：后者只能返回「当前选中后端」的配置，
    而后端自动降级需要按候选后端名逐个取配置来尝试加载。

    backend_key 为 None 时等价于「当前选中的后端」，行为与旧函数完全一致。

    注意：不同后端的模型源不同 —— ollama/dashscope 只读自己那一段配置，
    llama_cpp/transformers 读后端专属 model_path（可由 _resolve_backend_model_path 解析）。
    """
    cfg = _read_cfg()
    backend = (backend_key or get_llm_backend()).lower()
    out = {"backend": backend}
    ollama = cfg.get("ollama", {}) or {}
    dashscope = cfg.get("dashscope", {}) or {}
    vllm = cfg.get("vllm", {}) or {}
    if backend == "llama_cpp":
        out["llm_model"] = _resolve_backend_model_path(cfg, "llama_cpp")
    elif backend == "ollama":
        out["ollama_url"] = ollama.get("url") or "http://localhost:11434"
        out["ollama_model"] = ollama.get("model") or "qwen2.5:7b"
    elif backend == "dashscope":
        out["dashscope_url"] = dashscope.get("url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        out["dashscope_model"] = dashscope.get("model") or "qwen-plus"
        out["dashscope_api_key"] = dashscope.get("api_key") or ""
        # 思考链开关（三态）：只有 yaml 里显式写了 enable_thinking 才下发给厂商，
        # 缺省则不发送该字段 —— 部分 OpenAI 兼容厂商会拒绝不认识的私有参数。
        if "enable_thinking" in dashscope:
            out["dashscope_enable_thinking"] = bool(dashscope.get("enable_thinking"))
    elif backend == "vllm":
        out["vllm_url"] = vllm.get("url") or "http://localhost:8000/v1"
        out["vllm_model"] = vllm.get("model") or ""
        out["vllm_api_key"] = vllm.get("api_key") or ""
    elif backend == "transformers":
        out["llm_model"] = _resolve_backend_model_path(cfg, "transformers")
    return out


def get_llm_backend_config():
    """获取 LLM 后端完整配置（供各后端加载器使用）。

    保持原有对外行为：始终返回「当前选中后端」的配置。
    """
    return get_backend_config()


def get_backend_probe_cfg(backend_key):
    """供 qa.probe_backend_connectivity 使用的原始配置块 {url, model, api_key}。

    探活函数要的键名与各后端加载配置（ollama_url / dashscope_model 等）不同，
    这里做一次归一化，避免调用方各写一遍从 yaml 取字段的逻辑。
    """
    cfg = _read_cfg()
    key = (backend_key or "").lower()
    if key == "ollama":
        o = cfg.get("ollama") or {}
        return {"url": o.get("url") or "", "model": o.get("model") or ""}
    if key == "dashscope":
        d = cfg.get("dashscope") or {}
        return {"url": d.get("url") or "", "model": d.get("model") or "",
                "api_key": d.get("api_key") or ""}
    if key == "vllm":
        v = cfg.get("vllm") or {}
        return {"url": v.get("url") or "", "model": v.get("model") or "",
                "api_key": v.get("api_key") or ""}
    return {}


def get_ai_mode():
    """获取当前 AI 运行模式：off / light / full。"""
    return (_read_cfg().get("ai_mode") or "light").lower()


def is_ai_enabled():
    """AI 是否启用（模块 enabled 且模式非 off 且后端非 off）。"""
    cfg = _read_cfg()
    if not cfg.get("enabled", True):
        return False
    return get_ai_mode() != "off" and get_llm_backend() != "off"


def is_embedding_enabled():
    """向量化（embedding）是否可用 —— 与 LLM 生成后端解耦。

向量化/索引/检索依赖的是 embedding 模型，
    与「用哪个大模型生成答案」完全无关。而 embedder.get_model 原先直接调
    is_ai_enabled()，该判断要求 get_llm_backend() != "off"，
    于是只要没配大模型后端，连索引都建不起来（重建索引全部失败）。
    这里放宽为：模块启用 且 运行模式非 off 即认为可向量化。

    注意：is_ai_enabled() 本身语义不变，问答/摘要等需要 LLM 的路径仍走原判断。
    """
    cfg = _read_cfg()
    if not cfg.get("enabled", True):
        return False
    return get_ai_mode() != "off"


def get_vector_dim():
    """当前模式对应的向量维度。"""
    return FULL_EMBEDDING_DIM if get_ai_mode() == "full" else LIGHT_EMBEDDING_DIM


def get_current_embedding_model_name():
    """当前模式对应的 embedding 模型路径（用于 vector_mappings 区分）。"""
    paths = get_model_paths()
    return paths["full_embedding"] if get_ai_mode() == "full" else paths["light_embedding"]


def is_security_locked():
    """涉密安全开关：True 表示禁止读取图书正文进行 AI 处理。"""
    return bool(_read_cfg().get("security_lock", False))


def is_hyde_enabled():
    """查询侧 HyDE（假设文档嵌入）是否启用。默认开启；LLM 不可用时自动失效（回退原始查询）。

    仅影响检索向量、不改库内向量，故开启/关闭均无需重建索引。
    """
    v = _override("hyde")
    if v is not None:
        return bool(v)
    try:
        return bool(_read_cfg().get("hyde", True))
    except Exception:
        return True


def is_citation_check_enabled():
    """生成答案后做引用校验（groundedness）是否启用。默认开启；开关关闭时视为通过、不阻断。"""
    v = _override("citation_check")
    if v is not None:
        return bool(v)
    try:
        return bool(_read_cfg().get("citation_check", True))
    except Exception:
        return True


def is_rerank_enabled():
    """二阶 cross-encoder 精排是否启用。默认开启。

    这是当前问答链路最贵的一步（本机 CPU 上实测约 18.5s/次，568M 模型跑
    ≤24 个候选对）。关闭后只保留一阶字面去噪（_rerank_by_lexical）的截断结果，
    行为与「reranker 模型不可用」时完全一致：无需重建索引、不改库内向量、
    不影响权限过滤与后续拼接。
    """
    v = _override("rerank")
    if v is not None:
        return bool(v)
    try:
        return bool(_read_cfg().get("rerank", True))
    except Exception:
        return True


def get_rerank_device():
    """二阶重排运行设备："auto"（默认）/ "cuda" / "cpu"。

    auto：有 CUDA 用 GPU，无 CUDA 用 CPU。非法值一律回落到 auto。
    部署到 GPU 服务器时无需改代码，保持 auto 即可自动生效。
    """
    try:
        d = (_read_cfg().get("rerank_device") or "auto").strip().lower()
        return d if d in ("auto", "cuda", "cpu") else "auto"
    except Exception:
        return "auto"


def get_rerank_max_candidates():
    """二阶重排候选数上限，默认 24。

    CPU 上耗时与该值近似线性：调小到 12 约省一半重排耗时，是介于
    「全开（最准最慢）」与「全关（最快但失去语义精排）」之间的折中档。
    """
    v = _override("rerank_max_candidates")
    if v is not None:
        try:
            n = int(v)
            return n if n > 0 else 24
        except Exception:
            pass
    try:
        n = int(_read_cfg().get("rerank_max_candidates", 24))
        return n if n > 0 else 24
    except Exception:
        return 24


# ---------------------------------------------------------------------------
# 速度档位（每问可选）：请求级覆盖全局开关
# ---------------------------------------------------------------------------
# 各开关函数会先查这里的覆盖值，查不到才回落到 config/modules.yaml。
# 用 ContextVar 而非全局变量：并发请求之间互不干扰，且 SSE 流式生成运行在
# 同一请求上下文内，覆盖值在整个流期间持续有效。
_QA_OVERRIDES = ContextVar("ai_qa_overrides", default=None)

# 档位 -> 覆盖值。未选档位时完全走全局配置，行为与改动前一致。
#移除 fast（极速）档——该档关闭 HyDE + rerank 后检索质量骤降，
# 问候类问题会召回不相干切片并生成莫名拒答，用户明确要求删除。
SPEED_PROFILES = {
    "high":     {"hyde": True,  "rerank": True,  "citation_check": True,
                 "rerank_max_candidates": 24},
    "balanced": {"hyde": True,  "rerank": True,  "citation_check": True,
                 "rerank_max_candidates": 12},
}


def set_speed_profile(name):
    """设置当前上下文的速度档位覆盖；None / 未知档位则清空（回落全局配置）。"""
    try:
        _QA_OVERRIDES.set(SPEED_PROFILES.get(name) if name else None)
    except Exception:
        pass


def get_speed_profile_names():
    """可用档位名列表（供路由校验入参）。"""
    return list(SPEED_PROFILES.keys())


def _override(key):
    """取当前上下文的覆盖值；无覆盖返回 None。"""
    try:
        d = _QA_OVERRIDES.get()
    except Exception:
        return None
    return d.get(key) if isinstance(d, dict) else None


def is_grounding_async():
    """引用校验是否异步（生成结束先出答案，校验结果随后单独下发）。默认开启。

    关闭则回到「等校验完成再下发 done」的旧行为。注意：strict 模式下若异步
    校验判定不通过，保守答案会在稍后的 grounding 事件中下发并覆盖已显示内容。
    """
    v = _override("grounding_async")
    if v is not None:
        return bool(v)
    try:
        return bool(_read_cfg().get("grounding_async", True))
    except Exception:
        return True


def get_chat_thinking_mode():
    """AI 问答思维模式：

    - "mixed"  混合标注（默认）：检索资料并依据回答，资料未覆盖处用通用知识/推断
               补充，回答逐段标注来源（📚资料 / 💡补充），全程透明。
    - "strict" 严格依据资料：仅根据已索引资料回答，不补充任何外部知识。
    """
    try:
        from core.config_loader import get_config as _gc
        m = _gc("modules.ai_center.chat_thinking_mode")
        if m in ("mixed", "strict"):
            return m
    except Exception:
        pass
    return "mixed"


# ============================================================
# 文档档位（public / general / precise）
# ============================================================
# 档位决定两件事：
#   1) OCR 扫描页数：公共/一般只扫前若干页（快），精确档整本全扫（慢但完整）；
#   2) 后续回答策略：精确档要求严格依据原文并标注出处（见 qa.py）。
QA_MODES = ("public", "general", "precise")

# 各档位默认 OCR 页数上限；0 表示不限制（整本全扫）
_DEFAULT_OCR_PAGES = {"public": 12, "general": 12, "precise": 0}
# 未匹配到任何配置时的默认档位
_DEFAULT_QA_MODE = "general"


def _qa_mode_cfg():
    """读取档位配置块（ai_center.qa_mode），读不到返回空 dict。"""
    try:
        from core.config_loader import get_config as _gc
        cfg = _gc("modules.ai_center.qa_mode") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def get_qa_mode_by_category(cat1=None, cat2=None):
    """按分类查档位：先匹配「cat1/cat2」，再匹配「cat1」，最后用全局默认。"""
    cfg = _qa_mode_cfg()
    by_cat = cfg.get("by_category") or {}
    if not isinstance(by_cat, dict):
        by_cat = {}
    if cat1 and cat2:
        m = by_cat.get(f"{cat1}/{cat2}")
        if m in QA_MODES:
            return m
    if cat1:
        m = by_cat.get(str(cat1))
        if m in QA_MODES:
            return m
    d = cfg.get("default")
    return d if d in QA_MODES else _DEFAULT_QA_MODE


def get_book_qa_mode(book):
    """解析某本书的档位。优先级：单本 qa_mode > 分类配置 > 全局默认。"""
    if isinstance(book, dict):
        own = (book.get("qa_mode") or "").strip().lower()
        if own in QA_MODES:
            return own
        return get_qa_mode_by_category(book.get("cat1"), book.get("cat2"))
    return get_qa_mode_by_category(None, None)


# 各档位的相关性阈值（0~1）：命中分数低于该值即判为「资料中没有」。
# 目的是宁可回答「找不到」，也不拿无关片段凑数让模型编造。
# 实测 bge-large 归一化内积：相关命中约 0.47~0.55，无关命中约 0.36~0.42。
_DEFAULT_MIN_SCORE = {"public": 0.30, "general": 0.40, "precise": 0.45}


def get_min_score(qa_mode=None, cfg_scores=None):
    """返回相关性阈值。qa_mode 为空或非法时用配置默认档位的阈值。"""
    scores = cfg_scores if isinstance(cfg_scores, dict) else (_qa_mode_cfg().get("min_score") or {})
    if isinstance(scores, dict) and scores:
        key = qa_mode if qa_mode in QA_MODES else (_qa_mode_cfg().get("default") or _DEFAULT_QA_MODE)
        v = scores.get(key, scores.get(_DEFAULT_QA_MODE))
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    if qa_mode in _DEFAULT_MIN_SCORE:
        return _DEFAULT_MIN_SCORE[qa_mode]
    return _DEFAULT_MIN_SCORE.get(_DEFAULT_QA_MODE, 0.40)


def get_ocr_max_pages_for_mode(qa_mode, cfg_pages=None):
    """返回该档位的 OCR 页数上限；0 表示不限（整本扫描）。"""
    pages = cfg_pages if isinstance(cfg_pages, dict) else (_qa_mode_cfg().get("ocr_max_pages") or {})
    if isinstance(pages, dict):
        v = pages.get(qa_mode)
        if v is not None:
            try:
                return max(0, int(v))
            except Exception:
                pass
    return _DEFAULT_OCR_PAGES.get(qa_mode, 12)
