"""PDF 页面管理：提取 / 删除 / 重排 / 裁剪页边距。

一个工具覆盖清单里「提取页面 / 删除页面 / 页面排序 / 裁剪页面边距」四项，
避免为四种同质操作各开一个工具位（工具位越多，二级导航越难用）。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files
from ._pdf_pages_util import parse_pages

TOOL_ID = "document_pdf_pages"
CATEGORY = "document"
LABEL = "PDF 页面管理"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "pages", "label": "页码范围，如 1-3,5,8", "type": "text"},
    {"name": "order", "label": "新顺序，如 3,1,2（仅排序）", "type": "text"},
    {"name": "margin", "label": "裁掉的边距(pt)，仅裁剪", "type": "number"},
]

_ACTION_SUFFIX = {
    "extract": "extract",
    "delete": "deleted",
    "reorder": "reorder",
    "crop": "crop",
}


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF：pip install PyMuPDF", code=503)

    params = params or {}
    action = (params.get("action") or "extract").strip()
    if action not in _ACTION_SUFFIX:
        return fail(f"不支持的操作: {action}", code=400)

    in_dir, out_dir = in_out(temp_id)
    files = [f for f in input_files(in_dir) if f.lower().endswith(".pdf")]
    if not files:
        return fail("未找到 PDF 文件", code=400)

    try:
        margin = float(params.get("margin") or 0)
    except (TypeError, ValueError):
        margin = 0.0
    if action == "crop" and margin <= 0:
        return fail("裁剪需要填写大于 0 的边距(pt)", code=400)

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_{_ACTION_SUFFIX[action]}.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            src = fitz.open(fpath)
            total = len(src)
            new = fitz.open()

            if action == "extract":
                picks = parse_pages(params.get("pages"), total)
                if not picks:
                    src.close()
                    new.close()
                    return fail("请填写有效的页码范围，例如 1-3,5", code=400)
                for i in picks:
                    new.insert_pdf(src, from_page=i, to_page=i)

            elif action == "delete":
                drops = set(parse_pages(params.get("pages"), total))
                if not drops:
                    src.close()
                    new.close()
                    return fail("请填写要删除的页码范围，例如 1-3,5", code=400)
                for i in range(total):
                    if i not in drops:
                        new.insert_pdf(src, from_page=i, to_page=i)

            elif action == "reorder":
                order = []
                for p in str(params.get("order") or "").split(","):
                    p = p.strip()
                    if not p:
                        continue
                    try:
                        idx = int(p) - 1
                    except ValueError:
                        continue
                    if 0 <= idx < total:
                        order.append(idx)
                if len(order) != total or len(set(order)) != total:
                    src.close()
                    new.close()
                    return fail(
                        f"新顺序必须恰好包含全部 {total} 个页码且不重复，例如 3,1,2", code=400)
                for i in order:
                    new.insert_pdf(src, from_page=i, to_page=i)

            else:  # crop
                new.insert_pdf(src)
                for page in new:
                    r = page.rect
                    page.set_cropbox(fitz.Rect(r.x0 + margin, r.y0 + margin,
                                               r.x1 - margin, r.y1 - margin))

            if len(new) == 0:
                src.close()
                new.close()
                results.append({"name": out_name, "error": "结果为空（全部页面被移除）"})
                continue

            new.save(out_path, garbage=4, deflate=True)
            new.close()
            src.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})

    return ok(data={"results": results})
