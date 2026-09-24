"""PDF 合并：可按 params.order 指定顺序，否则按文件名排序合并全部 PDF。"""
import os

from core.response import ok, fail
from .._common import in_out

TOOL_ID = "document_pdf_merge"
CATEGORY = "document"
LABEL = "PDF 合并"
NEEDS_FILES = True
PARAMS = [
    {"name": "order", "label": "合并顺序（文件名数组，可留空）", "type": "text"},
]


def run(temp_id, params):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install PyMuPDF", code=503)

    params = params or {}
    in_dir, out_dir = in_out(temp_id)

    order = params.get("order") or []
    if isinstance(order, str):
        order = [s.strip() for s in order.split(",") if s.strip()]

    files = []
    if order:
        for name in order:
            fpath = os.path.join(in_dir, os.path.basename(str(name)))
            if os.path.isfile(fpath) and fpath.lower().endswith(".pdf"):
                files.append(fpath)
    if not files:
        files = sorted(
            os.path.join(in_dir, f) for f in os.listdir(in_dir)
            if os.path.isfile(os.path.join(in_dir, f)) and f.lower().endswith(".pdf")
        )

    # 空输入防护：旧实现会在 fitz 里抛 "cannot save with zero pages"
    if not files:
        return fail("未找到可合并的 PDF 文件", code=400)

    merged = fitz.open()
    try:
        for fpath in files:
            with fitz.open(fpath) as doc:
                merged.insert_pdf(doc)
        out_path = os.path.join(out_dir, "merged.pdf")
        merged.save(out_path)
    finally:
        merged.close()

    return ok(data={"results": [{"name": "merged.pdf", "size": os.path.getsize(out_path)}]})
