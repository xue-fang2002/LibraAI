"""图片尺寸修改：按宽度 / 按高度 / 按比例 / 精确尺寸，并可改 DPI。

与「缩略图」的区别：缩略图只做等比缩小且输出统一尺寸，这里允许指定一边、
按比例缩放、或强制精确尺寸，并支持改写 DPI（印刷投稿常用 300dpi）。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_resize"
CATEGORY = "image"
LABEL = "尺寸修改"
NEEDS_FILES = True
PARAMS = [
    {"name": "mode", "label": "缩放方式", "type": "select"},
    {"name": "width", "label": "目标宽度(px)", "type": "number"},
    {"name": "height", "label": "目标高度(px)", "type": "number"},
    {"name": "percent", "label": "缩放比例", "type": "number"},
    {"name": "dpi", "label": "输出 DPI（0=保持原值）", "type": "number"},
]


def run(temp_id, params):
    try:
        from PIL import Image
    except ImportError:
        return fail("服务器未安装 Pillow：pip install Pillow", code=503)

    params = params or {}
    mode = (params.get("mode") or "width").strip()

    def _num(name, default=0):
        try:
            v = params.get(name)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    width, height = _num("width"), _num("height")
    percent, dpi = _num("percent", 100), _num("dpi")
    if mode == "width" and width <= 0:
        return fail("按宽度缩放请填写大于 0 的宽度", code=400)
    if mode == "height" and height <= 0:
        return fail("按高度缩放请填写大于 0 的高度", code=400)
    if mode == "percent" and percent <= 0:
        return fail("按比例缩放请填写大于 0 的比例", code=400)
    if mode == "exact" and (width <= 0 or height <= 0):
        return fail("精确尺寸请同时填写宽和高", code=400)

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        fname = os.path.basename(fpath)
        out_name = f"resized_{fname}"
        out_path = os.path.join(out_dir, out_name)
        try:
            with Image.open(fpath) as im:
                im.load()
                ow, oh = im.size

                if mode == "width":
                    nw, nh = int(width), max(1, int(oh * width / ow))
                elif mode == "height":
                    nw, nh = max(1, int(ow * height / oh)), int(height)
                elif mode == "percent":
                    nw, nh = max(1, int(ow * percent / 100)), max(1, int(oh * percent / 100))
                else:
                    nw, nh = int(width), int(height)

                out = im.resize((nw, nh), Image.LANCZOS)
                save_kw = {}
                if dpi > 0:
                    save_kw["dpi"] = (int(dpi), int(dpi))
                fmt = os.path.splitext(fname)[1].lower().lstrip(".")
                if fmt in ("jpg", "jpeg") and out.mode in ("RGBA", "LA", "P"):
                    out = out.convert("RGB")
                out.save(out_path, **save_kw)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": fname, "error": str(e)})
    return ok(data={"results": results})
