"""PDF 加页码 / 页眉：在每页底部居中加页码，可选页眉文字。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files, cjk_font

TOOL_ID = "document_pdf_pagenumber"
CATEGORY = "document"
LABEL = "PDF 加页码"
NEEDS_FILES = True
PARAMS = [
    {"name": "format", "label": "页码格式", "type": "text",
     "default": "{page}/{total}"},
    {"name": "header", "label": "页眉文字（可留空）", "type": "text"},
    {"name": "start", "label": "起始页码", "type": "number", "default": 1},
]


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install PyMuPDF", code=503)

    params = params or {}
    fmt = params.get("format") or "{page}/{total}"
    header = params.get("header") or ""
    start = int(params.get("start") or 1)

    # 中文页码需 CJK 字体；fitz 内嵌系统字体较麻烦，这里用文本，若需中文可后续增强
    fontname = "china-s" if any('\u4e00' <= c <= '\u9fff' for c in fmt + header) else "helv"

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = base + "_numbered.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            doc = fitz.open(fpath)
            total = doc.page_count
            for i, page in enumerate(doc):
                label = fmt.replace("{page}", str(start + i)).replace("{total}", str(total))
                rect = page.rect
                # 底部居中页码
                page.insert_text(
                    fitz.Point(rect.width / 2 - 20, rect.height - 20),
                    label, fontsize=9, fontname=fontname, color=(0.3, 0.3, 0.3),
                )
                if header:
                    page.insert_text(
                        fitz.Point(rect.width / 2 - 30, 30),
                        header, fontsize=9, fontname=fontname, color=(0.3, 0.3, 0.3),
                    )
            doc.save(out_path)
            doc.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可处理的 PDF 文件", code=400)
    return ok(data={"results": results})
