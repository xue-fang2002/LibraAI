"""图片格式批量转换。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_convert"
CATEGORY = "image"
LABEL = "格式转换"
NEEDS_FILES = True
PARAMS = [
    {"name": "format", "label": "目标格式", "type": "enum",
     "options": ["jpg", "png", "webp", "bmp"], "default": "png"},
]


def run(temp_id, params):
    try:
        from PIL import Image
        params = params or {}
        fmt = str(params.get("format", "png")).upper()
        in_dir, out_dir = in_out(temp_id)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    out_name = os.path.splitext(fname)[0] + f".{fmt.lower()}"
                    out_path = os.path.join(out_dir, out_name)
                    if im.mode == "RGBA" and fmt in ("JPEG", "JPG"):
                        im = im.convert("RGB")
                    im.save(out_path, fmt)
                    results.append({"name": out_name, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except ImportError:
        return fail("服务器未安装 Pillow", code=503)
