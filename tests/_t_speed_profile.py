# -*- coding: utf-8 -*-
"""速度档位（#1C）与引用校验异步化（#2）回归测试。

覆盖：
  1. 三档位对 hyde / rerank / citation_check / rerank_max_candidates 的覆盖；
  2. 未选档位时回落到全局配置（向后兼容）；
  3. ContextVar 请求级隔离（并发互不干扰）；
  4. 流式事件顺序：done 先出（grounding_pending），grounding 随后单独下发；
  5. grounding_async=false 时回到旧的同步收尾（done 直接带 grounding 布尔）；
  6. strict 模式异步校验不通过时，保守答案在 grounding 事件中下发。

跑法：D:/anaconda/python.exe _t_speed_profile.py
"""
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.ai_center.internal import config as cfg
from modules.ai_center.internal import qa

ok_all = True


def check(idx, cond, msg):
    global ok_all
    ok_all = ok_all and bool(cond)
    print("[%s] %s -> %s" % (idx, "PASS" if cond else "FAIL", msg))


# ---------------------------------------------------------------------------
# 1~3. 档位覆盖
# ---------------------------------------------------------------------------
print("=== 速度档位覆盖 ===")
cfg.set_speed_profile(None)
base = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(),
        cfg.is_citation_check_enabled(), cfg.get_rerank_max_candidates())
print("   未选档位(全局默认): hyde=%s rerank=%s citation=%s cand=%s" % base)
check("1", base[0] and base[1] and base[2],
      "未选档位时回落全局配置（默认全开）")

cfg.set_speed_profile("high")
h = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(),
     cfg.is_citation_check_enabled(), cfg.get_rerank_max_candidates())
print("   high:     hyde=%s rerank=%s citation=%s cand=%s" % h)
check("2", h == (True, True, True, 24), "high 档 = 全开 + 候选 24")

cfg.set_speed_profile("balanced")
b = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(),
     cfg.is_citation_check_enabled(), cfg.get_rerank_max_candidates())
print("   balanced: hyde=%s rerank=%s citation=%s cand=%s" % b)
check("3", b == (True, True, True, 12), "balanced 档 = 全开 + 候选 12（省约一半重排）")

cfg.set_speed_profile("fast")
f = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(),
     cfg.is_citation_check_enabled(), cfg.get_rerank_max_candidates())
print("   fast(已删): hyde=%s rerank=%s citation=%s cand=%s" % f)
check("4", "fast" not in cfg.SPEED_PROFILES and f[0] and f[1],
      "fast 档已删除，传入 'fast' 安全回落全局（不误关）")

cfg.set_speed_profile("不存在的档位")
u = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(), cfg.is_citation_check_enabled())
check("5", u == (True, True, True), "未知档位安全回落全局（不误关）")

cfg.set_speed_profile(None)
check("6", (cfg.is_hyde_enabled(), cfg.is_rerank_enabled()) == (True, True),
      "清空档位后恢复全局默认")


# ---------------------------------------------------------------------------
# 7. ContextVar 并发隔离：子线程设 balanced（cand=12），不得污染主线程 high（cand=24）
# ---------------------------------------------------------------------------
print("\n=== 并发隔离（ContextVar） ===")
seen = {}
cfg.set_speed_profile("high")


def worker():
    cfg.set_speed_profile("balanced")
    seen["sub"] = cfg.get_rerank_max_candidates()


t = threading.Thread(target=worker)
t.start()
t.join()
seen["main"] = cfg.get_rerank_max_candidates()
check("7", seen.get("sub") == 12 and seen.get("main") == 24,
      "子线程 balanced(cand=12) 未污染主线程 high(cand=24)：sub=%s main=%s"
      % (seen.get("sub"), seen.get("main")))


# ---------------------------------------------------------------------------
# 8~11. 流式事件顺序（桩掉检索与 LLM，只测收尾逻辑）
# ---------------------------------------------------------------------------
print("\n=== 流式引用校验异步化 ===")

# 注意：_pack_sources / _build_ctx 读的是 content 字段（不是 text），
# 桩数据必须带 content，否则 sources 为空 -> _verify_grounding 直接短路返回 True，
# 会让「校验不通过」的用例永远测不出来。
FAKE_HITS = [
    {"book_id": 1, "book_name": "测试手册", "section_path": "第一章",
     "content": "某岗位的职责包含 A、B、C。", "score": 0.9},
]

state = {"grounding_reply": "通过：资料中有依据", "llm_calls": 0}


def fake_llm_generate(prompt, max_tokens=512, temperature=0.7, history=None, stream=False):
    state["llm_calls"] += 1
    if "可信度校验员" in (prompt or ""):
        return state["grounding_reply"]
    if stream:
        def _gen():
            for ch in "参考答案内容":
                yield ch
        return _gen()
    return "参考答案内容"


# 桩：跳过向量管线（角色直查命中即短路），并把 LLM 与 HyDE 换掉。
#is_mode_ready 也要桩掉——若本机索引恰好「未就绪」（如与 DB 不一致
# 触发懒重建），ready 检查在角色召回之前就短路走 _stream_mixed_free，导致 8~13 全失真。
qa._recall_role_content = lambda q, user=None: (list(FAKE_HITS), "某岗位")
qa.llm_generate = fake_llm_generate
qa._hyde_hypothetical = lambda q: ""
qa._encode_query = lambda t, user=None: [0.1, 0.2, 0.3]
from modules.ai_center.internal import vector_store as _vs
_vs.is_mode_ready = lambda mode=None: (True, "")
_vs.ensure_mode_ready = lambda mode=None, lazy=False: None


def run_stream(mode, profile=None):
    cfg.set_speed_profile(profile)
    return list(qa.global_chat_stream("某岗位的职责有哪些？", history=[], mode=mode, user=None))


# --- 异步（默认） ---
cfg.set_speed_profile(None)
evs = run_stream("mixed")
types = [e.get("type") for e in evs]
print("   事件序列: %s" % types)
i_done = types.index("done") if "done" in types else -1
i_gr = types.index("grounding") if "grounding" in types else -1
check("8", i_done >= 0 and i_gr > i_done,
      "done(%d) 先于 grounding(%d) 下发 —— 答案不必等校验" % (i_done, i_gr))
done_ev = evs[i_done]
check("9", done_ev.get("grounding_pending") is True and done_ev.get("grounding") is None,
      "done 带 grounding_pending=True（前端显示「校验中…」）")
check("9b", len(done_ev.get("sources") or []) > 0,
      "桩数据确实产出 sources（否则校验会被静默短路，后续用例失真）")
gr_ev = evs[i_gr]
check("10", gr_ev.get("grounding") is True, "校验通过 -> grounding=True")

# --- 校验不通过 + strict：保守答案延后下发 ---
state["grounding_reply"] = "不通过：答案中的数字在资料里找不到"
evs2 = run_stream("strict")
t2 = [e.get("type") for e in evs2]
gr2 = evs2[t2.index("grounding")]
check("11", gr2.get("grounding") is False and "引用校验" in (gr2.get("answer") or ""),
      "strict 校验不通过 -> grounding 事件下发保守答案（覆盖展示）")
state["grounding_reply"] = "通过：资料中有依据"

# --- 同步（grounding_async=false）回到旧行为 ---
cfg.set_speed_profile(None)
orig_cfg_get = cfg._read_cfg
cfg._read_cfg = lambda: dict(orig_cfg_get(), grounding_async=False)
evs3 = run_stream("mixed")
cfg._read_cfg = orig_cfg_get
t3 = [e.get("type") for e in evs3]
check("12", "grounding" not in t3 and evs3[t3.index("done")].get("grounding") is True,
      "grounding_async=false -> 无独立 grounding 事件，done 直接带结果（旧行为）")

# --- fast 档已删除：传 'fast' 回落全局，仍正常走完整管线（含异步校验） ---
cfg.set_speed_profile(None)
state["llm_calls"] = 0
evs4 = run_stream("mixed", "fast")   # 档位必须随调用传入，否则 run_stream 会清掉覆盖
cfg.set_speed_profile(None)
t4 = [e.get("type") for e in evs4]
i_d4 = t4.index("done") if "done" in t4 else -1
i_g4 = t4.index("grounding") if "grounding" in t4 else -1
check("13", i_d4 >= 0 and i_g4 > i_d4 and state["llm_calls"] >= 2,
      "'fast' 回落全局（异步校验仍在）: done=%d grounding=%d llm_calls=%d"
      % (i_d4, i_g4, state["llm_calls"]))

# --- 问候语直答：不进检索、零 LLM 调用 ---
print("\n=== 问候语直答 ===")
state["llm_calls"] = 0
evs5 = list(qa.global_chat_stream("你好，你能做什么", history=[], mode="mixed", user=None))
t5 = [e.get("type") for e in evs5]
ans5 = next((e for e in evs5 if e.get("type") == "answer"), None)
done5 = next((e for e in evs5 if e.get("type") == "done"), None)
check("18", ans5 is not None and done5 is not None and state["llm_calls"] == 0
      and not (done5.get("sources") or []),
      "「你好，你能做什么」直答：answer+done 齐全、零 LLM 调用、无 sources")

evs6 = list(qa.global_chat_stream("某岗位的职责有哪些？", history=[], mode="mixed", user=None))
t6 = [e.get("type") for e in evs6]
check("19", "answer" in t6 or "delta" in t6,
      "非问候问题不受影响，仍走正常检索/生成管线")

# ---------------------------------------------------------------------------
# 14~17. 配置中心写入接口 set_qa_speed（#1B）
# ---------------------------------------------------------------------------
print("\n=== 配置中心写入接口（#1B） ===")
try:
    from core.ai_config_store import set_qa_speed, get_qa_speed, read_ai_config

    orig = get_qa_speed()
    print("   原始值: %s" % orig)
    try:
        # 14. 全关 + 设备/候选数 写入后能被 qa 侧实时读到
        set_qa_speed({"hyde": False, "rerank": False, "citation_check": False,
                      "grounding_async": False, "rerank_device": "cpu",
                      "rerank_max_candidates": 8})
        cfg.set_speed_profile(None)
        got = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled(),
               cfg.is_citation_check_enabled(), cfg.is_grounding_async(),
               cfg.get_rerank_device(), cfg.get_rerank_max_candidates())
        check("14", got == (False, False, False, False, "cpu", 8),
              "关全部开关后 qa 侧实时读到: %s" % (got,))

        # 15. 非法值被纠正（设备 gpu -> auto，候选数 999 -> 24）
        set_qa_speed({"rerank_device": "gpu", "rerank_max_candidates": 999})
        g2 = (cfg.get_rerank_device(), cfg.get_rerank_max_candidates())
        check("15", g2 == ("auto", 24), "非法设备/越界候选数被纠正: %s" % (g2,))

        # 16. 白名单：非白名单键不得落盘（防误改其它配置）
        before_other = (read_ai_config() or {}).get("security_lock")
        set_qa_speed({"security_lock": True, "evil_key": "x"})
        after_other = (read_ai_config() or {}).get("security_lock")
        has_evil = "evil_key" in (read_ai_config() or {})
        check("16", before_other == after_other and not has_evil,
              "非白名单键被忽略（security_lock 未变、evil_key 未写入）")

        # 17. 字符串布尔值（表单提交场景）也能正确解析
        set_qa_speed({"hyde": "false", "rerank": "true"})
        g4 = (cfg.is_hyde_enabled(), cfg.is_rerank_enabled())
        check("17", g4 == (False, True), "字符串 'false'/'true' 解析正确: %s" % (g4,))
    finally:
        # 无论成败都要把真实配置还原，避免污染用户环境
        set_qa_speed(orig)
        restored = get_qa_speed()
        print("   已还原: %s（与原始一致=%s）" % (restored, restored == orig))
        if restored != orig:
            ok_all = False
except Exception as e:
    print("   跳过（core.ai_config_store 不可用）: %s" % e)

cfg.set_speed_profile(None)

print("")
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
