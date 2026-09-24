"""截图 OCR（粘贴 / 上传的截图）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files, ocr_with_tesseract

TOOL_ID = "recognition_ocr_screenshot"
CATEGORY = "recognition"
LABEL = "截图 OCR"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    in_dir, _ = in_out(temp_id)
    files = input_files(in_dir)
    if not files:
        return fail("请先粘贴或上传截图", code=400)
    lines = []
    for fpath in files:
        try:
            lines.append(ocr_with_tesseract(fpath) or "（未识别到文字）")
        except RuntimeError as e:
            return fail(str(e), code=503)
        except Exception as e:
            lines.append(f"{os.path.basename(fpath)} 识别失败: {e}")
    return ok(data={"result": "\n".join(lines)})
