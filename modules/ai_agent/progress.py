"""异步任务的进度存储（文件化，避免 SQLite 多线程写竞争）。

每个异步任务在 temp/ai_agent_tasks/<task_id>/ 下有一份 progress.json：
    {"current": n, "total": m, "status": "running", "message": "..."}
worker 线程高频写它；前端轮询端点读它（再结合 DB 里的任务终态）。

另有 progress.log：每次 write 追加一行（时间 + 状态 + 步骤 + message），
供「最近任务」回看执行明细 —— progress.json 只留最新一条，刷新即丢。

刻意不进 DB：进度是高频、易失的，落文件既零锁竞争又能随任务目录一起清理。
"""
import os
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TASKS_DIR = os.path.join(_ROOT, "temp", "ai_agent_tasks")


def work_dir(task_id: int) -> str:
    """该任务的专属工作目录（含输入 work/ 与产物 out/）。"""
    d = os.path.join(_TASKS_DIR, str(task_id))
    os.makedirs(d, exist_ok=True)
    return d


def _progress_path(task_id: int) -> str:
    return os.path.join(work_dir(task_id), "progress.json")


def _log_path(task_id: int) -> str:
    return os.path.join(work_dir(task_id), "progress.log")


def write(task_id: int, current: int, total: int, status: str, message: str = "",
          log: bool = True) -> None:
    """写进度快照。

    log=False 用于「调用方自己逐行 append_log」的场景（如轮询流程日志时
    每写一次快照就重复追加一行），避免日志出现重复行。
    """
    data = {"current": current, "total": total, "status": status, "message": message}
    try:
        with open(_progress_path(task_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        # 进度写失败不应中断真正的工作，吞掉即可
        pass
    if log:
        append_log(task_id, status, message, current, total)


def append_log(task_id: int, status: str, message: str,
               current: int = 0, total: int = 0) -> None:
    """追加一行执行日志到 progress.log。

    progress.json 只保留最新一条（高频覆盖），任务结束后历史就没了；
    这里额外落一份按时间追加的日志，供「最近任务」回看每一步做了什么。
    """
    import time
    try:
        stamp = time.strftime("%H:%M:%S")
        step = f"{current}/{total}" if total else str(current)
        with open(_log_path(task_id), "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {status} {step} {message or ''}\n")
    except Exception:
        pass


# 「成功 N 个，失败 M 个」——流程汇总行
_STAT_RE = None
# 「  3. 某某公司  ✅ 完成 / ❌ 失败：原因」——逐条明细行
_ITEM_RE = None


def _ensure_re():
    global _STAT_RE, _ITEM_RE
    if _STAT_RE is None:
        import re
        _STAT_RE = re.compile(r"成功\s*(\d+)\s*个[，,]\s*失败\s*(\d+)\s*个")
        # 前缀可选：append_log 落盘的行会带 "[HH:MM:SS] status step "
        _ITEM_RE = re.compile(
            r"^(?:\[\d{2}:\d{2}:\d{2}\]\s+\S+\s+\S+\s*)?\s*(\d+)\s*[.、]\s*(.+?)\s+"
            r"(✅|❌)\s*(完成|失败)?\s*[:：]?\s*(.*)$")


def parse_results(lines: list) -> dict:
    """从日志行里解析「哪些成功、哪些失败」，供前端直接展示。

    约定来自流程自身的输出（如发票上传流程）：
        汇总：📊 执行完毕：总共 5 个单据，成功 4 个，失败 1 个
        明细：  3. 某某公司  ✅ 完成
                4. 另一家    ❌ 失败：找不到部门
    没有这类行时返回空的 items，前端只显示原始日志，不会报错。
    """
    _ensure_re()
    items = []
    success = fail = None
    for ln in lines or []:
        m = _STAT_RE.search(ln)
        if m and success is None:
            success, fail = int(m.group(1)), int(m.group(2))
            continue
        m2 = _ITEM_RE.match(ln)
        if m2:
            # 分组：1=序号 2=名称 3=✅/❌ 4=完成/失败 5=失败原因
            ok = m2.group(3) == "✅"
            items.append({
                "index": int(m2.group(1)),
                "name": m2.group(2).strip(),
                "ok": ok,
                "error": (m2.group(5) or "").strip(),
            })
    if success is None:
        success = sum(1 for i in items if i["ok"])
        fail = len(items) - success
    return {"total": success + fail, "success": success, "fail": fail, "items": items}


def read_log(task_id: int, limit: int = 300) -> list:
    """读取执行日志，返回最近 limit 行（每行字符串）。"""
    try:
        with open(_log_path(task_id), "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    except Exception:
        return []
    return lines[-limit:]


def read(task_id: int) -> dict:
    """读进度；文件不存在返回空 dict（前端据此判断任务尚未真正启动）。"""
    try:
        with open(_progress_path(task_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
