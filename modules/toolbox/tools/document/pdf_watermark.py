"""PDF 批量加文字水印（居中，中文自动用内置简体中文字体）。

注：PyMuPDF 的 rotate 只接受 0/90/180/270，斜向水印需用 morph 实现，当前为水平居中。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_watermark"
CATEGORY = "document"
LABEL = "PDF 水印"
NEEDS_FILES = True
PARAMS = [
    {"name": "text", "label": "水印文字", "type": "text", "required": True},
]


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install pymupdf", code=503)

    params = params or {}
    text = (params.get("text") or "").strip()
    if not text:
        return fail("请输入水印文字", code=400)

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        try:
            doc = fitz.open(fpath)
            for page in doc:
                rect = page.rect
                font = "china-s" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "helv"
                page.insert_text(
                    (rect.width / 2 - len(text) * 12, rect.height / 2),
                    text, fontsize=36, fontname=font,
                    color=(0.65, 0.65, 0.65),
                )
            out_name = "wm_" + os.path.basename(fpath)
            out_path = os.path.join(out_dir, out_name)
            doc.save(out_path)
            doc.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可处理的 PDF 文件", code=400)
    return ok(data={"results": results})
