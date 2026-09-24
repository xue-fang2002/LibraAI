"""任务持久化与状态机。

一张 agent_tasks 表承载「用户下过的每一条任务」：计划、执行方式、状态、产物。
有了它，跨页面跳转不会丢结果，也能做历史回看与失败重试。

状态流转：
    pending ──确认──> running ──> done
       │                  └──> failed
       └──取消──> cancelled
（link 型任务不进 running，确认后直接 done —— 它只是给个链接，没有服务端动作）
"""
import json
from typing import Any, Dict, List, Optional

from core.db_base import db_execute, db_query, db_query_one

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_ALLOWED = {
    STATUS_PENDING: {STATUS_CONFIRMED, STATUS_RUNNING, STATUS_CANCELLED, STATUS_FAILED},
    STATUS_CONFIRMED: {STATUS_RUNNING, STATUS_CANCELLED, STATUS_FAILED},
    STATUS_RUNNING: {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_DONE: set(),
    STATUS_FAILED: set(),
    STATUS_CANCELLED: set(),
}


def init_tables():
    """建表（幂等，首次访问时调用）。

    ⚠️ 表名必须是 agent_tasks，不能叫 ai_tasks —— 库里已有一张同名的
    ai_center 任务表（task_type / progress / book_id…），
    CREATE TABLE IF NOT EXISTS 遇到同名表会静默跳过，接着所有查询都会
    报 "no column named xxx"。踩过一次，别改回去。
    """
    db_execute("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            query TEXT,
            intent TEXT,
            capability TEXT,
            plan_json TEXT,
            execution TEXT,
            link TEXT,
            status TEXT,
            result_json TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db_execute("CREATE INDEX IF NOT EXISTS idx_agent_tasks_user ON agent_tasks(user_id, id DESC)")


def recover_orphan_running() -> int:
    """进程启动时恢复孤儿任务：把所有 running 状态的任务标记为 failed。

    为什么必须做：async 任务跑在 Flask 进程内的后台线程里
    （executor._async_worker / _run_flow_worker），进程一死线程就没了，
    但 agent_tasks.status 仍停在 running —— 前端会永远显示「后台执行中」，
    且 confirm 只认 pending/confirmed，卡死的任务连重跑都不行。

    ⚠️ 只能在进程启动时调用一次（模块加载期），不能放到请求路径里，
    否则会把正在正常执行的任务误杀。表尚不存在时静默跳过。
    """
    try:
        cur = db_execute(
            "UPDATE agent_tasks SET status = ?, error = ?,"
            " updated_at = CURRENT_TIMESTAMP"
            " WHERE status = ?",
            (STATUS_FAILED, "服务重启，后台任务随进程退出而中断，请重新下发",
             STATUS_RUNNING),
        )
        return int(cur or 0)
    except Exception:
        # 首次启动 agent_tasks 表还不存在（init_tables 未跑过），忽略即可
        return 0


def create(user_id: Any, query: str, intent: str = "", capability: str = "",
           plan: Optional[Dict[str, Any]] = None, execution: str = "",
           link: str = "") -> Optional[int]:
    init_tables()
    return db_execute(
        "INSERT INTO agent_tasks (user_id, query, intent, capability, plan_json, execution, link, status)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (str(user_id or ""), query or "", intent, capability,
         json.dumps(plan or {}, ensure_ascii=False), execution, link, STATUS_PENDING),
    )


def get(task_id: int) -> Optional[Dict[str, Any]]:
    row = db_query_one("SELECT * FROM agent_tasks WHERE id = ?", (task_id,))
    return _decode(row)


def list_by_user(user_id: Any, limit: int = 20) -> List[Dict[str, Any]]:
    init_tables()
    rows = db_query(
        "SELECT * FROM agent_tasks WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (str(user_id or ""), limit),
    )
    return [_decode(r) for r in rows]


def set_status(task_id: int, status: str,
               result: Optional[Dict[str, Any]] = None, error: str = "") -> bool:
    """更新状态。非法流转直接拒绝，返回 False。"""
    row = get(task_id)
    if not row:
        return False
    current = row.get("status") or STATUS_PENDING
    if status != current and status not in _ALLOWED.get(current, set()):
        return False

    # 注意：get() 已把 result_json 解成 dict，写回前必须重新序列化，
    # 否则 SQLite 会报 "Error binding parameter - probably unsupported type"。
    result_obj = result if result is not None else row.get("result_json")
    result_str = json.dumps(result_obj or {}, ensure_ascii=False)
    error_str = error if error else (row.get("error") or "")

    db_execute(
        "UPDATE agent_tasks SET status = ?, result_json = ?, error = ?,"
        " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, result_str, error_str, task_id),
    )
    return True


def _decode(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = dict(row)
    for key in ("plan_json", "result_json"):
        raw = out.get(key)
        if isinstance(raw, str) and raw:
            try:
                out[key] = json.loads(raw)
            except Exception:
                out[key] = {}
        elif raw in (None, ""):
            out[key] = {}
    return out
