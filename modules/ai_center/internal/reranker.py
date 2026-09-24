# -*- coding: utf-8 -*-
"""二阶重排（cross-encoder）：向量召回后用 bge-reranker-v2-m3 精排。

与一阶的关键词判别（qa._rerank_by_lexical）分工互补：
  - 一阶：判断问题关键词是否真出现在书名/正文，抑制「字面沾边、实则无关」的噪声，
    并对命中书做整书保留（防分类问题整书被误杀）；
  - 二阶：把 (问题, 段落) **成对**送进 cross-encoder 打分，能捕捉「字面不重叠但语义
    高度相关」的命中，解决纯向量召回「语义最近却答非所问」的问题。

设计要点：
  - **懒加载**：首次调用才加载模型；加载失败后打标记不再重试，避免每次提问都卡一次。
  - **设备可配**：默认 auto —— 有 CUDA 用 GPU，无 CUDA 用 CPU。
    原先 device 硬编码 cpu，导致即使部署到带 GPU 的服务器，二阶重排仍跑在 CPU 上，
    「上 GPU」完全不生效。改为可配后，无 CUDA 环境与改动前行为一致。
  - **优雅降级**：任何异常都返回空列表，由调用方回退到一阶结果，绝不因重排失败而中断问答。
"""
import os
import threading

_MODEL = None
_TOKENIZER = None
_DEVICE = None      # 模型实际所在设备（score_pairs 需把输入张量搬到同一设备）
_LOAD_FAILED = False
_LOCK = threading.Lock()

# 4 层 dirname 落到 D:\xianmufile（与 qa._build_book_outline 同一套算法）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_DEFAULT_MODEL_DIR = os.path.join(_BASE_DIR, "models", "bge-reranker-v2-m3")


def _resolve_model_dir():
    """重排模型目录：优先用配置 model_paths.reranker，未配置则用默认目录。

原先路径在模块加载时硬编码为 models/bge-reranker-v2-m3，
    配置中心的「模型路径」设置对重排模型完全无效。
    改为可配置后，**未配置时仍回落到原目录**，默认行为与改动前一致。
    """
    try:
        from .config import get_model_paths
        p = ((get_model_paths() or {}).get("reranker") or "").strip()
        if p:
            return p if os.path.isabs(p) else os.path.join(_BASE_DIR, p)
    except Exception:
        pass
    return _DEFAULT_MODEL_DIR

# CPU 上 cross-encoder 逐对打分较慢：候选封顶 + 分批，单次提问重排耗时控制在秒级
MAX_CANDIDATES = 24
_BATCH_SIZE = 8
# 段落截断字符数（tokenizer 还会再按 max_length=512 截断，这里先挡一道避免超长输入）
_MAX_PASSAGE_CHARS = 1200


def _resolve_device():
    """重排模型运行设备：auto（默认）/ cuda / cpu。

原先 device 硬编码为 cpu，导致部署到带 GPU 的服务器后二阶重排
    依旧跑 CPU（本机实测 ~18.5s/次），「上 GPU」不产生任何收益。
    现在：未配置时走 auto —— 有 CUDA 用 GPU，无 CUDA 用 CPU（与改动前一致）。
    """
    dev = "auto"
    try:
        from .config import get_rerank_device
        dev = (get_rerank_device() or "auto").strip().lower()
    except Exception:
        dev = "auto"
    try:
        import torch
        if dev != "cpu" and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    except Exception:
        return None


def _place_model(mdl):
    """把模型放到目标设备：**优先 GPU，失败自动降级 CPU**，返回实际设备。

原先设备写死 cpu，部署到 GPU 服务器也享受不到加速。现在：
      - 目标为 GPU 但放不上去（显存不足 / 驱动异常 / CUDA 初始化失败）时，
        自动回退 CPU 并记下实际设备，绝不因设备问题中断整个问答；
      - 结果写入 _DEVICE，score_pairs 据此把输入张量搬到同一设备。
    """
    global _DEVICE
    import torch
    dev = _resolve_device()
    if dev is not None and str(dev) != "cpu":
        cands = [dev, torch.device("cpu")]
    else:
        cands = [torch.device("cpu")]
    for c in cands:
        try:
            mdl.to(c)
            _DEVICE = c
            print("🧠 重排设备: %s%s" % (c, "" if str(c) == "cpu" else "（GPU 加速）"))
            return c
        except Exception as e:
            print("⚠️ 重排模型放到 %s 失败，尝试降级: %s" % (c, e))
    _DEVICE = torch.device("cpu")
    return _DEVICE


def get_max_candidates():
    """二阶重排候选数上限（可配置，默认 24）。

    CPU 上每个候选都要过一遍 568M cross-encoder，耗时近似线性：上限 24→12
    大约省一半重排耗时，是「既不想全关 rerank、又嫌慢」时的折中档。
    """
    try:
        from .config import get_rerank_max_candidates
        n = int(get_rerank_max_candidates())
        if n > 0:
            return n
    except Exception:
        pass
    return MAX_CANDIDATES


def is_available():
    """模型目录是否就绪（只查文件、不加载模型）。

    必须同时确认 config 与**权重文件**都在：只查 config.json 会在权重尚未下载完时
    误判为可用，后果是召回被白白放大、且每次提问都白试一次加载。
    """
    if _LOAD_FAILED:
        return False
    md = _resolve_model_dir()   #支持配置覆盖，默认仍为原目录
    if not os.path.isdir(md):
        return False
    if not os.path.exists(os.path.join(md, "config.json")):
        return False
    for w in ("model.safetensors", "pytorch_model.bin"):
        if os.path.exists(os.path.join(md, w)):
            return True
    return False


def _load():
    """懒加载 cross-encoder，返回 (tokenizer, model)；不可用时返回 (None, None)。"""
    global _MODEL, _TOKENIZER, _LOAD_FAILED
    if _MODEL is not None:
        return _TOKENIZER, _MODEL
    if _LOAD_FAILED:
        return None, None
    with _LOCK:
        if _MODEL is not None:
            return _TOKENIZER, _MODEL
        if _LOAD_FAILED:
            return None, None
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForSequenceClassification

            md = _resolve_model_dir()   #支持配置覆盖，默认仍为原目录
            print("⏳ 加载重排模型: %s" % md)
            tok = AutoTokenizer.from_pretrained(md)
            mdl = AutoModelForSequenceClassification.from_pretrained(md)
            mdl.eval()
            try:
                _place_model(mdl)
            except Exception as e:
                print("⚠️ 重排设备放置失败，保持默认设备: %s" % e)
            _TOKENIZER, _MODEL = tok, mdl
            print("✅ 重排模型加载完成")
            return tok, mdl
        except Exception as e:
            print("⚠️ 重排模型加载失败，本次会话降级为关键词重排: %s" % e)
            _LOAD_FAILED = True
            return None, None


def score_pairs(question, passages):
    """对 (问题, 段落) 逐对打分，返回分数列表（越大越相关），失败返回 []。"""
    tok, mdl = _load()
    if tok is None or mdl is None or not passages:
        return []
    try:
        import torch

        pairs = [(question or "", (p or "")[:_MAX_PASSAGE_CHARS]) for p in passages]
        scores = []
        for i in range(0, len(pairs), _BATCH_SIZE):
            batch = pairs[i:i + _BATCH_SIZE]
            enc = tok(batch, padding=True, truncation=True,
                      max_length=512, return_tensors="pt")
            # 关键：tokenizer 产出在 CPU，模型可能在 GPU —— 不搬到同一设备会直接报
            # "Expected all tensors to be on the same device"，上 GPU 反而崩。
            try:
                if _DEVICE is not None:
                    enc = {k: v.to(_DEVICE) for k, v in enc.items()}
            except Exception:
                pass
            with torch.no_grad():
                logits = mdl(**enc).logits
                logits = logits.view(-1)
            scores.extend(float(x) for x in logits)
        return scores
    except Exception as e:
        print("⚠️ 重排打分失败，回退关键词重排: %s" % e)
        return []
