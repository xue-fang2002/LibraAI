"""查看图片 EXIF 信息 / 清除隐私数据。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "image_exif"
CATEGORY = "image"
LABEL = "EXIF 查看/清除"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "动作", "type": "enum",
     "options": ["view", "clear"], "default": "view"},
]


def run(temp_id, params):
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        params = params or {}
        action = params.get("action", "view")
        in_dir, out_dir = in_out(temp_id)

        if action == "clear":
            results = []
            for fpath in input_files(in_dir):
                fname = os.path.basename(fpath)
                try:
                    with Image.open(fpath) as im:
                        data = list(im.getdata())
                        clean = Image.new(im.mode, im.size)
                        clean.putdata(data)
                        out_name = "clean_" + fname
                        out_path = os.path.join(out_dir, out_name)
                        clean.save(out_path)
                        results.append({"name": out_name, "size": os.path.getsize(out_path)})
                except Exception as e:
                    results.append({"name": fname, "error": str(e)})
            return ok(data={"results": results})

        lines = []
        for fpath in input_files(in_dir):
            fname = os.path.basename(fpath)
            lines.append(f"=== {fname} ===")
            try:
                with Image.open(fpath) as im:
                    exif = im.getexif()
                    if not exif:
                        lines.append("  （无 EXIF 信息）")
                        continue
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        lines.append(f"  {tag}: {value}")
            except Exception as e:
                lines.append(f"  读取失败: {e}")
        return ok(data={"result": "\n".join(lines) or "无 EXIF 信息"})
    except ImportError:
        return fail("服务器未安装 Pillow，请联系管理员安装: pip install Pillow", code=503)
