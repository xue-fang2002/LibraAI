"""Excel 数据清洗：去重、去首尾空格、空值填充或删除。"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_clean"
CATEGORY = "document"
LABEL = "Excel 数据清洗"
NEEDS_FILES = True
PARAMS = [
    {"name": "dedup", "label": "删除重复行", "type": "bool", "default": True},
    {"name": "trim", "label": "去除文本首尾空格", "type": "bool", "default": True},
    {"name": "na_action", "label": "空值处理", "type": "enum",
     "options": ["none", "fill", "drop"], "default": "none"},
    {"name": "na_fill", "label": "空值填充为（选择「填充」时生效）", "type": "text", "default": "0"},
]


def run(temp_id, params):
    import pandas as pd
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    dedup = bool(params.get("dedup", True))
    trim = bool(params.get("trim", True))
    na_action = params.get("na_action") or "none"
    na_fill = params.get("na_fill") or "0"

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = "cleaned_" + base + ".xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            before = len(df)
            if trim:
                df = df.apply(lambda s: s.str.strip() if s.dtype == "object" else s)
            if dedup:
                df = df.drop_duplicates()
            if na_action == "drop":
                df = df.dropna()
            elif na_action == "fill":
                df = df.fillna(na_fill)
            df.to_excel(out_path, index=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path),
                            "rows_before": before, "rows_after": int(len(df))})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
