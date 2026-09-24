"""文本对比（unified diff）。"""
import difflib

from core.response import ok

TOOL_ID = "text_diff"
CATEGORY = "text"
LABEL = "文本对比"
NEEDS_FILES = False
PARAMS = [
    {"name": "text_a", "label": "原文", "type": "textarea", "required": True},
    {"name": "text_b", "label": "对比文本", "type": "textarea", "required": True},
]


def run(temp_id, params):
    params = params or {}
    text_a = params.get("text_a", "").splitlines()
    text_b = params.get("text_b", "").splitlines()
    diff = list(difflib.unified_diff(text_a, text_b, lineterm=""))
    return ok(data={"diff": "\n".join(diff), "lines": len(diff)})
