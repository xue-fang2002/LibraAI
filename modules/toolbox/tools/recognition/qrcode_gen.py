"""二维码生成（用 OpenCV 自带编码器，无需额外安装 qrcode 库）。"""
import os

from core.response import ok, fail
from .._common import in_out

TOOL_ID = "recognition_qrcode_gen"
CATEGORY = "recognition"
LABEL = "生成二维码"
NEEDS_FILES = False
PARAMS = [
    {"name": "text", "label": "二维码内容", "type": "text", "required": True},
]


def run(temp_id, params):
    params = params or {}
    text = (params.get("text") or "").strip()
    if not text:
        return fail("内容不能为空", code=400)
    try:
        import cv2
    except ImportError:
        return fail("服务器未安装 OpenCV: pip install opencv-python", code=503)
    try:
        _, out_dir = in_out(temp_id)
        enc = cv2.QRCodeEncoder_create()
        data = text.encode("utf-8")          # 中文按 UTF-8 字节编码
        qr = enc.encode(data)
        scale = 10
        qr = cv2.resize(qr, (qr.shape[0] * scale, qr.shape[1] * scale),
                        interpolation=cv2.INTER_NEAREST)
        pad = 40
        qr = cv2.copyMakeBorder(qr, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
        out_name = "qrcode.png"
        out_path = os.path.join(out_dir, out_name)
        cv2.imwrite(out_path, qr)
        return ok(data={"results": [{"name": out_name,
                                     "size": os.path.getsize(out_path)}]})
    except Exception as e:
        return fail(f"生成失败：{e}", code=500)
