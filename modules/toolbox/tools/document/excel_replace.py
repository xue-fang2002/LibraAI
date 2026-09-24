"""Excel 批量替换：对单元格文本做查找替换（支持整表或指定列）。"""
import os

from core.response import ok, fail
from .._common import in_out
from ._excel_common import list_excels, read_table

TOOL_ID = "document_excel_replace"
CATEGORY = "document"
LABEL = "Excel 批量替换"
NEEDS_FILES = True
PARAMS = [
    {"name": "find_text", "label": "查找文字", "type": "text"},
    {"name": "replace_text", "label": "替换为", "type": "text"},
    {"name": "columns", "label": "仅这些列（留空=整表）", "type": "text"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_excels(in_dir)
    if not files:
        return fail("未找到表格文件", code=400)

    old = (params.get("find_text") or "")
    new = params.get("replace_text") or ""
    if old == "":
        return fail("请填写要查找的文字", code=400)

    cols_raw = (params.get("columns") or "").strip()
    cols = [c.strip() for c in cols_raw.replace("，", ",").split(",") if c.strip()]

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = "replaced_" + base + ".xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            df = read_table(fpath)
            targets = cols if cols else list(df.columns)
            missing = [c for c in targets if c not in df.columns]
            if missing:
                results.append({"name": os.path.basename(fpath),
                                "error": "列不存在：" + ", ".join(missing)})
                continue
            count = 0
            for c in targets:
                if df[c].dtype == "object":
                    cnt = df[c].astype(str).str.count(old.replace("\\", "\\\\")).sum()
                    count += int(cnt) if cnt == cnt else 0
                df[c] = df[c].astype(str).str.replace(old, new)
            df.to_excel(out_path, index=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path),
                            "replaced": int(count)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
