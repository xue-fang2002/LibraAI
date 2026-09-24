# -*- coding: utf-8 -*-
"""
AI 问答链路分步耗时统计（非侵入式，不修改 qa.py）。

做法：在运行时把 qa 模块里的各步骤函数 / vector_store.query 包一层
perf_counter 计时器，然后驱动 global_chat_stream 跑一批代表性问题，
汇总每步的 次数 / 均值 / 最小 / 最大 / P50 / P95，并落一份 markdown 报告。

运行：D:\anaconda\python.exe _bench_qa.py
"""
import os
import sys
import time
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------- 1. 准备 Flask 应用上下文（与生产一致） ----------
_app = None
try:
    from app import create_app
    _app = create_app()
    _ctx = _app.app_context()
    _ctx.push()
    print("[init] Flask app context ready")
except Exception as e:
    print(f"[init] 无法创建 app context（{e}），尝试无上下文运行")

# ---------- 2. 导入被测模块 ----------
from modules.ai_center.internal import qa as qa_mod
from modules.ai_center.internal import vector_store as vs_mod

STATS = {}   # step_name -> [durations]
TOTALS = []  # 每次问答的总耗时
META = []    # 每次问答的元信息


def record(name, dt):
    STATS.setdefault(name, []).append(dt)


def timed(label):
    def deco(f):
        def w(*a, **k):
            t0 = time.perf_counter()
            try:
                return f(*a, **k)
            finally:
                record(label, time.perf_counter() - t0)
        return w
    return deco


# ---------- 3. 特殊包裹 llm_generate（区分主生成 vs 引用校验；流式计时覆盖真实生成） ----------
_orig_llm = qa_mod.llm_generate


def _timed_llm_generate(*a, **k):
    # 启发式区分：引用校验调用 max_tokens<=80 且 temperature==0.0
    mt = k.get("max_tokens", a[1] if len(a) > 1 else 512)
    tp = k.get("temperature", a[2] if len(a) > 2 else 0.3)
    label = "llm_grounding" if (mt <= 80 and tp == 0.0) else "llm_answer"
    t0 = time.perf_counter()
    res = _orig_llm(*a, **k)
    if not k.get("stream"):          # 非流式：直接计时
        record(label, time.perf_counter() - t0)
        return res
    gen = res                        # 流式：真实生成发生在迭代期，包裹迭代计时

    def _w():
        tg = time.perf_counter()
        try:
            yield from gen
        finally:
            record(label, time.perf_counter() - tg)
    return _w()


qa_mod.llm_generate = _timed_llm_generate

# ---------- 4. 包裹其余步骤函数 ----------
_STEP_FUNCS = [
    ("_rewrite_query", "rewrite_query"),
    ("_encode_query", "encode_query"),
    ("_recall_role_content", "recall_role_content"),
    ("_recall_person_attr", "recall_person_attr"),
    ("_excel_table_recall", "excel_table_recall"),
    ("_filter_by_score", "filter_by_score"),
    ("_rerank_two_stage", "rerank_two_stage"),
    ("_filter_by_permission", "filter_by_permission"),
    ("_merge_literal_hits", "merge_literal_hits"),
    ("_build_ctx", "build_ctx"),
    ("_book_outline_block", "book_outline_block"),
    ("_verify_grounding", "verify_grounding"),
]
for fn_name, label in _STEP_FUNCS:
    orig = getattr(qa_mod, fn_name)
    setattr(qa_mod, fn_name, timed(label)(orig))

# vector_store.query 单独包裹
_orig_vq = vs_mod.query


def _timed_query(*a, **k):
    t0 = time.perf_counter()
    try:
        return _orig_vq(*a, **k)
    finally:
        record("vector_query", time.perf_counter() - t0)


vs_mod.query = _timed_query

# ---------- 5. 代表性问题集（混合命中 / 未命中 / 分类 / strict） ----------
QUESTIONS = [
    {"q": "公司员工年假天数是怎么规定的？", "mode": "mixed"},
    {"q": "请假需要走什么流程？", "mode": "mixed"},
    {"q": "公司都有哪些岗位分类？", "mode": "mixed"},
    {"q": "考勤制度里迟到和早退怎么处理？", "mode": "mixed"},
    {"q": "报销审批的权限是怎么划分的？", "mode": "strict"},
    {"q": "公司安全生产职责由谁负责？", "mode": "mixed"},
    {"q": "请用一句话解释什么是机器学习。", "mode": "mixed"},
    {"q": "今天天气怎么样？", "mode": "mixed"},
]
PASSES = 2  # 每题跑两遍，增加样本量


def run_one(q, mode):
    t0 = time.perf_counter()
    evs = list(qa_mod.global_chat_stream(q, history=[], mode=mode, user=None))
    total = time.perf_counter() - t0
    TOTALS.append(total)
    kinds = sorted({e.get("type") for e in evs})
    ans = ""
    for e in evs:
        if e.get("type") == "done":
            ans = (e.get("answer") or "")[:40]
        elif e.get("type") == "answer":
            ans = (e.get("text") or "")[:40]
    META.append((q[:20], mode, len(evs), ",".join(kinds), total, ans))
    return evs


# ---------- 6. 预热（冷启动：加载嵌入/重排/远端模型），预热结果丢弃 ----------
print("[warmup] 预热中（加载模型，约数十秒）...")
run_one("测试一下系统", "mixed")
STATS.clear()
TOTALS.clear()
META.clear()
print("[warmup] 预热完成，开始正式统计\n")

# ---------- 7. 正式跑批 ----------
plan = [(q["q"], q["mode"]) for q in QUESTIONS for _ in range(PASSES)]
for i, (q, mode) in enumerate(plan, 1):
    print(f"[{i}/{len(plan)}] {q}  ({mode})")
    run_one(q, mode)

# ---------- 8. 汇总 ----------
_ORDER = [
    "rewrite_query", "encode_query", "recall_role_content", "recall_person_attr",
    "excel_table_recall", "vector_query", "filter_by_score", "rerank_two_stage",
    "filter_by_permission", "merge_literal_hits", "build_ctx", "book_outline_block",
    "llm_answer", "verify_grounding", "llm_grounding",
]


def pct(lst, p):
    if not lst:
        return 0.0
    s = sorted(lst)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def agg(name):
    lst = STATS.get(name, [])
    if not lst:
        return None
    return {
        "n": len(lst), "total": sum(lst), "mean": statistics.mean(lst),
        "min": min(lst), "max": max(lst),
        "p50": pct(lst, 0.5), "p95": pct(lst, 0.95),
    }


rows = []
for name in _ORDER:
    a = agg(name)
    if a:
        rows.append((name, a))

tot = agg("__totals__")
TOTALS_stats = {
    "n": len(TOTALS), "total": sum(TOTALS), "mean": statistics.mean(TOTALS),
    "min": min(TOTALS), "max": max(TOTALS),
    "p50": pct(TOTALS, 0.5), "p95": pct(TOTALS, 0.95),
}

# ---------- 9. 打印 & 写报告 ----------
def fmt(x):
    return f"{x*1000:7.1f} ms"


print("\n================= AI 问答分步耗时统计（热身后） =================")
print(f"{'步骤':<22}{'n':>4}{'累计':>10}{'均值':>10}{'最小':>10}{'最大':>10}{'P50':>10}{'P95':>10}")
for name, a in rows:
    print(f"{name:<22}{a['n']:>4}{fmt(a['total']):>10}{fmt(a['mean']):>10}"
          f"{fmt(a['min']):>10}{fmt(a['max']):>10}{fmt(a['p50']):>10}{fmt(a['p95']):>10}")
print("-" * 84)
print(f"{'【整条问答 端到端】':<22}{TOTALS_stats['n']:>4}"
      f"{fmt(TOTALS_stats['total']):>10}{fmt(TOTALS_stats['mean']):>10}"
      f"{fmt(TOTALS_stats['min']):>10}{fmt(TOTALS_stats['max']):>10}"
      f"{fmt(TOTALS_stats['p50']):>10}{fmt(TOTALS_stats['p95']):>10}")

# markdown 报告
md = ["# AI 问答链路分步耗时统计", "",
      f"> 环境：backend=ollama（{qa_mod.__dict__.get('__name__','')}），"
      "问题数={0}（每题 {1} 遍，预热后统计）".format(len(QUESTIONS), PASSES),
      "", "## 分步耗时（毫秒）", "",
      "| 步骤 | 调用次数 | 累计 | 均值 | 最小 | 最大 | P50 | P95 |",
      "|---|---:|---:|---:|---:|---:|---:|---:|"]
for name, a in rows:
    md.append(f"| {name} | {a['n']} | {a['total']*1000:.1f} | {a['mean']*1000:.1f} | "
              f"{a['min']*1000:.1f} | {a['max']*1000:.1f} | {a['p50']*1000:.1f} | {a['p95']*1000:.1f} |")
md.append(f"| **整条问答 端到端** | {TOTALS_stats['n']} | {TOTALS_stats['total']*1000:.1f} | "
          f"{TOTALS_stats['mean']*1000:.1f} | {TOTALS_stats['min']*1000:.1f} | "
          f"{TOTALS_stats['max']*1000:.1f} | {TOTALS_stats['p50']*1000:.1f} | {TOTALS_stats['p95']*1000:.1f} |")
md.append("")
md.append("## 各次问答端到端耗时与走向")
md.append("")
md.append("| # | 问题 | 模式 | 事件数 | 事件类型 | 端到端(ms) | 回答预览 |")
md.append("|---|---|---|---:|---|---:|---|")
for i, (q, mode, ne, kinds, total, ans) in enumerate(META, 1):
    md.append(f"| {i} | {q} | {mode} | {ne} | {kinds} | {total*1000:.1f} | {ans} |")
md.append("")
md.append("## 说明")
md.append("")
md.append("- 计时不修改 qa.py，运行时包裹各步骤函数 + vector_store.query。")
md.append("- `llm_answer` = 主回答生成；`verify_grounding` = 引用校验（其内部再调一次 llm_generate，计为 `llm_grounding`，是 verify_grounding 的子集）。")
md.append("- 部分步骤为条件分支（如 recall_role_content / excel_table_recall / verify_grounding 仅在命中资料路径触发），故“调用次数”随问题而异。")
md.append("- 端到端包含生成器惰性求值：首包前的检索/重排/拼接与逐字流式生成都计入。")

with open(os.path.join(HERE, "_bench_qa_report.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(md))

print("\n[done] 报告已写入 _bench_qa_report.md")
