"""Office 文档转换引擎：Microsoft Office(COM) 与 LibreOffice headless 双后端自动选择。

为什么需要它
------------
「导出 PDF」这类操作必须借助外部渲染引擎，原先代码硬编码成 Windows COM
（``win32com.Dispatch("Word.Application")``），导致：
- Windows 装了 Office  -> 正常
- Windows 没装 Office  -> 直接不可用
- Linux / 容器部署      -> 完全不可用

本模块把引擎差异收口成一个 ``convert_to_pdf()``：运行时探测当前系统装了什么，
优先用 Office COM（保真度最高），没有就自动降级到 LibreOffice headless，
两个都没有时给友好中文提示。业务代码不用再关心底层是哪种引擎。

只有「导出 PDF」需要本模块；读写单元格、删分表等结构化操作走 openpyxl，
本就不需要任何办公软件（见 office_tools/service.py）。

设计约束（项目铁律）
--------------------
- 平台相关重型依赖（win32com / pythoncom）一律函数内惰性导入，禁止模块顶层 import，
  否则会污染模块加载期（曾引发 plugin_scan reload 陷阱导致 toolbox 全站 404）。
- 本模块属 core 层，不 import 任何业务模块（toolbox / office_tools），保持单向依赖。
"""
import os
import shutil
import subprocess
import sys
import tempfile

__all__ = [
    "EngineUnavailable",
    "engine_status",
    "convert_to_pdf",
    "has_any_engine",
    "find_soffice",
]


class EngineUnavailable(RuntimeError):
    """没有任何可用的文档转换引擎（Office COM 与 LibreOffice 都不可用/都失败）。

    继承 RuntimeError，使业务路由里已有的 ``except RuntimeError -> fail(503)`` 能接住。
    """


# 扩展名 -> Microsoft Office COM ProgID
_PROG_IDS = {
    ".doc": "Word.Application",
    ".docx": "Word.Application",
    ".rtf": "Word.Application",
    ".odt": "Word.Application",
    ".ppt": "PowerPoint.Application",
    ".pptx": "PowerPoint.Application",
    ".odp": "PowerPoint.Application",
    ".xls": "Excel.Application",
    ".xlsx": "Excel.Application",
    ".xlsm": "Excel.Application",
    ".ods": "Excel.Application",
}

_SOFFICE_NAMES = ("soffice", "libreoffice", "soffice.exe", "libreoffice.exe")
_SOFFICE_WIN_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

_UNSET = object()
_soffice_cache = _UNSET


# ---------------------------------------------------------------- 引擎探测

def find_soffice():
    """定位 LibreOffice 可执行文件；找不到返回 None。

    探测顺序：环境变量 SOFFICE_PATH -> PATH -> Windows 常见安装目录。
    结果缓存（首次命中后不再重复扫描磁盘）。
    """
    global _soffice_cache
    if _soffice_cache is not _UNSET:
        return _soffice_cache or None

    found = None
    env_path = os.environ.get("SOFFICE_PATH")
    if env_path and os.path.isfile(env_path):
        found = env_path
    if not found:
        for name in _SOFFICE_NAMES:
            p = shutil.which(name)
            if p:
                found = p
                break
    if not found and sys.platform == "win32":
        for p in _SOFFICE_WIN_PATHS:
            if os.path.isfile(p):
                found = p
                break

    _soffice_cache = found or ""
    return found


def _office_registered(prog_id):
    """该 COM ProgID 是否在注册表里注册过（= Office 装没装）。

    刻意只查注册表而不是真去 Dispatch：Dispatch 会真的启动 Word/Excel 进程，
    在环境自检这种高频调用里太慢。真正的启动留给转换时，失败再降级。
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id))
        return True
    except Exception:
        return False


def _ms_office_available():
    """本机是否同时具备 pywin32 与 Microsoft Office。"""
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401  仅探测可导入性
    except ImportError:
        return False
    return any(
        _office_registered(p)
        for p in ("Word.Application", "Excel.Application", "PowerPoint.Application")
    )


def engine_status():
    """返回当前引擎可用性，供前端环境自检展示。

    engine: "com" | "libreoffice" | None（None 表示转 PDF 不可用）
    """
    soffice = find_soffice()
    ms_office = _ms_office_available()

    if ms_office:
        engine = "com"
        message = "Microsoft Office（COM）可用，转 PDF 走原生 Office，保真度最佳"
    elif soffice:
        engine = "libreoffice"
        message = ("LibreOffice headless 可用（未检测到 Microsoft Office），"
                   "转 PDF 排版保真度略低于原生 Office")
    else:
        engine = None
        message = (
            "未检测到任何文档转换引擎，转 PDF 不可用。"
            "Windows 请安装 Microsoft Office 或 LibreOffice；"
            "Linux/容器请安装 libreoffice（apt install libreoffice），"
            "或用环境变量 SOFFICE_PATH 指向 soffice 可执行文件。"
            "注：单元格替换（.xlsx/.xlsm）、删除分表（.xlsx/.xlsm）走 openpyxl，不受影响。"
        )

    return {
        "platform": sys.platform,
        "ms_office": ms_office,
        "libreoffice": soffice,
        "has_win32com": ms_office,     # 前端已用此字段，保留语义：Office COM 是否可用
        "has_libreoffice": bool(soffice),
        "engine": engine,
        "message": message,
    }


def has_any_engine():
    """是否至少有一个引擎可用。"""
    return engine_status()["engine"] is not None


# ---------------------------------------------------------------- 转换实现

def _file_url(path):
    """把本地路径转成 LibreOffice -env:UserInstallation 需要的 file:// URL。"""
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p              # Windows: C:/xxx -> /C:/xxx
    return "file://" + p         # file:// + /C:/xxx = file:///C:/xxx


def _lo_profile_dir():
    """每个进程一个 LibreOffice 用户配置目录，避免多进程抢同一份 profile 锁。"""
    d = os.path.join(tempfile.gettempdir(), "lo_profile_%d" % os.getpid())
    os.makedirs(d, exist_ok=True)
    return d


def _convert_via_libreoffice(soffice, src_path, out_dir):
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--invisible",
        "-env:UserInstallation=" + _file_url(_lo_profile_dir()),
        "--convert-to", "pdf",
        "--outdir", out_dir,
        src_path,
    ]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip()
        raise EngineUnavailable(
            "LibreOffice 退出码 %d：%s" % (proc.returncode, tail[-300:])
        )


def _convert_via_com(src_path, out_path, prog_id):
    import win32com.client as win32
    import pythoncom

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32.Dispatch(prog_id)
        if prog_id.startswith("Word"):
            app.Visible = False
            doc = app.Documents.Open(src_path)
            doc.SaveAs(out_path, FileFormat=17)      # 17 = wdFormatPDF
            doc.Close()
        elif prog_id.startswith("PowerPoint"):
            pres = app.Presentations.Open(src_path, WithWindow=False)
            pres.SaveAs(out_path, 32)                # 32 = ppSaveAsPDF
            pres.Close()
        else:                                        # Excel
            app.Visible = False
            app.DisplayAlerts = False
            wb = app.Workbooks.Open(src_path)
            wb.ExportAsFixedFormat(0, out_path)      # 0 = xlTypePDF
            wb.Close()
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def convert_to_pdf(src_path, out_dir=None):
    """把一个 Office 文档转成 PDF，返回生成的 pdf 绝对路径。

    引擎选择：Windows + 已注册 Office -> COM；否则 -> LibreOffice headless。
    前者失败会自动尝试后者；两个都不可用/都失败时抛 EngineUnavailable。

    out_dir 省略时输出到源文件同目录。
    """
    src_path = os.path.abspath(src_path)
    if not os.path.isfile(src_path):
        raise FileNotFoundError("文件不存在：%s" % src_path)

    out_dir = os.path.abspath(out_dir or os.path.dirname(src_path))
    os.makedirs(out_dir, exist_ok=True)

    ext = os.path.splitext(src_path)[1].lower()
    prog_id = _PROG_IDS.get(ext)
    if not prog_id:
        raise ValueError("不支持转换的文件格式：%s" % (ext or "(无扩展名)"))

    out_path = os.path.join(
        out_dir, os.path.splitext(os.path.basename(src_path))[0] + ".pdf"
    )

    errors = []

    if sys.platform == "win32" and _office_registered(prog_id):
        try:
            _convert_via_com(src_path, out_path, prog_id)
            if os.path.isfile(out_path):
                return out_path
            errors.append("Office COM 调用成功但未生成 PDF")
        except Exception as e:
            errors.append("Office COM 失败：%s" % e)

    soffice = find_soffice()
    if soffice:
        try:
            _convert_via_libreoffice(soffice, src_path, out_dir)
            if os.path.isfile(out_path):
                return out_path
            errors.append("LibreOffice 执行成功但未生成 PDF")
        except Exception as e:
            errors.append("LibreOffice 失败：%s" % e)

    if errors:
        # 有引擎但都失败了：报具体失败原因
        raise EngineUnavailable("；".join(errors))
    # 压根没有引擎：给安装指引，别让用户对着一句「失败」发呆
    raise EngineUnavailable(engine_status()["message"])
