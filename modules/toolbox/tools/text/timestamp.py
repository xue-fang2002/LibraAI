"""时间戳与日期互转（自动识别秒 / 毫秒 / 微秒）。"""
from datetime import datetime

from core.response import ok, fail

TOOL_ID = "text_timestamp"
CATEGORY = "text"
LABEL = "时间戳转换"
NEEDS_FILES = False
PARAMS = [
    {"name": "action", "label": "方向", "type": "enum",
     "options": ["ts_to_date", "date_to_ts"], "default": "ts_to_date"},
    {"name": "value", "label": "值", "type": "text", "required": True},
]


def run(temp_id, params):
    params = params or {}
    action = params.get("action", "ts_to_date")
    value = params.get("value", "")
    try:
        if action == "ts_to_date":
            ts = float(value)
            if ts > 1e16:
                ts = ts / 1e6
            elif ts > 1e13:
                ts = ts / 1e3
            dt = datetime.fromtimestamp(ts)
            result = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            result = str(int(dt.timestamp()))
        return ok(data={"result": result})
    except Exception as e:
        return fail(f"转换失败: {str(e)}", code=400)
