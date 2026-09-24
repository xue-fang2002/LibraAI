"""正则表达式测试：列出所有匹配及分组。"""
import re

from core.response import ok, fail

TOOL_ID = "text_regex"
CATEGORY = "text"
LABEL = "正则表达式测试"
NEEDS_FILES = False
PARAMS = [
    {"name": "text", "label": "待匹配文本", "type": "textarea", "required": True},
    {"name": "pattern", "label": "正则表达式", "type": "text", "required": True},
    {"name": "ignore_case", "label": "忽略大小写", "type": "bool", "default": False},
    {"name": "multiline", "label": "多行模式", "type": "bool", "default": False},
]


def run(temp_id, params):
    params = params or {}
    text = params.get("text", "")
    pattern = params.get("pattern", "")
    flags = 0
    if params.get("ignore_case"):
        flags |= re.IGNORECASE
    if params.get("multiline"):
        flags |= re.MULTILINE
    try:
        matches = []
        for m in re.finditer(pattern, text, flags):
            matches.append({
                "match": m.group(),
                "groups": m.groups(),
                "start": m.start(),
                "end": m.end(),
            })
        return ok(data={"matches": matches, "count": len(matches)})
    except Exception as e:
        return fail(f"正则错误: {str(e)}", code=400)
