"""PDF 栅格化 helper：把页面渲染成位图，再重新合成 PDF（用于转纯图 / 转灰度）。

两个工具（pdf_to_imagepdf / pdf_grayscale）逻辑完全一样，只差色彩空间，
故收在这里共用，避免两份重复代码。
"""
import os

from core.response import ok, fail
from .._common import input_files


def rasterize_to_pdf(files, out_dir, dpi=150, gray=False, prefix="raster"):
    """把每个输入 PDF 的每页渲染成位图后重新合成 PDF（原文本层丢弃）。

    返回 ok(data={"results": [...]})。
    """
    import fitz

    if not files:
        return fail("未找到 PDF 文件", code=400)

    dpi = int(dpi or 150)
    dpi = max(72, min(dpi, 600))
    cs = fitz.csGRAY if gray else fitz.csRGB

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_{prefix}.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            src = fitz.open(fpath)
            new = fitz.open()
            for page in src:
                pix = page.get_pixmap(dpi=dpi, colorspace=cs)
                rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
                img_pdf = fitz.open()
                img_page = img_pdf.new_page(width=rect.width, height=rect.height)
                img_page.insert_image(rect, pixmap=pix)
                new.insert_pdf(img_pdf)
                img_pdf.close()
            new.save(out_path, garbage=4, deflate=True)
            new.close()
            src.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})
    return ok(data={"results": results})


def list_pdfs(in_dir):
    return [f for f in input_files(in_dir) if f.lower().endswith(".pdf")]
