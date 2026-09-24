import os
import time
import uuid
import json
import shutil
from flask import render_template, request, send_file, Response, stream_with_context, g
from modules.toolbox import bp
from core.auth import login_required
from core.response import ok, fail
from core.exceptions import module_exception_guard
from .handlers import dispatch, get_temp_dir
from . import workspace
from . import ai_assistant


def ensure_temp():
    os.makedirs(get_temp_dir(), exist_ok=True)


# ---------- 临时目录归属----------
# 原先 temp_id 是一串裸 uuid，任何登录用户只要拿到/枚举到它，就能下载他人
# 的处理产物、甚至整目录 rmtree 删掉。这里把创建者写进临时目录内的
# _owner.json（下划线开头，_common.input_files() 会自动跳过，不会被当成输入文件），
# 读取类接口一律先校验归属。管理员/超管放行，便于排查问题。
def _temp_owner_file(temp_id):
    return os.path.join(get_temp_dir(), os.path.basename(str(temp_id or "")), "_owner.json")


def _write_temp_owner(temp_id, user):
    try:
        with open(_temp_owner_file(temp_id), "w", encoding="utf-8") as fh:
            json.dump({"user_id": (user or {}).get("id"),
                       "account": (user or {}).get("account")}, fh, ensure_ascii=False)
    except Exception:
        pass


def _check_temp_owner(temp_id):
    """校验当前用户是否有权访问该临时目录，返回 (allowed, err_msg)。"""
    user = getattr(g, "current_user", None) or {}
    if user.get("role") in ("admin", "superadmin"):
        return True, None
    owner = None
    try:
        with open(_temp_owner_file(temp_id), "r", encoding="utf-8") as fh:
            owner = (json.load(fh) or {}).get("user_id")
    except Exception:
        owner = None
    cur = user.get("id")
    if owner is None:
        # 无归属记录的临时目录（历史遗留 / 非本模块创建）一律拒绝，
        # 宁可让个别老链接失效，也不能让别人的产物被任意读取。
        return False, "无权访问该临时目录"
    if not cur or str(owner) != str(cur):
        return False, "无权访问该临时目录"
    return True, None


@bp.route("/")
@module_exception_guard("toolbox")
def index():
    # embedded=1：返回无 layout_base 的纯操作区，供 Hub 首页 iframe 内联展开
    if request.args.get("embedded") == "1":
        return render_template("toolbox/embedded.html")
    # 不再显式传 current_module_id：本模块已并入「工具箱 Hub」，归属导航项由
    # PluginScanner.HUB_PARENT_MODULE 在 app.py 统一归一化为 toolbox_hub。
    # （显式传参优先级高于全局注入，会把归一化结果覆盖掉，导致侧栏无任何高亮）
    return render_template("toolbox/index.html")


# ---------- 文件上传（所有工具共用） ----------
@bp.route("/api/upload", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_upload():
    ensure_temp()
    temp_id = str(uuid.uuid4())
    save_dir = os.path.join(get_temp_dir(), temp_id)
    os.makedirs(save_dir, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return fail("未选择文件", code=400)

    saved = []
    stored_order = []
    for f in files:
        if f.filename:
            # 防路径穿越：仅取原始文件名，并用 uuid 重命名落盘（与 office_tools 一致）
            safe_name = os.path.basename(f.filename)
            stored_name = f"{uuid.uuid4().hex}_{safe_name}"
            path = os.path.join(save_dir, stored_name)
            f.save(path)
            saved.append({"name": safe_name, "size": os.path.getsize(path)})
            stored_order.append(stored_name)

    # 记录真实上传顺序：落盘名带 uuid 前缀，按文件名排序后顺序是随机的，
    # 「两表匹配 / 名单比对」这类要区分「第一个表 / 第二个表」的工具必须有它。
    # 文件名以下划线开头，会被 _common.input_files() 自动跳过，不会被当成用户输入。
    try:
        with open(os.path.join(save_dir, "_upload_order.json"), "w", encoding="utf-8") as fh:
            json.dump(stored_order, fh, ensure_ascii=False)
    except Exception:
        pass  # 顺序只是增强信息，写失败不影响上传本身

    # 绑定创建者：后续下载/清理都要按此校验，避免他人 uuid 被冒用
    _write_temp_owner(temp_id, getattr(g, "current_user", None))

    return ok(data={"temp_id": temp_id, "files": saved})


# ---------- 统一处理入口 ----------
@bp.route("/api/<category>/<tool>", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_process(category, tool):
    ensure_temp()
    data = request.get_json(silent=True) or {}
    temp_id = data.get("temp_id")
    params = data.get("params", {})

    if not temp_id:
        # 纯前端工具（如文本工具）可能不需要 temp_id
        pass
    else:
        #处理前先确认这个 temp_id 是自己上传时拿到的
        _allowed, _err = _check_temp_owner(temp_id)
        if not _allowed:
            return fail(_err, code=403)

    result = dispatch(category, tool, temp_id, params)
    # handler 经 core.response.ok/fail 返回的是 Flask Response（jsonify），
    # 直接透传；仅当返回 dict 时才走 code 判断（兼容两种形态）
    if isinstance(result, Response):
        return result
    if result.get("code") != 200:
        return result
    return ok(data=result.get("data"))


# ---------- 下载结果 ----------
@bp.route("/api/download/<temp_id>/<filename>")
@module_exception_guard("toolbox")
@login_required
def api_download(temp_id, filename):
    _allowed, _err = _check_temp_owner(temp_id)
    if not _allowed:
        return fail(_err, code=403)
    safe_id = os.path.basename(temp_id)
    safe_name = os.path.basename(filename)
    path = os.path.join(get_temp_dir(), safe_id, "out", safe_name)
    if not os.path.exists(path):
        return fail("文件不存在", code=404)
    return send_file(path, as_attachment=True)


# ---------- 下载整个 ZIP ----------
@bp.route("/api/download-zip/<temp_id>")
@module_exception_guard("toolbox")
@login_required
def api_download_zip(temp_id):
    _allowed, _err = _check_temp_owner(temp_id)
    if not _allowed:
        return fail(_err, code=403)
    safe_id = os.path.basename(temp_id)
    out_dir = os.path.join(get_temp_dir(), safe_id, "out")
    if not os.path.exists(out_dir):
        return fail("没有可下载的文件", code=404)

    zip_path = os.path.join(get_temp_dir(), safe_id, "result.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)

    shutil.make_archive(zip_path.replace(".zip", ""), "zip", out_dir)
    return send_file(zip_path, as_attachment=True, download_name="toolbox_result.zip")


# ---------- 清理临时文件 ----------
@bp.route("/api/cleanup/<temp_id>", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_cleanup(temp_id):
    # 最危险的一个：原先任何登录用户都能 rmtree 掉别人的整个临时目录
    _allowed, _err = _check_temp_owner(temp_id)
    if not _allowed:
        return fail(_err, code=403)
    safe_id = os.path.basename(temp_id)
    path = os.path.join(get_temp_dir(), safe_id)
    # 二次确认落在 temp 目录内，避免 basename 之外的意外拼接
    if os.path.isdir(path) and os.path.dirname(os.path.abspath(path)) == os.path.abspath(get_temp_dir()):
        shutil.rmtree(path)
    return ok()

# ============================================================
# 工作区（持久文件池，跨工具复用）
# ============================================================
@bp.route("/api/workspace/list")
@module_exception_guard("toolbox")
@login_required
def api_workspace_list():
    """列出当前用户工作区文件。"""
    return ok(data={"files": workspace.list_files()})


@bp.route("/api/workspace/upload", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_workspace_upload():
    """上传文件到工作区（可多文件）。"""
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        return fail("未选择文件", code=400)

    saved = []
    for f in files:
        if not f.filename:
            continue
        try:
            name = workspace.save_file(f)
        except ValueError as e:
            return fail(str(e), code=400)
        if name:
            saved.append(name)
    return ok(data={"files": saved})


@bp.route("/api/workspace/delete", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_workspace_delete():
    """删除工作区文件（body: {"name": "xxx"}）。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return fail("缺少文件名", code=400)
    if not workspace.delete_file(name):
        return fail("文件不存在", code=404)
    return ok()


@bp.route("/api/workspace/file/<name>")
@module_exception_guard("toolbox")
@login_required
def api_workspace_file(name):
    """取用工作区文件（前端拉成 File 后走原上传链路）。"""
    path = workspace.file_path(name)
    if not path:
        return fail("文件不存在", code=404)
    return send_file(path, as_attachment=False, download_name=workspace.safe_name(name))


# ============================================================
# AI 工具助手（自然语言 -> JSON 计划 -> 预览确认后执行）
# ============================================================
@bp.route("/api/ai/plan", methods=["POST"])
@module_exception_guard("toolbox")
@login_required
def api_ai_plan():
    """
    生成工具执行计划。
    body: {"text": "需求描述", "tools": [{id,label,category,params:[...],needUpload}, ...]}
    返回 plan: {tool, label, category, params, files, reply}
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    tools = data.get("tools") or []
    if not text:
        return fail("请输入需求描述", code=400)
    if not tools:
        return fail("缺少工具清单", code=400)

    tools_by_id = {t.get("id"): t for t in tools if isinstance(t, dict) and t.get("id")}
    if not tools_by_id:
        return fail("工具清单为空", code=400)

    # 带上工作区文件名，AI 才能在计划里引用文件
    ws_files = [f["name"] for f in workspace.list_files()]

    from modules.ai_center.internal.qa import llm_generate
    prompt = ai_assistant.build_plan_prompt(text, list(tools_by_id.values()), ws_files)
    raw = llm_generate(prompt, max_tokens=600, temperature=0.2)
    if not raw:
        return fail("模型未响应，请检查 AI 中心后端配置")

    plan = ai_assistant.parse_plan(raw)
    clean, err = ai_assistant.validate_plan(plan, tools_by_id)
    if err:
        return fail(f"计划解析失败：{err}", data={"raw": raw[:400]})
    if clean is None:
        # AI 判断匹配不上，把 reply 原样带回
        reply = str((plan or {}).get("reply") or "没有找到匹配的工具")
        return ok(data={"plan": None, "reply": reply, "raw": raw[:400]})
    return ok(data={"plan": clean, "reply": clean.get("reply") or "", "raw": raw[:400]})
