"""图片去背景（依赖 rembg，首次运行会下载约 170MB 的 u2net 模型）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_remove_bg"
CATEGORY = "image"
LABEL = "图片去背景"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    try:
        import rembg
    except ImportError:
        return fail(
            "服务器未安装去背景依赖，请管理员执行: pip install rembg"
            "（首次运行还会自动下载约 170MB 的 u2net 模型）", code=503)
    try:
        from PIL import Image
        in_dir, out_dir = in_out(temp_id)
        results = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            try:
                with Image.open(fpath) as im:
                    out_img = rembg.remove(im.convert("RGBA"))
                    out_name = os.path.splitext(fname)[0] + "_nobg.png"
                    out_path = os.path.join(out_dir, out_name)
                    out_img.save(out_path)
                    results.append({"name": out_name, "size": os.path.getsize(out_path)})
            except Exception as e:
                results.append({"name": fname, "error": str(e)})
        return ok(data={"results": results})
    except Exception as e:
        return fail(f"去背景失败：{e}", code=500)
