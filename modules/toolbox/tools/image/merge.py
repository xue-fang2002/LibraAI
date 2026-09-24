"""多图拼接为一张长图（纵向 / 横向）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_merge"
CATEGORY = "image"
LABEL = "图片拼接"
NEEDS_FILES = True
PARAMS = [
    {"name": "direction", "label": "方向", "type": "enum",
     "options": ["vertical", "horizontal"], "default": "vertical"},
    {"name": "gap", "label": "间距(px)", "type": "number", "default": 0},
]


def run(temp_id, params):
    try:
        from PIL import Image
        params = params or {}
        direction = params.get("direction", "vertical")
        gap = max(0, int(params.get("gap", 0)))
        in_dir, out_dir = in_out(temp_id)

        imgs = []
        for fpath in input_files(in_dir):
            try:
                imgs.append(Image.open(fpath).convert("RGB"))
            except Exception:
                continue
        if len(imgs) < 2:
            return fail("至少需要 2 张可识别的图片", code=400)

        if direction == "horizontal":
            w = max(i.width for i in imgs)
            imgs = [i.resize((w, int(i.height * w / i.width)), Image.LANCZOS) for i in imgs]
            total_w = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
            total_h = max(i.height for i in imgs)
            canvas = Image.new("RGB", (total_w, total_h), "white")
            x = 0
            for i in imgs:
                canvas.paste(i, (x, 0))
                x += i.width + gap
        else:
            h = max(i.height for i in imgs)
            imgs = [i.resize((int(i.width * h / i.height), h), Image.LANCZOS) for i in imgs]
            total_w = max(i.width for i in imgs)
            total_h = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
            canvas = Image.new("RGB", (total_w, total_h), "white")
            y = 0
            for i in imgs:
                canvas.paste(i, (0, y))
                y += i.height + gap

        out_name = "merged.png"
        out_path = os.path.join(out_dir, out_name)
        canvas.save(out_path)
        return ok(data={"results": [{"name": out_name, "size": os.path.getsize(out_path)}]})
    except ImportError:
        return fail("服务器未安装 Pillow，请联系管理员安装: pip install Pillow", code=503)
