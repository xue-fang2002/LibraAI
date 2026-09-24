"""图片 OCR（需要 Tesseract 引擎）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files, ocr_with_tesseract

TOOL_ID = "recognition_ocr_image"
CATEGORY = "recognition"
LABEL = "图片 OCR"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    in_dir, _ = in_out(temp_id)
    lines = []
    for fpath in input_files(in_dir):
        fname = os.path.basename(fpath)
        try:
            lines.append(f"=== {fname} ===")
            lines.append(ocr_with_tesseract(fpath) or "（未识别到文字）")
        except RuntimeError as e:
            return fail(str(e), code=503)
        except Exception as e:
            lines.append(f"识别失败: {e}")
    if not lines:
        return fail("未找到可识别的图片", code=400)
    return ok(data={"result": "\n".join(lines)})
