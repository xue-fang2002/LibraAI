"""Word 模板填充：占位符 {{字段}} 批量替换，数据来自 Excel。

用法：上传 1 个 .docx 模板（含 {{字段}} 占位符）+ 1 个数据表（.xlsx/.csv，
第一行为字段名），每行数据生成一个 Word 文档。
"""
import os

from core.response import ok, fail
from .._common import in_out, input_files
from ._excel_common import read_table, list_excels

TOOL_ID = "document_word_template"
CATEGORY = "document"
LABEL = "Word 模板填充"
NEEDS_FILES = True
PARAMS = [
    {"name": "name_field", "label": "用作文件名的列（留空=行号）", "type": "text"},
]


def _replace_in_paragraph(paragraph, mapping):
    full = "".join(r.text for r in paragraph.runs)
    if not full:
        return False
    if not any(k in full for k in mapping):
        return False
    for k, v in mapping.items():
        full = full.replace(k, str(v))
    if paragraph.runs:
        paragraph.runs[0].text = full
        for r in paragraph.runs[1:]:
            r.text = ""
    return True


def _fill_document(doc, mapping):
    changed = False
    for p in doc.paragraphs:
        changed = _replace_in_paragraph(p, mapping) or changed
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    changed = _replace_in_paragraph(p, mapping) or changed
    for section in doc.sections:
        for p in section.header.paragraphs:
            changed = _replace_in_paragraph(p, mapping) or changed
        for p in section.footer.paragraphs:
            changed = _replace_in_paragraph(p, mapping) or changed
    return changed


def _safe(name):
    return "".join(c for c in str(name) if c not in r'\\/:*?"<>|').strip() or "doc"


def run(temp_id, params):
    try:
        import docx
    except ImportError:
        return fail("服务器未安装 python-docx，请联系管理员安装: pip install python-docx", code=503)

    params = params or {}
    in_dir, out_dir = in_out(temp_id)

    templates = [f for f in input_files(in_dir) if f.lower().endswith(".docx")]
    datas = list_excels(in_dir)
    if not templates:
        return fail("请上传一个 .docx 模板", code=400)
    if not datas:
        return fail("请上传一个数据表（xlsx/csv），第一行为字段名", code=400)

    tpl_path = templates[0]
    data_path = datas[0]
    try:
        df = read_table(data_path)
    except Exception as e:
        return fail("读取数据表失败：" + str(e), code=500)

    name_field = (params.get("name_field") or "").strip()
    results = []
    for idx, (_, row) in enumerate(df.iterrows()):
        mapping = {str(k): "" if v != v else v for k, v in row.items()}  # NaN -> 空串
        # 同时支持 {{字段}} 与 {字段} 两种写法
        mapping2 = {"{" + str(k) + "}": str(v) for k, v in mapping.items()}
        mapping.update(mapping2)

        doc = docx.Document(tpl_path)
        _fill_document(doc, mapping)

        fname = str(row.get(name_field, idx + 1)) if name_field else str(idx + 1)
        out_name = _safe(fname) + ".docx"
        out_path = os.path.join(out_dir, out_name)
        doc.save(out_path)
        results.append({"name": out_name, "size": os.path.getsize(out_path)})

    return ok(data={"results": results})
