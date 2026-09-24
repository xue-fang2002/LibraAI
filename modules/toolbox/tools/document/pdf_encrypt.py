"""PDF 加密 / 解密（依赖 pikepdf）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "document_pdf_encrypt"
CATEGORY = "document"
LABEL = "PDF 加密/解密"
NEEDS_FILES = True
PARAMS = [
    {"name": "action", "label": "操作", "type": "enum",
     "options": ["encrypt", "decrypt"], "default": "encrypt"},
    {"name": "password", "label": "密码", "type": "text"},
]


def run(temp_id, params):
    try:
        import pikepdf
    except ImportError:
        return fail("服务器未安装 pikepdf，请联系管理员安装: pip install pikepdf", code=503)

    params = params or {}
    action = params.get("action") or "encrypt"
    pwd = params.get("password") or ""
    if not pwd:
        return fail("请填写密码", code=400)

    in_dir, out_dir = in_out(temp_id)
    results = []
    for fpath in input_files(in_dir):
        if not fpath.lower().endswith(".pdf"):
            continue
        base = os.path.splitext(os.path.basename(fpath))[0]
        try:
            with pikepdf.open(fpath) as pdf:
                if action == "encrypt":
                    out_name = base + "_encrypted.pdf"
                    pdf.save(os.path.join(out_dir, out_name),
                             encryption=pikepdf.Encryption(owner=pwd, user=pwd, R=4))
                else:
                    out_name = base + "_decrypted.pdf"
                    try:
                        pdf.save(os.path.join(out_dir, out_name))
                    except pikepdf.PasswordError:
                        # 先尝试用密码解锁
                        pdf = pikepdf.open(fpath, password=pwd)
                        pdf.save(os.path.join(out_dir, out_name))
                results.append({"name": out_name,
                                "size": os.path.getsize(os.path.join(out_dir, out_name))})
        except pikepdf.PasswordError:
            results.append({"name": os.path.basename(fpath), "error": "密码错误"})
        except Exception as e:
            results.append({"name": os.path.basename(fpath), "error": str(e)})

    if not results:
        return fail("未找到可处理的 PDF 文件", code=400)
    return ok(data={"results": results})
