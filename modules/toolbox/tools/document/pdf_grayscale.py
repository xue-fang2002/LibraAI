"""PDF 转灰度：页面栅格化为灰度图后重新合成（省墨打印 / 归档用）。"""
from core.response import fail
from .._common import in_out
from ._pdf_raster import rasterize_to_pdf, list_pdfs

TOOL_ID = "document_pdf_grayscale"
CATEGORY = "document"
LABEL = "PDF 转灰度"
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
                            gray=True, prefix="gray")
