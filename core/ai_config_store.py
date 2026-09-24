"""AI 配置持久化存储（写 modules.yaml 的 ai_center 块，支持热重载，无需重启）。"""

import os
import threading

try:
    import yaml
except ImportError:
    yaml = None

from .config_loader import load_modules_config, _cache, _cache_lock

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG_PATH = os.path.join(_BASE_DIR, "config", "modules.yaml")

_lock = threading.Lock()

# ai_center 块的默认值结构
_DEFAULT_AI_CFG = {
    "enabled": True,
    "sort_order": 0,
    "ai_mode": "light",
    "security_lock": False,
    "backend": "off",
    "model_paths": {
        "light_emb": "models/bge-small-zh-v1.5",
        "full_emb": "models/bge-large-zh-v1.5",
        "local_llm": "models/qwen2.5-7b-instruct-q4_k_m.gguf",
    },
    "ollama": {"url": "http://localhost:11434", "model": "qwen2.5:7b"},
    "dashscope": {"url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen-plus", "api_key": ""},
    "vllm": {"url": "http://localhost:8000/v1", "model": "", "api_key": ""},
    "transformers": {"model_path": "models/"},
    # 「问答加速」可选步骤开关：必须登记在默认值表里，否则 read_ai_config()
    # 只按默认值键取值、会把 yaml 中已写入的这些键直接丢掉（历史 bug）。
    "hyde": True,                  # HyDE 查询增强
    "rerank": True,                # 二阶 cross-encoder 精排
    "citation_check": True,        # 生成后引用校验
    "grounding_async": True,       # 引用校验异步（答案先出）
    "rerank_device": "auto",       # 重排设备：auto / cuda / cpu
    "rerank_max_candidates": 20,   # 重排候选数上限
}


def _read_yaml():
    if not os.path.exists(_CFG_PATH):
        return {}
    try:
        with open(_CFG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) if yaml else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml(data):
    """原子写入 yaml：先写临时文件并 fsync，再 os.replace 覆盖，
    避免写入中途崩溃/断电把配置文件截空。"""
    import tempfile
    cfg_dir = os.path.dirname(os.path.abspath(_CFG_PATH))
    fd, tmp_path = tempfile.mkstemp(prefix=".modules_", suffix=".tmp", dir=cfg_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _CFG_PATH)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def read_ai_config():
    """读取 ai_center 配置，缺失字段用默认值补齐。"""
    data = _read_yaml()
    block = (data.get("modules") or {}).get("ai_center") or {}
    cfg = {}
    for k, v in _DEFAULT_AI_CFG.items():
        if isinstance(v, dict):
            cfg[k] = dict(v)
            cfg[k].update(block.get(k) or {})
        else:
            cfg[k] = block.get(k, v)
    # 确保 model_paths 子键完整
    mp = dict(_DEFAULT_AI_CFG["model_paths"])
    mp.update((block.get("model_paths") or {}))
    cfg["model_paths"] = mp
    return cfg


def write_ai_config(partial):
    """局部更新 ai_center 配置块并写回 yaml，同时刷新内存缓存。"""
    with _lock:
        data = _read_yaml()
        modules = data.setdefault("modules", {})
        block = modules.setdefault("ai_center", {})
        for k, v in (partial or {}).items():
            if isinstance(v, dict) and isinstance(block.get(k), dict):
                block[k].update(v)
            else:
                block[k] = v
        _write_yaml(data)
    # 强制刷新 config_loader 缓存，使 ai_interface 立即读到新值
    try:
        load_modules_config(force=True)
    except Exception:
        pass
    return read_ai_config()


def set_ai_mode(mode):
    """热切换 AI 模式（off/light/full），写回 yaml 并刷新缓存。"""
    mode = str(mode).lower()
    if mode not in ("off", "light", "full"):
        mode = "light"
    return write_ai_config({"ai_mode": mode, "enabled": mode != "off"})


def set_ai_backend(backend, backend_cfg=None):
    backend = str(backend or "off").lower()
    if backend not in ("off", "llama_cpp", "ollama", "dashscope", "vllm", "transformers"):
        backend = "off"
    partial = {"backend": backend}
    if backend_cfg:
        partial[backend] = backend_cfg
    return write_ai_config(partial)


def set_ai_model_paths(paths):
    cur = read_ai_config().get("model_paths", {})
    cur.update({k: v for k, v in (paths or {}).items() if v})
    return write_ai_config({"model_paths": cur})


def set_security_lock(enabled):
    return write_ai_config({"security_lock": bool(enabled)})


# 「问答加速」可选步骤开关：仅这些键允许由配置中心 UI 写入，防止误改其它配置
_QA_SPEED_KEYS = (
    "hyde",              # HyDE 查询增强（每次问答多 1 次 LLM 调用）
    "rerank",            # 二阶 cross-encoder 精排
    "citation_check",    # 生成后引用校验（groundedness）
    "grounding_async",   # 引用校验异步（答案先出、校验后到）
    "rerank_device",     # 重排设备：auto / cuda / cpu
    "rerank_max_candidates",  # 重排候选数上限
)


def set_qa_speed(partial):
    """热更新「问答加速」可选步骤开关（白名单 + 取值校验），写回 yaml 并刷新缓存。

    校验放在这里而不是调用方：无论谁调用都保证落盘的是合法值。

    这些开关每次问答都会被实时读取，因此**无需重启服务**；唯一例外是
    rerank_device——重排模型一旦加载就不会再迁移设备，改它需重启才生效。
    """
    p = partial or {}
    patch = {}
    for k in ("hyde", "rerank", "citation_check", "grounding_async"):
        if k in p:
            v = p[k]
            if isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "yes", "on")
            patch[k] = bool(v)
    if "rerank_device" in p:
        d = str(p.get("rerank_device") or "auto").strip().lower()
        patch["rerank_device"] = d if d in ("auto", "cuda", "cpu") else "auto"
    if "rerank_max_candidates" in p:
        try:
            n = int(p.get("rerank_max_candidates"))
        except Exception:
            n = 24
        patch["rerank_max_candidates"] = n if 1 <= n <= 64 else 24
    return write_ai_config(patch)


def get_qa_speed():
    """读取「问答加速」开关当前值（缺失/非法值一律回落到默认，与 qa 侧默认一致）。"""
    cfg = read_ai_config() or {}
    dev = str(cfg.get("rerank_device") or "auto").strip().lower()
    if dev not in ("auto", "cuda", "cpu"):
        dev = "auto"
    try:
        cand = int(cfg.get("rerank_max_candidates", 24) or 24)
    except Exception:
        cand = 24
    if not (1 <= cand <= 64):
        cand = 24
    return {
        "hyde": bool(cfg.get("hyde", True)),
        "rerank": bool(cfg.get("rerank", True)),
        "citation_check": bool(cfg.get("citation_check", True)),
        "grounding_async": bool(cfg.get("grounding_async", True)),
        "rerank_device": dev,
        "rerank_max_candidates": cand,
    }


# ============================================================
# 分类 → 问答档位映射（modules.ai_center.qa_mode.by_category）
#
# 语义：分类本身不存分数，只存「档位名」，再由档位决定两件事——
#   · 相关性阈值 min_score（public .30 / general .40 / precise .45）
#   · OCR 扫描页数 ocr_max_pages（public/general 12 页，precise 0=整本全扫）
# 取值时按 单本 qa_mode > cat1/cat2 > cat1 > 全局 default 回落（见 ai_center config.py）。
# ============================================================
_QA_MODES = ("public", "general", "precise")


def is_valid_category_path(cat_path):
    """分类路径合法性：只允许「一级」或「一级/二级」两种形态。

    历史脏数据形如「文件资料/会议纪要/会议纪要/…」（重复嵌套多层），这类键
    get_qa_mode_by_category 永远匹配不到，属死数据——写入侧一律拒收。
    """
    if not cat_path or not isinstance(cat_path, str):
        return False
    parts = [p.strip() for p in cat_path.split("/")]
    if len(parts) not in (1, 2):
        return False
    if not all(parts):
        return False
    # 两段同名（「会议纪要/会议纪要」）必然是构造错误
    if len(parts) == 2 and parts[0] == parts[1]:
        return False
    return True


def _norm_cat_path(cat_path):
    return "/".join(p.strip() for p in str(cat_path).split("/"))


def set_qa_mode_by_category(cat_path, mode):
    """设置或清除某分类的问答档位，写回 yaml 并热刷新缓存。

    mode 为空 / None → 删除该键，语义为「继承上级分类或全局默认」。
    顺带清掉历史脏键（超过两级 / 空 / 同名重复 / 值非法），避免死数据继续堆积。

    注意不能直接用 write_ai_config()：它对 dict 做 update 合并，
    只能增改、删不掉键，而「设为继承」本质就是删除。
    """
    if not is_valid_category_path(cat_path):
        raise ValueError("分类路径非法：必须是「一级分类」或「一级/二级」")
    path = _norm_cat_path(cat_path)
    m = str(mode or "").strip().lower()
    if m and m not in _QA_MODES:
        raise ValueError("档位非法：只能是 public / general / precise")

    with _lock:
        data = _read_yaml()
        modules = data.setdefault("modules", {})
        block = modules.setdefault("ai_center", {})
        qa_mode = block.get("qa_mode")
        if not isinstance(qa_mode, dict):
            qa_mode = {}
            block["qa_mode"] = qa_mode
        by_cat = qa_mode.get("by_category")
        if not isinstance(by_cat, dict):
            by_cat = {}

        clean = {}
        for k, v in by_cat.items():
            if is_valid_category_path(k) and v in _QA_MODES:
                clean[_norm_cat_path(k)] = v
        if m:
            clean[path] = m
        else:
            clean.pop(path, None)
        removed = [k for k in by_cat if k not in clean]

        qa_mode["by_category"] = clean
        _write_yaml(data)

    try:
        load_modules_config(force=True)
    except Exception:
        pass
    return {"by_category": clean, "removed": removed}


def clean_qa_mode_by_category():
    """一次性清理 by_category 中的所有非法 / 失效键，返回被删除的键列表。"""
    with _lock:
        data = _read_yaml()
        qa_mode = ((data.get("modules") or {}).get("ai_center") or {}).get("qa_mode") or {}
        by_cat = qa_mode.get("by_category")
        if not isinstance(by_cat, dict) or not by_cat:
            return {"removed": [], "before": 0, "after": 0}

        clean, removed = {}, []
        for k, v in by_cat.items():
            if is_valid_category_path(k) and v in _QA_MODES:
                clean[_norm_cat_path(k)] = v
            else:
                removed.append(str(k))

        if removed:
            modules = data.setdefault("modules", {})
            block = modules.setdefault("ai_center", {})
            if not isinstance(block.get("qa_mode"), dict):
                block["qa_mode"] = {}
            block["qa_mode"]["by_category"] = clean
            _write_yaml(data)

    if removed:
        try:
            load_modules_config(force=True)
        except Exception:
            pass
    return {"removed": removed, "before": len(by_cat), "after": len(clean)}
