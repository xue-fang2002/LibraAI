"""PDF 批量旋转。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_rotate"
CATEGORY = "document"
LABEL = "PDF 旋转"
NEEDS_FILES = True
PARAMS = [
    {"name": "angle", "label": "角度", "type": "enum",
     "options": ["90", "180", "270"], "default": "90"},
]


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install pymupdf", code=503)

    params = params or {}
    angle = int(params.get("angle", 90))
    in_dir, out_dir = in_out(temp_id)

    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        try:
            doc = fitz.open(fpath)
            for page in doc:
                page.set_rotation(angle)
            out_name = "rot_" + os.path.basename(fpath)
            out_path = os.path.join(out_dir, out_name)
            doc.save(out_path)
            doc.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可处理的 PDF 文件", code=400)
    return ok(data={"results": results})
