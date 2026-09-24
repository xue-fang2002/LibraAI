"""批量生成缩略图。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_thumbnail"
CATEGORY = "image"
LABEL = "生成缩略图"
NEEDS_FILES = True
PARAMS = [
    {"name": "size", "label": "最大边长(px)", "type": "number", "default": 256},
]


def run(temp_id, params):
    try:
        from PIL import Image
        params = params or {}
        size = int(params.get("size", 256))
        in_dir, out_dir = in_out(temp_id)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    im.thumbnail((size, size), Image.LANCZOS)
                    out_name = f"thumb_{fname}"
                    out_path = os.path.join(out_dir, out_name)
                    im.save(out_path)
                    results.append({"name": out_name, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except ImportError:
        return fail("服务器未安装 Pillow", code=503)
