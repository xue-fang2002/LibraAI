"""安全护栏：路径白名单 / SQL 只读校验 / 审计。

运维助手本质上是个「能碰生产环境的 AI」，所以所有能力都必须先过这一层。
护栏设计原则：**默认拒绝，显式放行**。

1) 文件：只允许项目根以内的路径；二进制 / 模型 / 数据库 / 密钥配置一律拒绝；
   大文件只读片段。
2) 数据库：sqlite 以 `mode=ro` 只读连接；只允许单条 SELECT / WITH；
   禁注释、禁多语句、禁 DDL/DML/ATTACH/PRAGMA；强制 LIMIT；带执行超时。
3) 审计：每次工具调用都落一条日志（文件 logger + 库 operation_logs，库写失败不影响主流程）。
"""
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 文件护栏

# 任意层级命中即拒绝（模型、索引、浏览器、临时产物等大目录/无关目录）
BLACKLIST_DIRS = {
    ".git", "models", "ai_vector_store", "vector_data", "_pw_browsers",
    "_cleanup", "_shots", "db_export", "__pycache__", "node_modules",
    "temp", "_backup", "backup",
}

# 敏感文件：含密钥或全量业务数据，绝不喂给模型
SENSITIVE_FILES = {
    "modules.yaml", "app.yaml", "book_manager.db", ".env", ".env.local",
    "modules.yaml.example",
}

# 后缀黑名单：二进制 / 模型 / 归档 / 图片
BLACKLIST_EXTS = {
    ".db", ".sqlite", ".sqlite3", ".gguf", ".safetensors", ".bin", ".pt",
    ".pkl", ".faiss", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip",
    ".rar", ".7z", ".pdf", ".docx", ".xlsx", ".pptx", ".exe", ".dll", ".so",
}

MAX_FILE_BYTES = 512 * 1024      # 单文件超过此大小只读片段
MAX_READ_LINES = 200             # 单次读取行数上限
MAX_READ_CHARS = 12000           # 单次读取字符上限

# ---------------------------------------------------------------- SQL 护栏

SQL_MAX_ROWS = 200               # 返回行数硬上限
SQL_TIMEOUT_SEC = 3.0            # 单条查询执行超时
SQL_MAX_CELL_CHARS = 200         # 单格内容截断长度

_FORBIDDEN_SQL_WORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex", "begin", "commit",
    "rollback", "trigger", "grant", "revoke",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_SQL_WORDS) + r")\b", re.I)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.I)


def _logger():
    from core.logger import get_logger
    return get_logger("xianmufile.ai_staff")


# ---------------------------------------------------------------- 审计

def audit(user: Optional[Dict[str, Any]], action: str, detail: str = "",
          target: str = "") -> None:
    """审计：文件 logger 必写；库审计失败不影响主流程。"""
    who = ""
    if isinstance(user, dict):
        who = user.get("account") or user.get("name") or str(user.get("id") or "")
    line = f"[ai_staff] user={who or '-'} action={action} target={target or '-'} {detail}"
    try:
        _logger().info(line)
    except Exception:
        print(line)
    try:
        from core.db_base import add_log
        add_log(
            (user or {}).get("id"),
            f"ai_staff.{action}",
            target_type="ai_staff",
            target_id=target,
            detail=detail[:1000],
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 路径校验

def _split_parts(rel: str) -> List[str]:
    rel = (rel or "").replace("\\", "/")
    return [p for p in rel.split("/") if p not in ("", ".")]


def resolve_path(root: str, rel: str) -> Tuple[Optional[str], str]:
    """把相对路径解析为项目根以内的绝对路径。

    返回 (abs_path, "")，拒绝时返回 (None, 原因)。
    """
    parts = _split_parts(rel)
    if any(p == ".." for p in parts):
        return None, "路径不允许包含 .."
    for p in parts[:-1] if parts else []:
        if p in BLACKLIST_DIRS:
            return None, f"目录在黑名单中：{p}"
    name = parts[-1] if parts else ""
    if name and name in SENSITIVE_FILES:
        return None, f"敏感文件，已拒绝读取：{name}"
    if name and os.path.splitext(name)[1].lower() in BLACKLIST_EXTS:
        return None, f"二进制/归档/模型文件，已拒绝读取：{name}"

    abs_path = os.path.abspath(os.path.join(root, *parts)) if parts else os.path.abspath(root)
    root_abs = os.path.abspath(root)
    try:
        if os.path.commonpath([os.path.realpath(abs_path), os.path.realpath(root_abs)]) != os.path.realpath(root_abs):
            return None, "路径超出项目根目录"
    except ValueError:
        return None, "路径解析失败"
    return abs_path, ""


def is_blacklisted_path(abs_path: str, root: str) -> bool:
    """判断绝对路径（相对项目根）是否落在黑名单目录里。"""
    try:
        rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(root))
    except ValueError:
        return True
    parts = _split_parts(rel)
    return any(p in BLACKLIST_DIRS for p in parts)


def safe_read_text(abs_path: str, start_line: int = 1, end_line: int = 0,
                   max_lines: int = MAX_READ_LINES) -> Dict[str, Any]:
    """按行区间读文本，自动编码兜底 + 硬截断。"""
    try:
        size = os.path.getsize(abs_path)
    except Exception as e:
        return {"ok": False, "error": f"无法访问文件：{e}"}
    if size > 8 * 1024 * 1024:
        return {"ok": False, "error": f"文件过大（{size} 字节），请指定行区间"}
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"ok": False, "error": f"读取失败：{e}"}

    total = len(lines)
    s = max(1, int(start_line or 1))
    e = int(end_line or 0) or (s + max_lines - 1)
    e = min(e, s + max_lines - 1, total)
    chunk = "".join(lines[s - 1:e])
    truncated = len(chunk) > MAX_READ_CHARS
    if truncated:
        chunk = chunk[:MAX_READ_CHARS]
    return {
        "ok": True,
        "path": abs_path,
        "total_lines": total,
        "from_line": s,
        "to_line": e,
        "content": chunk,
        "truncated": truncated,
    }


# ---------------------------------------------------------------- SQL 校验

def validate_select(sql: str) -> Tuple[Optional[str], str]:
    """校验并规范化一条只读 SQL。返回 (safe_sql, "") 或 (None, 原因)。"""
    raw = (sql or "").strip().rstrip(";").strip()
    if not raw:
        return None, "SQL 为空"
    if ";" in raw:
        return None, "不允许一次执行多条语句（含分号）"
    if "--" in raw or "/*" in raw:
        return None, "不允许 SQL 注释"
    low = raw.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return None, "只允许 SELECT / WITH 查询"
    m = _FORBIDDEN_RE.search(raw)
    if m:
        return None, f"包含被禁止的关键字：{m.group(1)}"

    lm = _LIMIT_RE.search(raw)
    if lm:
        n = int(lm.group(1))
        if n > SQL_MAX_ROWS:
            raw = raw[:lm.start()] + f"LIMIT {SQL_MAX_ROWS}" + raw[lm.end():]
    else:
        raw = raw + f" LIMIT {SQL_MAX_ROWS}"
    return raw, ""


def run_readonly_sql(db_path: str, sql: str) -> Dict[str, Any]:
    """以只读连接执行一条 SELECT。任何异常都被收成结构化错误返回给模型。"""
    safe, err = validate_select(sql)
    if not safe:
        return {"ok": False, "error": err, "sql": sql}
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"数据库文件不存在：{db_path}"}

    uri = "file:" + db_path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    deadline = time.time() + SQL_TIMEOUT_SEC

    def _progress():
        return 1 if time.time() > deadline else 0

    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.set_progress_handler(_progress, 1000)
        cur = conn.execute(safe)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(SQL_MAX_ROWS + 1)
        truncated = len(rows) > SQL_MAX_ROWS
        rows = rows[:SQL_MAX_ROWS]
        out = []
        for r in rows:
            cells = []
            for v in r:
                if isinstance(v, bytes):
                    v = f"<{len(v)} bytes>"
                s = str(v)
                cells.append(s[:SQL_MAX_CELL_CHARS])
            out.append(cells)
        return {
            "ok": True,
            "columns": cols,
            "rows": out,
            "row_count": len(out),
            "truncated": truncated,
            "sql": safe,
        }
    except Exception as e:
        # sqlite3 被 progress handler 中断时抛 OperationalError("interrupted")
        msg = str(e)
        if "interrupted" in msg.lower():
            msg = f"查询超时（>{SQL_TIMEOUT_SEC}s），请加 WHERE 缩小范围"
        return {"ok": False, "error": msg, "sql": safe}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
