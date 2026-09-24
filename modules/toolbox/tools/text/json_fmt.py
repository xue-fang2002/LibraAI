"""JSON 格式化 / 压缩。"""
import json

from core.response import ok, fail

TOOL_ID = "text_json_fmt"
CATEGORY = "text"
LABEL = "JSON 格式化"
NEEDS_FILES = False
PARAMS = [
    {"name": "text", "label": "JSON 原文", "type": "textarea", "required": True},
    {"name": "mode", "label": "模式", "type": "enum",
     "options": ["format", "compress"], "default": "format"},
]


def run(temp_id, params):
    text = (params or {}).get("text", "")
    mode = (params or {}).get("mode", "format")
    try:
        obj = json.loads(text)
        if mode == "format":
            out = json.dumps(obj, ensure_ascii=False, indent=2)
        else:
            out = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return ok(data={"result": out, "lines": out.count("\n") + 1})
    except Exception as e:
        return fail(f"JSON 解析错误: {str(e)}", code=400)
