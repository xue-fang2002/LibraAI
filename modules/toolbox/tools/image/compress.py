"""图片批量压缩（等比缩放 + 转 JPG）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_compress"
CATEGORY = "image"
LABEL = "批量压缩"
NEEDS_FILES = True
PARAMS = [
    {"name": "quality", "label": "压缩质量(1-100)", "type": "number", "default": 80},
    {"name": "max_width", "label": "最大宽度(px)", "type": "number", "default": 1920},
]


def run(temp_id, params):
    try:
        from PIL import Image
        params = params or {}
        quality = int(params.get("quality", 85))
        max_width = int(params.get("max_width", 1920))
        in_dir, out_dir = in_out(temp_id)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    if im.width > max_width:
                        ratio = max_width / im.width
                        new_h = int(im.height * ratio)
                        im = im.resize((max_width, new_h), Image.LANCZOS)
                    if im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    out_name = os.path.splitext(fname)[0] + ".jpg"
                    out_path = os.path.join(out_dir, out_name)
                    im.save(out_path, "JPEG", quality=quality)
                    results.append({
                        "name": out_name, "original": fname,
                        "size": os.path.getsize(out_path),
                    })
            except Exception as e:
                results.append({"name": fname, "error": str(e)})

        return ok(data={"results": results, "count": len(results)})
    except ImportError:
        return fail("服务器未安装 Pillow，请联系管理员安装: pip install Pillow", code=503)
