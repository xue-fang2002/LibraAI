"""Excel 拆分：按工作表或按某列取值，把一个大表拆成多个文件。

- mode=sheet：每个工作表存成独立文件
- mode=column：按指定列的每个不同取值分组，每组一个文件
"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_all_sheets, read_table

TOOL_ID = "document_excel_split"
CATEGORY = "document"
LABEL = "Excel 拆分"
NEEDS_FILES = True
PARAMS = [
    {"name": "mode", "label": "拆分方式", "type": "enum",
     "options": ["sheet", "column"], "default": "sheet"},
    {"name": "column", "label": "按哪一列拆分（按列值模式必填）", "type": "text"},
    {"name": "out_format", "label": "输出格式", "type": "enum",
     "options": ["xlsx", "csv"], "default": "xlsx"},
]


def _safe(name):
    return "".join(c for c in str(name) if c not in r'\/:*?"<>|').strip() or "sheet"


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    mode = (params.get("mode") or "sheet").lower()
    out_fmt = (params.get("out_format") or "xlsx").lower()
    if out_fmt not in ("xlsx", "csv"):
        out_fmt = "xlsx"
    ext = "." + out_fmt

    results = []

    def _save(df, base_name):
        name = _safe(base_name) + ext
        path = os.path.join(out_dir, name)
        if out_fmt == "csv":
            df.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            df.to_excel(path, index=False)
        results.append({"name": name, "size": os.path.getsize(path)})

    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        if mode == "column":
            col = (params.get("column") or "").strip()
            if not col:
                return fail("按列拆分需要指定「按哪一列拆分」", code=400)
            try:
                df = read_table(fpath)
            except Exception as e:
                results.append({"name": os.path.basename(fpath), "error": str(e)})
                continue
            if col not in df.columns:
                return fail(f"列「{col}」不存在，可用列：{', '.join(map(str, df.columns))}", code=400)
            for val, sub in df.groupby(col, dropna=False):
                _save(sub, f"{base}_{val}")
        else:
            try:
                sheets = read_all_sheets(fpath)
            except Exception as e:
                results.append({"name": os.path.basename(fpath), "error": str(e)})
                continue
            if len(sheets) == 1:
                # 只有一张表时按每 N 行拆？为避免歧义，直接提示
                results.append({"name": os.path.basename(fpath),
                                "error": "只有一个工作表，无法按工作表拆分（可改用按列拆分）"})
                continue
            for sname, sdf in sheets.items():
                _save(sdf, f"{base}_{sname}")

    if not results:
        return fail("未生成任何文件", code=400)
    return ok(data={"results": results})
