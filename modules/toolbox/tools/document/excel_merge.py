"""Excel 合并：把多个结构相同的表格纵向拼接成一个文件。

默认读每个文件的第一个 sheet，按列名对齐后上下拼接（列缺失自动补空）。
"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_merge"
CATEGORY = "document"
LABEL = "Excel 合并"
NEEDS_FILES = True
PARAMS = [
    {"name": "sheet", "label": "工作表名（留空=第一个表）", "type": "text"},
    {"name": "out_format", "label": "输出格式", "type": "enum",
     "options": ["xlsx", "csv"], "default": "xlsx"},
]


def run(temp_id, params):
    import pandas as pd
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到可合并的表格文件（支持 xlsx/csv/tsv）", code=400)

    sheet = (params.get("sheet") or "").strip() or None
    out_fmt = (params.get("out_format") or "xlsx").lower()
    if out_fmt not in ("xlsx", "csv"):
        out_fmt = "xlsx"

    frames, errors = [], []
    for fpath in files:
        name = os.path.basename(fpath)
        try:
            frames.append(read_table(fpath, sheet))
        except Exception as e:
            errors.append({"name": name, "error": str(e)})
    if not frames:
        return fail("读取表格失败：" + "; ".join(e["error"] for e in errors), code=500)

    merged = pd.concat(frames, ignore_index=True, sort=False)
    out_name = "merged." + out_fmt
    out_path = os.path.join(out_dir, out_name)
    if out_fmt == "csv":
        merged.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        merged.to_excel(out_path, index=False)
    return ok(data={"results": [{"name": out_name, "size": os.path.getsize(out_path),
                                  "rows": int(len(merged))}],
                    "warnings": errors})
