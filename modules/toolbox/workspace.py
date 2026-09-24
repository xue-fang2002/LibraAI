"""
工作区（Phase 2 工作区上传 / 浏览器上传 C 模式）

定位：每个登录用户一个**持久文件池**，跨工具、跨会话复用。
与现有一次性 `temp_id` 会话并存：工作区只负责存/取文件，
处理链路（handlers.dispatch）完全不动，取用文件时在前端转成 File 后走原上传链路。

目录：temp/toolbox_workspace/<user_id>/
"""
import os
from flask import current_app, g

MAX_FILES = 200          # 单个用户工作区最多文件数
MAX_FILE_MB = 100        # 单文件大小上限（MB）
MAX_TOTAL_MB = 500       # 工作区总大小上限（MB）


def get_root():
    """工作区根目录（项目自己的 temp 下）。

    ⚠️ 历史 bug：这里曾经写成 os.path.dirname(current_app.root_path)，
    由于 root_path 本身就是项目根目录（D:\\xianmufile），再退一级会把工作区
    落到项目外的 D:\\temp，导致文件“存了却不在项目里”、备份/清理全都漏掉。
    现改为直接使用项目根目录，并对历史目录做一次迁移。
    """
    return os.path.join(current_app.root_path, "temp", "toolbox_workspace")


_MIGRATED = False


def _migrate_legacy_root():
    """把历史（项目外）工作区里的数据搬回本项目目录。

    - 只做一次（进程内标记），且全程异常安全：迁移失败不影响正常使用。
    - 优先 os.replace（同一盘符，纯改名，不复制数据）；失败退化为复制，
      源文件保留、人工可兜底，绝不丢文件。
    """
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    try:
        new_root = get_root()
        legacy_root = os.path.join(
            os.path.dirname(current_app.root_path), "temp", "toolbox_workspace"
        )
        if os.path.abspath(legacy_root) == os.path.abspath(new_root):
            return
        if not os.path.isdir(legacy_root):
            return
        import shutil

        for owner in os.listdir(legacy_root):
            src_dir = os.path.join(legacy_root, owner)
            if not os.path.isdir(src_dir):
                continue
            names = [n for n in os.listdir(src_dir) if os.path.isfile(os.path.join(src_dir, n))]
            if not names:
                continue
            dst_dir = os.path.join(new_root, owner)
            os.makedirs(dst_dir, exist_ok=True)
            for name in names:
                src, dst = os.path.join(src_dir, name), _unique_path(dst_dir, name)
                try:
                    os.replace(src, dst)
                except OSError:
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass
    except Exception:
        pass


def _resolve_uid(uid=None):
    """解析工作区所属用户。

    优先用显式传入的 uid（异步/后台执行时当前请求上下文里没有 g.current_user，
    必须由调用方显式带上，否则会落到 anonymous 目录导致文件找不到或被串用）；
    未传时回退到 g.current_user（普通 HTTP 请求场景）。
    用户标识只保留字母数字与 -_，杜绝路径穿越。
    """
    if uid:
        raw = str(uid)
    else:
        user = getattr(g, "current_user", None) or {}
        raw = str(user.get("id") or "anonymous")
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))
    return clean or "anonymous"


def get_user_dir(create=True, uid=None):
    """用户的工作区目录；uid 可显式指定，否则取当前登录用户。"""
    d = os.path.join(get_root(), _resolve_uid(uid))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def safe_name(name):
    """仅取文件名部分，去掉任何目录成分。"""
    return os.path.basename(str(name or ""))


def _unique_path(directory, filename):
    """同名文件自动加后缀，避免覆盖（a.txt -> a_2.txt）。"""
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    i = 2
    while True:
        candidate = os.path.join(directory, f"{stem}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def total_size(uid=None):
    d = get_user_dir(create=False, uid=uid)
    if not os.path.isdir(d):
        return 0
    return sum(
        os.path.getsize(os.path.join(d, n))
        for n in os.listdir(d)
        if os.path.isfile(os.path.join(d, n))
    )


def list_files(uid=None):
    """返回 [{name, size, mtime}]，按名称排序。"""
    _migrate_legacy_root()
    d = get_user_dir(create=False, uid=uid)
    if not os.path.isdir(d):
        return []
    items = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            st = os.stat(p)
            items.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return items


def save_file(file_storage, uid=None):
    """保存一个 Werkzeug FileStorage，返回落盘后的文件名（可能带 _2 后缀）。"""
    directory = get_user_dir(uid=uid)
    filename = safe_name(file_storage.filename)
    if not filename:
        return None

    # 容量校验
    if len(list_files()) >= MAX_FILES:
        raise ValueError(f"工作区文件数已达上限（{MAX_FILES} 个）")
    if total_size() / 1024 / 1024 >= MAX_TOTAL_MB:
        raise ValueError(f"工作区容量已达上限（{MAX_TOTAL_MB}MB）")

    path = _unique_path(directory, filename)
    file_storage.save(path)

    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb > MAX_FILE_MB:
        try:
            os.remove(path)
        except OSError:
            pass
        raise ValueError(f"单个文件超过 {MAX_FILE_MB}MB 上限")

    return os.path.basename(path)


def delete_file(name, uid=None):
    """删除工作区文件。

    优先直接删除；某些环境（沙箱 / 无回收站的受限运行时）会禁掉 os.remove，
    此时退化为「移动到 _trash 子目录」的软删除，保证功能在任何环境都可用。
    """
    import time
    filename = safe_name(name)
    d = get_user_dir(create=False, uid=uid)
    path = os.path.join(d, filename)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except Exception:
        trash = os.path.join(d, "_trash")
        os.makedirs(trash, exist_ok=True)
        os.replace(path, os.path.join(trash, f"{int(time.time())}_{filename}"))
    return True


def file_path(name, uid=None):
    filename = safe_name(name)
    path = os.path.join(get_user_dir(create=False, uid=uid), filename)
    return path if os.path.isfile(path) else None
