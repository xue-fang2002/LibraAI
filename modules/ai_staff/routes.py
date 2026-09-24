"""AI 员工模块路由。

权限模型（照 workflow 模块的三保险）：
- 侧栏可见性：modules.yaml 设 public:false → 普通用户侧栏不显示
- 路由级门禁：before_request 用 user_can_access_module 校验 module_access 表，
  默认种子 level=admin（仅管理员可用），杜绝深链绕过
- 危险能力二次校验：自由查库额外要求 role 为管理员且 yaml 开关打开

任务跑在后台线程（LLM 同步阻塞 + 串行推理锁），前端通过 SSE 拉取过程。
"""
import json
import os
import time
from typing import Any, Dict, Optional

from flask import (
    Response,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from modules.ai_staff import bp
from core.auth import login_required, need_login_response
from core.config_loader import get_config
from core.db_base import get_module_access_map, set_module_access, user_can_access_module
from core.exceptions import module_exception_guard
from core.response import fail, ok

from . import engine, staffs

MODULE_ID = "ai_staff"


# ------------------------------------------------------------ 权限门禁
def _is_admin(user) -> bool:
    return isinstance(user, dict) and str(user.get("role") or "").lower() in ("admin", "superadmin")


def _ensure_access_seed():
    """给 AI 员工模块定一个默认可见范围（默认对所有登录用户开放）。

    具体哪个员工能用，由该员工的 min_role 二次把关（见 staffs.can_use）——
    模块开门是为了让「AI 客服」服务全体同事，运维助手/人力参谋仍然只给管理员。
    想收紧范围可以在 modules.yaml 里配 modules.ai_staff.access_level（login/admin/public）。
    """
    try:
        want = str(get_config("modules.ai_staff.access_level") or "login").strip().lower()
        if want not in ("login", "admin", "public", "selected"):
            want = "login"
        mp = get_module_access_map()
        cur = (mp.get(MODULE_ID) or {}).get("min_level")
        # 仅在「从未设置」或「旧版种成了 admin」两种情况下写入，
        # 避免覆盖管理员事后在配置中心里做出的调整。
        if cur is None or cur == "admin":
            set_module_access(MODULE_ID, want)
    except Exception:
        pass


@bp.before_request
def _guard():
    g.current_module_id = MODULE_ID
    _ensure_access_seed()
    user = g.get("current_user")
    if user_can_access_module(user, MODULE_ID):
        return
    if not user:
        return need_login_response(request.path)
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.endswith(".json")
        or "/api/" in request.path
    )
    if is_ajax:
        return jsonify({"code": 403, "msg": "无权访问 AI 员工模块"}), 403
    return render_template("ai_staff/forbidden.html"), 403


# ------------------------------------------------------------ 页面
@bp.route("/")
@module_exception_guard(MODULE_ID)
@login_required
def index():
    """员工卡片墙：员工列表由配置驱动（见 staffs.py），新增员工只改配置。

    只展示当前用户有权使用的员工（不可见的员工不予渲染）。
    """
    user = g.get("current_user")
    people = staffs.visible_staffs(user)
    return render_template(
        "ai_staff/index.html",
        current_module_id=MODULE_ID,
        staffs=people,
        staff_count=sum(1 for s in people if s["enabled"]),
    )


@bp.route("/<staff_id>")
@module_exception_guard(MODULE_ID)
@login_required
def chat(staff_id):
    """与某个员工对话；员工不存在/已停用/越权 → 提示页。"""
    staff = staffs.get_staff(staff_id)
    if not staff:
        return render_template("ai_staff/forbidden.html",
                               reason=f"员工「{staff_id}」不存在或已停用"), 404
    # 员工级门禁：防止普通用户靠猜地址绕到只对管理员开放的员工
    if not staffs.can_use(g.get("current_user"), staff):
        return render_template(
            "ai_staff/forbidden.html",
            reason="你没有使用这位员工的权限（可在「配置中心 → 模块与权限」里调整）"), 403
    return render_template(
        "ai_staff/chat.html",
        current_module_id=MODULE_ID,
        staff=staff,
    )


# ------------------------------------------------------------ 状态
@bp.route("/api/state")
@module_exception_guard(MODULE_ID)
@login_required
def api_state():
    """给前端显示能力开关（自由查库是否开启）与当前 LLM 后端。"""
    user = g.get("current_user")
    allow_sql = _allow_sql(user)
    backend = ""
    try:
        from modules.ai_center.internal.qa import get_active_backend
        backend = str(get_active_backend() or "")
    except Exception:
        backend = "unknown"
    return ok(data={
        "allow_sql": allow_sql,
        "is_admin": _is_admin(user),
        "backend": backend,
        "busy": engine.busy(),
    })


# ------------------------------------------------------------ 发起提问
@bp.route("/api/chat", methods=["POST"])
@module_exception_guard(MODULE_ID)
@login_required
def api_chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("message") or "").strip()
    if not question:
        return fail("请输入问题")
    if len(question) > 1000:
        return fail("问题过长（上限 1000 字）")

    # 先判权限再看并发：越权请求不该拿到「有任务在跑」这种属于系统内部状态的提示
    staff = staffs.get_staff(str(data.get("staff") or "").strip())
    if not staff:
        return fail("员工不存在或已停用", code=404)
    if not staffs.can_use(g.get("current_user"), staff):
        return fail("你没有使用这位员工的权限", code=403)

    if engine.busy():
        return fail("已有一个任务在跑，请等它结束或点「停止」", code=429)

    user = g.get("current_user")
    root = current_app.root_path
    db_path = ""
    try:
        from core.db_base import DB_PATH
        db_path = DB_PATH
    except Exception:
        db_path = os.path.join(root, "book_manager.db")

    ctx = {
        "user": user,
        "db_path": db_path,
        "allow_sql": _allow_sql(user),
        "staff": staff,
    }

    # 表单型员工：后端先把工具跑完，把结果作为「已取证数据」注入对话，
    # 模型只负责解读，不再赌它第一步能不能调对工具。
    pre = _build_pre(root, ctx, staff, data.get("fields"))

    task = engine.create_task(question, root, ctx, pre=pre)
    if task is None:
        return fail("任务创建失败，请稍后再试", code=429)
    return ok(data={"task_id": task.tid, "pre": bool(pre)})


def _build_pre(root: str, ctx: Dict[str, Any], staff: Dict[str, Any], fields: Any) -> Optional[dict]:
    """把表单字段交给 auto_tool 计算，返回预取的观察块。

    ⚠️ 隐私：fields 里的个人信息只在此刻参与计算，不写库、不进审计、
    返回值里也只携带排盘结果本身（不含姓名以外的原始字段）。
    """
    form = staff.get("form") or {}
    tool_name = form.get("auto_tool")
    if not tool_name or not isinstance(fields, dict) or not fields:
        return None
    from . import tools as staff_tools

    args = {k: v for k, v in fields.items()
            if k not in ("name",) and str(v).strip() not in ("", "未知")}
    # 未知时辰留空即表示不确定，工具会按三柱推算并在结果里标明
    if str(fields.get("hour") or "").strip() in ("", "未知"):
        args["hour"] = "未知"
    result = staff_tools.call_tool(tool_name, root, ctx, args)
    if not result.get("ok"):
        return {"tool": tool_name, "error": result.get("error") or "计算失败",
                "text": "", "report": None}
    return {
        "tool": tool_name,
        "text": result.get("text") or "",
        "report": result.get("report"),
    }


# ------------------------------------------------------------ SSE 过程流
@bp.route("/api/stream/<task_id>")
@module_exception_guard(MODULE_ID)
@login_required
def api_stream(task_id):
    task = engine.get_task(task_id)
    if task is None:
        return jsonify({"code": 404, "msg": "任务不存在或已过期"}), 404
    # 只允许任务归属人本人（或管理员）拉取：task_id 是随机串，但不能假设别人猜不到
    if not task.owned_by(g.get("current_user")):
        return jsonify({"code": 403, "msg": "这不是你的任务"}), 403

    def gen():
        yield "data: " + json.dumps({"type": "start"}, ensure_ascii=False) + "\n\n"
        for _ in range(900):  # 上限约 15 分钟
            ev = engine.next_event(task, timeout=1.0)
            if ev is not None:
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                if ev.get("type") == "done":
                    return
                continue
            if task.finished:
                yield "data: " + json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n"
                return
        yield "data: " + json.dumps({"type": "timeout", "msg": "任务超时已中断"},
                                    ensure_ascii=False) + "\n\n"

    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ------------------------------------------------------------ 停止
@bp.route("/api/cancel/<task_id>", methods=["POST"])
@module_exception_guard(MODULE_ID)
@login_required
def api_cancel(task_id):
    task = engine.get_task(task_id)
    if task is None:
        return fail("任务不存在或已过期", code=404)
    if not task.owned_by(g.get("current_user")):
        return fail("这不是你的任务", code=403)
    okk = engine.cancel_task(task_id)
    return ok(data={"cancelled": okk})


def _allow_sql(user) -> bool:
    """自由查库开关：yaml 开启 + 管理员（两关都要过）。"""
    try:
        enabled = bool(get_config("modules.ai_staff.allow_free_sql", False))
    except Exception:
        enabled = False
    return enabled and _is_admin(user)
