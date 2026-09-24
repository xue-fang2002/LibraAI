"""解压：zip / tar / tar.gz / tgz。

解压后的文件仍留在临时目录并重新打包返回 —— 这是本项目的安全模型
（上传 → 临时目录处理 → 产物打包下载），服务器不会把文件写到其它位置。
rar / 7z 需要本机 7-Zip，检测到缺失时给出明确提示而不是静默失败。
"""
import os
import re
import tarfile
import zipfile

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "archive_unpack"
CATEGORY = "archive"
LABEL = "解压"
NEEDS_FILES = True
PARAMS = [
    {"name": "flatten", "label": "忽略目录层级（全部平铺到一层）", "type": "checkbox"},
]

_SAFE_PREFIXES = ("", "./", ".\\")


def _safe_join(base, member):
    """防 zip-slip：拒绝 ../ 、绝对路径、盘符与 UNC 成员。

    注意 os.path.join 的语义陷阱：只要第二个参数是绝对路径（Windows 下
    "C:/evil" 或 UNC "//host/share"），它会**直接丢弃 base**。而 name 在前面
    已被 replace("\\", "/") 归一化，所以 "C:\\evil" 会变成 "C:/evil" 一路躲过
    原有的 startswith("/") 与 ".." 检查 —— 仅靠字符串判断是挡不住的，末尾必须
    再用 commonpath 做一次「落盘目标仍在 base 内」的实证复核。
    """
    name = str(member or "").replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if not name or name.startswith("/") or ".." in name.split("/"):
        return None
    # 盘符绝对路径（C:/、d:/）与 UNC（//host/share）
    if re.match(r"^[A-Za-z]:", name) or name.startswith("//"):
        return None
    target = os.path.join(base, name)
    try:
        base_abs = os.path.abspath(base)
        if os.path.commonpath([base_abs, os.path.abspath(target)]) != base_abs:
            return None
    except ValueError:
        # 跨盘符等无法求公共前缀的情况，一律拒绝
        return None
    return target


def _decode_zip_name(name, flag_bits=0):
    """正确还原 zip 条目名（修复中文乱码）。

    Python 的 zipfile 对**未设置 UTF-8 标志位（bit 11）**的条目默认按 cp437
    解码文件名；而 Windows/7-Zip 打的中文压缩包文件名是 GBK 字节且无该标志位，
    会得到 «─Ω╘┬» 这类乱码。有 UTF-8 标志就用原串，否则 cp437→GBK（gb18030 兜底）。
    """
    if not name:
        return name
    if flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("gb18030")
    except Exception:
        return name


def run(temp_id, params):
    params = params or {}
    flatten = bool(params.get("flatten"))

    in_dir, out_dir = in_out(temp_id)
    files = input_files(in_dir)
    if not files:
        return fail("请先上传压缩包", code=400)

    results = []
    for fpath in files:
        lower = fpath.lower()
        try:
            if lower.endswith(".zip"):
                with zipfile.ZipFile(fpath) as zf:
                    for info in zf.infolist():
                        item = _decode_zip_name(info.filename, info.flag_bits)
                        if item.endswith("/"):
                            continue
                        target = os.path.join(out_dir, os.path.basename(item)) if flatten \
                            else _safe_join(out_dir, item)
                        if not target:
                            continue
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with zf.open(info) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        results.append({"name": os.path.relpath(target, out_dir)})

            elif lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
                with tarfile.open(fpath) as tf:
                    for member in tf.getmembers():
                        if not member.isfile():
                            continue
                        target = os.path.join(out_dir, os.path.basename(member.name)) if flatten \
                            else _safe_join(out_dir, member.name)
                        if not target:
                            continue
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with src, open(target, "wb") as dst:
                            dst.write(src.read())
                        results.append({"name": os.path.relpath(target, out_dir)})

            elif lower.endswith((".rar", ".7z")):
                results.append({"name": os.path.basename(fpath),
                                "error": "rar / 7z 需要服务器安装 7-Zip，暂不支持"})

            else:
                results.append({"name": os.path.basename(fpath),
                                "error": "不是受支持的压缩包（zip / tar / tar.gz）"})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("压缩包为空或解压失败", code=400)
    return ok(data={"results": results})
