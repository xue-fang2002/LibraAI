"""Excel 两表匹配补全（VLOOKUP）：用参照表的列去补全主表。

文件顺序 = 上传顺序（第一个是主表，第二个是参照表），顺序由上传接口写入的
_upload_order.json 还原；若只上传了一个文件则报错而不是猜。
"""
import os

from core.response import ok, fail
from .._common import in_out, ordered_files
from ._excel_common import is_supported, read_table, write_table

TOOL_ID = "document_excel_match"
CATEGORY = "document"
LABEL = "Excel 两表匹配"
NEEDS_FILES = True
PARAMS = [
    {"name": "key", "label": "主表关联列", "type": "text"},
    {"name": "ref_key", "label": "参照表关联列（留空=同名）", "type": "text"},
    {"name": "cols", "label": "要带回的列（逗号分隔，留空=参照表全部）", "type": "text"},
    {"name": "how", "label": "匹配方式", "type": "select"},
]


def run(temp_id, params):
    params = params or {}
    in_dir, out_dir = in_out(temp_id)

    tables = [f for f in ordered_files(in_dir) if is_supported(f)]
    if len(tables) < 2:
        return fail("请上传两个表格：第一个为主表，第二个为参照表", code=400)

    main_path, ref_path = tables[0], tables[1]
    key = (params.get("key") or "").strip()
    if not key:
        return fail("请填写主表关联列（如 工号 / 姓名）", code=400)
    ref_key = (params.get("ref_key") or "").strip() or key

    try:
        import pandas as pd

        main = read_table(main_path)
        ref = read_table(ref_path)
    except Exception as e:
        return fail(f"读取表格失败：{e}", code=400)

    if key not in main.columns:
        return fail(f"主表没有「{key}」列，现有列：{', '.join(map(str, main.columns[:10]))}", code=400)
    if ref_key not in ref.columns:
        return fail(f"参照表没有「{ref_key}」列，现有列：{', '.join(map(str, ref.columns[:10]))}", code=400)

    want = [c.strip() for c in str(params.get("cols") or "").split(",") if c.strip()]
    if want:
        missing = [c for c in want if c not in ref.columns]
        if missing:
            return fail(f"参照表缺少列：{', '.join(missing)}", code=400)
    else:
        # 默认带回参照表里主表没有的列，避免同名列被 _x/_y 改名
        want = [c for c in ref.columns if c not in main.columns or c == ref_key]

    right = ref[[ref_key] + [c for c in want if c != ref_key]].copy()
    rename = {c: f"{c}_ref" for c in right.columns if c != ref_key and c in main.columns}
    if rename:
        right = right.rename(columns=rename)

    how = (params.get("how") or "left").strip()
    if how not in ("left", "inner"):
        how = "left"

    try:
        merged = main.merge(right, left_on=key, right_on=ref_key, how=how)
        if ref_key != key and ref_key in merged.columns:
            merged = merged.drop(columns=[ref_key])
        # 加一列让用户一眼看出哪些行没匹配上
        brought = [rename.get(c, c) for c in want if c != ref_key]
        probe = brought[0] if brought else None
        if probe and probe in merged.columns:
            merged["_matched"] = merged[probe].notna().map({True: "是", False: "否"})
    except Exception as e:
        return fail(f"匹配失败：{e}", code=400)

    base = os.path.splitext(os.path.basename(main_path))[0]
    out_name = f"{base}_matched.xlsx"
    out_path = os.path.join(out_dir, out_name)
    try:
        write_table(merged, out_path)
    except Exception as e:
        return fail(f"写出结果失败：{e}", code=500)
    return ok(data={"results": [{"name": out_name, "size": os.path.getsize(out_path)}]})
