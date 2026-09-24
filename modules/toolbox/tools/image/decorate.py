"""图片版式装饰：圆角 / 加背景 / 四周留白 / 平铺填充。

四种操作都是「不改内容、只改画布」，合并成一个工具位而不是四个。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_decorate"
CATEGORY = "image"
LABEL = "版式装饰"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "radius", "label": "圆角半径(px)，仅圆角", "type": "number"},
    {"name": "color", "label": "颜色（#RRGGBB 或 英文色名）", "type": "text"},
    {"name": "padding", "label": "留白(px)，仅留白", "type": "number"},
    {"name": "canvas_w", "label": "画布宽(px)，仅平铺", "type": "number"},
    {"name": "canvas_h", "label": "画布高(px)，仅平铺", "type": "number"},
]


def _color(s, default=(255, 255, 255)):
    s = (s or "").strip()
    if not s:
        return default
    if s.startswith("#") and len(s) == 7:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return default
    try:
        from PIL import ImageColor
        return ImageColor.getrgb(s)[:3]
    except Exception:
        return default


def run(temp_id, params):
    try:
        from PIL import Image
    except ImportError:
        return fail("服务器未安装 Pillow：pip install Pillow", code=503)

    params = params or {}
    action = (params.get("action") or "round").strip()
    if action not in ("round", "background", "padding", "tile"):
        return fail(f"不支持的操作: {action}", code=400)

    def _num(name, default=0):
        try:
            v = params.get(name)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    radius = _num("radius", 24)
    padding = _num("padding", 40)
    canvas_w, canvas_h = _num("canvas_w", 1920), _num("canvas_h", 1080)
    color = _color(params.get("color"))

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        fname = os.path.basename(fpath)
        stem, ext = os.path.splitext(fname)
        try:
            with Image.open(fpath) as im:
                im.load()

                if action == "round":
                    im = im.convert("RGBA")
                    from PIL import ImageDraw
                    r = max(0, int(radius))
                    mask = Image.new("L", im.size, 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        [(0, 0), (im.size[0] - 1, im.size[1] - 1)], radius=r, fill=255)
                    im.putalpha(mask)
                    out_name = f"{stem}_round.png"
                    im.save(os.path.join(out_dir, out_name))

                elif action == "background":
                    base = Image.new("RGB", im.size, color)
                    if im.mode in ("RGBA", "LA", "P"):
                        im = im.convert("RGBA")
                        base.paste(im, (0, 0), im)
                    else:
                        base.paste(im.convert("RGB"), (0, 0))
                    out_name = f"{stem}_bg{ext or '.png'}"
                    base.save(os.path.join(out_dir, out_name))

                elif action == "padding":
                    p = max(0, int(padding))
                    w, h = im.size
                    canvas = Image.new("RGB", (w + p * 2, h + p * 2), color)
                    src = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
                    canvas.paste(src, (p, p), src if src.mode == "RGBA" else None)
                    out_name = f"{stem}_pad{ext or '.png'}"
                    canvas.save(os.path.join(out_dir, out_name))

                else:  # tile
                    cw, ch = max(1, int(canvas_w)), max(1, int(canvas_h))
                    canvas = Image.new("RGB", (cw, ch), color)
                    src = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
                    sw, sh = src.size
                    for y in range(0, ch, sh):
                        for x in range(0, cw, sw):
                            canvas.paste(src, (x, y), src if src.mode == "RGBA" else None)
                    out_name = f"{stem}_tile.png"
                    canvas.save(os.path.join(out_dir, out_name))

            results.append({"name": out_name,
                            "size": os.path.getsize(os.path.join(out_dir, out_name))})
        except Exception as e:
            results.append({"name": fname, "error": str(e)})
    return ok(data={"results": results})
