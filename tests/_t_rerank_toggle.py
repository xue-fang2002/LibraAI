# -*- coding: utf-8 -*-
"""
验证 rerank 开关闭包：
  - 开启时：二阶 cross-encoder（score_pairs）被调用；
  - 关闭时：直接回退一阶 lexical 结果，连 is_available 都不该被调用。

打桩掉真实 reranker（不加载 568M 模型），只验证控制流。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app import create_app

_app, _host, _port, _debug = create_app()
_ctx = _app.app_context()
_ctx.push()

from modules.ai_center.internal import qa as qa_mod
from modules.ai_center.internal import config as cfg_mod

CALLS = {"is_available": 0, "score_pairs": 0}


class FakeReranker(object):
    MAX_CANDIDATES = 24

    @staticmethod
    def get_max_candidates():
        return 24

    @staticmethod
    def is_available():
        CALLS["is_available"] += 1
        return True

    @staticmethod
    def score_pairs(question, passages):
        CALLS["score_pairs"] += 1
        return [0.95] * len(passages)


qa_mod.reranker = FakeReranker


def make_hits(n=6):
    out = []
    for i in range(n):
        out.append({
            "book_name": "测试手册",
            "section_path": "第%d章" % (i + 1),
            "content": "这是第%d段关于报销流程与审批权限的正文内容。" % (i + 1),
            "score": 0.90 - i * 0.05,
        })
    return out


def run_case(enabled):
    CALLS["is_available"] = 0
    CALLS["score_pairs"] = 0
    cfg_mod.is_rerank_enabled = (lambda: True) if enabled else (lambda: False)
    res = qa_mod._rerank_two_stage("报销流程是什么", make_hits())
    return res


ok = True

# --- 0. 默认值（未配置 rerank key 时应为 True，保证向后兼容）---
default_on = cfg_mod.is_rerank_enabled()
print("[0] 默认 is_rerank_enabled() = %s（期望 True）" % default_on)
ok = ok and (default_on is True)

# --- 1. 开关开启 ---
res_on = run_case(True)
print("[1] rerank=ON  -> score_pairs 调用 %d 次，is_available %d 次，返回 %d 条"
      % (CALLS["score_pairs"], CALLS["is_available"], len(res_on)))
ok = ok and CALLS["score_pairs"] >= 1

# --- 2. 开关关闭 ---
res_off = run_case(False)
print("[2] rerank=OFF -> score_pairs 调用 %d 次，is_available %d 次，返回 %d 条"
      % (CALLS["score_pairs"], CALLS["is_available"], len(res_off)))
ok = ok and CALLS["score_pairs"] == 0
ok = ok and CALLS["is_available"] == 0
ok = ok and len(res_off) > 0

# --- 3. 设备解析：无 CUDA 必须落回 cpu（与改动前一致，向后兼容）---
from modules.ai_center.internal import reranker as rr

try:
    import torch
    _cuda = torch.cuda.is_available()
except Exception:
    _cuda = False

dev_auto = rr._resolve_device()
print("[3] CUDA 可用=%s，rerank_device=auto -> %s" % (_cuda, dev_auto))
ok = ok and (str(dev_auto) == ("cuda" if _cuda else "cpu"))

cfg_mod.get_rerank_device = lambda: "cpu"
dev_cpu = rr._resolve_device()
print("[4] rerank_device=cpu 强制 -> %s（期望 cpu）" % dev_cpu)
ok = ok and (str(dev_cpu) == "cpu")

# --- 4. 候选数可配（CPU 上的折中档）---
cfg_mod.get_rerank_max_candidates = lambda: 12
n12 = rr.get_max_candidates()
cfg_mod.get_rerank_max_candidates = lambda: 24
n24 = rr.get_max_candidates()
print("[5] rerank_max_candidates 配置 12 -> %d，配置 24 -> %d（期望 12 / 24）" % (n12, n24))
ok = ok and (n12 == 12 and n24 == 24)


# --- 5. 优先 GPU、失败自动降级 CPU（假模型模拟，不加载 568M）---
class FakeModel(object):
    def __init__(self, fail_on=None):
        self.moves = []
        self.fail_on = fail_on

    def to(self, dev):
        # 先记录尝试，再抛错 —— 这样能断言「确实先试了 GPU 才降级」
        self.moves.append(str(dev))
        if self.fail_on is not None and str(dev) == self.fail_on:
            raise RuntimeError("CUDA out of memory")
        return self


import torch as _torch

_orig_cuda = _torch.cuda.is_available
cfg_mod.get_rerank_device = lambda: "auto"

try:
    # 5a. 有 GPU 且放得上去 -> 必须用 GPU
    _torch.cuda.is_available = lambda: True
    m1 = FakeModel()
    d1 = rr._place_model(m1)
    print("[6] 模拟有 GPU -> 设备=%s，尝试顺序=%s（期望 cuda）" % (d1, m1.moves))
    ok = ok and (str(d1) == "cuda" and m1.moves == ["cuda"])

    # 5b. 有 GPU 但显存不足 -> 必须自动降级 CPU，绝不中断
    m2 = FakeModel(fail_on="cuda")
    d2 = rr._place_model(m2)
    print("[7] 模拟 GPU 放置失败 -> 设备=%s，尝试顺序=%s（期望最终 cpu）" % (d2, m2.moves))
    ok = ok and (str(d2) == "cpu" and m2.moves == ["cuda", "cpu"])

    # 5c. 无 GPU（本机真实情况）-> cpu
    _torch.cuda.is_available = _orig_cuda
    m3 = FakeModel()
    d3 = rr._place_model(m3)
    print("[8] 无 GPU（本机真实）-> 设备=%s（期望 cpu）" % d3)
    ok = ok and (str(d3) == "cpu")
finally:
    _torch.cuda.is_available = _orig_cuda

print("")
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
