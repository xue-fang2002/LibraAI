"""PDF 表格 → Excel：逐页提取表格写入 xlsx（每页一个 sheet）。

只处理「有表格线或规整排版」的表格；扫描件/图片型 PDF 提取不到表格，
须先走 OCR —— 这类情况在结果里显式提示，而不是静默返回空文件。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_to_excel"
CATEGORY = "document"
LABEL = "PDF 转 Excel"
NEEDS_FILES = True
PARAMS = [
    {"name": "merge", "label": "所有页合并到一个 sheet", "type": "checkbox"},
]


def run(temp_id, params):
    try:
        import pdfplumber
    except ImportError:
        return fail("服务器未安装 pdfplumber：pip install pdfplumber", code=503)

    params = params or {}
    merge = bool(params.get("merge"))

    in_dir, out_dir = in_out(temp_id)
    files = [f for f in input_files(in_dir) if f.lower().endswith(".pdf")]
    if not files:
        return fail("未找到 PDF 文件", code=400)

    import pandas as pd

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}.xlsx"
        out_path = os.path.join(out_dir, out_name)
        try:
            frames = []          # [(sheet_name, DataFrame)]
            merged_rows = []
            with pdfplumber.open(fpath) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    tables = page.extract_tables() or []
                    rows = []
                    for t in tables:
                        for r in t:
                            rows.append([("" if c is None else str(c)) for c in r])
                        rows.append([])
                    if not rows:
                        continue
                    if merge:
                        merged_rows.extend(rows)
                    else:
                        frames.append((f"p{i}", pd.DataFrame(rows)))

            if merge:
                frames = [("Sheet1", pd.DataFrame(merged_rows))]

            if not frames:
                results.append({
                    "name": out_name,
                    "error": "未从该 PDF 提取到任何表格（扫描件/图片型 PDF 需先 OCR）"
                })
                continue

            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                for sheet, df in frames:
                    df.to_excel(writer, sheet_name=sheet[:31], index=False, header=False)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})

    return ok(data={"results": results})
