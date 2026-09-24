"""PDF 转纯图 PDF：每页渲染成位图后重新合成，原文本层丢弃。

用途：定稿归档（防改动 / 防复制）、嵌入字体缺失导致排版错乱时的兜底。
注意：输出后不能再复制文字，请保留原文件。
"""
from core.response import fail
from .._common import in_out
from ._pdf_raster import rasterize_to_pdf, list_pdfs

TOOL_ID = "document_pdf_to_imagepdf"
CATEGORY = "document"
LABEL = "PDF 转纯图"
NEEDS_FILES = True
PARAMS = [
    {"name": "dpi", "label": "渲染分辨率", "type": "select"},
]


def run(temp_id, params):
    try:
        import fitz  # noqa: F401
    except ImportError:
        return fail("服务器未安装 PyMuPDF：pip install PyMuPDF", code=503)

    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    files = list_pdfs(in_dir)
    if not files:
        return fail("未找到 PDF 文件", code=400)
    return rasterize_to_pdf(files, out_dir, dpi=params.get("dpi") or 150,
                            gray=False, prefix="image")
