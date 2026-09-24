"""Excel 列处理：提取指定列 / 删除指定列。"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_columns"
CATEGORY = "document"
LABEL = "Excel 提取/删除列"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "enum",
     "options": ["keep", "drop"], "default": "keep"},
    {"name": "columns", "label": "列名（多个用逗号分隔）", "type": "text"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    action = params.get("action") or "keep"
    cols_raw = (params.get("columns") or "").strip()
    cols = [c.strip() for c in cols_raw.replace("，", ",").split(",") if c.strip()]
    if not cols:
        return fail("请填写要处理的列名", code=400)

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = ("keep_" if action == "keep" else "drop_") + base + ".xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            missing = [c for c in cols if c not in df.columns]
            if missing:
                results.append({"name": os.path.basename(fpath),
                                "error": "列不存在：" + ", ".join(missing)})
                continue
            if action == "keep":
                df = df[cols]
            else:
                df = df[[c for c in df.columns if c not in cols]]
            df.to_excel(out_path, index=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
