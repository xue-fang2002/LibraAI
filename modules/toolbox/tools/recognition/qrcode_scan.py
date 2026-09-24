"""识别图片中的二维码 / 条形码（OpenCV）。"""
import os

from core.response import ok, fail
from .._common import in_out, input_files

TOOL_ID = "recognition_qrcode_scan"
CATEGORY = "recognition"
LABEL = "识别二维码"
NEEDS_FILES = True
PARAMS = []


def run(temp_id, params):
    try:
        import cv2
    except ImportError:
        return fail("服务器未安装 OpenCV: pip install opencv-python", code=503)

    in_dir, _ = in_out(temp_id)
    det = cv2.QRCodeDetector()
    lines = []
    found = 0
    for fpath in input_files(in_dir):
        fname = os.path.basename(fpath)
        try:
            img = cv2.imread(fpath)
            if img is None:
                lines.append(f"{fname}: 无法读取图片")
                continue
            data, pts, _ = det.detectAndDecode(img)
            if data:
                found += 1
                lines.append(f"{fname}: {data}")
            else:
                lines.append(f"{fname}: 未识别到二维码")
        except Exception as e:
            lines.append(f"{fname}: {e}")
    if not lines:
        return fail("未找到可识别的图片", code=400)
    return ok(data={"result": "\n".join(lines), "count": found})
