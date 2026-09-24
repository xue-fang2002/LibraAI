"""PDF 提取文字（扫描件无文字层时会提示）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_extract_text"
CATEGORY = "document"
LABEL = "PDF 提取文字"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install pymupdf", code=503)

    in_dir, _ = in_out(temp_id)
    lines = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        try:
            doc = fitz.open(fpath)
            lines.append(f"=== {os.path.basename(fpath)} ===")
            for i, page in enumerate(doc):
                txt = page.get_text().strip()
                lines.append(f"--- 第 {i+1} 页 ---")
                lines.append(txt or "（无文字内容，可能是扫描件）")
            doc.close()
        except Exception as e:
            lines.append(f"{os.path.basename(fpath)} 提取失败: {e}")
    return ok(data={"result": "\n".join(lines) or "未提取到文字"})
