"""Excel 拆列 / 合列：按分隔符把一列拆成多列，或把多列拼成一列。"""
import os

from core.response import ok, fail
from .._common import in_out, ordered_files
from ._excel_common import is_supported, read_table, write_table

TOOL_ID = "document_excel_splitcol"
CATEGORY = "document"
LABEL = "Excel 拆列/合列"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "column", "label": "要拆的列（仅拆列）", "type": "text"},
    {"name": "columns", "label": "要合并的列，逗号分隔（仅合列）", "type": "text"},
    {"name": "sep", "label": "分隔符", "type": "text"},
    {"name": "new_name", "label": "合并后的列名", "type": "text"},
    {"name": "keep_original", "label": "保留原列", "type": "checkbox"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    tables = [f for f in ordered_files(in_dir) if is_supported(f)]
    if not tables:
        return fail("未找到表格文件（xlsx / csv / tsv）", code=400)

    action = (params.get("action") or "split").strip()
    if action not in ("split", "merge"):
        return fail(f"不支持的操作: {action}", code=400)
    sep = params.get("sep") or ","
    keep = bool(params.get("keep_original"))

    results = []
    for fpath in tables:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_{action}col.xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)

            if action == "split":
                col = (params.get("column") or "").strip()
                if not col:
                    return fail("请填写要拆分的列名", code=400)
                if col not in df.columns:
                    return fail(f"表格没有「{col}」列", code=400)
                parts = df[col].astype(str).str.split(sep, expand=True)
                width = max(1, parts.shape[1])
                parts.columns = [f"{col}_{i+1}" for i in range(width)]
                df = df.drop(columns=[col]) if not keep else df
                df = df.join(parts) if keep else df.join(parts)
            else:
                cols = [c.strip() for c in str(params.get("columns") or "").split(",") if c.strip()]
                if len(cols) < 2:
                    return fail("合列请至少填写两个列名（逗号分隔）", code=400)
                missing = [c for c in cols if c not in df.columns]
                if missing:
                    return fail(f"表格缺少列：{', '.join(missing)}", code=400)
                new_name = (params.get("new_name") or "").strip() or ("_".join(cols))
                merged = df[cols].astype(str).agg(sep.join, axis=1)
                if not keep:
                    df = df.drop(columns=cols)
                df[new_name] = merged

            write_table(df, out_path)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})
    return ok(data={"results": results})
