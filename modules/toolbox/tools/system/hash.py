"""文件哈希计算：只对**用户上传**的文件计算 MD5 / SHA1 / SHA256。

刻意不做「服务端遍历目录算哈希」：那要读服务器任意路径，与本项目
「上传 → 临时目录 → 产物下载」的安全模型冲突（本机文件搜索工具已因此下线）。
"""
import hashlib
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "system_hash"
CATEGORY = "system"
LABEL = "文件哈希"
NEEDS_FILES = True
PARAMS = [
    {"name": "algo", "label": "算法", "type": "select"},
]

_ALGOS = ("md5", "sha1", "sha256")


def run(temp_id, params):
    params = params or {}
    algo = (params.get("algo") or "md5").strip().lower()
    if algo not in _ALGOS:
        return fail(f"不支持的算法: {algo}", code=400)

    in_dir, _ = in_out(temp_id)
    files = input_files(in_dir)
    if not files:
        return fail("请先上传文件", code=400)

    lines = []
    for fpath in files:
        h = hashlib.new(algo)
        size = 0
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        lines.append(f"{h.hexdigest()}  {os.path.basename(fpath)}  ({size} bytes)")

    return ok(data={"result": "\n".join(lines)})
