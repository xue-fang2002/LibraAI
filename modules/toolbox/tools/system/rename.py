"""按规则批量生成重命名后的文件名：正则替换 / 前后缀 / 序号。

注：本工具只**生成**新文件名清单（纯规则、不落盘），真正改名由前端/调用方执行。
"""
import os
import re

from core.response import ok, fail

TOOL_ID = "system_rename"
CATEGORY = "system"
LABEL = "批量重命名"
NEEDS_FILES = False
PARAMS = [
    {"name": "text", "label": "文件名列表（每行一个）", "type": "textarea", "required": True},
    {"name": "action", "label": "方式", "type": "enum",
     "options": ["replace", "prefix", "suffix", "numbering"], "default": "replace"},
    {"name": "pattern", "label": "查找/正则", "type": "text"},
    {"name": "replacement", "label": "替换为", "type": "text"},
    {"name": "prefix", "label": "前缀", "type": "text"},
    {"name": "suffix", "label": "后缀", "type": "text"},
    {"name": "numbering", "label": "加序号", "type": "bool", "default": False},
]


def run(temp_id, params):
    params = params or {}
    raw = params.get("text") or ""
    names = [n.strip() for n in raw.splitlines() if n.strip()]
    if not names:
        return fail("请先输入文件名列表（每行一个）", code=400)

    pattern = params.get("pattern") or ""
    replacement = params.get("replacement") or ""
    prefix = params.get("prefix") or ""
    suffix = params.get("suffix") or ""
    numbering = bool(params.get("numbering"))

    lines = []
    for i, name in enumerate(names, 1):
        stem, ext = os.path.splitext(name)
        new_stem = stem
        if pattern:
            try:
                new_stem = re.sub(pattern, replacement, new_stem)
            except re.error as e:
                return fail(f"正则表达式无效：{e}", code=400)
        new_stem = f"{prefix}{new_stem}{suffix}"
        if numbering:
            new_stem = f"{new_stem}{i}"
        lines.append(f"{name}  →  {new_stem}{ext}")
    return ok(data={"result": "\n".join(lines), "count": len(lines)})
