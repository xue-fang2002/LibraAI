"""意图路由与计划生成。

只有本文件会调用 LLM，其它模块想知道「该干什么」都从这里拿结果。

两级路由（刻意的，为了省 token）：
  1) 粗分类 —— 先判断属于哪个模块（甚至可能是「这只是个问题，不是任务」）
  2) 精选   —— 只把该模块的能力清单交给模型挑一个，并抽参数

如果关键词就能确定模块，第 1 步直接跳过，省掉一次完整推理
（llm_generate 是同步阻塞的，一次可能几十秒）。
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import registry
from .connectors.base import Capability, EXEC_INPLACE, EXEC_JUMP, EXEC_LINK

# 一句话里出现这些词时，基本可以确定不是「任务」，应引导回首页问答
_QA_HINTS = ("是什么", "为什么", "怎么做", "如何", "解释", "介绍一下", "区别",
             "请问", "吗？", "呢？", "什么意思")

_MAX_CANDIDATES = 40   # 单次交给 LLM 的能力上限（超出先按关键词预筛）。
                       # 工具箱自动发现后能力数可达 30+，故放宽到 40，确保不截断。


def _extract_json(raw):
    """容错抽取 LLM 输出里的 JSON。

    没有复用 toolbox/ai_assistant 的同名函数：那是工具箱的内部实现，
    ai_agent 不该反向依赖具体业务模块，宁可重复这十行。
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    text = str(raw)
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).replace("```", "")
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _llm(prompt: str, max_tokens: int = 400) -> Optional[str]:
    """调用 LLM。AI 不可用 / 超时都返回 None，由上层决定怎么兜底。"""
    try:
        from modules.ai_center.internal.qa import llm_generate
        return llm_generate(prompt, max_tokens=max_tokens, temperature=0.2)
    except Exception as e:
        print(f"⚠️ [ai_agent] LLM 不可用: {e}")
        return None


def looks_like_question(text: str) -> bool:
    """粗判：这是「提问」而不是「任务」。命中就引导用户回首页问答。"""
    t = (text or "").strip()
    if len(t) < 6 and not any(c in t for c in "？?"):
        return False
    return any(h in t for h in _QA_HINTS)


def _score_by_keywords(text: str, caps: List[Capability]) -> List[Capability]:
    """按关键词给能力打分，用于预筛（不调用 LLM）。"""
    t = (text or "").lower()
    scored = []
    for cap in caps:
        score = 0
        for kw in cap.keywords:
            if kw and str(kw).lower() in t:
                score += 1
        if cap.label and cap.label.lower() in t:
            score += 2
        scored.append((score, cap))
    scored.sort(key=lambda x: -x[0])
    return [cap for _, cap in scored]


def route_module(text: str) -> Tuple[Optional[str], str]:
    """第一级：判断属于哪个模块。返回 (module_id, 来源)。

    来源取 keyword（规则命中）或 llm（模型判断）；两者都没结果时返回 (None, "none")。
    """
    summaries = registry.modules_summary()
    if not summaries:
        return None, "none"

    # 规则优先：关键词命中唯一模块时不再问模型
    # 用 registry.match_module_by_keywords（遍历全量能力），
    # 而不是 summaries 里那份被截断的关键词列表 —— 详见该函数注释。
    hits = registry.match_module_by_keywords(text)
    if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]):
        return hits[0][1], "keyword"

    prompt = _build_route_prompt(text, summaries)
    raw = _llm(prompt, max_tokens=120)
    data = _extract_json(raw)
    if isinstance(data, dict):
        mod = (data.get("module") or "").strip()
        if mod in {s["module"] for s in summaries}:
            return mod, "llm"
    return None, "none"


def _build_route_prompt(text: str, summaries: List[Dict[str, Any]]) -> str:
    lines = []
    for s in summaries:
        kws = "、".join(s.get("keywords", [])[:8])
        lines.append(f'- {s["module"]}（{s["name"]}，{s["count"]}项能力）关键词：{kws}')
    return (
        "你是任务分发助手。判断下面这句话该交给哪个模块处理。\n"
        "只输出 JSON，不要解释、不要 markdown 围栏。\n\n"
        "可选模块：\n" + "\n".join(lines) + "\n\n"
        '输出格式：{"module": "模块id"}；若这句话只是提问（想了解某件事）而非要执行任务，'
        '输出 {"module": null}。\n\n'
        f"用户的话：{text}\n\n"
        "请只输出 JSON："
    )


def pick_capability(text: str, caps: List[Capability]) -> Dict[str, Any]:
    """第二级：在某模块的能力里挑一个并抽参数。

    返回 {capability, params, reply}；没匹配上时 capability 为 None。
    """
    if not caps:
        return {"capability": None, "params": {}, "reply": "当前没有可用的能力"}

    ranked = _score_by_keywords(text, caps)
    candidates = ranked[:_MAX_CANDIDATES]

    prompt = _build_pick_prompt(text, candidates)
    raw = _llm(prompt, max_tokens=500)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return {"capability": None, "params": {},
                "reply": "模型没有返回可解析的计划，请换个说法再试"}

    cid = (data.get("capability") or data.get("id") or "").strip()
    by_id = {c.cid: c for c in candidates}
    cap = by_id.get(cid)
    if cap is None:
        return {"capability": None, "params": {},
                "reply": str(data.get("reply") or "没有匹配到合适的能力")}

    params = normalize_params(data.get("params") or {}, cap)
    return {
        "capability": cap.cid,
        "params": params,
        "reply": str(data.get("reply") or ""),
        "missing": [p.name for p in cap.params if p.required and p.name not in params],
    }


def _build_pick_prompt(text: str, caps: List[Capability]) -> str:
    caps_json = json.dumps(registry.to_prompt_list(caps), ensure_ascii=False)
    return (
        "你是办公与工具助手。从下面的能力清单里选出最合适的一个，并给出参数。\n"
        "只输出 JSON，不要解释、不要 markdown 围栏。\n\n"
        "能力清单：\n" + caps_json + "\n\n"
        "输出格式：\n"
        '{"capability": "能力id", "params": {"参数名": "值"}, "reply": "一句话说明你的方案"}\n\n'
        "规则：\n"
        "1. capability 必须是清单里的 id，不要编造。\n"
        "2. 只给清单里存在的参数名；enum 只能取 options 里的值；bool 给 true/false。\n"
        "3. 缺必要参数时照样输出，但 reply 里说明还需要什么。\n"
        "4. 都不匹配时输出 {\"capability\": null, \"reply\": \"说明为什么匹配不上\"}。\n\n"
        f"用户需求：{text}\n\n"
        "请只输出 JSON："
    )


def normalize_params(raw: Dict[str, Any], cap: Capability) -> Dict[str, Any]:
    """按 ParamSpec 校验并规范化参数：类型不对就回落默认，越界值会被收住。"""
    out: Dict[str, Any] = {}
    for spec in cap.params:
        if spec.name not in raw:
            if spec.default is not None:
                out[spec.name] = spec.default
            continue
        val = raw[spec.name]
        try:
            if spec.type == "number":
                val = float(val)
            elif spec.type == "bool":
                val = val if isinstance(val, bool) else str(val).strip().lower() in (
                    "1", "true", "yes", "是", "on")
            elif spec.type == "enum":
                if spec.options and str(val) not in [str(o) for o in spec.options]:
                    val = spec.default if spec.default is not None else spec.options[0]
                val = str(val)
            else:
                val = str(val)
        except (TypeError, ValueError):
            val = spec.default
        if val is not None:
            out[spec.name] = val
    # 保留模型额外给出的未知参数，便于排查（但不参与执行）
    # 跳过 "__" 开头的内部键：那是前端/后端之间传文件列表等控制信息用的，
    # 绝不能让模型生成的内容混进来。
    for k, v in (raw or {}).items():
        if k.startswith("__"):
            continue
        if k not in out and isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


def plan(text: str) -> Dict[str, Any]:
    """完整规划流程。返回统一结构，routes 直接转成响应。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty", "reply": "请先描述你要做的事"}

    if looks_like_question(text):
        return {
            "ok": False,
            "reason": "question",
            "reply": "这更像是一个问题而不是任务，去首页「AI 智能问答」会更合适。",
        }

    module, source = route_module(text)
    caps = registry.by_module(module) if module else []

    if not caps:
        # 粗分类没结果时的降级：不分模块，直接拿关键词预筛出的候选做一次精筛。
        # 这样「二维码」「JSON」这类没被模型判出来、但关键词能命中的需求仍有救。
        caps = _score_by_keywords(text, registry.all_capabilities())[:_MAX_CANDIDATES]
        source = "fallback" if not module else source

    picked = pick_capability(text, caps)
    if not picked.get("capability"):
        return {"ok": False, "reason": "no_capability",
                "module": module, "reply": picked.get("reply") or "没有匹配到具体能力"}

    cap = registry.get_capability(picked["capability"])
    if cap is None:
        return {"ok": False, "reason": "no_capability", "reply": "能力已失效"}
    module = cap.module

    conn = registry.get_connector(module)
    link = conn.build_link(cap, picked["params"]) if conn else ""

    return {
        "ok": True,
        "module": module,
        "module_name": conn.display_name if conn else module,
        "route_source": source,
        "capability": cap.to_dict(),
        "params": picked["params"],
        "missing": picked.get("missing", []),
        "execution": cap.execution,
        "link": link,
        "needs_files": cap.needs_files,
        "reply": picked.get("reply") or "",
    }
