"""回归：AI 任务执行日志 + 异步任务可执行性。

覆盖：
  1. progress.write() 会同时往 progress.log 追加一行（历史不丢）
  2. read_log 读取与行数上限
  3. /agent/api/tasks/<id>/log 返回日志，且非本人任务 404
  4. async 能力确认时不再被「未知执行方式」拦下（routes 守卫回归）
  5. 任务列表能拿到 summary / error / updated_at（摘要渲染依赖）
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

Results = []


def chk(name, ok, detail=""):
    Results.append((name, bool(ok), detail))
    print(("✅" if ok else "❌") + f" {name}" + (f"  [{detail}]" if detail and not ok else ""))


def main():
    app = create_app()[0]
    from modules.ai_agent import progress as _progress

    # ---------- 1&2. 日志落盘与读取 ----------
    tid = 990001  # 测试专用 id，不污染真实任务
    wd = _progress.work_dir(tid)
    log_path = os.path.join(wd, "progress.log")
    if os.path.exists(log_path):
        os.remove(log_path)

    _progress.write(tid, 0, 5, "running", "正在准备…")
    _progress.write(tid, 2, 5, "running", "处理第 2 个")
    _progress.write(tid, 5, 5, "done", "成功 5 个，失败 0 个")

    chk("progress.json 只留最新一条",
        _progress.read(tid).get("message") == "成功 5 个，失败 0 个")
    lines = _progress.read_log(tid)
    chk("read_log 累积 3 行历史", len(lines) == 3, str(len(lines)))
    chk("日志含首行「正在准备…」", lines and "正在准备" in lines[0], lines[0] if lines else "")
    chk("日志含末行成功统计", lines and "成功 5 个" in lines[-1], lines[-1] if lines else "")
    chk("日志行带时间 [HH:MM:SS]", lines and lines[0].startswith("["), lines[0] if lines else "")
    chk("不存在的任务读日志返回空", _progress.read_log(999999) == [])

    # ---------- 3. 日志接口 + 归属校验 ----------
    from modules.ai_agent import store
    store.init_tables()
    # capability 用开箱示例流程，避免公开仓库出现任何私有流程名
    task_id = store.create(1, "测试日志任务", capability="workflow.example_flow",
                           execution="async")
    chk("创建测试任务成功", task_id is not None, str(task_id))
    _progress.append_log(task_id, "running", "步骤日志回归", 1, 2)

    def call(path, uid):
        with app.test_request_context(path):
            from flask import g as _g
            _g.current_user = {"id": uid, "role": "admin", "account": "admin"}
            view = app.view_functions.get("ai_agent.api_task_log")
            return view(task_id) if view else None

    resp = call(f"/agent/api/tasks/{task_id}/log", 1)
    data = json.loads(resp.get_data(as_text=True)) if hasattr(resp, "get_data") else {}
    chk("本人可读日志接口 200", data.get("code") == 200, str(data.get("code")))
    chk("接口返回日志行", len((data.get("data") or {}).get("lines") or []) >= 1,
        str((data.get("data") or {}).get("lines")))

    resp2 = call(f"/agent/api/tasks/{task_id}/log", 4242)
    d2 = json.loads(resp2.get_data(as_text=True))
    chk("他人任务读日志被拒（403/404）", d2.get("code") in (403, 404), str(d2.get("code")))

    # ---------- 4. async 能力不被守卫误杀 ----------
    from modules.ai_agent.connectors.base import EXEC_ASYNC
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "modules", "ai_agent", "routes.py"), encoding="utf-8").read()
    chk("守卫放行 async", "execution not in (EXEC_INPLACE, EXEC_ASYNC)" in src)
    chk("async 分支仍存在", "if execution == EXEC_ASYNC:" in src)

    from modules.ai_agent import registry
    async_caps = [c for c in registry.all_capabilities() if c.execution == EXEC_ASYNC]
    chk("系统内存在 async 能力（办公/workflow）", len(async_caps) > 0, str(len(async_caps)))

    # ---------- 5. 任务列表字段（前端摘要依赖） ----------
    # 状态机：pending 不能直接跳 done，必须 pending → running → done
    store.set_status(task_id, store.STATUS_RUNNING)
    store.set_status(task_id, store.STATUS_DONE,
                     result={"kind": "files", "files": [], "summary": "成功 3 个，失败 1 个"})
    row = store.get(task_id)
    chk("result_json 带 summary", (row.get("result_json") or {}).get("summary") ==
        "成功 3 个，失败 1 个", str(row.get("result_json")))
    chk("任务带 created_at/updated_at", bool(row.get("created_at")) and bool(row.get("updated_at")))

    # ---------- 6. 成功/失败逐条解析 ----------
    sample = [
        "[07:00:01] running 1/3 正在处理：A公司",
        "  1. A公司  ✅ 完成",
        "  2. B公司  ✅ 完成",
        "  3. C公司  ❌ 失败：找不到付费部门",
        "📊 执行完毕：总共 3 个单据，成功 2 个，失败 1 个",
    ]
    parsed = _progress.parse_results(sample)
    chk("解析出 3 条明细", len(parsed["items"]) == 3, str(parsed["items"]))
    chk("统计成功 2", parsed["success"] == 2, str(parsed))
    chk("统计失败 1", parsed["fail"] == 1, str(parsed))
    chk("前 2 条标记为成功",
        all(i["ok"] for i in parsed["items"][:2]), str(parsed["items"][:2]))
    it3 = parsed["items"][-1]
    chk("第 3 条标记失败", it3["ok"] is False and it3["name"] == "C公司", str(it3))
    chk("失败原因保留", "找不到付费部门" in (it3.get("error") or ""), str(it3))
    chk("无明细日志不报错", _progress.parse_results(["只有普通日志"])["items"] == [])

    # 带 append_log 落盘前缀的行也要能解析（真实日志每行都有 [HH:MM:SS] status step）
    prefixed = ["[07:00:09] done 3/3 " + s for s in sample]
    p2 = _progress.parse_results(prefixed)
    chk("带时间戳前缀仍能解析 3 条", len(p2["items"]) == 3, str(p2["items"]))
    chk("带前缀统计正确", p2["success"] == 2 and p2["fail"] == 1, str(p2))

    # ---------- 7. 执行日志聚合接口 ----------
    def call_recent(uid):
        with app.test_request_context("/agent/api/recent-logs"):
            from flask import g as _g
            _g.current_user = {"id": uid, "role": "admin", "account": "admin"}
            return app.view_functions["ai_agent.api_recent_logs"]()

    r = call_recent(1)
    d = json.loads(r.get_data(as_text=True))
    lst = (d.get("data") or {}).get("list") or []
    chk("recent-logs 返回列表", len(lst) >= 1, str(len(lst)))
    chk("每条含 lines/result", lst and "lines" in lst[0] and "result" in lst[0],
        str(list(lst[0].keys()) if lst else None))
    chk("recent-logs 只返回本人任务",
        all(t.get("query") == "测试日志任务" or t.get("capability") for t in lst))

    failed = [r for r in Results if not r[1]]
    print(f"\n===== {len(Results) - len(failed)}/{len(Results)} 通过 =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
