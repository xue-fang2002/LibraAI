"""UUID 批量生成。"""
import uuid as uuid_mod

from core.response import ok

TOOL_ID = "text_uuid"
CATEGORY = "text"
LABEL = "UUID 生成"
NEEDS_FILES = False
PARAMS = [
    {"name": "count", "label": "生成数量", "type": "number", "default": 5},
]


def run(temp_id, params):
    count = int((params or {}).get("count", 5))
    uuids = [str(uuid_mod.uuid4()) for _ in range(count)]
    return ok(data={"uuids": uuids})
