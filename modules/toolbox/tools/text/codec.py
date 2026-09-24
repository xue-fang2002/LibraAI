"""文本编解码：Base64 / URL / HTML。"""
import base64
import html
import urllib.parse

from core.response import ok, fail

TOOL_ID = "text_codec"
CATEGORY = "text"
LABEL = "文本编解码"
NEEDS_FILES = False
PARAMS = [
    {"name": "text", "label": "待处理文本", "type": "textarea", "required": True},
    {"name": "action", "label": "操作", "type": "enum",
     "options": ["base64_encode", "base64_decode", "url_encode",
                 "url_decode", "html_encode", "html_decode"],
     "default": "base64_encode"},
]


def run(temp_id, params):
    params = params or {}
    text = params.get("text", "")
    action = params.get("action", "base64_encode")
    try:
        if action == "base64_encode":
            result = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        elif action == "base64_decode":
            result = base64.b64decode(text.encode("utf-8")).decode("utf-8")
        elif action == "url_encode":
            result = urllib.parse.quote(text, safe="")
        elif action == "url_decode":
            result = urllib.parse.unquote(text)
        elif action == "html_encode":
            result = html.escape(text)
        elif action == "html_decode":
            result = html.unescape(text)
        else:
            return fail("未知的编解码操作", code=400)
        return ok(data={"result": result})
    except Exception as e:
        return fail(f"编解码失败: {str(e)}", code=400)
