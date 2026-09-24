"""随机密码生成。"""
import random
import string

from core.response import ok, fail

TOOL_ID = "text_password"
CATEGORY = "text"
LABEL = "密码生成"
NEEDS_FILES = False
PARAMS = [
    {"name": "length", "label": "长度", "type": "number", "default": 16},
    {"name": "count", "label": "生成个数", "type": "number", "default": 5},
    {"name": "lower", "label": "含小写", "type": "bool", "default": True},
    {"name": "upper", "label": "含大写", "type": "bool", "default": True},
    {"name": "digit", "label": "含数字", "type": "bool", "default": True},
    {"name": "symbol", "label": "含符号", "type": "bool", "default": False},
]


def run(temp_id, params):
    params = params or {}
    length = int(params.get("length", 16))
    use_upper = params.get("upper", True)
    use_lower = params.get("lower", True)
    use_digit = params.get("digit", True)
    use_symbol = params.get("symbol", False)
    count = int(params.get("count", 5))

    chars = ""
    if use_lower:
        chars += string.ascii_lowercase
    if use_upper:
        chars += string.ascii_uppercase
    if use_digit:
        chars += string.digits
    if use_symbol:
        chars += "!@#$%^&*()_+-=[]{}|;:,.<>?"

    if not chars:
        return fail("至少选择一种字符类型", code=400)

    passwords = []
    for _ in range(count):
        passwords.append("".join(random.choice(chars) for _ in range(length)))
    return ok(data={"passwords": passwords})
