"""原地执行器：只处理 execution=inplace 的能力。

能在这里跑的能力必须满足「全后端闭环、不依赖任何页面状态」。
目前只有 AI 图表满足；后续工具箱打通「工作区 → temp → dispatch」后，
照着 _run_chart 的模式加一个分支即可。

产物落在 static/ai_agent/out/<user_id>/ 下，通过本模块自己的授权路由按
「任务归属」放行（见 routes.py 的 /api/out 与 /api/tasks/<id>/file），
不再走 toolbox 的无归属校验下载接口，杜绝跨用户越权访问。
"""
import os
import json
import shutil
import uuid
import threading
from typing import Any, Dict
from urllib.parse import quote

from flask import current_app

from .connectors.base import EXEC_INPLACE, EXEC_ASYNC
from . import progress as _progress

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_OUT_BASE = os.path.join(_ROOT, "static", "ai_agent", "out")


def _out_dir_for(user_id: Any):
    uid = "".join(ch for ch in str(user_id or "anonymous")
                  if ch.isalnum() or ch in ("-", "_")) or "anonymous"
    d = os.path.join(_OUT_BASE, uid)
    os.makedirs(d, exist_ok=True)
    return d


def _out_url_for(user_id: Any) -> str:
    uid = "".join(ch for ch in str(user_id or "anonymous")
                  if ch.isalnum() or ch in ("-", "_")) or "anonymous"
    return f"/agent/api/out/{uid}"


def _out_path(user_id: Any, filename: str):
    """按用户隔离的图表产物落地路径；filename 只取文件名，杜绝路径穿越。"""
    name = os.path.basename(str(filename or ""))
    return os.path.join(_out_dir_for(user_id), name)


def run(cap, params: Dict[str, Any], user_id: Any = None,
        task_id: Any = None) -> Dict[str, Any]:
    """执行一个 inplace 能力，统一返回 {ok, result, error}。

    user_id / task_id 必须由调用方显式传入（不能依赖请求上下文 g），
    这样即便将来放到后台线程/进程里异步执行，也能正确锁定到「是谁的任务、
    从谁的工作区取文件、产物落到谁的目录」。
    """
    if cap is None:
        return {"ok": False, "error": "能力不存在"}
    if cap.execution != EXEC_INPLACE:
        return {"ok": False, "error": f"该能力不支持原地执行（{cap.execution}）"}
    if not user_id:
        return {"ok": False, "error": "缺少用户上下文，无法执行"}

    if cap.cid == "chart.ai_render":
        return _run_chart(params, user_id=user_id)
    if cap.cid.startswith("toolbox."):
        return _run_toolbox(cap, params, user_id=user_id, task_id=task_id)
    return {"ok": False, "error": f"尚未接入执行逻辑：{cap.cid}"}


def _run_toolbox(cap, params: Dict[str, Any], user_id: Any = None,
                 task_id: Any = None) -> Dict[str, Any]:
    """执行工具箱里「可原地完成」的能力。

    复用 handlers.dispatch()，一行工具箱代码都不用改：
      - dispatch 先查 "{category}_{tool}"，查不到再单独用 tool 查，
        所以 category 传空串、tool 传完整 id（如 recognition_qrcode_gen）即可命中
      - 产物落在 temp/toolbox/<temp_id>/out/，用本模块授权路由取（归属校验）

    输入文件统一来自**对应用户的工作区**（持久文件池）：前端只传文件名列表
    （params["__files"]），这里按 user_id 从工作区复制进临时目录，
    handlers 完全感知不到差别，也不会串到别人的文件。
    """
    try:
        from modules.toolbox.handlers import dispatch, get_temp_dir
        from modules.toolbox import workspace
    except Exception as e:
        return {"ok": False, "error": f"工具箱不可用：{e}"}

    if not cap.handler:
        return {"ok": False, "error": "该能力未声明后端处理器（handler）"}

    # 复制一份再 pop，避免污染调用方的 params
    clean = dict(params or {})
    picked = clean.pop("__files", None) or []

    if cap.needs_files and not picked:
        return {"ok": False, "error": "该能力需要输入文件，请在计划卡里选择或上传"}

    temp_id = uuid.uuid4().hex
    in_dir = os.path.join(get_temp_dir(), temp_id)
    try:
        os.makedirs(in_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"无法创建临时目录：{e}"}

    # 从【指定用户】的工作区取文件（显式传 uid，后台执行也正确）
    missing = []
    for name in picked:
        src = workspace.file_path(name, uid=user_id)
        if not src:
            missing.append(str(name))
            continue
        try:
            shutil.copy(src, os.path.join(in_dir, workspace.safe_name(name)))
        except Exception as e:
            return {"ok": False, "error": f"复制文件 {name} 失败：{e}"}
    if missing:
        return {"ok": False, "error": "这些文件不在你的工作区：" + "、".join(missing)}

    try:
        raw = dispatch("", cap.handler, temp_id, clean)
    except Exception as e:
        return {"ok": False, "error": f"执行异常：{e}"}

    return _normalize_toolbox_result(raw, temp_id, task_id)


def _normalize_toolbox_result(raw, temp_id: str, task_id: Any = None) -> Dict[str, Any]:
    """把工具箱的返回（Flask Response 或 dict）规整成统一结构。

    下载链接改为本模块的授权路由 /agent/api/tasks/<task_id>/...，
    路由会先校验「任务是不是当前用户的」再放行（见 routes.py）。
    """
    payload = None
    if hasattr(raw, "get_json"):
        try:
            payload = raw.get_json()
        except Exception:
            payload = None
    if isinstance(raw, dict):
        payload = raw
    if not isinstance(payload, dict):
        return {"ok": False, "error": "工具返回了无法解析的结果"}
    if payload.get("code") != 200:
        return {"ok": False, "error": payload.get("msg") or "工具执行失败"}

    data = payload.get("data") or {}

    # 纯文本型结果（如 JSON 格式化）：直接回显，不产生文件
    if isinstance(data.get("result"), str):
        return {"ok": True, "result": {
            "kind": "text",
            "text": data["result"],
            "title": "处理结果",
        }}

    # 文件型结果
    files = data.get("results") or []
    if not files:
        return {"ok": False, "error": "工具未产出任何文件"}

    items = []
    for f in files:
        name = f.get("name") if isinstance(f, dict) else str(f)
        if not name:
            continue
        url = (f"/agent/api/tasks/{task_id}/file/{quote(name)}"
               if task_id is not None else
               f"/toolbox/api/download/{temp_id}/{quote(name)}")
        items.append({
            "name": name,
            "size": f.get("size") if isinstance(f, dict) else None,
            "url": url,
        })
    if not items:
        return {"ok": False, "error": "工具未产出任何文件"}

    # 只出一个图片时直接预览，体验比给下载链接好
    if len(items) == 1 and items[0]["name"].lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return {"ok": True, "result": {
            "kind": "image",
            "url": items[0]["url"],
            "title": items[0]["name"],
            "_temp_id": temp_id,  # 仅供下载路由定位目录，前端无需处理
        }}

    return {"ok": True, "result": {
        "kind": "files",
        "files": items,
        "zip": (f"/agent/api/tasks/{task_id}/download"
                if task_id is not None else
                f"/toolbox/api/download-zip/{temp_id}"),
        "_temp_id": temp_id,
    }}


def _run_chart(params: Dict[str, Any], user_id: Any = None) -> Dict[str, Any]:
    """自然语言 → spec → PNG。复用 chart 模块已有的两条后端链路。

    产物按用户隔离写入 static/ai_agent/out/<user_id>/，通过本模块
    授权路由 /agent/api/out/<user_id>/<file> 访问（校验归属）。
    """
    text = (params or {}).get("text") or ""
    if not text:
        return {"ok": False, "error": "缺少图表描述"}

    try:
        from modules.chart.ai_chart import (
            build_chart_prompt, parse_chart_spec, render_spec,
        )
    except Exception as e:
        return {"ok": False, "error": f"图表引擎不可用：{e}"}

    try:
        from modules.ai_center.internal.qa import llm_generate
        raw = llm_generate(build_chart_prompt(text), max_tokens=700, temperature=0.2)
    except Exception as e:
        return {"ok": False, "error": f"模型调用失败：{e}"}

    if not raw:
        return {"ok": False, "error": "模型未响应，请检查 AI 中心后端配置"}

    spec = parse_chart_spec(raw)
    if not isinstance(spec, dict):
        return {"ok": False, "error": "模型没有返回可解析的图表结构"}

    theme = (params or {}).get("theme") or "quantum"
    try:
        png = render_spec(spec, theme=theme, dark=False)
    except Exception as e:
        return {"ok": False, "error": f"出图失败：{e}"}

    name = f"chart_{uuid.uuid4().hex[:10]}.png"
    with open(_out_path(user_id, name), "wb") as f:
        f.write(png)

    return {
        "ok": True,
        "result": {
            "kind": "image",
            "url": f"{_out_url_for(user_id)}/{name}",
            "title": spec.get("title") or "AI 生成的图表",
            "spec": spec,
        },
    }


# ============================================================
# 异步执行：慢任务（办公自动化等）后台线程跑，前端轮询进度
# ============================================================

def run_async(cap, params: Dict[str, Any], user_id: Any = None,
              task_id: Any = None) -> Dict[str, Any]:
    """提交一个异步能力，立即返回（任务进入后台线程执行）。

    与 run() 一样必须显式带 user_id / task_id，后台线程不再依赖请求上下文 g，
    所以即便在独立线程里也能正确锁定「是谁的任务、从谁的工作区取文件」。
    """
    if cap is None:
        return {"ok": False, "error": "能力不存在"}
    if cap.execution != EXEC_ASYNC:
        return {"ok": False, "error": f"该能力不是异步执行方式（{cap.execution}）"}
    if not user_id:
        return {"ok": False, "error": "缺少用户上下文，无法执行"}
    if task_id is None:
        return {"ok": False, "error": "缺少任务标识，无法执行"}

    work_dir = _progress.work_dir(task_id)
    # workflow 流程：输入文件在 worker 里复制进 work_dir/flow 子目录——
    # 流程框架会在自己的 work_dir 里写 run.log/progress.json（与 ai_agent 的
    # 进度文件同名），必须目录隔离，所以流程 work_dir 用 work_dir/flow。
    is_flow = (cap.module == "workflow")
    in_dir = os.path.join(work_dir, "work")
    if not is_flow:
        os.makedirs(in_dir, exist_ok=True)

    # 从【指定用户】的工作区把输入文件复制进 work/（多用户隔离，不会串到别人）
    clean = dict(params or {})
    picked = clean.pop("__files", None) or []
    if cap.needs_files and not picked:
        return {"ok": False, "error": "该能力需要输入文件，请在计划卡里选择或上传"}
    if cap.needs_files and is_flow:
        # workflow 流程：这里只校验文件在不在工作区，真正的复制在 worker 里做
        try:
            from modules.toolbox import workspace
        except Exception as e:
            return {"ok": False, "error": f"工作区不可用：{e}"}
        missing = [str(n) for n in picked if not workspace.file_path(n, uid=user_id)]
        if missing:
            return {"ok": False, "error": "这些文件不在你的工作区：" + "、".join(missing)}
    if cap.needs_files and not is_flow:
        try:
            from modules.toolbox import workspace
        except Exception as e:
            return {"ok": False, "error": f"工作区不可用：{e}"}
        missing = []
        for name in picked:
            src = workspace.file_path(name, uid=user_id)
            if not src:
                missing.append(str(name))
                continue
            try:
                shutil.copy(src, os.path.join(in_dir, workspace.safe_name(name)))
            except Exception as e:
                return {"ok": False, "error": f"复制文件 {name} 失败：{e}"}
        if missing:
            return {"ok": False, "error": "这些文件不在你的工作区：" + "、".join(missing)}

        # 用后即清：输入文件已复制进任务目录（work/），从工作区移除
        # （用户要求：上传的文件不要保存）。清理失败不影响任务本身。
        removed = 0
        for name in picked:
            try:
                if workspace.delete_file(name, uid=user_id):
                    removed += 1
            except Exception:
                pass
        if removed:
            _progress.append_log(task_id, "running",
                                 f"已从工作区清理 {removed} 个输入文件（副本已随任务保留）")

    # 在请求上下文里捕获 app 实例传给线程：worker 里要调 workspace.file_path()
    # （内部用 current_app.root_path），而后台线程没有 app context，不传会直接
    # 抛 "Working outside of application context"（任务 46 的根因）。
    app_obj = current_app._get_current_object()
    t = threading.Thread(
        target=_async_worker,
        args=(cap, clean, user_id, task_id, work_dir, list(picked), app_obj),
        daemon=True,
    )
    t.start()
    return {"ok": True, "status": "running", "task_id": task_id,
            "message": "任务已提交，正在后台执行"}


def _async_worker(cap, params: Dict[str, Any], user_id: Any,
                  task_id: Any, work_dir: str, picked_files=None, app_obj=None):
    """后台线程：跑办公异步处理器，写进度、打包产物、更新任务状态。"""
    from . import store

    def _body():
        # workflow 私有 RPA 流程走独立分支（进度复用流程自身外抛的三件套文件）
        if cap.module == "workflow":
            # 兜底：worker 线程一旦因意外（如 launch_flow 抛异常、内部越界）死亡，
            # 必须有机会把任务标记 failed —— 否则会像之前那样永久卡在 running 且日志全无。
            try:
                _run_flow_worker(cap, params, user_id, task_id, work_dir, picked_files or [])
            except Exception as _e:
                _progress.write(task_id, 1, 1, "failed", f"流程执行异常：{_e}")
                store.set_status(task_id, store.STATUS_FAILED, error=f"流程执行异常：{_e}")
            return

        from . import office_runner

        _progress.write(task_id, 0, 1, "running", "正在准备…")
        try:
            def _cb(current, total, message):
                _progress.write(task_id, current, total, "running", message)

            office_runner.dispatch(cap.handler, work_dir, params, _cb)
            out_dir = os.path.join(work_dir, "out")
            items = _collect_async_files(out_dir, task_id)

            # 单张图片直接预览，体验更好；否则给文件列表 + 打包下载
            if len(items) == 1 and items[0]["name"].lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                result = {
                    "kind": "image",
                    "url": items[0]["url"],
                    "title": items[0]["name"],
                    "_out_dir": out_dir,
                }
            else:
                result = {
                    "kind": "files",
                    "files": items,
                    "zip": f"/agent/api/tasks/{task_id}/download",
                    "_out_dir": out_dir,
                }
            _progress.write(task_id, 1, 1, "done", "执行完成")
            store.set_status(task_id, store.STATUS_DONE, result=result)
        except Exception as e:
            _progress.write(task_id, 1, 1, "failed", f"失败：{e}")
            store.set_status(task_id, store.STATUS_FAILED, error=str(e) or "执行失败")

    # 后台线程没有 Flask app context，而流程/工作区代码会用到 current_app
    # （如 workspace.get_root() 取 root_path），必须手动推一个再干活。
    if app_obj is not None:
        with app_obj.app_context():
            _body()
    else:
        _body()


def _collect_async_files(out_dir: str, task_id: Any) -> list:
    """列出 out/ 下的产物，url 指向本模块的授权下载路由（先校验任务归属）。"""
    import urllib.parse as _up
    items = []
    if not os.path.isdir(out_dir):
        return items
    for name in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, name)
        if os.path.isfile(p):
            items.append({
                "name": name,
                "size": os.path.getsize(p),
                "url": f"/agent/api/tasks/{task_id}/file/{_up.quote(name)}",
            })
    return items


# ============================================================
# 工作自动化流程（workflow 私有 RPA）异步分支
# ============================================================

def _run_flow_worker(cap, params: Dict[str, Any], user_id: Any,
                     task_id: Any, work_dir: str, picked_files: list):
    """后台线程：跑一个工作自动化流程。

    - 流程 work_dir 用 work_dir/flow（与 ai_agent 自己的 progress.json 目录隔离）；
    - 输入文件从该用户工作区复制进 flow/（流程在自己 work_dir 根部找 Excel/zip）；
    - 启动复用 toolbox 的 automation.launcher（与 /workflow 页面同一套代码）；
    - 进度轮询流程外抛的 run.log / progress.json / done.flag 三件套，
      转写进 ai_agent 的进度文件，前端轮询端点零改动。
    """
    import time as _time
    from . import store

    flow_dir = os.path.join(work_dir, "flow")
    os.makedirs(flow_dir, exist_ok=True)

    def _fail(msg: str):
        _progress.write(task_id, 1, 1, "failed", msg)
        store.set_status(task_id, store.STATUS_FAILED, error=msg)

    # 输入文件：从该用户的工作区复制进 flow/（多用户隔离）
    if cap.needs_files:
        try:
            from modules.toolbox import workspace
        except Exception as e:
            _fail(f"工作区不可用：{e}")
            return
        for name in picked_files:
            src = workspace.file_path(name, uid=user_id)
            if not src:
                _fail("这些文件不在你的工作区：" + str(name))
                return
            try:
                shutil.copy(src, os.path.join(flow_dir, workspace.safe_name(name)))
            except Exception as e:
                _fail(f"复制文件 {name} 失败：{e}")
                return

        # 用后即清：输入文件已复制进任务目录（flow/），从工作区移除，
        # 不再长期占用文件池（用户要求：上传的文件不要保存）。
        # 清理失败不影响任务本身；副本始终保留在任务目录里。
        removed = 0
        for name in picked_files:
            try:
                if workspace.delete_file(name, uid=user_id):
                    removed += 1
            except Exception:
                pass
        if removed:
            _progress.append_log(task_id, "running",
                                 f"已从工作区清理 {removed} 个输入文件（副本已随任务保留）")

    _progress.write(task_id, 0, 1, "running", "正在启动自动化流程…")
    try:
        from modules.toolbox.automation.launcher import launch_flow
    except Exception as e:
        _fail(f"自动化引擎不可用：{e}")
        return
    try:
        launch_ok, _code, msg = launch_flow(cap.handler, flow_dir, params)
    except Exception as e:
        _fail(f"启动自动化流程异常：{e}")
        return
    if not launch_ok:
        _fail(msg)
        return

    # 轮询流程进度三件套（与 toolbox 的 SSE 端点同一套约定）
    log_path = os.path.join(flow_dir, "run.log")
    prog_path = os.path.join(flow_dir, "progress.json")
    done_path = os.path.join(flow_dir, "done.flag")

    last_len = 0
    last_line = ""
    tail_buf = b""  # 上轮没换行完的半行字节（多字节 UTF-8 不能拦腰截断）
    prog = {}
    deadline = _time.time() + 1800  # 与 toolbox SSE 一致：上限约 30 分钟
    timed_out = True

    def _drain_flow_log(status="running"):
        """把流程新产生的日志行全部并进 agent 的 progress.log。

        以前只保留最后一行，流程结束时那串「1. 甲公司 ✅ 完成 / 2. 乙公司 ❌ 失败：…」
        就丢了，用户看不到到底哪些成功哪些失败。现在整段原样并入。

        ⚠️ 偏移必须用「字节数」推进：run.log 是 UTF-8 多字节，若用 len(new)
        （字符数）推进会越追越慢，导致重复发同一行 + 字节边界乱码。
        """
        nonlocal last_len, last_line, tail_buf
        try:
            if not os.path.exists(log_path):
                return
            with open(log_path, "rb") as f:
                f.seek(last_len)
                chunk = f.read()
            if not chunk:
                return
            last_len += len(chunk)
            tail_buf += chunk
            parts = tail_buf.split(b"\n")
            tail_buf = parts[-1]
            for raw in parts[:-1]:
                ln = raw.decode("utf-8", errors="replace").rstrip("\r").strip()
                if ln:
                    last_line = ln
                    _progress.append_log(task_id, status, ln)
        except Exception:
            pass

    while _time.time() < deadline:
        _drain_flow_log()
        try:
            if os.path.exists(prog_path):
                with open(prog_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                if raw:
                    prog = json.loads(raw)
        except Exception:
            pass
        cur = prog.get("current", 0)
        tot = prog.get("total", 1) or 1
        # 快照只更新进度条，日志行已由 _drain_flow_log 逐行写入（避免重复）
        _progress.write(task_id, cur, tot, "running", last_line, log=False)
        if os.path.exists(done_path):
            timed_out = False
            break
        _time.sleep(1)

    if timed_out:
        _fail("流程超时未结束（30 分钟上限），已放弃等待")
        return

    # 流程自身报告失败（如缺账号/缺 Excel）
    if str(prog.get("status") or "") == "failed" or prog.get("error"):
        _fail(str(prog.get("error") or "流程执行失败"))
        return

    # done.flag 出现后再捞一次尾部日志（汇总行与逐条明细都是最后一刻写的）
    _drain_flow_log(status="done")

    bits = []
    if "success" in prog or "fail" in prog:
        bits.append(f"成功 {prog.get('success', 0)} 个，失败 {prog.get('fail', 0)} 个")
    summary = "自动化流程执行完成" + ("：" + "，".join(bits) if bits else "")
    _progress.write(task_id, 1, 1, "done", summary)

    # 收集产物：flow/out/* + flow/screenshots/* + run.log → work_dir/out（agent 统一出口）
    out_dir = os.path.join(work_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    for src_dir in (os.path.join(flow_dir, "out"),
                    os.path.join(flow_dir, "screenshots")):
        if os.path.isdir(src_dir):
            for fn in sorted(os.listdir(src_dir)):
                src = os.path.join(src_dir, fn)
                if os.path.isfile(src):
                    dst = os.path.join(out_dir, fn)
                    if not os.path.exists(dst):
                        try:
                            shutil.copy(src, dst)
                        except Exception:
                            pass
    if os.path.isfile(log_path):
        try:
            shutil.copy(log_path, os.path.join(out_dir, "run.log"))
        except Exception:
            pass

    items = _collect_async_files(out_dir, task_id)
    if items:
        result = {
            "kind": "files",
            "files": items,
            "zip": f"/agent/api/tasks/{task_id}/download",
            "_out_dir": out_dir,
            "summary": summary,
        }
    else:
        result = {"kind": "text", "text": summary, "title": "执行结果"}
    store.set_status(task_id, store.STATUS_DONE, result=result)
