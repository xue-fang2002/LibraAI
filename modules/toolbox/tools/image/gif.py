"""GIF 拆分 / 合成：把动图拆成逐帧 PNG，或把多张图片合成 GIF。"""
import os

from core.response import ok, fail
from .._common import in_out, ordered_files

TOOL_ID = "image_gif"
CATEGORY = "image"
LABEL = "GIF 拆分/合成"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "duration", "label": "每帧时长(ms)，仅合成", "type": "number"},
    {"name": "loop", "label": "循环次数（0=无限），仅合成", "type": "number"},
]

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


def run(temp_id, params):
    try:
        from PIL import Image
    except ImportError:
        return fail("服务器未安装 Pillow：pip install Pillow", code=503)

    params = params or {}
    action = (params.get("action") or "split").strip()

    def _num(name, default=0):
        try:
            v = params.get(name)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    duration = max(20, int(_num("duration", 300)))
    loop = max(0, int(_num("loop", 0)))

    in_dir, out_dir = in_out(temp_id)
    # 合成时必须按用户上传顺序，否则帧序是乱的
    files = [f for f in ordered_files(in_dir) if f.lower().endswith(_IMG_EXTS)]
    if not files:
        return fail("未找到图片文件", code=400)

    results = []

    if action == "merge":
        frames = []
        for fpath in files:
            try:
                with Image.open(fpath) as im:
                    im.load()
                    frames.append(im.convert("RGB"))
            except Exception as e:
                results.append({"name": os.path.basename(fpath), "error": str(e)})
        if len(frames) < 2:
            return fail("合成 GIF 至少需要 2 张图片", code=400)
        out_name = "merged.gif"
        out_path = os.path.join(out_dir, out_name)
        try:
            first, rest = frames[0], frames[1:]
            first.save(out_path, save_all=True, append_images=rest,
                       duration=duration, loop=loop, optimize=True)
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})
        return ok(data={"results": results})

    if action != "split":
        return fail(f"不支持的操作: {action}", code=400)

    for fpath in files:
        stem = os.path.splitext(os.path.basename(fpath))[0]
        try:
            with Image.open(fpath) as im:
                im.load()
                nframes = getattr(im, "n_frames", 1)
                if nframes <= 1:
                    results.append({"name": os.path.basename(fpath),
                                    "error": "不是动图（只有 1 帧），无需拆分"})
                    continue
                for i in range(nframes):
                    im.seek(i)
                    frame = im.convert("RGBA")
                    out_name = f"{stem}_frame{i+1:03d}.png"
                    frame.save(os.path.join(out_dir, out_name))
                    results.append({"name": out_name})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})
    return ok(data={"results": results})
