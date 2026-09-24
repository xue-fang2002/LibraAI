"""图片批量编辑：旋转 / 水平翻转 / 垂直翻转。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_edit"
CATEGORY = "image"
LABEL = "旋转/翻转"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "动作", "type": "enum",
     "options": ["rotate", "flip_h", "flip_v"], "default": "rotate"},
    {"name": "value", "label": "旋转角度", "type": "number", "default": 90},
]


def run(temp_id, params):
    try:
        from PIL import Image
        params = params or {}
        action = params.get("action", "rotate")
        value = params.get("value", 90)
        in_dir, out_dir = in_out(temp_id)

        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    if action == "rotate":
                        im = im.rotate(-int(value), expand=True)
                    elif action == "flip_h":
                        im = im.transpose(Image.FLIP_LEFT_RIGHT)
                    elif action == "flip_v":
                        im = im.transpose(Image.FLIP_TOP_BOTTOM)
                    out_path = os.path.join(out_dir, fname)
                    im.save(out_path)
                    results.append({"name": fname, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except ImportError:
        return fail("服务器未安装 Pillow", code=503)
