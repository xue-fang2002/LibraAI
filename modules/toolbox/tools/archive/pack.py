"""多文件打包：zip / tar / tar.gz。

rar / 7z 需要调用 7-Zip 子进程（Windows 上通常没装），这里不支持，
并在参数里直接说明，避免用户选了却得到失败结果。
"""
import os
import tarfile
import zipfile

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "archive_pack"
CATEGORY = "archive"
LABEL = "打包压缩"
NEEDS_FILES = True
PARAMS = [
    {"name": "format", "label": "打包格式", "type": "select"},
    {"name": "name", "label": "压缩包文件名（留空=archive）", "type": "text"},
]


def run(temp_id, params):
    params = params or {}
    fmt = (params.get("format") or "zip").strip().lower()
    if fmt not in ("zip", "tar", "tar.gz", "tgz"):
        return fail(f"不支持的打包格式: {fmt}（rar / 7z 需本机安装 7-Zip，暂不支持）", code=400)

    name = (params.get("name") or "").strip() or "archive"
    name = os.path.basename(name)
    if fmt in ("tar.gz", "tgz"):
        out_name = f"{name}.tar.gz"
        fmt = "tar.gz"
    else:
        out_name = f"{name}.{fmt}"

    in_dir, out_dir = in_out(temp_id)
    files = input_files(in_dir)
    if not files:
        return fail("请先上传要打包的文件", code=400)

    out_path = os.path.join(out_dir, out_name)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, os.path.basename(f))
        else:
            mode = "w:gz" if fmt == "tar.gz" else "w"
            with tarfile.open(out_path, mode) as tf:
                for f in files:
                    tf.add(f, arcname=os.path.basename(f))
    except Exception as e:
        return fail(f"打包失败：{e}", code=500)

    return ok(data={"results": [{"name": out_name, "size": os.path.getsize(out_path)}]})
