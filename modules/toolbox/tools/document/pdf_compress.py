"""PDF 压缩：垃圾回收 + 压缩流 + 图片降采样，减小文件体积。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_compress"
CATEGORY = "document"
LABEL = "PDF 压缩"
NEEDS_FILES = True
PARAMS = [
    {"name": "level", "label": "压缩强度", "type": "enum",
     "options": ["light", "medium", "strong"], "default": "medium"},
]


def _downsample_images(doc, target_dpi):
    """对嵌入图片做降采样（按目标 DPI 缩小像素尺寸，减少体积）。"""
    import fitz
    for xref in range(1, doc.xref_length()):
        try:
            # 仅处理图片类型的对象
            if doc.xref_get_key(xref, "Subtype")[1] != "/Image":
                continue
            pix = fitz.Pixmap(doc, xref)
            if pix is None or pix.width <= 64:
                pix = None
                continue
            # 估算当前 DPI：多数图片按 96~150 记录，这里以宽度近似
            w = pix.width
            scale = min(1.0, target_dpi / 150.0)
            if scale >= 1.0 or w * scale < 64:
                pix = None
                continue
            nw = max(64, int(w * scale))
            nh = max(64, int(pix.height * scale))
            # 用 Pixmap 缩放重采样
            import fitz as _f
            if pix.n - pix.alpha >= 4:  # CMYK 转 RGB
                pix = _f.Pixmap(_f.csRGB, pix)
            pix2 = _f.Pixmap(pix, _f.Matrix(nw / w, nh / pix.height))
            if pix2.samples and pix2.width == nw:
                # 用缩放后的像素替换原图
                doc.xref_set_key(xref, "Width", str(nw))
                doc.xref_set_key(xref, "Height", str(nh))
                doc.update_stream(xref, pix2.tobytes(pix2.colorspace.name))
            pix2 = None
            pix = None
        except Exception:
            continue


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF，请联系管理员安装: pip install PyMuPDF", code=503)

    params = params or {}
    level = params.get("level") or "medium"
    dpi_map = {"light": 200, "medium": 150, "strong": 100}

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = base + "_compressed.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            doc = fitz.open(fpath)
            before = os.path.getsize(fpath)
            if level != "light":
                _downsample_images(doc, dpi_map[level])
            doc.save(out_path, garbage=4, deflate=True, clean=True)
            doc.close()
            after = os.path.getsize(out_path)
            ratio = (1 - after / before) * 100 if before else 0
            results.append({"name": out_name, "size": after,
                            "before": before, "saved_percent": round(ratio, 1)})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可压缩的 PDF 文件", code=400)
    return ok(data={"results": results})
