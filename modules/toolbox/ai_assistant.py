"""
AI 工具助手（Phase 3）：自然语言 -> JSON 计划 -> 计划预览 -> 用户确认后执行。

安全边界：
- 只能从**前端传入的工具清单**里选工具，参数按该工具声明的类型/选项规范化，
  越界值一律回落默认；后端不执行任何计划外动作，执行永远由前端走既有处理链路。
"""
import json
import re


def _extract_json(raw):
    """容错抽取 LLM 输出里的 JSON（剥 ``` 围栏 / 取首个 {...}）。"""
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    text = str(raw)
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_plan(raw):
    return _extract_json(raw)


def build_plan_prompt(text, tools, workspace_files):
    """构造计划生成 prompt。tools 为前端传来的工具摘要，workspace_files 为当前工作区文件名。"""
    import json as _json
    tools_json = _json.dumps(tools, ensure_ascii=False)
    ws_json = _json.dumps(workspace_files, ensure_ascii=False)
    return (
        "你是工具箱的 AI 助手。根据用户的中文需求，从下面的工具清单里选出最合适的一个工具，"
        "并给出它的参数值。\n"
        "只输出 JSON 本身，不要任何解释文字、不要 markdown 围栏。\n\n"
        "工具清单（id / 名称 / 参数定义）：\n" + tools_json + "\n\n"
        "当前用户工作区里已有的文件（可作为 files 引用，必须用这里的原名）：\n" + ws_json + "\n\n"
        "输出格式：\n"
        "{\n"
        '  "tool": "工具id",\n'
        '  "params": {"参数名": "值"},\n'
        '  "files": ["工作区文件名", "..."],\n'
        '  "reply": "一句话向用户说明你的方案"\n'
        "}\n\n"
        "规则：\n"
        "1. tool 必须是工具清单里的 id，不要编造。\n"
        "2. params 只给该工具定义里存在的参数名；数值参数给数字，开关参数给 true/false，"
        "下拉参数只能给选项里的 value。\n"
        "3. files 只能引用工作区文件清单里的原名；需求没提到具体文件就给空数组 []。\n"
        "4. 需求与任何工具都不匹配时，输出 {\"tool\": null, \"reply\": \"说明为什么匹配不上\"}。\n\n"
        f"用户需求：{text}\n\n"
        "请只输出 JSON："
    )


def validate_plan(plan, tools_by_id):
    """校验并规范化计划。返回 (clean_plan, error)。"""
    if not isinstance(plan, dict):
        return None, "计划不是合法的 JSON 对象"

    if not plan.get("tool"):
        return None, None  # AI 明确说匹配不上，reply 交给前端展示

    tool_id = str(plan.get("tool"))
    tool = tools_by_id.get(tool_id)
    if tool is None:
        return None, f"AI 选择了未知工具：{tool_id}"

    params_in = plan.get("params") if isinstance(plan.get("params"), dict) else {}
    clean_params = {}
    for p in tool.get("params") or []:
        name = p.get("name")
        if not name or name not in params_in:
            continue
        val = params_in[name]
        ptype = p.get("type") or "text"
        try:
            if ptype == "number":
                val = float(val)
                # 兜底：越界值回落到参数声明的 min/max，避免 AI 给出「最大宽度 0」这类危险值
                lo, hi = p.get("min"), p.get("max")
                if isinstance(lo, (int, float)) and val < lo:
                    val = float(lo)
                if isinstance(hi, (int, float)) and val > hi:
                    val = float(hi)
            elif ptype == "checkbox":
                if isinstance(val, str):
                    val = val.strip().lower() in ("1", "true", "yes", "是", "on")
                else:
                    val = bool(val)
            elif ptype == "select":
                opts = [o.get("value") for o in (p.get("options") or []) if isinstance(o, dict)]
                if opts and val not in opts:
                    val = opts[0]
            else:
                val = str(val)
        except (TypeError, ValueError):
            val = p.get("value")
        clean_params[name] = val

    files = []
    for f in (plan.get("files") or []):
        f = str(f).strip()
        if f:
            files.append(f)

    return {
        "tool": tool_id,
        "label": tool.get("label") or tool_id,
        "category": tool.get("category"),
        "params": clean_params,
        "files": files,
        "reply": str(plan.get("reply") or ""),
    }, None
