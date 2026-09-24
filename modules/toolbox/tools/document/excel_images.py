"""Excel 内嵌图片提取：xlsx 本质是 zip，直接取 xl/media/ 下的图片资源。

比 openpyxl 遍历 drawing 更稳（浮动图片、批注背景等一并取出），且不依赖单元格锚定信息。
"""
import os
import zipfile

from core.response import ok, fail
from .._common import in_out, ordered_files

TOOL_ID = "document_excel_images"
CATEGORY = "document"
LABEL = "Excel 提取图片"
NEEDS_FILES = True
PARAMS = []

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".emf", ".wmf")


def run(temp_id, params):
    in_dir, out_dir = in_out(temp_id)
    books = [f for f in ordered_files(in_dir) if f.lower().endswith((".xlsx", ".xlsm"))]
    if not books:
        return fail("未找到 xlsx / xlsm 文件（csv 不含图片）", code=400)

    results = []
    for fpath in books:
        base = os.path.splitext(os.path.basename(fpath))[0]
        try:
            zf = zipfile.ZipFile(fpath)
        except Exception as e:
            results.append({"name": base, "error": f"不是有效的 xlsx：{e}"})
            continue

        count = 0
        with zf:
            for item in zf.namelist():
                if not item.lower().startswith("xl/media/"):
                    continue
                name = os.path.basename(item)
                if not name or not name.lower().endswith(_IMG_EXTS):
                    continue
                out_name = f"{base}_{name}"
                with zf.open(item) as src, open(os.path.join(out_dir, out_name), "wb") as dst:
                    dst.write(src.read())
                results.append({"name": out_name})
                count += 1

        if count == 0:
            results.append({"name": base, "error": "该工作簿内没有内嵌图片"})

    if not results:
        return fail("未提取到任何图片", code=400)
    return ok(data={"results": results})
