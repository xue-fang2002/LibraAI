"""PDF 元数据读取 / 编辑（标题、作者、主题、关键词、创建工具等）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_meta"
CATEGORY = "document"
LABEL = "PDF 元数据"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "select"},
    {"name": "title", "label": "标题", "type": "text"},
    {"name": "author", "label": "作者", "type": "text"},
    {"name": "subject", "label": "主题", "type": "text"},
    {"name": "keywords", "label": "关键词", "type": "text"},
]

_FIELDS = ("title", "author", "subject", "keywords")


def run(temp_id, params):
    try:
        import fitz
    except ImportError:
        return fail("服务器未安装 PyMuPDF：pip install PyMuPDF", code=503)

    params = params or {}
    action = (params.get("action") or "view").strip()

    in_dir, out_dir = in_out(temp_id)
    files = [f for f in input_files(in_dir) if f.lower().endswith(".pdf")]
    if not files:
        return fail("未找到 PDF 文件", code=400)

    if action == "view":
        lines = []
        for fpath in files:
            with fitz.open(fpath) as doc:
                meta = doc.metadata or {}
                lines.append(f"=== {os.path.basename(fpath)} ===")
                lines.append(f"页数: {len(doc)}")
                for key in ("format", "title", "author", "subject",
                            "keywords", "creator", "producer",
                            "creationDate", "modDate", "encryption"):
                    val = meta.get(key)
                    lines.append(f"{key}: {val if val else '(空)'}")
                lines.append("")
        return ok(data={"result": "\n".join(lines).strip()})

    if action != "edit":
        return fail(f"不支持的操作: {action}", code=400)

    patch = {k: (params.get(k) or "").strip() for k in _FIELDS}
    if not any(patch.values()):
        return fail("请至少填写一个要写入的字段", code=400)

    results = []
    for fpath in files:
        base = os.path.splitext(os.path.basename(fpath))[0]
        out_name = f"{base}_meta.pdf"
        out_path = os.path.join(out_dir, out_name)
        try:
            doc = fitz.open(fpath)
            meta = dict(doc.metadata or {})
            meta.update(patch)
            # PyMuPDF 不允许 producer/format 被随意改写，这里只更新四个可写字段
            doc.set_metadata({k: meta.get(k, "") for k in ("title", "author",
                                                           "subject", "keywords")})
            doc.save(out_path, garbage=4, deflate=True)
            doc.close()
            results.append({"name": out_name, "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": out_name, "error": str(e)})
    return ok(data={"results": results})
