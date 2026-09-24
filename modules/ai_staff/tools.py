"""只读工具实现（运维助手的手）。

每个工具都是纯函数：fn(root, ctx, args) -> dict，返回结果会原样作为
Observation 回灌给模型。**工具本身不做权限判断**，权限与开关在 routes / engine
入口统一判定（db_query 例外：它自带 allow_sql 二次校验，双保险）。

⚠️ 所有跨模块 import 都写在函数体内，避免触发 plugin_scan 的 reload 陷阱。
"""
import ast
import difflib
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from . import bazi, guard

MAX_WALK_FILES = 6000        # 代码检索最多遍历的文件数
MAX_SCAN_BYTES = 2 * 1024 * 1024   # 单文件超过 2MB 不参与全文检索
LOG_MAX_LINES = 600          # 日志扫描行数上限

# 未指定 path 时默认只扫这些目录：项目根下还有 temp/ uploads/ 日志等噪音目录，
# 全量扫会先把预算耗光，反而找不到真正的代码。想搜别处，显式传 path 即可。
DEFAULT_SEARCH_DIRS = ["modules", "core", "common", "templates", "static"]


# ============================================================ list_dir
def list_dir(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(args.get("path") or "")
    depth = int(args.get("depth") or 1)
    depth = max(1, min(depth, 3))
    max_entries = int(args.get("max_entries") or 60)
    max_entries = max(5, min(max_entries, 200))

    abs_path, err = guard.resolve_path(root, rel)
    if not abs_path:
        return {"ok": False, "error": err}
    if not os.path.isdir(abs_path):
        return {"ok": False, "error": f"不是目录：{rel or '/'}"}

    entries: List[Dict[str, Any]] = []
    stack = [(abs_path, 1)]
    while stack and len(entries) < max_entries:
        cur, d = stack.pop(0)
        try:
            names = sorted(os.listdir(cur))
        except Exception as e:
            entries.append({"name": os.path.basename(cur), "error": str(e)})
            continue
        dirs, files = [], []
        for n in names:
            full = os.path.join(cur, n)
            if os.path.isdir(full):
                if n in guard.BLACKLIST_DIRS:
                    continue
                dirs.append(n + "/")
                if d < depth:
                    stack.append((full, d + 1))
            else:
                files.append(n)
        r = os.path.relpath(cur, root).replace("\\", "/")
        entries.append({
            "dir": "." if r == "." else r,
            "dirs": dirs[:40],
            "files": files[:60],
        })
    return {"ok": True, "root": rel or ".", "entries": entries[:max_entries]}


# ============================================================ read_file
def read_file(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(args.get("path") or "")
    if not rel:
        return {"ok": False, "error": "缺少 path 参数"}
    abs_path, err = guard.resolve_path(root, rel)
    if not abs_path:
        return {"ok": False, "error": err}
    if not os.path.isfile(abs_path):
        return {"ok": False, "error": f"不是文件或不存在：{rel}"}
    res = guard.safe_read_text(
        abs_path,
        start_line=int(args.get("start_line") or 1),
        end_line=int(args.get("end_line") or 0),
    )
    if res.get("ok"):
        res["path"] = os.path.relpath(abs_path, root).replace("\\", "/")
    return res


# ============================================================ code_search
def code_search(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {"ok": False, "error": "缺少 pattern 参数"}
    rel = str(args.get("path") or "")
    use_regex = bool(args.get("regex"))
    max_hits = int(args.get("max_hits") or 30)
    max_hits = max(1, min(max_hits, 80))

    base, err = guard.resolve_path(root, rel)
    if not base:
        return {"ok": False, "error": err}
    if os.path.isfile(base):
        targets = [base]
    else:
        targets = None  # 走 walk

    try:
        rx = re.compile(pattern, re.I) if use_regex else None
    except Exception as e:
        return {"ok": False, "error": f"正则表达式非法：{e}"}

    hits: List[Dict[str, Any]] = []
    scanned = 0
    names_seen: set = set()      # 扫描到的文件名（小写），未命中时用来给相似候选
    searched_dirs: List[str] = []

    def _scan_file(fp: str, display: str):
        nonlocal scanned
        scanned += 1
        try:
            if os.path.getsize(fp) > MAX_SCAN_BYTES:
                return
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if len(hits) >= max_hits:
                        return
                    ok = bool(rx.search(line)) if rx else (pattern.lower() in line.lower())
                    if ok:
                        hits.append({
                            "file": display,
                            "line": i,
                            "text": line.rstrip()[:300],
                        })
        except Exception:
            return

    if targets:
        _scan_file(targets[0], os.path.relpath(targets[0], root).replace("\\", "/"))
    else:
        if rel:
            bases = [base]
        else:
            bases = [os.path.join(root, d) for d in DEFAULT_SEARCH_DIRS
                     if os.path.isdir(os.path.join(root, d))] or [base]
        for b in bases:
            searched_dirs.append(os.path.relpath(b, root).replace("\\", "/"))
            for dirpath, dirnames, filenames in os.walk(b):
                dirnames[:] = [d for d in dirnames
                               if d not in guard.BLACKLIST_DIRS and not d.startswith("_")]
                for fn in filenames:
                    names_seen.add(fn.lower())
                    if len(hits) >= max_hits or scanned >= MAX_WALK_FILES:
                        break
                    # 与工具箱约定一致：下划线开头的是内部/临时产物，不参与检索
                    if fn.startswith("_") or fn in guard.SENSITIVE_FILES:
                        continue
                    if os.path.splitext(fn)[1].lower() in guard.BLACKLIST_EXTS:
                        continue
                    fp = os.path.join(dirpath, fn)
                    _scan_file(fp, os.path.relpath(fp, root).replace("\\", "/"))
                if len(hits) >= max_hits or scanned >= MAX_WALK_FILES:
                    break
            if len(hits) >= max_hits or scanned >= MAX_WALK_FILES:
                break

    out = {
        "ok": True,
        "pattern": pattern,
        "hits": hits,
        "hit_count": len(hits),
        "files_scanned": scanned,
        "truncated": len(hits) >= max_hits,
    }
    if not hits:
        # 未命中时不能只回一句「没搜到」——那样模型拿不到任何新线索，只会原地重试。
        # 这里回灌：搜过哪些目录、扫了多少文件、相似文件名候选、以及下一步建议。
        out["searched_dirs"] = searched_dirs or [rel or "."]
        out["suggestions"] = _similar_filenames(pattern, names_seen)
        out["hint"] = _miss_hint(pattern, rel)
    return out


def _similar_filenames(pattern: str, names_seen: set, limit: int = 6) -> List[str]:
    """未命中时给几个「可能你想找的是它」的文件名候选（只对英文词根有效）。"""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", pattern or "")
    if not tokens or not names_seen:
        return []
    pool = sorted(names_seen)
    out: List[str] = []
    for tok in tokens[:3]:
        for cand in difflib.get_close_matches(tok.lower(), pool, n=4, cutoff=0.55):
            if cand not in out:
                out.append(cand)
    return out[:limit]


def _miss_hint(pattern: str, rel: str) -> str:
    """未命中时的下一步建议；中文关键词是这里最常见的原因，必须点破。"""
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", pattern or ""))
    if has_cjk:
        return ("没搜到。注意：代码里的标识符、配置项、字段名都是英文，中文关键词几乎搜不到。"
                "请先把中文问题翻译成可能的英文标识符再搜（例如 切片→chunk/max_chars/split，"
                "侧栏→sidebar，权限→permission/access），或先用 list_dir 看目录、read_file 看具体文件。")
    return ("没搜到。建议：换更短的词根（去掉前后缀）、改用 regex:true 做模糊匹配、"
            "把 path 限定到具体子目录，或先用 list_dir 确认文件在哪。")


# ============================================================ 证据校验
# 模型答「定义在 config/settings.py 第 25 行」时，settings.py 可能根本不存在。
# 这里在 final 之前把答案里提到的 文件:行号 全部核一遍，抓编造。
_CITE_RE = re.compile(
    r"([A-Za-z0-9_][\w./\-]*\.(?:py|js|css|html?|ya?ml|json|md|txt|ini|cfg|toml|sh))"
    r"(?:\s*[:：]\s*(\d+))?"
)
_ZH_LINE_RE = re.compile(r"第\s*(\d+)\s*行")


def verify_evidence(root: str, text: str, limit: int = 8) -> List[str]:
    """校验答案里的路径/行号引用是否真实存在。

    只判断「文件是否存在、行号是否越界」，不做敏感文件策略（那属于读取权限，
    不是证据真伪）。返回问题列表，空列表表示全部核实通过。
    """
    bad: List[str] = []
    seen = set()
    for m in _CITE_RE.finditer(text or ""):
        rel = m.group(1).strip("`\"'（）()[] ")
        line = m.group(2)
        if not line:
            lm = _ZH_LINE_RE.search(text[m.end():m.end() + 14])
            if lm:
                line = lm.group(1)
        key = (rel, line or "")
        if key in seen:
            continue
        seen.add(key)
        if len(bad) >= limit:
            break

        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts) or not parts:
            bad.append(f"{rel}（路径非法）")
            continue
        fp = os.path.join(root, *parts)
        if not os.path.isfile(fp):
            bad.append(f"{rel}（文件不存在）")
            continue
        if line:
            try:
                n = int(line)
                if os.path.getsize(fp) <= MAX_SCAN_BYTES:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        total = sum(1 for _ in f)
                    if n > total:
                        bad.append(f"{rel}:{n}（该文件只有 {total} 行）")
            except Exception:
                pass
    return bad


def _norm(rel: str) -> str:
    return (rel or "").replace("\\", "/").lstrip("./")


def collect_refs(result: Dict[str, Any], files: set, lines: set) -> None:
    """从工具结果里收集「本次真正见过」的文件与 (文件, 行号)。

    用于第二层证据校验：答案只能引用这些被证实过的证据。
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return
    for h in (result.get("hits") or []):
        f = _norm(str(h.get("file") or ""))
        if not f:
            continue
        files.add(f)
        try:
            lines.add((f, int(h.get("line"))))
        except Exception:
            pass
    if result.get("path"):
        f = _norm(str(result.get("path")))
        files.add(f)
        try:
            for n in range(int(result.get("from_line") or 1), int(result.get("to_line") or 0) + 1):
                lines.add((f, n))
        except Exception:
            pass
    for e in (result.get("entries") or []):
        d = _norm(str(e.get("dir") or ""))
        for n in list(e.get("files") or [])[:60]:
            files.add(f"{d}/{n}" if d and d != "." else n)


def unverified_refs(text: str, files: set, lines: set, limit: int = 6) -> List[str]:
    """第二层：答案里引用的 文件:行号 是否在本次取数中真的出现过。

    存在性校验挡不住「文件是真的、行号指向无关内容」的张冠李戴，这一层挡。
    """
    out: List[str] = []
    seen = set()
    for m in _CITE_RE.finditer(text or ""):
        rel = _norm(m.group(1).strip("`\"'（）()[] "))
        line = m.group(2)
        if not line:
            lm = _ZH_LINE_RE.search(text[m.end():m.end() + 14])
            if lm:
                line = lm.group(1)
        key = (rel, line or "")
        if key in seen:
            continue
        seen.add(key)
        if len(out) >= limit:
            break
        if line:
            try:
                n = int(line)
            except Exception:
                n = 0
            if n and (rel, n) in lines:
                continue          # 这一行本次真的命中过
            if n:
                out.append(f"{rel}:{n}（本次取数未命中该行）")
                continue
        if rel in files:
            continue              # 没写行号、文件确实见过 → 放行
        out.append(rel)
    return out


# ============================================================ 导航证据校验
# AI客服这类员工的答案里最常见的话术是「X 模块」「A → B → C 路径」。
# 文件级校验（_CITE_RE）抓不到这种说法——7B 会把源码目录名（ai_center）翻译成
# 一个看起来很真的「AI 中心」，再配一个编造的入口名。这一层专管它：
# 答案里出现的模块名与导航路径，必须能在本次 site_map / tool_usage 的观察里
# 找到原文，找不到就是编造。

_NAV_CTX_RE = re.compile(r"(路径|入口|模块|去哪|在哪|位于|导航|功能在)")
# 注意：中文引号「」等必须排除，否则「上传图书功能在「图书馆→图书管理」」会把
# 引号前的整句话吞进路径断言，造成误杀
_ARROW_PATH_RE = re.compile(
    r"[^\n，。;；！？!（）()「」『』【】\"'“”‘’]{2,24}"
    r"(?:\s*(?:→|->|➡|–>|—>)\s*[^\n，。;；！？!（）()「」『』【】\"'“”‘’]{1,24})+")
_IN_MODULE_RE = re.compile(r"在\s*([^\n，。;；！？!]{2,14}?)\s*模块")
_STOP_MODULE_WORDS = {"这个", "该", "哪个", "那些", "什么", "对应", "其他", "哪個"}


def _norm_nav(s: str) -> str:
    """路径说法归一化：统一箭头、去空白，让「工具箱 → 文档处理」和「工具箱→文档处理」相等。"""
    s = (s or "").strip()
    s = re.sub(r"\s*(?:→|->|➡|–>|—>)\s*", "→", s)
    # 「路径是 / 入口为」这类引导词会被箭头正则吞进第一段，剥掉后再比对
    s = re.sub(r"^(?:导航)?(?:路径|入口)(?:是|为|[:：])?\s*", "", s)
    return s.strip("。；;，, 「」『』【】\"'“”‘’")


def collect_nav_refs(result: Dict[str, Any], paths: set, modules: set) -> None:
    """从 site_map / tool_usage 的结果里收集「本次真正见过」的模块名与入口路径。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return
    for key in ("modules", "all_modules"):
        for m in (result.get(key) or []):
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            if name:
                modules.add(name)
            for e in (m.get("entries") or []):
                if isinstance(e, dict) and str(e.get("path") or "").strip():
                    paths.add(_norm_nav(e["path"]))
    for t in (result.get("tools") or []):
        if isinstance(t, dict) and str(t.get("path") or "").strip():
            paths.add(_norm_nav(t["path"]))


def unverified_nav(text: str, paths: set, modules: set, limit: int = 6) -> List[str]:
    """第三层：答案里的导航说法是否在本次取数中真的出现过。

    只检查带导航语境的说法（前面出现过 路径/入口/模块/在哪 等词），
    避免「先上传 → 再索引」这种操作步骤描述被误伤。
    返回问题列表，空列表表示全部核实通过。
    """
    out: List[str] = []
    seen: set = set()
    norm_paths = {_norm_nav(p) for p in paths}

    def _add(msg: str):
        key = msg
        if key not in seen and len(out) < limit:
            seen.add(key)
            out.append(msg)

    for m in _ARROW_PATH_RE.finditer(text or ""):
        # 只查导航语境：前后各看一段，有没有「路径/入口/在哪」等词。
        # 引导词（如「路径是」）常被正则吞进匹配体里，所以窗口要覆盖匹配本身。
        window = text[max(0, m.start() - 12):min(len(text), m.end() + 8)]
        if not _NAV_CTX_RE.search(window):
            continue
        claim = _norm_nav(m.group(0))
        if not claim:
            continue
        # 兜底：断言里完整包含任一真实路径（模型给路径套了前缀/引号）→ 视为引用过
        if any(p and p in claim for p in norm_paths):
            continue
        head = claim.split("→")[0].strip()
        if head in modules:
            _add(f"「{claim}」（模块存在，但本次取数没有返回这个入口）")
        else:
            _add(f"「{claim}」（观察里没有这个模块或入口）")

    for m in _IN_MODULE_RE.finditer(text or ""):
        name = m.group(1).strip().strip("「」『』【】\"'“”‘’").strip()
        if name in _STOP_MODULE_WORDS or name in modules:
            continue
        _add(f"「{name} 模块」（观察里没有叫这个名字的模块）")
    return out


# ============================================================ db_schema
def db_schema(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    db_path = ctx.get("db_path") or ""
    if not db_path or not os.path.exists(db_path):
        return {"ok": False, "error": "数据库文件不可用"}
    table = str(args.get("table") or "").strip()
    with_count = bool(args.get("with_count"))
    uri = "file:" + db_path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        if table:
            try:
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            except Exception as e:
                return {"ok": False, "error": f"读取表结构失败：{e}"}
            if not cols:
                return {"ok": False, "error": f"表不存在：{table}"}
            info = {
                "table": table,
                "columns": [{"name": c[1], "type": c[2], "notnull": c[3], "pk": c[5]} for c in cols],
            }
            if with_count:
                info["row_count"] = _safe_count(conn, table)
            return {"ok": True, "tables": [info]}
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        out = []
        for n in names:
            item: Dict[str, Any] = {"table": n}
            if with_count:
                item["row_count"] = _safe_count(conn, n)
            out.append(item)
        return {"ok": True, "tables": out, "table_count": len(out),
                "hint": "需要某张表的字段名时用 table 参数再查一次"}
    except Exception as e:
        return {"ok": False, "error": f"读取 schema 失败：{e}"}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _safe_count(conn: sqlite3.Connection, table: str) -> Any:
    deadline = time.time() + guard.SQL_TIMEOUT_SEC
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 1000)
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except Exception:
        return "timeout/error"
    finally:
        try:
            conn.set_progress_handler(None, 1000)
        except Exception:
            pass


# ============================================================ db_query
def db_query(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    if not ctx.get("allow_sql"):
        return {"ok": False, "error": "自由查库已在配置中关闭（modules.ai_staff.allow_free_sql=false）"}
    sql = str(args.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "缺少 sql 参数"}
    db_path = ctx.get("db_path") or ""
    res = guard.run_readonly_sql(db_path, sql)
    if res.get("ok") and res.get("row_count") == 0:
        res["hint"] = "查询成功但无结果；可去掉 WHERE 或放宽条件再试"
    return res


# ============================================================ log_search
_LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[([A-Z]+)\]\s+(\S+)\s+\|\s?(.*)$")


def log_search(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    level = str(args.get("level") or "").strip().upper()
    minutes = int(args.get("minutes") or 0)
    keyword = str(args.get("keyword") or "").strip()
    max_lines = int(args.get("max_lines") or 20)
    max_lines = max(1, min(max_lines, 100))

    log_dir = os.path.join(root, "logs")
    if not os.path.isdir(log_dir):
        return {"ok": False, "error": f"日志目录不存在：{log_dir}"}
    files = sorted(
        [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log") or ".log." in f],
        reverse=True,
    )
    if not files:
        return {"ok": False, "error": "logs/ 下没有日志文件"}

    since = datetime.now() - timedelta(minutes=minutes) if minutes > 0 else None
    matched: List[Dict[str, Any]] = []
    by_level: Dict[str, int] = {}
    scanned = 0

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        for raw in reversed(lines[-LOG_MAX_LINES:]):
            scanned += 1
            m = _LOG_RE.match(raw.rstrip())
            lvl = m.group(2) if m else "OTHER"
            by_level[lvl] = by_level.get(lvl, 0) + 1
            if level and lvl != level:
                continue
            ts = m.group(1) if m else ""
            if since and ts:
                try:
                    if datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") < since:
                        continue
                except Exception:
                    pass
            msg = m.group(4) if m else raw.rstrip()
            if keyword and keyword.lower() not in raw.lower():
                continue
            matched.append({
                "file": os.path.basename(fp),
                "time": ts,
                "level": lvl,
                "logger": m.group(3) if m else "",
                "msg": msg[:300],
            })
            if len(matched) >= max_lines:
                break
        if len(matched) >= max_lines:
            break

    return {
        "ok": True,
        "files": [os.path.basename(f) for f in files],
        "level_counts": by_level,
        "scanned_lines": scanned,
        "matched": matched[:max_lines],
        "match_count": len(matched),
        "filters": {"level": level or "*", "minutes": minutes or "*", "keyword": keyword or "*"},
    }


# ============================================================ calc_bazi
def _parse_hour(raw: Any) -> Optional[float]:
    """把「子时 / 子 / 14 / 14:30 / 未知」统一解析成 24 小时制浮点。"""
    s = str(raw if raw is not None else "").strip()
    if not s or s in ("未知", "不确定", "unknown", "?"):
        return None
    if s.endswith("时"):
        s = s[:-1].strip()
    if s in bazi.SHICHEN_HOUR:
        return float(bazi.SHICHEN_HOUR[s])
    try:
        if ":" in s:
            h, m = s.split(":", 1)
            return float(h) + float(m) / 60.0
        return float(s)
    except Exception:
        return None


def calc_bazi(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """按出生信息排四柱，输出复制到 structured 事实文本 + 结构化报告。

    ⚠️ 隐私：本工具的输入（生日/时辰/性别/出生地）只在内存中流转，
    不写库、不落审计日志（TOOL_SPECS 里标记 private_args）。
    """
    try:
        from datetime import datetime as _dt
        year = int(args.get("year") or 0)
        month = int(args.get("month") or 0)
        day = int(args.get("day") or 0)
        if not (1900 <= year <= _dt.now().year and 1 <= month <= 12 and 1 <= day <= 31):
            return {"ok": False, "error": "出生日期不合法或缺失，需要 year/month/day 三个数字参数"}
        hour = _parse_hour(args.get("hour"))
        hour_known = hour is not None
        hour = 12.0 if hour is None else hour

        report = bazi.analyze(
            year, month, day, hour,
            gender=str(args.get("gender") or "").strip(),
            city=str(args.get("city") or "").strip(),
            hour_known=hour_known,
        )
        if not report.get("ok"):
            return {"ok": False, "error": report.get("error", "排盘失败")}
        return {"ok": True, "text": bazi.to_text(report), "report": report}
    except Exception as e:
        return {"ok": False, "error": f"排盘异常：{type(e).__name__}: {e}"}


# ============================================================ 静态解析辅助
def _lit_value(node) -> Any:
    """把 AST 字面量节点安全转成 Python 值；转不了返回 None（如 blueprint 变量）。"""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _lit_dict(node) -> Dict[str, Any]:
    """读字面量字典，跳过非常量值（module_info 里的 blueprint 是变量）。"""
    out: Dict[str, Any] = {}
    if not isinstance(node, ast.Dict):
        return out
    for k, v in zip(node.keys, node.values):
        try:
            key = ast.literal_eval(k)
        except Exception:
            continue
        val = _lit_value(v)
        if val is None and isinstance(v, ast.Name):
            continue      # blueprint / 变量引用：忽略
        out[str(key)] = val
    return out


def _scan_assign(src_tree, name: str):
    """在模块级 AST 里找 `name = <字面量>` 并返回值；找不到返回 None。"""
    for node in src_tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    return None


def _strip_alias(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉只参与召回的内部字段 _alias，再作为观察结果回灌给模型。"""
    return [{k: v for k, v in it.items() if k != "_alias"} for it in items or []]


# ============================================================ site_map
# 工具箱一级分类的中文名，与 static/toolbox/tools-config.js 的 categories 保持一致
CATEGORY_LABELS = {
    "document": "文档处理", "image": "图片处理", "text": "文本工具",
    "recognition": "识别工具", "calc": "计算换算", "archive": "压缩打包",
    "system": "系统工具",
}

# 意图别名：用户口语说法 vs 模块里的书面说法往往对不上
# （用户问「在哪上传图书」，而 book_lib 的描述写的是「录入 / 批量管理图书资料」）。
# 这里补一层别名，让 site_map 召回口语化提问；别名不参与展示，只参与匹配。
SITE_HINTS = {
    "book_lib": ["上传", "上传图书", "上传资料", "录书", "添加图书", "添加资料",
                 "找书", "电子书", "下载书", "看书", "在线阅读", "我的书",
                 "私人图书", "私有图书", "分类", "图书分类", "书目"],
    "homepage": ["AI 问答", "AI问答", "智能问答", "问书", "提问", "知识问答", "聊天"],
    "chart": ["图表", "画图", "作图", "可视化", "报表图", "统计图表"],
    "toolbox_hub": ["工具箱", "在线工具", "小工具", "处理文件"],
    "toolbox": ["工具箱", "在线工具", "文件处理", "转换格式", "压缩", "水印"],
    "office_tools": ["办公", "批量处理", "批量重命名", "建文件夹", "批量建表", "Excel 分表"],
    "workflow": ["自动化", "流程", "RPA", "自动执行", "批处理任务"],
    "experience_hub": ["经验", "笔记", "笔记本", "案例", "knowhow"],
    "nav_hub": ["导航", "网址", "收藏夹", "常用网站", "书签"],
    "feedback": ["反馈", "意见", "建议", "报错", "投诉", "留言"],
    "config_center": ["配置", "设置", "权限", "用户管理", "账号管理", "模块开关", "索引重建"],
    "ai_agent": ["AI 任务", "任务中心", "智能体", "让 AI 做事"],
}


def site_map(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """站点地图：本项目有哪些模块、各自的地址与用途，以及模块自报的功能入口。

    只用 ast 静态解析 modules/*/__init__.py，**不 import、不需要 app 上下文**，
    因此能在任务线程里安全调用。回答「去哪做某事」必须先查这里，不要凭印象编 URL。
    """
    keyword = str(args.get("keyword") or "").strip()
    mods_dir = os.path.join(root, "modules")
    if not os.path.isdir(mods_dir):
        return {"ok": False, "error": f"模块目录不存在：{mods_dir}"}

    items: List[Dict[str, Any]] = []
    for entry in sorted(os.listdir(mods_dir)):
        init_py = os.path.join(mods_dir, entry, "__init__.py")
        if not os.path.isfile(init_py):
            continue
        try:
            tree = ast.parse(open(init_py, "r", encoding="utf-8", errors="replace").read())
        except Exception:
            continue
        raw = _scan_assign(tree, "module_info")
        if raw is None:
            continue
        info = _lit_dict(raw)
        url = str(info.get("route_prefix") or "").strip()
        if not url:
            continue      # 无页面的后端模块（如 ai_center）不进导航答案
        entries: List[Dict[str, Any]] = []
        for e in (_lit_value(_scan_assign(tree, "config_entries")) or []):
            if isinstance(e, dict) and e.get("name"):
                entries.append({
                    "name": str(e.get("name")),
                    "url": str(e.get("url") or ""),
                    "desc": str(e.get("desc") or ""),
                    # 直接给可复用的导航路径，省得模型自己拼（它会拼错前缀）
                    "path": f"{info.get('display_name') or entry} → {e.get('name')}",
                })
        alias = SITE_HINTS.get(str(info.get("module_id") or entry), [])
        items.append({
            "id": str(info.get("module_id") or entry),
            "name": str(info.get("display_name") or entry),
            "icon": str(info.get("icon") or ""),
            "url": url,
            "desc": str(info.get("description") or ""),
            "entries": entries,
            "_alias": list(alias),
        })

    if not items:
        return {"ok": False, "error": "没能解析出任何模块信息"}

    names = [f"{it['name']}({it['url']})" for it in items]
    clean_all = _strip_alias(items)
    if keyword:
        keys = re.split(r"[\s/、,，]+", keyword)
        keys = [k for k in keys if k]
        hit = []
        for it in items:
            blob = " ".join([it["id"], it["name"], it["url"], it["desc"], " ".join(it["_alias"])] +
                            [" ".join([e["name"], e["url"], e["desc"]]) for e in it["entries"]])
            if any(k.lower() in blob.lower() for k in keys):
                hit.append(it)
        if hit:
            items = hit
        else:
            items = []
            return {
                "ok": True, "hit_count": 0, "modules": [], "keyword": keyword,
                "hint": "没有任何模块匹配该关键词。清单里没有的功能就是没有，"
                        "请据此回答「我没找到这个入口」，不要自己编 URL；"
                        "特别注意：modules/ 下的源码目录名（如 ai_center）不是页面名称，"
                        "禁止把它们翻译成中文模块名来回答；"
                        "如果问的是某个文件/格式处理（转 PDF、压缩、加水印等），"
                        "请改用 tool_usage 去工具箱里查。参考完整清单："
                        + "；".join(names),
                "all_modules": _strip_alias(clean_all),
            }

    # 别名只是内部召回用的，不回灌给模型，免得它把口语词当成真实功能名
    clean = _strip_alias(items)
    return {
        "ok": True,
        "hit_count": len(clean),
        "keyword": keyword or "（未过滤）",
        "modules": clean,
        "hint": "这些是本项目真实存在的模块地址。回答「在哪做 XX」时只能从这里挑；"
                "清单里没有的功能，就回答没这个功能，不要自己编 URL。",
    }


# ============================================================ tool_usage
def tool_usage(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """工具箱用法：69 个在线工具的中文名、所属分类、入口路径与参数说明。

    数据来源是 tools/<分类>/<工具>.py 里的模块级常量（TOOL_ID / LABEL / PARAMS）
    和文件首段的 docstring。回答「某个功能怎么用 / 在哪做」必须先查这里。
    """
    keyword = str(args.get("keyword") or "").strip()
    tool_id = str(args.get("tool_id") or "").strip()
    max_items = max(1, min(int(args.get("max_items") or 12), 40))
    tools_dir = os.path.join(root, "modules", "toolbox", "tools")
    if not os.path.isdir(tools_dir):
        return {"ok": False, "error": f"工具目录不存在：{tools_dir}"}

    recs: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(tools_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith("_")]
        for fn in filenames:
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                src = open(fp, "r", encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
            except Exception:
                continue
            tid = _lit_value(_scan_assign(tree, "TOOL_ID"))
            if not tid:
                continue
            label = _lit_value(_scan_assign(tree, "LABEL")) or tid
            cat = _lit_value(_scan_assign(tree, "CATEGORY")) or os.path.basename(dirpath)
            params = _lit_value(_scan_assign(tree, "PARAMS")) or []
            plist = [{"name": str(p.get("name")), "label": str(p.get("label") or p.get("name")),
                      "type": str(p.get("type") or "")}
                     for p in params if isinstance(p, dict)]
            doc = (ast.get_docstring(tree) or "").strip()
            doc = "\n".join([ln for ln in doc.splitlines() if ln.strip()][:2])[:220]
            recs.append({
                "id": str(tid),
                "label": str(label),
                "category": str(cat),
                "category_label": CATEGORY_LABELS.get(str(cat), str(cat)),
                "path": f"工具箱 → {CATEGORY_LABELS.get(str(cat), str(cat))} → {label}",
                "entry": "/tools",
                "need_files": bool(_lit_value(_scan_assign(tree, "NEEDS_FILES"))),
                "params": plist,
                "desc": doc,
            })

    if not recs:
        return {"ok": False, "error": "没能解析出任何工具信息"}

    if tool_id:
        hit = [r for r in recs if r["id"] == tool_id or tool_id.lower() in r["id"].lower()]
        if hit:
            return {"ok": True, "hit_count": len(hit), "tools": hit[:max_items]}
        near = difflib.get_close_matches(tool_id, [r["id"] for r in recs], n=5, cutoff=0.4)
        return {"ok": True, "hit_count": 0, "tools": [],
                "hint": f"没有 id 为 {tool_id} 的工具。相近的：" + "、".join(near)}

    if keyword:
        keys = re.split(r"[\s/、,，]+", keyword)
        keys = [k for k in keys if k]
        scored = []
        for r in recs:
            blob = " ".join([r["id"], r["label"], r["category_label"], r["desc"],
                             " ".join(p["label"] for p in r["params"])])
            score = sum(1 for k in keys if k.lower() in blob.lower())
            score += 2 if any(k.lower() in r["label"].lower() for k in keys) else 0
            if score:
                scored.append((score, r))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return {"ok": True, "hit_count": len(scored), "keyword": keyword,
                    "tools": [r for _, r in scored][:max_items]}
        near = difflib.get_close_matches(keyword, [r["label"] for r in recs], n=6, cutoff=0.35)
        labels = "、".join(f"{r['label']}({r['category_label']})" for r in recs[:40])
        return {"ok": True, "hit_count": 0, "keyword": keyword, "tools": [],
                "hint": f"没有工具匹配「{keyword}」。"
                        + (f"名字相近的：{'、'.join(near)}。" if near else "")
                        + "全部工具（工具箱 → 分类 → 名称）：" + labels}

    # 无关键词：按分类聚合给个概览（不让 69 条全塞进上下文）
    by_cat: Dict[str, List[str]] = {}
    for r in recs:
        by_cat.setdefault(f"{r['category']}|{r['category_label']}", []).append(r["label"])
    summary = [{"category": k.split("|")[0], "category_label": k.split("|")[1],
                "count": len(v), "tools": v} for k, v in sorted(by_cat.items())]
    return {"ok": True, "hit_count": len(recs), "entry": "/tools",
            "by_category": summary,
            "hint": "这是工具箱的概览。用户问具体功能时，请带上 keyword 再查一次，"
                    "拿到准确的中文名与入口路径后再作答。",
            "tool_count": len(recs)}


# ============================================================ book_search
def book_search(root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """按书名关键字查图书属于哪个分类。

    ⚠️ 两个硬约束：
    1. 只读：mode=ro 连接 + 纯 SELECT + LIMIT；
    2. 行级权限：非管理员只能看到公共书（pc1 为空）与自己拥有（owner_id）的书，
       别人的私有书一条也看不见（无需也不允许在这里放行）。
    出于安全，**文件路径（path/savepath）一律不返回**。
    """
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "缺少 name 参数（书名关键字，支持部分匹配）"}
    limit = max(1, min(int(args.get("limit") or 20), 50))
    db_path = ctx.get("db_path") or os.path.join(root, "book_manager.db")
    user = ctx.get("user") or {}
    is_admin = str(user.get("role") or "").lower() in ("admin", "superadmin")
    uid = str(user.get("id") or "")

    sql = ("SELECT name, cat1, cat2, file_type, owner_id, pc1 FROM books b "
           "WHERE b.name LIKE ? ")
    params: List[Any] = [f"%{name}%"]
    if not is_admin:
        sql += "AND ((b.pc1 IS NULL OR b.pc1='') OR b.owner_id=?) "
        params.append(uid)
        # 第二道门禁：resource_permissions（图书级可见性）。
        # 「scope=global 私有上传」只写 resource_permissions(selected + allowed_users=[owner])、
        # **不写 pc1**，于是 pc1 为空 → 上面的条件恒真，别人的私有书会被所有人查到。
        # 主站 user_can_view_book 是会拦的，这里原先漏掉了这层，护栏形同虚设。
        # 注：只处理图书级规则；分类级（cat1/cat2）权限由主站 _collect_permission_candidates
        # 汇总，此处不展开（AI 员工仅回书名与分类，不返回正文/路径）。
        sql += (
            "AND NOT EXISTS (SELECT 1 FROM resource_permissions rp "
            "WHERE rp.resource_type='book' AND rp.resource_id=b.id "
            "  AND (rp.min_level='admin' "
            "       OR (rp.min_level='selected' "
            "           AND IFNULL(rp.allowed_users,'') NOT LIKE ?))) "
        )
        params.append(f"%{uid}%")
    sql += f"LIMIT {int(limit)}"

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": f"查询失败：{type(e).__name__}: {e}"}

    books = []
    for r in rows:
        d = dict(r)
        c1 = (d.get("cat1") or "").strip()
        c2 = (d.get("cat2") or "").strip()
        books.append({
            "name": d.get("name") or "",
            "cat1": c1,
            "cat2": c2,
            "category_path": " / ".join([x for x in (c1, c2) if x]) or "未分类",
            "file_type": d.get("file_type") or "",
            "private": bool((d.get("pc1") or "").strip()),
        })

    res: Dict[str, Any] = {
        "ok": True,
        "name": name,
        "row_count": len(books),
        "books": books,
        "scope": "全部图书" if is_admin else "公共图书 + 你自己的私有图书",
    }
    if not books:
        res["hint"] = ("没查到匹配的书"
                       + ("" if is_admin else "（你只能看到公共图书和你自己的私有图书）")
                       + "。可以换成更短的书名关键字再试。")
    return res


# ============================================================ 工具清单
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "list_dir",
        "fn": list_dir,
        "desc": "列出项目目录结构（一级或两级），用于先定位文件在哪",
        "args": {"path": "相对项目根的路径，空表示根", "depth": "层级 1-3，默认 1",
                 "max_entries": "最多返回多少条，默认 60"},
    },
    {
        "name": "code_search",
        "fn": code_search,
        "desc": "在源码里检索关键字/正则，返回 文件:行号:内容；pattern 请写英文标识符"
                "（代码里的变量/配置项都是英文，中文关键词搜不到）",
        "args": {"pattern": "要找的字符串或正则", "path": "限定子目录/文件，可空",
                 "regex": "是否按正则匹配，true/false", "max_hits": "最多命中数，默认 30"},
    },
    {
        "name": "read_file",
        "fn": read_file,
        "desc": "按行区间读取一个文本文件的内容",
        "args": {"path": "相对项目根的文件路径（必填）", "start_line": "起始行，默认 1",
                 "end_line": "结束行，0 表示读到上限"},
    },
    {
        "name": "db_schema",
        "fn": db_schema,
        "desc": "查看数据库有哪些表，或某张表的字段名与类型",
        "args": {"table": "表名，空表示列出全部表", "with_count": "是否附带行数，true/false"},
    },
    {
        "name": "db_query",
        "fn": db_query,
        "desc": "执行一条只读 SELECT 查询（自动强制 LIMIT，禁止写操作）",
        "args": {"sql": "一条 SELECT 语句（必填）"},
    },
    {
        "name": "log_search",
        "fn": log_search,
        "desc": "检索 logs/ 下的运行日志，可按级别、时间窗、关键字过滤",
        "args": {"level": "ERROR/WARNING/INFO，空表示全部", "minutes": "最近 N 分钟，0 表示不限",
                 "keyword": "关键字，可空", "max_lines": "最多返回几条，默认 20"},
    },
    {
        "name": "calc_bazi",
        "fn": calc_bazi,
        "desc": "按出生信息排八字四柱，输出日主强弱、五行分布、十神归类，"
                "以及性格要点与适合的工作方向（纯内存计算，结果不落库不写日志）",
        "args": {"year": "出生年（公历，必填）", "month": "出生月（必填）", "day": "出生日（必填）",
                 "hour": "出生时间：接受 14 / 14:30 / 子 / 子时；留空或填「未知」表示时辰不确定",
                 "gender": "男/女，影响大运排法，可选",
                 "city": "出生城市名（如 北京），用于真太阳时校正，可选"},
        # 隐私标记：审计时不记录参数（生日/时辰属个人信息，一律不落日志）
        "private_args": True,
    },
    {
        "name": "site_map",
        "fn": site_map,
        "desc": "列出本网站所有模块的中文名、访问地址与用途，以及模块自报的功能入口。"
                "回答「去哪做某事 / 某个功能在哪里」必须先查这个；清单里没有的就回答没有",
        "args": {"keyword": "按关键词（中文即可）过滤模块，空表示列出全部"},
    },
    {
        "name": "tool_usage",
        "fn": tool_usage,
        "desc": "查询工具箱里某个在线工具的中文名、所属分类、入口路径与参数怎么用。"
                "回答「某功能怎么用 / 有没有 XX 工具」必须先查这个",
        "args": {"keyword": "按关键词（中文即可）搜索，如 PDF、压缩、换算、加水印",
                 "tool_id": "已知工具 id 时精确查询，如 document_convert",
                 "max_items": "最多返回几条，默认 12"},
    },
    {
        "name": "book_search",
        "fn": book_search,
        "desc": "按书名关键字查资料属于哪个分类（一级 / 二级）。"
                "只会返回公共图书和提问者自己的私有图书",
        "args": {"name": "书名关键字，支持部分匹配（必填）", "limit": "最多返回几条，默认 20"},
    },
]

TOOLS: Dict[str, Callable] = {t["name"]: t["fn"] for t in TOOL_SPECS}


def call_tool(name: str, root: str, ctx: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """统一入口：未知工具名也要返回结构化错误（模型可以据此自我纠正）。"""
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"未知道具：{name}", "available": [t["name"] for t in TOOL_SPECS]}
    try:
        return fn(root, ctx, args or {})
    except Exception as e:
        return {"ok": False, "error": f"工具执行异常：{type(e).__name__}: {e}"}
