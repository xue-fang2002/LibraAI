"""
办公自动化 AI 助手：自然语言 -> JSON 计划 -> 预览确认后执行。

安全边界（比工具箱更严格）：
- 只能从前端传入的**能力注册表**里选，参数按声明类型/枚举规范化，越界回落默认。
- 办公自动化的输入文件（zip/Excel）**只能由用户在页面上上传**，AI 不允许编造路径或文件名；
  计划里若出现文件类参数一律丢弃，缺失时前端会在执行前阻断并提示。
- 不可逆操作（如删除分表）仍走原有确认/日志链路，不改变其行为。
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


def build_plan_prompt(text, caps):
    """构造计划 prompt。caps 为前端传来的能力摘要。"""
    caps_json = json.dumps(caps, ensure_ascii=False)
    return (
        "你是办公自动化工具的 AI 助手。根据用户的中文需求，从下面的能力清单里选出最合适的一个，"
        "并给出它的参数值。\n"
        "只输出 JSON 本身，不要任何解释文字、不要 markdown 围栏。\n\n"
        "能力清单（id / 名称 / 说明 / 参数定义）：\n" + caps_json + "\n\n"
        "输出格式：\n"
        "{\n"
        '  "tool": "能力id",\n'
        '  "params": {"参数名": "值"},\n'
        '  "reply": "一句话向用户说明你的方案"\n'
        "}\n\n"
        "规则：\n"
        "1. tool 必须是能力清单里的 id，不要编造。\n"
        "2. params 只给该能力定义里存在的参数名；enum 类型只能给 options 里的值；"
        "bool 类型给 true/false。\n"
        "3. 输入文件（压缩包/Excel）由用户自己在页面上传，你**不要**输出任何文件路径或文件名，"
        "也不要输出 files 字段。\n"
        "4. 需求与任何能力都不匹配时，输出 {\"tool\": null, \"reply\": \"说明为什么匹配不上\"}。\n\n"
        f"用户需求：{text}\n\n"
        "请只输出 JSON："
    )


def validate_plan(plan, caps_by_id):
    """校验并规范化计划。返回 (clean_plan, error)。"""
    if not isinstance(plan, dict):
        return None, "计划不是合法的 JSON 对象"

    if not plan.get("tool"):
        return None, None  # AI 明确说匹配不上，reply 交给前端展示

    cap_id = str(plan.get("tool"))
    cap = caps_by_id.get(cap_id)
    if cap is None:
        return None, f"AI 选择了未知能力：{cap_id}"

    params_in = plan.get("params") if isinstance(plan.get("params"), dict) else {}
    clean_params = {}
    for p in cap.get("params") or []:
        name = p.get("name")
        if not name or name not in params_in:
            continue
        val = params_in[name]
        ptype = p.get("type") or "text"
        try:
            if ptype == "bool":
                if isinstance(val, str):
                    val = val.strip().lower() in ("1", "true", "yes", "是", "on")
                else:
                    val = bool(val)
            elif ptype == "enum":
                opts = p.get("options") or []
                if opts and str(val) not in [str(o) for o in opts]:
                    val = opts[0]
                val = str(val)
            else:
                val = str(val)
        except (TypeError, ValueError):
            continue
        clean_params[name] = val

    return {
        "tool": cap_id,
        "label": cap.get("label") or cap_id,
        "params": clean_params,
        "reply": str(plan.get("reply") or ""),
    }, None
