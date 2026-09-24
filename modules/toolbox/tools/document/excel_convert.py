"""Excel 格式转换：xlsx / csv / tsv 互转。"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_convert"
CATEGORY = "document"
LABEL = "Excel 格式转换"
NEEDS_FILES = True
PARAMS = [
    {"name": "out_format", "label": "目标格式", "type": "enum",
     "options": ["xlsx", "csv", "tsv"], "default": "xlsx"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    out_fmt = (params.get("out_format") or "xlsx").lower()
    if out_fmt not in ("xlsx", "csv", "tsv"):
        return fail("不支持的目标格式", code=400)

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = base + "." + out_fmt
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            if out_fmt == "csv":
                df.to_csv(out_path, index=False, encoding="utf-8-sig")
            elif out_fmt == "tsv":
                df.to_csv(out_path, index=False, sep="\t", encoding="utf-8-sig")
            else:
                df.to_excel(out_path, index=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
