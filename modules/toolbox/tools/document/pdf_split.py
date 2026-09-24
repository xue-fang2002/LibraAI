"""PDF 拆分：按页码范围（如 "1-3,4-6"）拆，留空则每页一个文件。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_split"
CATEGORY = "document"
LABEL = "PDF 拆分"
NEEDS_FILES = True
PARAMS = [
    {"name": "ranges", "label": "页码范围，如 1-3,4-6（留空=每页一个）", "type": "text"},
]


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF", code=503)

    params = params or {}
    in_dir, out_dir = in_out(temp_id)
    ranges_str = (params.get("ranges") or "").strip()

    files = [f for f in input_files(in_dir) if f.lower().endswith(".pdf")]
    if not files:
        return fail("未找到 PDF 文件", code=400)

    results = []
    with fitz.open(files[0]) as doc:
        if not ranges_str:
            for i in range(len(doc)):
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                out_name = f"page_{i+1}.pdf"
                out_path = os.path.join(out_dir, out_name)
                new_doc.save(out_path)
                new_doc.close()
                results.append({"name": out_name})
        else:
            for idx, part in enumerate(ranges_str.split(",")):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    start, end = int(start) - 1, int(end) - 1
                else:
                    start = end = int(part) - 1
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=start, to_page=end)
                out_name = f"split_{idx+1}.pdf"
                out_path = os.path.join(out_dir, out_name)
                new_doc.save(out_path)
                new_doc.close()
                results.append({"name": out_name})
    return ok(data={"results": results})
