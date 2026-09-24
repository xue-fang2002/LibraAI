"""提取 PDF 内嵌图片。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_extract_image"
CATEGORY = "document"
LABEL = "PDF 提取图片"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install pymupdf", code=503)

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        try:
            doc = fitz.open(fpath)
            base = os.path.splitext(os.path.basename(fpath))[0]
            idx = 0
            for page in doc:
                for info in page.get_images(full=True):
                    xref = info[0]
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    idx += 1
                    out_name = f"{base}_img{idx}.png"
                    pix.save(os.path.join(out_dir, out_name))
                    results.append({"name": out_name,
                                    "size": os.path.getsize(os.path.join(out_dir, out_name))})
            doc.close()
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未在 PDF 中找到内嵌图片", code=400)
    return ok(data={"results": results})
