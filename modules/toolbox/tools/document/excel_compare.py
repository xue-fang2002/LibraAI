"""Excel 两份名单比对：找出「仅在主表 / 仅在参照表 / 两边都有」。

典型用途：未提交名单查找（主表=应到名单，参照表=已交名单，输出「仅在主表」即未交）。
文件顺序 = 上传顺序（第一个为主表/应到，第二个为参照表/已交）。
"""
import os

from core.response import ok, fail
from .._common import in_out, ordered_files
from ._excel_common import is_supported, read_table

TOOL_ID = "document_excel_compare"
CATEGORY = "document"
LABEL = "Excel 名单比对"
NEEDS_FILES = True
PARAMS = [
    {"name": "key", "label": "比对列（留空=第一列）", "type": "text"},
    {"name": "keep_cols", "label": "一并带出的列（逗号分隔，留空=全部）", "type": "text"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)

    tables = [f for f in ordered_files(in_dir) if is_supported(f)]
    if len(tables) < 2:
        return fail("请上传两个表格：第一个为主表（应到），第二个为参照表（已交）", code=400)

    try:
        import pandas as pd

        a = read_table(tables[0])
        b = read_table(tables[1])
    except Exception as e:
        return fail(f"读取表格失败：{e}", code=400)

    key = (params.get("key") or "").strip() or str(a.columns[0])
    if key not in a.columns:
        return fail(f"主表没有「{key}」列", code=400)
    if key not in b.columns:
        return fail(f"参照表没有「{key}」列（两张表要用同名的比对列）", code=400)

    keep = [c.strip() for c in str(params.get("keep_cols") or "").split(",") if c.strip()]
    cols_a = [c for c in (keep or list(a.columns)) if c in a.columns]
    cols_b = [c for c in (keep or list(b.columns)) if c in b.columns]

    sa = a[cols_a].copy()
    sb = b[cols_b].copy()
    sa["_k"] = a[key].astype(str).str.strip()
    sb["_k"] = b[key].astype(str).str.strip()

    set_a, set_b = set(sa["_k"]), set(sb["_k"])
    only_a = sa[sa["_k"].isin(set_a - set_b)].drop(columns=["_k"])
    only_b = sb[sb["_k"].isin(set_b - set_a)].drop(columns=["_k"])
    both = sa[sa["_k"].isin(set_a & set_b)].drop(columns=["_k"])

    out_name = "名单比对结果.xlsx"
    out_path = os.path.join(out_dir, out_name)
    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as w:
            only_a.to_excel(w, sheet_name="仅在主表", index=False)
            only_b.to_excel(w, sheet_name="仅在参照表", index=False)
            both.to_excel(w, sheet_name="两边都有", index=False)
    except Exception as e:
        return fail(f"写出结果失败：{e}", code=500)

    return ok(data={"results": [{"name": out_name, "size": os.path.getsize(out_path)}]})
