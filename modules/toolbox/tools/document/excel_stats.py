"""Excel 汇总统计：按某列分组，对数值列做 求和/计数/均值/最大/最小。"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_stats"
CATEGORY = "document"
LABEL = "Excel 汇总统计"
NEEDS_FILES = True
PARAMS = [
    {"name": "group_by", "label": "分组列", "type": "text"},
    {"name": "agg", "label": "统计方式", "type": "enum",
     "options": ["sum", "count", "mean", "max", "min"], "default": "sum"},
]


def run(temp_id, params):
    import pandas as pd
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    group_col = (params.get("group_by") or "").strip()
    if not group_col:
        return fail("请填写分组列", code=400)
    agg = params.get("agg") or "sum"

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"stats_{agg}_{base}.xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            if group_col not in df.columns:
                results.append({"name": os.path.basename(fpath),
                                "error": f"列「{group_col}」不存在"})
                continue
            num_cols = df.select_dtypes(include="number").columns.tolist()
            grouped = df.groupby(group_col, dropna=False)[num_cols].agg(agg).reset_index() if num_cols else \
                       df.groupby(group_col, dropna=False).size().reset_index(name="count")
            grouped.to_excel(out_path, index=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
