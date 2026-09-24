"""PDF OCR：先用 PyMuPDF 逐页转图，再逐页 OCR（需要 Tesseract 引擎）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files, ocr_with_tesseract

TOOL_ID = "recognition_ocr_pdf"
CATEGORY = "recognition"
LABEL = "PDF OCR"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF: pip install pymupdf", code=503)

    in_dir, out_dir = in_out(temp_id)
    lines = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        lines.append(f"=== {os.path.basename(fpath)} ===")
        try:
            doc = fitz.open(fpath)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=200)
                tmp = os.path.join(out_dir, f"_ocr_p{i+1}.png")
                pix.save(tmp)
                try:
                    lines.append(f"--- 第 {i+1} 页 ---")
                    lines.append(ocr_with_tesseract(tmp) or "（未识别到文字）")
                except RuntimeError as e:
                    doc.close()
                    return fail(str(e), code=503)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            doc.close()
        except Exception as e:
            lines.append(f"处理失败: {e}")
    if not lines:
        return fail("未找到可处理的 PDF", code=400)
    return ok(data={"result": "\n".join(lines)})
