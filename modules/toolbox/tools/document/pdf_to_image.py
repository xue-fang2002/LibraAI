"""PDF 转图片（每页一张 PNG）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_to_image"
CATEGORY = "document"
LABEL = "PDF 转图片"
NEEDS_FILES = True
PARAMS = [
    {"name": "dpi", "label": "分辨率 DPI", "type": "number", "default": 150},
]


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install pymupdf", code=503)

    params = params or {}
    dpi = int(params.get("dpi", 150))
    in_dir, out_dir = in_out(temp_id)

    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        try:
            doc = fitz.open(fpath)
            base = os.path.splitext(os.path.basename(fpath))[0]
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=dpi)
                out_name = f"{base}_p{i+1}.png"
                pix.save(os.path.join(out_dir, out_name))
                results.append({"name": out_name,
                                "size": os.path.getsize(os.path.join(out_dir, out_name))})
            doc.close()
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可处理的 PDF 文件", code=400)
    return ok(data={"results": results})
