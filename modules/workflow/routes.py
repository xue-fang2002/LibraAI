"""
工作自动化流程模块路由。

权限模型：
- 侧栏可见性：modules.yaml 设 public:false → 非管理员不在侧栏出现；
- 路由级门禁：before_request 用 user_can_access_module 校验 module_access 表，
  未授权页面请求渲染 forbidden 页、AJAX/SSE 返回 403，杜绝深链绕过；
- 默认权限种子：首次访问时若 module_access 无 workflow 记录，写入 level=admin
  （仅管理员可见可跑）。要放开给具体内部用户，到 配置中心 → 模块访问权限 改成
  「指定成员」并勾选用户即可。
"""
import os
import json
import time

from flask import (
    g,
    request,
    jsonify,
    render_template,
    Response,
    stream_with_context,
)

from modules.workflow import bp
from core.auth import login_required
from core.response import ok
from core.exceptions import module_exception_guard
from core.db_base import (
    user_can_access_module,
    get_module_access_map,
    set_module_access,
)


# ---------- 权限门禁（所有 workflow 路由统一拦截） ----------
def _ensure_access_seed():
    """保证 module_access 表存在 workflow 记录；默认仅管理员可见可跑。"""
    try:
        if "workflow" not in get_module_access_map():
            set_module_access("workflow", "admin")
    except Exception:
        pass


@bp.before_request
def _workflow_guard():
    # 侧栏高亮归属
    g.current_module_id = "workflow"
    _ensure_access_seed()
    user = g.get("current_user")
    if user_can_access_module(user, "workflow"):
        return
    # 未登录：沿用项目约定（AJAX 401 JSON / 页面登录提示）
    if not user:
        from core.auth import need_login_response
        return need_login_response(request.path)
    # 已登录但无权限
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.endswith(".json")
        or "/api/" in request.path
    )
    if is_ajax:
        return jsonify({"code": 403, "msg": "无权访问工作自动化模块"}), 403
    return render_template("workflow/forbidden.html"), 403


# ---------- 页面 ----------
@bp.route("/")
@module_exception_guard("workflow")
@login_required
def index():
    # flows 由服务端注入模板（同步、无竞态）：页面内联脚本把它们写进 TOOLBOX_CONFIG，
    # 之后复用工具箱前端本尊（style.css + components + app.js）渲染 —— 界面与原
    # toolbox「工作自动化流程」分类完全一致，不再自造 UI。
    from modules.toolbox.automation.registry import get_flows
    return render_template("workflow/index.html", flows=get_flows())


# ---------- 流程列表（调试/外部取数用；页面渲染走模板注入） ----------
@bp.route("/api/flows")
@module_exception_guard("workflow")
@login_required
def api_flows():
    from modules.toolbox.automation.registry import get_flows
    return ok(data={"flows": get_flows()})


# ---------- SSE 实时进度（与 toolbox 完全一致的 work_dir 约定） ----------
# 两条路径：自有形态 + 工具箱前端形态（app.js 拼的是
# ${TOOLBOX_API_BASE}/toolbox/api/<category>/<tool>/stream/<temp_id>）
@bp.route("/api/flows/<flow_id>/stream/<temp_id>")
@bp.route("/toolbox/api/automation/<flow_id>/stream/<temp_id>")
@module_exception_guard("workflow")
@login_required
def api_stream(flow_id, temp_id):
    from modules.toolbox.tools._common import get_temp_dir
    safe_id = os.path.basename(temp_id)
    work_dir = os.path.join(get_temp_dir(), safe_id)
    log_path = os.path.join(work_dir, "run.log")
    prog_path = os.path.join(work_dir, "progress.json")
    done_path = os.path.join(work_dir, "done.flag")

    def gen():
        # last 必须是「字节偏移」：run.log 是 UTF-8 多字节，若用字符数推进
        # （len(new)）会越追越慢，每次轮询都回头重读尾部 → 日志重复 + 字节边界截断出
        # 乱码（如把「✅ 无历史漏打卡，直接处理本月」读成「，直接处理本月」）。
        last = 0
        tail = b""  # 上轮没换行完的半行字节，避免把多字节字符拦腰截断
        last_prog = ""
        cancelled_path = os.path.join(work_dir, "cancelled.flag")
        if not os.path.isdir(work_dir):
            yield "data: " + json.dumps({"type": "error", "msg": "任务目录不存在或已过期"}, ensure_ascii=False) + "\n\n"
            return
        yield "data: " + json.dumps({"type": "start"}, ensure_ascii=False) + "\n\n"
        for _ in range(1800):  # 上限约 30 分钟
            if os.path.exists(log_path):
                try:
                    with open(log_path, "rb") as f:
                        f.seek(last)
                        chunk = f.read()
                    if chunk:
                        last += len(chunk)
                        tail += chunk
                        parts = tail.split(b"\n")
                        tail = parts[-1]
                        for raw in parts[:-1]:
                            line = raw.decode("utf-8", errors="replace").rstrip("\r")
                            if line.strip():
                                yield "data: " + json.dumps({"type": "log", "msg": line}, ensure_ascii=False) + "\n\n"
                except Exception:
                    pass
            if os.path.exists(prog_path):
                try:
                    with open(prog_path, "r", encoding="utf-8") as f:
                        raw = f.read().strip()
                    if raw and raw != last_prog:
                        last_prog = raw
                        data = json.loads(raw)
                        yield "data: " + json.dumps({"type": "progress", "data": data}, ensure_ascii=False) + "\n\n"
                except Exception:
                    pass
            if os.path.exists(cancelled_path):
                yield "data: " + json.dumps({"type": "stopped"}, ensure_ascii=False) + "\n\n"
                break
            if os.path.exists(done_path):
                yield "data: " + json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n"
                break
            time.sleep(1)
        else:
            # for-else：只有循环**未被 break**（既没等到 done 也没被取消、1800 次轮询耗尽）
            # 才发 timeout。原先这行写在循环外，导致正常完成/取消后必定再补一条 timeout，
            # 前端刚渲染的「已完成」被「超时」覆盖。
            yield "data: " + json.dumps({"type": "timeout"}, ensure_ascii=False) + "\n\n"

    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ============================================================
# 工具箱前端桥接端点
# ------------------------------------------------------------
# workflow 页面复用工具箱前端本尊（app.js / result-panel.js），其请求与下载
# 链接按 ${TOOLBOX_API_BASE}/toolbox/api/... 拼接（TOOLBOX_API_BASE=/workflow）。
# 这里按相同形态挂桥接路由，内部直接委托 toolbox 的既有 view 函数——
# 零逻辑复制，上传目录 / 任务目录 / 下载目录全部共用一套 temp/toolbox。
# 注意：私有流程的启动只经本模块（带权限门禁），toolbox 侧无 /api/automation/*。
# ============================================================
def _toolbox_views():
    """懒导入 toolbox 的 view 函数（避免跨模块顶层 import 触发 reload 陷阱）。"""
    from modules.toolbox import routes as tb
    return tb


@bp.route("/toolbox/api/upload", methods=["POST"])
@module_exception_guard("workflow")
@login_required
def bridge_upload():
    return _toolbox_views().api_upload()


@bp.route("/toolbox/api/automation/<tool>", methods=["POST"])
@module_exception_guard("workflow")
@login_required
def bridge_process(tool):
    # 与 toolbox 通用入口同构：dispatch(category="automation", tool=flow_id)
    return _toolbox_views().api_process("automation", tool)


@bp.route("/toolbox/api/download/<temp_id>/<filename>")
@module_exception_guard("workflow")
@login_required
def bridge_download(temp_id, filename):
    return _toolbox_views().api_download(temp_id, filename)


@bp.route("/toolbox/api/download-zip/<temp_id>")
@module_exception_guard("workflow")
@login_required
def bridge_download_zip(temp_id):
    return _toolbox_views().api_download_zip(temp_id)


@bp.route("/toolbox/api/cleanup/<temp_id>", methods=["POST"])
@module_exception_guard("workflow")
@login_required
def bridge_cleanup(temp_id):
    return _toolbox_views().api_cleanup(temp_id)


# ---------- 停止正在运行的流程 ----------
@bp.route("/api/flows/<flow_id>/cancel/<temp_id>", methods=["POST"])
@bp.route("/toolbox/api/automation/<tool>/cancel/<temp_id>", methods=["POST"])
@module_exception_guard("workflow")
@login_required
def api_cancel(flow_id=None, tool=None, temp_id=None):
    from modules.toolbox.tools._common import get_temp_dir
    from modules.toolbox.automation.launcher import cancel_flow

    fid = flow_id or tool
    safe_id = os.path.basename(temp_id or "")
    work_dir = os.path.join(get_temp_dir(), safe_id)
    if not os.path.isdir(work_dir):
        return ok(data={"cancelled": False, "msg": "任务目录不存在或已结束"})
    ok_flag, msg = cancel_flow(work_dir)
    return ok(data={"cancelled": ok_flag, "msg": msg, "flow_id": fid})
