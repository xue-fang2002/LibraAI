"""图片滤镜：模糊 / 锐化 / 边缘 / 浮雕 / 等高线 / 灰度。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_filter"
CATEGORY = "image"
LABEL = "图片滤镜"
NEEDS_FILES = True
PARAMS = [
    {"name": "filter", "label": "滤镜", "type": "enum",
     "options": ["blur", "sharpen", "edge", "emboss", "contour", "grayscale"],
     "default": "blur"},
]


def run(temp_id, params):
    try:
        from PIL import Image, ImageFilter
        params = params or {}
        filt = params.get("filter", "blur")
        in_dir, out_dir = in_out(temp_id)

        filter_map = {
            "blur": ImageFilter.GaussianBlur(radius=2),
            "sharpen": ImageFilter.SHARPEN,
            "edge": ImageFilter.FIND_EDGES,
            "emboss": ImageFilter.EMBOSS,
            "contour": ImageFilter.CONTOUR,
        }
        pil_filter = filter_map.get(filt)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    if pil_filter:
                        im = im.filter(pil_filter)
                    if filt == "grayscale":
                        im = im.convert("L").convert("RGB")
                    out_path = os.path.join(out_dir, fname)
                    im.save(out_path)
                    results.append({"name": fname, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except ImportError:
        return fail("服务器未安装 Pillow", code=503)
