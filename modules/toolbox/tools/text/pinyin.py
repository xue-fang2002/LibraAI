"""汉字转拼音：可带声调 / 不带声调 / 仅首字母。"""
from core.response import ok, fail

TOOL_ID = "text_pinyin"
CATEGORY = "text"
LABEL = "汉字转拼音"
NEEDS_FILES = False
PARAMS = [
    {"name": "style", "label": "拼音样式", "type": "select"},
    {"name": "sep", "label": "分隔符", "type": "text"},
]


def run(temp_id, params):
    try:
        from pypinyin import pinyin as to_pinyin, Style
    except ImportError:
        return fail("服务器未安装 pypinyin：pip install pypinyin", code=503)

    params = params or {}
    text = params.get("text") or ""
    if not text.strip():
        return fail("请输入要转换的文本", code=400)

    style = (params.get("style") or "tone").strip()
    style_map = {
        "tone": Style.TONE,
        "normal": Style.NORMAL,
        "first": Style.FIRST_LETTER,
    }
    sep = params.get("sep")
    sep = " " if sep is None else str(sep)

    try:
        seq = to_pinyin(text, style=style_map.get(style, Style.TONE), heteronym=False)
        out = sep.join(item[0] for item in seq if item and item[0])
    except Exception as e:
        return fail(f"转换失败：{e}", code=400)
    return ok(data={"result": out})
