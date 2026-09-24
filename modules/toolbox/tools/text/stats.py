"""字数统计：字符数 / 中文字符 / 英文单词 / 行数 / 段落数 / 去空格字符数。"""
from core.response import ok, fail

TOOL_ID = "text_stats"
CATEGORY = "text"
LABEL = "字数统计"
NEEDS_FILES = False
PARAMS = []


def run(temp_id, params):
    params = params or {}
    text = params.get("text") or ""
    if not text.strip():
        return fail("请输入要统计的文本", code=400)

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    words = sum(1 for w in text.replace("\n", " ").split(" ") if any(c.isalpha() for c in w))
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    paras = len([p for p in text.split("\n") if p.strip()])
    nospace = len([c for c in text if not c.isspace()])

    report = "\n".join([
        f"字符总数（含空格）: {len(text)}",
        f"字符总数（不含空格）: {nospace}",
        f"中文字符数: {cjk}",
        f"英文单词数: {words}",
        f"行数: {lines}",
        f"非空段落数: {paras}",
    ])
    return ok(data={"result": report})
