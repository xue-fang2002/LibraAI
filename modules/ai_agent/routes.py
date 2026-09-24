"""AI 任务中心路由（保持薄：只做参数校验与响应封装，业务一律下沉）。

接口一览：
  GET  /                        页面
  GET  /api/capabilities        能力清单（可按 module 过滤）
  POST /api/plan                生成计划（意图路由 + 参数抽取），并落一条任务
  POST /api/tasks/<id>/confirm  确认执行：inplace 直接跑，jump/link 只回地址
  POST /api/tasks/<id>/cancel   取消
  GET  /api/tasks               我的任务历史
"""
from flask import render_template, request, g, send_file
import os
import shutil

from modules.ai_agent import bp
from core.auth import login_required
from core.response import ok, fail
from core.exceptions import module_exception_guard

from . import registry, planner, store, executor, progress as _progress
from .connectors.base import EXEC_INPLACE, EXEC_ASYNC, EXEC_JUMP, EXEC_LINK
from core.db_base import user_can_access_module


def _uid():
    user = getattr(g, "current_user", None) or {}
    return user.get("id")


def _cap_allowed(cap) -> bool:
    """私有模块能力按 module_access 过滤（当前 admin 门禁模块：workflow）。

    workflow 流程属私有 RPA（模块默认仅管理员可见可跑），无权限的
    用户在能力清单 / 规划 / 确认执行三处都不可见、不可跑。
    """
    if cap is not None and cap.module == "workflow":
        return user_can_access_module(getattr(g, "current_user", None), "workflow")
    return True


@bp.route("/")
@module_exception_guard("ai_agent")
@login_required
def index():
    store.init_tables()
    return render_template("ai_agent/index.html", current_module_id="ai_agent")


@bp.route("/api/capabilities")
@module_exception_guard("ai_agent")
@login_required
def api_capabilities():
    """能力清单。module 参数可只取某个模块的。"""
    module = (request.args.get("module") or "").strip()
    caps = registry.by_module(module) if module else registry.all_capabilities()
    caps = [c for c in caps if _cap_allowed(c)]
    grouped: dict = {}
    for c in caps:
        grouped.setdefault(c.module, []).append(c.to_dict())
    return ok(data={
        "total": len(caps),
        "modules": [{"module": k, "count": len(v), "capabilities": v}
                    for k, v in grouped.items()],
    })


@bp.route("/api/plan", methods=["POST"])
@module_exception_guard("ai_agent")
@login_required
def api_plan():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return fail("请先描述你要做的事", code=400)
    if len(text) > 500:
        return fail("描述过长（最多 500 字）", code=400)

    result = planner.plan(text)
    if not result.get("ok"):
        return ok(data={"planned": False, **result})

    # 私有模块权限门禁：规划命中但用户无权访问该模块时，按「无支持」回复
    planned_cap = registry.get_capability(result.get("capability", {}).get("cid", ""))
    if planned_cap is not None and not _cap_allowed(planned_cap):
        return ok(data={"planned": False, "reason": "no_permission",
                        "reply": "你没有「工作自动化流程」模块的访问权限，"
                                 "请联系管理员在配置中心 → 模块访问权限开通。"})

    task_id = store.create(
        user_id=_uid(),
        query=text,
        intent=result.get("module", ""),
        capability=result.get("capability", {}).get("cid", ""),
        plan={"params": result.get("params", {}), "reply": result.get("reply", "")},
        execution=result.get("execution", ""),
        link=result.get("link", ""),
    )
    result["task_id"] = task_id
    result["planned"] = True
    return ok(data=result)


@bp.route("/api/tasks/<int:task_id>/confirm", methods=["POST"])
@module_exception_guard("ai_agent")
@login_required
def api_confirm(task_id):
    task = store.get(task_id)
    if not task:
        return fail("任务不存在", code=404)
    if task.get("status") not in (store.STATUS_PENDING, store.STATUS_CONFIRMED):
        return fail(f"任务已是 {task.get('status')} 状态，无法确认", code=400)

    body = request.get_json(silent=True) or {}
    execution = task.get("execution") or ""

    # link / jump：没有服务端动作，确认即完成，把地址交回前端
    if execution in (EXEC_JUMP, EXEC_LINK):
        # ⚠️ 状态机约束（store._ALLOWED）：PENDING 只能到 confirmed/running/
        # cancelled/failed，**不允许直接 DONE**。原先这里一步跳 DONE 被
        # set_status 拒收（返回 False），而返回值又被丢弃 —— 接口回 200 声称
        # done，库里却仍停在 pending：任务列表永远显示「待确认」且可无限重复确认。
        # 改为 PENDING→CONFIRMED→DONE 两段走，并校验返回值。
        store.set_status(task_id, store.STATUS_CONFIRMED)
        if not store.set_status(task_id, store.STATUS_DONE,
                                result={"kind": "link", "url": task.get("link")}):
            return fail("任务状态更新失败，请重试", code=400)
        return ok(data={"task_id": task_id, "executed": False,
                        "execution": execution, "link": task.get("link"),
                        "status": store.STATUS_DONE})

    # ⚠️ async 必须放行：办公自动化与 workflow 流程都是异步执行，
    # 之前这里只认 inplace，导致下面 async 分支成了死代码、异步能力一确认就报
    # 「未知执行方式：async」，任务根本跑不起来（也就永远不会产生执行日志）。
    if execution not in (EXEC_INPLACE, EXEC_ASYNC):
        return fail(f"未知执行方式：{execution}", code=400)

    cap = registry.get_capability(task.get("capability") or "")
    if cap is not None and not _cap_allowed(cap):
        return fail("无权执行该能力", code=403)
    params = (task.get("plan_json") or {}).get("params") or {}
    params.update(body.get("params") or {})

    # 异步能力：立即返回 running，真正的执行交后台线程，前端轮询进度
    if execution == EXEC_ASYNC:
        store.set_status(task_id, store.STATUS_RUNNING)
        outcome = executor.run_async(cap, params, user_id=_uid(), task_id=task_id)
        if not outcome.get("ok"):
            store.set_status(task_id, store.STATUS_FAILED,
                            error=outcome.get("error") or "提交失败")
            return fail(outcome.get("error") or "提交失败",
                        data={"task_id": task_id, "status": store.STATUS_FAILED})
        return ok(data={"task_id": task_id, "executed": True, "async": True,
                        "status": store.STATUS_RUNNING,
                        "message": outcome.get("message") or "后台执行中"})

    store.set_status(task_id, store.STATUS_RUNNING)
    outcome = executor.run(cap, params, user_id=_uid(), task_id=task_id)
    if outcome.get("ok"):
        _progress.append_log(task_id, "done", "执行完成", 1, 1)
        store.set_status(task_id, store.STATUS_DONE, result=outcome.get("result"))
        return ok(data={"task_id": task_id, "executed": True,
                        "result": outcome.get("result"), "status": store.STATUS_DONE})

    err = outcome.get("error") or "执行失败"
    _progress.append_log(task_id, "failed", err, 1, 1)
    store.set_status(task_id, store.STATUS_FAILED, error=err)
    return fail(outcome.get("error") or "执行失败",
                data={"task_id": task_id, "status": store.STATUS_FAILED})


@bp.route("/api/tasks/<int:task_id>/cancel", methods=["POST"])
@module_exception_guard("ai_agent")
@login_required
def api_cancel(task_id):
    # 归属校验不能省：task_id 是 agent_tasks 的自增主键，缺了这层校验任何人
    # 都能遍历 1,2,3… 把全站他人 running 中的任务静默置为 cancelled。
    # （本文件其余任务接口均走 _assert_task_owner，此处原先漏了。）
    if not _assert_task_owner(task_id):
        return fail("任务不存在", code=404)
    if not store.set_status(task_id, store.STATUS_CANCELLED):
        return fail("任务不存在或当前状态不可取消", code=400)
    return ok(data={"task_id": task_id, "status": store.STATUS_CANCELLED})


@bp.route("/api/tasks")
@module_exception_guard("ai_agent")
@login_required
def api_tasks():
    limit = request.args.get("limit", 20, type=int)
    return ok(data={"list": store.list_by_user(_uid(), limit=min(limit, 100))})


@bp.route("/api/tasks/<int:task_id>/progress")
@module_exception_guard("ai_agent")
@login_required
def api_task_progress(task_id):
    """异步任务进度：先校验归属，再返回 DB 终态 + 文件化进度。"""
    task = _assert_task_owner(task_id)
    if not task:
        return fail("任务不存在", code=404)
    return ok(data={
        "task_id": task_id,
        "status": task.get("status"),
        "progress": _progress.read(task_id),
        "result": task.get("result_json") or {},
    })


@bp.route("/api/tasks/<int:task_id>/log")
@module_exception_guard("ai_agent")
@login_required
def api_task_log(task_id):
    """异步任务的执行日志明细（progress.log），先校验归属。"""
    task = _assert_task_owner(task_id)
    if not task:
        return fail("任务不存在", code=404)
    lines = _progress.read_log(task_id)
    return ok(data={
        "task_id": task_id,
        "status": task.get("status"),
        "lines": lines,
        "result": _progress.parse_results(lines),
    })


@bp.route("/api/recent-logs")
@module_exception_guard("ai_agent")
@login_required
def api_recent_logs():
    """执行日志面板数据源：最近 N 条任务 + 每条的日志与成功/失败明细。"""
    limit = min(request.args.get("limit", 10, type=int), 30)
    tasks = store.list_by_user(_uid(), limit=limit)
    out = []
    for t in tasks:
        lines = _progress.read_log(t.get("id"))
        out.append({
            "id": t.get("id"),
            "query": t.get("query") or "",
            "capability": t.get("capability") or "",
            "status": t.get("status"),
            "error": t.get("error") or "",
            "created_at": t.get("created_at") or "",
            "updated_at": t.get("updated_at") or "",
            "result_json": t.get("result_json") or {},
            "lines": lines,
            "result": _progress.parse_results(lines),
        })
    return ok(data={"list": out})


# ============================================================
# 授权下载：产物均经「任务归属」校验，杜绝跨用户越权访问
# （替代 toolbox 的无归属校验下载接口，toolbox 本体无需改动）
# ============================================================

def _sanitize_uid(uid):
    return "".join(ch for ch in str(uid or "")
                   if ch.isalnum() or ch in ("-", "_")) or "anonymous"


def _assert_task_owner(task_id):
    """任务必须存在且属于当前用户，否则返回 None（统一按 404 处理，不泄露存在性）。"""
    task = store.get(task_id)
    if not task or _sanitize_uid(task.get("user_id")) != _sanitize_uid(_uid()):
        return None
    return task


def _resolve_task_base(task):
    """解析产物根目录（out/ 目录），兼容 toolbox 临时目录与 ai_agent 异步目录。

    - inplace/工具箱：result_json 带 _temp_id → temp/<temp_id>/out
    - 异步办公：result_json 带 _out_dir → 该绝对路径（已是 out 目录）
    调用方（_assert_task_owner 已校验归属）才能拿到，杜绝跨用户越权。
    """
    rj = task.get("result_json") or {}
    temp_id = rj.get("_temp_id")
    out_dir = rj.get("_out_dir")
    if temp_id:
        try:
            from modules.toolbox.handlers import get_temp_dir
        except Exception:
            return None
        return os.path.join(get_temp_dir(), os.path.basename(str(temp_id)), "out")
    if out_dir:
        return out_dir
    return None


@bp.route("/api/out/<user_id>/<path:filename>")
@module_exception_guard("ai_agent")
@login_required
def api_out_file(user_id, filename):
    """按用户隔离的图表产物：只能取自己目录下的文件（用于 <img> 内联预览）。"""
    if _sanitize_uid(user_id) != _sanitize_uid(_uid()):
        return fail("无权访问该文件", code=403)
    name = os.path.basename(filename)
    if not name or name != filename:
        return fail("非法文件名", code=400)
    from . import executor as _ex
    path = _ex._out_path(user_id, name)
    if not os.path.isfile(path):
        return fail("文件不存在", code=404)
    return send_file(path)


@bp.route("/api/tasks/<int:task_id>/file/<path:filename>")
@module_exception_guard("ai_agent")
@login_required
def api_task_file(task_id, filename):
    """产物单文件：先校验任务归属，再按 result 里的目录定位（toolbox 或异步通用）。"""
    task = _assert_task_owner(task_id)
    if not task:
        return fail("任务不存在", code=404)
    name = os.path.basename(filename)
    if not name or name != filename:
        return fail("非法文件名", code=400)
    base = _resolve_task_base(task)
    if not base:
        return fail("该任务没有可下载的文件", code=404)
    path = os.path.join(base, name)
    if not os.path.isfile(path):
        return fail("文件不存在", code=404)
    return send_file(path)


@bp.route("/api/tasks/<int:task_id>/download")
@module_exception_guard("ai_agent")
@login_required
def api_task_download(task_id):
    """产物整包 ZIP：先校验任务归属，再打包 out 目录（toolbox / 异步通用）。"""
    task = _assert_task_owner(task_id)
    if not task:
        return fail("任务不存在", code=404)
    base = _resolve_task_base(task)
    if not base or not os.path.isdir(base):
        return fail("没有可下载的文件", code=404)
    zip_path = os.path.join(os.path.dirname(base), "result.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    shutil.make_archive(zip_path.replace(".zip", ""), "zip", base)
    return send_file(zip_path, as_attachment=True, download_name="agent_result.zip")
