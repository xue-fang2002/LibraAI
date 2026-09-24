"""简体 / 繁体互转（zhconv，纯 Python 无二进制依赖）。"""
from core.response import ok, fail

TOOL_ID = "text_zhconv"
CATEGORY = "text"
LABEL = "简繁转换"
NEEDS_FILES = False
PARAMS = [
    {"name": "target", "label": "目标", "type": "select"},
]

_HAS_TEXTAREA = True


def run(temp_id, params):
    try:
        import zhconv
    except ImportError:
        return fail("服务器未安装 zhconv：pip install zhconv", code=503)

    params = params or {}
    text = params.get("text") or ""
    if not text.strip():
        return fail("请输入要转换的文本", code=400)

    target = (params.get("target") or "zh-cn").strip()
    if target not in ("zh-cn", "zh-tw", "zh-hk"):
        target = "zh-cn"
    return ok(data={"result": zhconv.convert(text, target)})
