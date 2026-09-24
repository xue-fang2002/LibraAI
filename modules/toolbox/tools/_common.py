"""工具箱共享 helper。

所有具体工具（tools/<分类>/<工具>.py）都从这里取公共能力，避免重复实现，
也让 handlers.py 从 1258 行单体里逐步瘦身。

设计约束：
- 本模块**不反向 import handlers**，杜绝循环依赖（handlers → tools → _common 单向）
- 无请求上下文依赖（get_temp_dir 首次调用时才需要 current_app）
- 工具统一契约 run(temp_id, params) -> ok()/fail()，产物写 temp_id/out

迁移期 handlers.py 会把这些函数以原名别名导入，旧 handler 一行都不用改。
"""
import os

from core.response import ok, fail

TEMP_DIR = None

_CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def get_temp_dir():
    """工具箱临时目录（与 temp/toolbox 一致）。"""
    global TEMP_DIR
    if TEMP_DIR is None:
        from flask import current_app
        TEMP_DIR = os.path.join(os.path.dirname(current_app.root_path), "temp", "toolbox")
    return TEMP_DIR


def safe_temp_id(temp_id):
    """把外部传入的 temp_id 规整成临时目录下的**一级目录名**，杜绝路径穿越。

    temp_id 直接来自请求体（api_process 的 JSON）。裸拼进 os.path.join 是危险的：
      - 绝对路径（"D:/机密" 或 "\\\\host\\share"）会让 os.path.join **丢弃基目录**，
        工具随即去该目录 input_files() 并把文件内容回显到响应里；
      - "../.." 能跳出 temp 根目录；
      - 还会用 makedirs 在任意可写位置建 out/ 子目录。
    这里统一取 basename（既挡绝对路径也挡 ..），非法输入返回 ""（落到临时目录根，
    后续 input_files 自然取不到文件，不会误伤）。
    """
    name = os.path.basename(str(temp_id or "").replace("\\", "/"))
    if not name or name in (".", ".."):
        return ""
    return name


def in_out(temp_id):
    """返回 (输入目录, 输出目录)；输出目录保证存在。"""
    in_dir = os.path.join(get_temp_dir(), safe_temp_id(temp_id))
    out_dir = os.path.join(in_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    return in_dir, out_dir


def input_files(in_dir):
    """列出输入目录里的用户文件（按名排序，只取文件不含子目录）。

    ⚠️ 下划线开头的文件一律跳过 —— 那是工具箱自己写的元数据（如 _upload_order.json），
    不是用户上传的内容。上传落盘名是 "uuidhex_原名"，不会以下划线开头，故不会误伤。
    """
    return [
        os.path.join(in_dir, f) for f in sorted(os.listdir(in_dir or ""))
        if os.path.isfile(os.path.join(in_dir, f)) and not f.startswith("_")
    ] if in_dir and os.path.isdir(in_dir) else []


def ordered_files(in_dir):
    """按**上传顺序**返回输入文件路径。

    input_files() 是按文件名排序的，而上传时文件名被 uuid 重命名，排序后顺序随机 ——
    「两表匹配」「名单比对」这类需要区分「第一个表 / 第二个表」的工具就无法工作。
    上传接口会把真实顺序写进 _upload_order.json，这里读取并还原；
    没有该文件（例如工作区直传）时回落到排序顺序，行为与以前一致。
    """
    fallback = input_files(in_dir)
    if not in_dir:
        return fallback
    order_path = os.path.join(in_dir, "_upload_order.json")
    if not os.path.isfile(order_path):
        return fallback
    try:
        import json
        with open(order_path, "r", encoding="utf-8") as f:
            names = json.load(f)
    except Exception:
        return fallback
    out = []
    for n in names or []:
        p = os.path.join(in_dir, os.path.basename(str(n)))
        if os.path.isfile(p):
            out.append(p)
    # 兜底：把 order 里漏掉的文件（理论上不会）按名补在后面
    known = {os.path.basename(p) for p in out}
    out.extend([p for p in fallback if os.path.basename(p) not in known])
    return out


def cjk_font(size):
    """加载一个支持中文的字体；找不到就回退 PIL 默认字体（中文会显示方块）。"""
    from PIL import ImageFont
    for path in _CJK_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def ocr_with_tesseract(image_path, lang="chi_sim+eng"):
    """调用 Tesseract 做 OCR。

    引擎路径：默认走 PATH；若安装在别处，可设置环境变量 TESSERACT_CMD 指向
    tesseract.exe（例如 D:\\Tools\\Tesseract-OCR\\tesseract.exe）。
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError("未安装 pytesseract/Pillow：pip install pytesseract")

    cmd = os.environ.get("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        with Image.open(image_path) as im:
            return pytesseract.image_to_string(im, lang=lang).strip()
    except Exception as e:
        # 引擎缺失/找不到/语言包缺失，统一转成友好中文提示（对外返回 503）
        if type(e).__name__ == "TesseractNotFoundError" or "tesseract" in str(e).lower():
            raise RuntimeError(
                "未检测到 Tesseract-OCR 引擎（或中文语言包缺失）。"
                "请先安装 Tesseract-OCR；若不在默认路径，设置环境变量 TESSERACT_CMD 指向 tesseract.exe。"
            )
        raise


def office_to_pdf(files, out_dir, prog_id=None, ext=".pdf"):
    """把 Office 文档批量转成 PDF，引擎自动选择（Office COM / LibreOffice headless）。

    原先硬编码 Windows COM，导致没装 Office 的机器（含 Linux 部署）完全不可用。
    现在统一走 core.office_engine：Windows+Office 用 COM，否则降级 LibreOffice，
    两个都没有时直接返回 503 + 安装指引，不再让用户看到莫名其妙的失败。

    prog_id: 兼容保留（"Word.Application" / "PowerPoint.Application"），
             实际引擎由 core.office_engine 按文件扩展名自动推断。
    ext:     目标扩展名，目前仅支持 ".pdf"。
    """
    if not files:
        return fail("未找到可转换的文件", code=400)

    from core.office_engine import convert_to_pdf, engine_status

    status = engine_status()
    if not status.get("engine"):
        return fail(status.get("message") or "无可用文档转换引擎", code=503)

    results = []
    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            out_path = convert_to_pdf(fpath, out_dir)
            results.append({"name": os.path.basename(out_path),
                            "size": os.path.getsize(out_path)})
        except Exception as e:
            results.append({"name": os.path.splitext(fname)[0] + ext, "error": str(e)})
    return ok(data={"results": results})
