"""PDF 签章 / 插图 / 加文字：按页面百分比定位，覆盖在原有内容之上。

定位刻意用「百分比」而不是绝对坐标：用户在不同尺寸的页面上摆放时，
百分比位置是可预期的，绝对坐标要反复试。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files
from ._pdf_pages_util import parse_pages

TOOL_ID = "document_pdf_stamp"
CATEGORY = "document"
LABEL = "PDF 签章/插图"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "text", "label": "文字内容（仅加文字）", "type": "text"},
    {"name": "pages", "label": "页码范围，如 1,3-5（留空=全部）", "type": "text"},
    {"name": "x", "label": "距左边", "type": "number"},
    {"name": "y", "label": "距顶部", "type": "number"},
    {"name": "width", "label": "占页宽", "type": "number"},
    {"name": "fontsize", "label": "字号（仅加文字）", "type": "number"},
    {"name": "color", "label": "文字颜色（#RRGGBB）", "type": "text"},
    {"name": "opacity", "label": "不透明度", "type": "number"},
]

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff")


def _hex2rgb(s, default=(0, 0, 0)):
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return default


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF：pip install PyMuPDF", code=503)

    params = params or {}
    action = (params.get("action") or "image").strip()
    if action not in ("image", "text"):
        return fail(f"不支持的操作: {action}", code=400)

    def _f(name, default):
        try:
            return float(params.get(name) if params.get(name) not in (None, "") else default)
        except (TypeError, ValueError):
            return default

    x_pct, y_pct = _f("x", 60), _f("y", 75)
    w_pct = _f("width", 25)
    opacity = max(0.0, min(1.0, _f("opacity", 100) / 100))
    fontsize = _f("fontsize", 14)
    color = _hex2rgb(params.get("color"), default=(0, 0, 0))
    text = (params.get("text") or "").strip()

    in_dir, out_dir = in_out(temp_id)
    all_files = input_files(in_dir)
    pdfs = [f for f in all_files if f.lower().endswith(".pdf")]
    if not pdfs:
        return fail("未找到 PDF 文件", code=400)

    img = None
    if action == "image":
        imgs = [f for f in all_files if f.lower().endswith(_IMG_EXTS)]
        if not imgs:
            return fail("请同时上传一张图片（签名 / 印章）", code=400)
        img = imgs[0]
        try:
            from PIL import Image
            with Image.open(img) as im:
                iw, ih = im.size
        except Exception:
            iw, ih = 1, 1
    elif not text:
        return fail("请填写要添加的文字", code=400)

    results = []
    for fpath in pdfs:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_stamp.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            doc = fitz.open(fpath)
            picks = parse_pages(params.get("pages"), len(doc)) if (params.get("pages") or "").strip() \
                else list(range(len(doc)))
            if not picks:
                doc.close()
                results.append({"name": out_name, "error": "页码范围无效"})
                continue

            for i in picks:
                page = doc[i]
                pr = page.rect
                left = pr.x0 + pr.width * x_pct / 100
                top = pr.y0 + pr.height * y_pct / 100
                w = pr.width * w_pct / 100

                if action == "image":
                    h = w * (ih / iw) if iw else w
                    page.insert_image(
                        fitz.Rect(left, top, left + w, top + h),
                        filename=img, overlay=True, alpha=opacity,
                    )
                else:
                    try:
                        page.insert_text(
                            fitz.Point(left, top + fontsize), text,
                            fontsize=fontsize, fontname="china-s",
                            color=color, fill_opacity=opacity, overlay=True,
                        )
                    except TypeError:
                        # 老版 PyMuPDF 的 insert_text 不支持 fill_opacity
                        page.insert_text(
                            fitz.Point(left, top + fontsize), text,
                            fontsize=fontsize, fontname="china-s",
                            color=color, overlay=True,
                        )

            doc.save(out_path, garbage=4, deflate=True)
            doc.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})

    return ok(data={"results": results})
