"""图片批量加文字水印（斜向平铺）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files, cjk_font

TOOL_ID = "image_watermark"
CATEGORY = "image"
LABEL = "图片水印"
NEEDS_FILES = True
PARAMS = [
    {"name": "text", "label": "水印文字", "type": "text", "required": True},
    {"name": "opacity", "label": "不透明度(5-100)", "type": "number", "default": 40},
]


def run(temp_id, params):
    try:
        from PIL import Image, ImageDraw
        params = params or {}
        text = (params.get("text") or "").strip()
        if not text:
            return fail("请输入水印文字", code=400)
        opacity = int(params.get("opacity", 40))
        opacity = max(5, min(100, opacity))
        in_dir, out_dir = in_out(temp_id)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    base = im.convert("RGBA")
                    layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
                    draw = ImageDraw.Draw(layer)
                    font_size = max(18, min(base.size) // 18)
                    font = cjk_font(font_size)
                    alpha = int(255 * opacity / 100)
                    step_x = max(font_size * len(text) // 2 + 60, 160)
                    step_y = max(font_size * 3, 120)
                    for y in range(-base.size[1], base.size[1] + step_y, step_y):
                        for x in range(-base.size[0], base.size[0] + step_x, step_x):
                            draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
                    layer = layer.rotate(30, resample=Image.BICUBIC, expand=False)
                    out = Image.alpha_composite(base, layer).convert("RGB")
                    out_name = "wm_" + os.path.splitext(fname)[0] + ".png"
                    out_path = os.path.join(out_dir, out_name)
                    out.save(out_path)
                    results.append({"name": out_name, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except ImportError:
        return fail("服务器未安装 Pillow，请联系管理员安装: pip install Pillow", code=503)
