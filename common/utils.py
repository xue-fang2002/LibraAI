"""通用工具函数"""

import os
import re
import sys
import hashlib
from datetime import datetime
from pathlib import Path
from flask import request

from core.config_loader import get_config

# ===================== 路径常量 =====================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UPLOAD_FOLDER = os.path.join(BASE_DIR, *get_config("uploads.folder", "uploads/books").split("/"))
MAX_FILE_SIZE = int(get_config("uploads.max_file_size", 50 * 1024 * 1024))
ALLOWED_EXTENSIONS = set(get_config("uploads.allowed_extensions", [
    'pdf','epub','mobi','azw','azw3','doc','docx','xls','xlsx','ppt','pptx',
    'zip','rar','7z','txt','csv','log','htm','html',
    'jpg','jpeg','png','gif','bmp','cbz','cbr',
    'mp4','avi','mov','dwg','dxf','scl','awl','l5x','step','stp',
]))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ===================== 文件工具 =====================
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_full_path(relative_path: str) -> str:
    try:
        return os.path.normpath(os.path.join(BASE_DIR, relative_path))
    except Exception:
        return ""


def lighten_color(hex_color: str, light_ratio: float = 0.8) -> str:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = int(r + (255 - r) * light_ratio)
    g = int(g + (255 - g) * light_ratio)
    b = int(b + (255 - b) * light_ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def is_safe_path(requested_path: str, base_path: str) -> bool:
    """防止路径遍历攻击：用路径层级判定，而非字符串前缀。"""
    try:
        real_requested = Path(requested_path).resolve()
        real_base = Path(base_path).resolve()
        # real_requested 必须等于 base，或位于 base 的子路径中（resolve 已解析符号链接）
        return real_requested == real_base or real_base in real_requested.parents
    except (ValueError, OSError):
        return False


# Windows 设备保留名（不能作为目录/文件名）
_DIR_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def _safe_dirname(name: str, default: str = "未分类", max_len: int = 50) -> str:
    """把分类名清洗为安全的目录名，挡掉路径穿越与 Windows 保留名。
    """
    name = "".join(c for c in (name or default) if c not in '\\/:*?"<>|').strip()
    if name in {".", ".."} or not name:
        name = default
    if name.upper() in _DIR_RESERVED_NAMES:
        name = default
    return name[:max_len]


def get_upload_save_path(filename, cat1="", cat2=""):
    """按分类分目录存储文件，返回 (save_path, rel_path)。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_c1 = _safe_dirname(cat1)
    safe_c2 = _safe_dirname(cat2)
    target_dir = os.path.join(UPLOAD_FOLDER, safe_c1, safe_c2)
    os.makedirs(target_dir, exist_ok=True)
    safe_name = filename
    for ch in '\\/:*?"<>|':
        safe_name = safe_name.replace(ch, '_')
    if not safe_name or safe_name.strip() in ['.', '']:
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        safe_name = f"file_{timestamp}.{ext}" if ext else f"file_{timestamp}"
    unique_name = f"{timestamp}_{safe_name}"
    save_path = os.path.join(target_dir, unique_name)
    if os.path.isabs(UPLOAD_FOLDER) or UPLOAD_FOLDER.startswith('\\'):
        try:
            rel_path = os.path.relpath(save_path, UPLOAD_FOLDER).replace("\\", "/")
            rel_path = f"__upload__/{rel_path}"
        except ValueError:
            rel_path = f"__upload__/{safe_c1}/{safe_c2}/{unique_name}"
    else:
        rel_path = os.path.relpath(save_path, BASE_DIR).replace("\\", "/")
    return save_path, rel_path


def resolve_file_path(rel_path):
    """解析文件路径并做安全检查，返回 (full_path, error_msg)。"""
    if not rel_path:
        return None, "路径参数缺失"
    if rel_path.startswith("__upload__"):
        sub_path = rel_path[len("__upload__"):].lstrip("/")
        full = os.path.normpath(os.path.join(UPLOAD_FOLDER, sub_path))
        if not is_safe_path(full, UPLOAD_FOLDER):
            return None, "非法路径"
    else:
        full = get_full_path(rel_path)
        if not is_safe_path(full, BASE_DIR):
            return None, "非法路径"
    if not os.path.exists(full):
        return None, "文件不存在"
    return full, None


def is_allowed_upload_path(rel_path, allowed_exts=None):
    """图书/资料路径白名单校验，返回 (full_path, error_msg)；err 为 None 表示通过。

    config/app.yaml 当图书下载出去。这里做三件事：
      ① 显式拒绝绝对路径 / 盘符 / UNC；
      ② 把允许范围从 BASE_DIR 收紧到 UPLOAD_FOLDER；
      ③ 可选校验扩展名。
    """
    if not rel_path:
        return None, "路径参数缺失"
    v = str(rel_path).strip()
    if os.path.isabs(v) or re.match(r'^[A-Za-z]:', v) or v.startswith(("/", "\\")):
        return None, "路径必须是相对路径"
    full, err = resolve_file_path(v)
    if err:
        return None, err
    if not is_safe_path(full, UPLOAD_FOLDER):
        return None, "路径不在允许的上传目录内"
    if allowed_exts:
        ext = os.path.splitext(full)[1].lower().lstrip(".")
        if ext and ext not in allowed_exts:
            return None, "不允许的文件类型"
    return full, None


def extract_original_filename(full_path):
    """从保存的文件名（时间戳前缀_原始名）中提取原始文件名。"""
    basename = os.path.basename(full_path)
    match = re.match(r'^\d{8}_\d{6}_\d+_(.+)$', basename)
    if match:
        return match.group(1)
    match2 = re.match(r'^\d{8}_\d{6}_(.+)$', basename)
    if match2:
        return match2.group(1)
    return basename


def compute_file_hash(file_path):
    """计算文件 SHA-256 哈希。"""
    try:
        if not file_path or not os.path.exists(file_path):
            return ""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


# ===================== 请求 & 校验工具 =====================
def _trust_proxy() -> bool:
    """是否部署在可信反向代理之后（默认否）。

    只有明确声明时才采信 X-Forwarded-For，声明方式二选一：
    Flask 配置 TRUST_PROXY=1，或环境变量 TRUST_PROXY=1。
    """
    v = None
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            v = current_app.config.get("TRUST_PROXY")
    except Exception:
        v = None
    if v is None:
        v = os.environ.get("TRUST_PROXY", "")
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def get_client_ip() -> str:
    """客户端 IP。

    ⚠️ 默认**不采信** X-Forwarded-For：该请求头完全由客户端伪造，而登录失败
    锁定键是「账号 + IP」（app.py::_login_lock_key），信任它等于允许攻击者
    每次请求换一个 XFF 值，把「失败 5 次锁 10 分钟」彻底绕过，同时污染审计日志。
    确需部署在反向代理后时，显式开启 TRUST_PROXY 才会走原逻辑。
    """
    if _trust_proxy():
        xff = request.headers.get('X-Forwarded-For')
        if xff:
            return xff.split(',')[0].strip()
    return request.remote_addr or ""


def sanitize_text(text: str, max_len: int = 100, allow_spaces: bool = True) -> str:
    """清理输入文本：过滤注入字符、限制长度。"""
    if not text:
        return ""
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len]
    for ch in ['<', '>', '"', "'", '&', ';', '`']:
        text = text.replace(ch, '')
    if not allow_spaces:
        text = text.replace(' ', '')
    return text


def validate_account(account: str):
    """验证账号格式：字母、数字、下划线，3-30字符。"""
    if not account or len(account) < 3 or len(account) > 30:
        return False, "账号长度需3-30个字符"
    if not re.match(r'^[a-zA-Z0-9_]+$', account):
        return False, "账号仅允许字母、数字、下划线"
    return True, ""


def now_str():
    """返回当前本地时间字符串，格式 YYYY-MM-DD HH:MM:SS，和 SQLite datetime('now','localtime') 保持一致。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
