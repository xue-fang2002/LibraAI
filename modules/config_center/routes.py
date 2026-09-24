"""
配置中心路由（前缀 /config，仅管理员）。

- 聚合页 index：展示 图书馆 / AI中心 / 用户与权限 三个分组入口
- 用户管理 users、操作日志 logs、AI 配置 ai：从 book_lib 收敛而来
- 用户 / 日志 / AI 配置相关 API：前缀 /config/api/*

所有管理接口最低要求 config_access 权限（管理员默认拥有）；
图书级 AI 问答/摘要接口（/config/api/ai/summary、/config/api/ai/ask）仅要求登录，
与图书详情页的调用行为保持一致。
"""
import os
import time
import threading
import uuid
import json
import yaml
from datetime import datetime
from flask import (
    render_template, request, jsonify, g, url_for,
)

from modules.config_center import bp
from core.auth import login_required, permission_required
from core.response import ok, fail
from core.db_base import (
    add_log, get_all_users, get_all_books, get_book_by_id,
    get_document_chunks, update_book_ai_fields, add_ai_log,
    get_logs, get_ai_tasks, get_ai_logs, get_db,
    create_ai_task, update_ai_task, get_books_need_indexing, mark_book_indexed,
    add_user, get_user_by_account, get_user_by_id,
    update_user_password, toggle_user_active, update_user_permissions,
    user_can_view_book, ALL_PERMISSIONS,
)
from core.exceptions import module_exception_guard, PermissionDeniedException
from core.ai_interface import (
    is_ai_enabled, get_ai_features, generate_book_summary, ask_book_question,
    trigger_rebuild_for_book, get_vector_stats, _try_load_ai_center,
)
from core.config_loader import get_ai_mode_from_config, is_module_enabled
from core.plugin_scan import get_config_entries_grouped, get_permissions_grouped, get_all_module_info, PluginScanner
from common.utils import (
    get_client_ip, sanitize_text, resolve_file_path, BASE_DIR,
)
from core.ai_config_store import (
    read_ai_config, set_ai_mode, set_ai_backend, set_ai_model_paths, set_security_lock,
)
from modules.ai_center.internal import config as ai_internal_config
from core.db_base import (
    clean_orphan_vectors, get_all_categories, get_all_books,
    set_resource_permission, delete_resource_permission,
    get_all_resource_permissions, get_all_resource_permission_details,
    get_module_access_map, set_module_access, delete_module_access,
)


def _require_admin():
    """校验当前用户为管理员，否则抛出 PermissionDeniedException。

    成功时返回当前用户字典，供调用方记录操作日志（user["id"] 等）。
    """
    u = g.get("current_user")
    if not u or u.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可访问此接口")
    return u


def _is_superadmin(user) -> bool:
    """是否超级管理员。

    admin 与 superadmin 在**业务权限**上完全等价（全项目判定都是
    `role in ("admin","superadmin")`），本次分级只区分**账号管理权**：
    创建/删除管理员账号属于提权操作，仅 superadmin 可为。
    否则 admin 可以无限造 admin，superadmin 这一级就形同虚设。
    """
    return bool(user and user.get("role") == "superadmin")


# ============================================================
# 聚合页
# ============================================================
@bp.route("/", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def index():
    # 横向大模块行：除配置中心自身外的所有已注册模块
    _module_urls = {
        "book_lib": "/book/config",
        "homepage": "/home",
        "chart": "/chart",
        "ai_center": "/config/ai",
    }
    _non_nav = PluginScanner.NON_NAV_MODULES
    modules_row = []
    try:
        for mid, info in get_all_module_info().items():
            if mid == "config_center":
                continue
            # 与侧边栏保持一致：被「工具箱 Hub」合并收口的模块不在配置中心单独列出，
            # 避免重复出现多个「工具箱」/同类入口（路由仍可用，从 Hub 进入）
            if mid in PluginScanner.HUB_MERGED_MODULES:
                continue
            # AI 中心是 NON_NAV（不在侧边栏），但配置中心是管理后台
            # 必须给管理员一个入口配置 AI 模式/后端/模型路径，因此放行 ai_center
            # 渲染逻辑：仍走通用"进入"卡片
            modules_row.append({
                "module_id": mid,
                "display_name": info.get("display_name", mid),
                "icon": info.get("icon", "📄"),
                "description": info.get("description", ""),
                "url": _module_urls.get(mid, info.get("route_prefix", "/")),
            })
    except Exception:
        modules_row = []

    # 图书馆模块内嵌数据：书、分类、当前权限级别 + 原始 /book/config 全功能数据
    books = get_all_books() or []
    cats = get_all_categories() or []
    rules = get_all_resource_permission_details() or []
    book_levels = {r["resource_id"]: r["permission_level"] for r in rules if r["resource_type"] == "book"}
    cat1_levels = {r["resource_id"]: r["permission_level"] for r in rules if r["resource_type"] == "cat1"}
    cat2_levels = {r["resource_id"]: r["permission_level"] for r in rules if r["resource_type"] == "cat2"}
    cat_map = {c["id"]: c for c in cats}
    users = get_all_users() or []
    for u in users:
        u.pop("password_hash", None)

    # 规则表（可读名称）
    for r in rules:
        rid = r.get("resource_id")
        if r["resource_type"] == "cat1":
            r["resource_name"] = (cat_map.get(rid) or {}).get("name", rid)
            r["type_label"] = "一级分类"
        elif r["resource_type"] == "cat2":
            r["resource_name"] = (cat_map.get(rid) or {}).get("name", rid)
            r["type_label"] = "二级分类"
        else:
            r["resource_name"] = (book_map := {b["id"]: b for b in books}).get(rid, {}).get("name", rid)
            r["type_label"] = "单本图书"

    # ---- 原始 /book/config 全功能数据（嵌入图书馆 tab） ----
    cat1_list = [c for c in cats if not c.get("parent_id")]
    cat1_list.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    nav_first = [c.get("name") for c in cat1_list]
    layout = {}
    cat_id_map = {}
    cat_colors = {}
    for c in cats:
        cat_id_map[c.get("name")] = c.get("id")
        if c.get("color"):
            cat_colors[c.get("name")] = c.get("color")
    for c in cats:
        if c.get("parent_id"):
            parent_name = None
            for pc in cats:
                if str(pc.get("id")) == str(c.get("parent_id")):
                    parent_name = pc.get("name"); break
            if parent_name:
                layout.setdefault(parent_name, []).append(c.get("name") or "")
    # 管理员拥有全部权限
    user_perms = [
        "book_add", "book_edit", "book_delete", "book_batch_delete",
        "book_batch_move", "book_batch_add", "c1_add", "c1_edit",
        "c1_delete", "c1_move", "c2_add", "c2_edit", "c2_delete",
        "c2_move", "file_upload", "user_manage", "view_all_records",
        "config_access", "admin",
    ]

    # ---- 问答档位（ai_center.qa_mode）：分类 → 档位，供「分类管理」行尾下拉使用 ----
    # 分类只存「档位名」，由档位决定相关性阈值 min_score 与 OCR 扫描页数；
    # 取值按 单本 qa_mode > cat1/cat2 > cat1 > 全局默认 回落。
    # 这里顺带过滤掉历史脏键（超过两级等），脏键不进前端。
    try:
        from core.config_loader import get_config as _gc
        from core.ai_config_store import is_valid_category_path as _valid_cat_path
        _qamode = _gc("modules.ai_center.qa_mode") or {}
    except Exception:
        _qamode = {}
        _valid_cat_path = None
    qa_by_cat = {}
    for _k, _v in (_qamode.get("by_category") or {}).items():
        if _valid_cat_path and _valid_cat_path(_k) and _v in ("public", "general", "precise"):
            qa_by_cat[_k] = _v
    qa_default = _qamode.get("default") or "general"
    qa_min_score = _qamode.get("min_score") or {}

    return render_template(
        "config_center/index.html",
        current_module_id="config_center",
        modules_row=modules_row,
        books=books,
        cats=cats,
        rules=rules,
        book_levels=book_levels,
        cat1_levels=cat1_levels,
        cat2_levels=cat2_levels,
        users=users,
        # 原始 /book/config 全功能数据
        cfg={"books": books, "layout": layout, "nav_first": nav_first, "cat_colors": cat_colors},
        nav_first=nav_first,
        layout=layout,
        cat_colors=cat_colors,
        cat_id_map=cat_id_map,
        user_perms=user_perms,
        total_books=len(books),
        total_categories=len(cat1_list),
        total_users=len(users),
        all_cat="全部",
        qa_by_cat=qa_by_cat,
        qa_default=qa_default,
        qa_min_score=qa_min_score,
    )


# ============================================================
# 用户管理
# ============================================================
@bp.route("/users", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def users_page():
    users = get_all_users() or []
    for u in users:
        u.pop("password_hash", None)
    perm_names = {p[0]: p[1] for p in ALL_PERMISSIONS}
    perm_groups = get_permissions_grouped()
    # AI 中心的访问级别统一在「模块访问权限」面板控制（默认公开）
    # 用户权限里不再细分 AI 问答/AI 深度思考等子项，避免冗余
    perm_groups = [g for g in perm_groups if g.get("module_id") != "ai_center"]
    # 竖排顺序固定：图书馆在上，配置中心在下
    _perm_order = {"book_lib": 0, "config_center": 1}
    perm_groups.sort(key=lambda g: _perm_order.get(g.get("module_id"), 9))
    # 模块访问权限面板数据（大模块竖排，默认公开；只列进侧边栏的模块，含配置中心）
    _non_nav = PluginScanner.NON_NAV_MODULES
    modules_row = []
    try:
        for mid, info in get_all_module_info().items():
            if mid in _non_nav:
                continue
            # 与侧边栏保持一致：被「工具箱 Hub」合并收口的模块不单独列出
            if mid in PluginScanner.HUB_MERGED_MODULES:
                continue
            modules_row.append({
                "module_id": mid,
                "display_name": info.get("display_name", mid),
                "icon": info.get("icon", "📄"),
            })
    except Exception:
        modules_row = []
    module_access = get_module_access_map()
    return render_template(
        "config_center/users.html",
        current_module_id="config_center",
        users=users,
        all_users=users,
        all_permissions=[p[0] for p in ALL_PERMISSIONS],
        perm_names=perm_names,
        perm_groups=perm_groups,
        modules_row=modules_row,
        module_access=module_access,
        is_superadmin=_is_superadmin(g.get("current_user")),
    )


@bp.route("/logs", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def logs_page():
    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", 50, type=int) or 50
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page
    result = get_logs(per_page, offset)
    total_pages = (result["total"] + per_page - 1) // per_page
    return render_template(
        "config_center/logs.html",
        current_module_id="config_center",
        logs=result["logs"],
        total=result["total"],
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


# ============================================================
# AI 配置
# ============================================================
@bp.route("/ai", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def ai_config_page():
    ai_mode = get_ai_mode_from_config()
    module_enabled = is_module_enabled("ai_center")
    features = get_ai_features()
    try:
        tasks = get_ai_tasks(limit=50)
    except Exception:
        tasks = []
    try:
        ai_log_result = get_ai_logs(per_page=50)
        ai_logs = ai_log_result["logs"]
        ai_log_total = ai_log_result["total"]
    except Exception:
        ai_logs, ai_log_total = [], 0
    try:
        vector_stats = get_vector_stats() or {}
    except Exception:
        vector_stats = {}
    # 界面不暴露绝对地址：把含 BASE_DIR 绝对前缀的路径字段转成相对项目根目录的路径
    if vector_stats:
        _base = os.path.normpath(BASE_DIR).lower()

        def _rel_if_abs(v):
            if isinstance(v, str) and v:
                try:
                    nv = os.path.normpath(v).lower()
                    if nv == _base or nv.startswith(_base + os.sep):
                        return os.path.relpath(v, BASE_DIR).replace(os.sep, "/")
                except Exception:
                    pass
            return v

        vector_stats = {k: _rel_if_abs(vv) for k, vv in vector_stats.items()}

    # ---- AI 配置（可热切换，无需重启） ----
    try:
        ai_cfg = read_ai_config()
    except Exception:
        ai_cfg = {}
    try:
        ai_internal = ai_internal_config.get_config()
    except Exception:
        ai_internal = {"backends": [], "dependencies": [], "model_status": {}, "config": {}, "ready": False, "note": ""}
    ai_deps = ai_internal.get("dependencies", [])
    ai_backends = ai_internal.get("backends", [])
    ai_model_status = ai_internal.get("model_status", {})

    # ---- 磁盘资源（模型 / 向量索引 / 摘要缓存） ----
    disk_resources = []
    try:
        for resource in [
            {"name": "嵌入模型 (bge-small-zh-v1.5)", "type": "embedding", "path": os.path.join(BASE_DIR, "models", "bge-small-zh-v1.5")},
            {"name": "大语言模型 (LFM2.5)", "type": "llm", "path": os.path.join(BASE_DIR, "models", "LFM2.5-2.6B-Q4_K_M.gguf")},
            {"name": "FAISS 向量索引", "type": "vector_index", "path": os.path.join(BASE_DIR, "vector_data")},
            {"name": "AI 摘要缓存", "type": "summary_cache", "path": os.path.join(BASE_DIR, "ai_vector_store", "summaries")},
        ]:
            p = resource["path"]
            exists = os.path.exists(p)
            total = 0
            file_count = 0
            if exists:
                if os.path.isfile(p):
                    total = os.path.getsize(p)
                    file_count = 1
                else:
                    for dirpath, _, filenames in os.walk(p):
                        for fn in filenames:
                            fp = os.path.join(dirpath, fn)
                            try:
                                total += os.path.getsize(fp)
                                file_count += 1
                            except OSError:
                                pass
            # 界面不暴露绝对地址：把 BASE_DIR 前缀剥掉，只展示相对项目根目录的路径
            try:
                rel_path = os.path.relpath(p, BASE_DIR)
            except Exception:
                rel_path = p
            disk_resources.append({
                **resource,
                "exists": exists,
                "size_bytes": total,
                "size_mb": round(total / (1024 * 1024), 2),
                "file_count": file_count,
                "rel_path": rel_path,
            })
    except Exception as e:
        disk_resources = [{"name": "读取失败", "type": "?", "exists": False, "size_bytes": 0, "size_mb": 0, "file_count": 0, "path": str(e), "rel_path": str(e)}]

    # ---- 选择性索引：图书分类树数据 ----
    # 只取前端建树所需的轻量字段（id/名称/一级/二级/是否已索引），
    # 不传整本内容，避免模板渲染开销过大。
    book_tree = []
    try:
        for b in (get_all_books() or []):
            fh = (b.get("file_hash") or "").strip()
            ih = (b.get("indexed_hash") or "").strip()
            book_tree.append({
                "id": b.get("id"),
                "name": b.get("name") or "(无名)",
                "cat1": (b.get("cat1") or "").strip() or "未分类",
                "cat2": (b.get("cat2") or "").strip() or "未分类",
                "indexed": bool(fh and ih and fh == ih),
            })
    except Exception:
        book_tree = []

    # ---- 配置文件原始内容 ----
    config_text = {}
    try:
        cfg_yaml = os.path.join(BASE_DIR, "config", "modules.yaml")
        if os.path.exists(cfg_yaml):
            with open(cfg_yaml, "r", encoding="utf-8") as f:
                config_text["modules_yaml"] = f.read()
    except Exception:
        config_text["modules_yaml"] = "# 读取失败"
    try:
        cfg_yaml = os.path.join(BASE_DIR, "config", "app.yaml")
        if os.path.exists(cfg_yaml):
            with open(cfg_yaml, "r", encoding="utf-8") as f:
                config_text["app_yaml"] = f.read()
    except Exception:
        config_text["app_yaml"] = "# 读取失败"

    # ---- 问答加速（可选步骤开关，热生效） ----
    # 惰性 import：跨模块引用必须放在函数内，否则触发模块加载器 reload 陷阱（路由丢失）。
    qa_speed = {}
    try:
        from modules.ai_center.internal import config as _qa_cfg
        qa_speed = {
            "hyde": _qa_cfg.is_hyde_enabled(),
            "rerank": _qa_cfg.is_rerank_enabled(),
            "citation_check": _qa_cfg.is_citation_check_enabled(),
            "grounding_async": _qa_cfg.is_grounding_async(),
            "rerank_device": _qa_cfg.get_rerank_device(),
            "rerank_max_candidates": _qa_cfg.get_rerank_max_candidates(),
        }
    except Exception:
        qa_speed = {}
    if not qa_speed:
        try:
            from core.ai_config_store import get_qa_speed
            qa_speed = get_qa_speed()
        except Exception:
            qa_speed = {"hyde": True, "rerank": True, "citation_check": True,
                        "grounding_async": True, "rerank_device": "auto",
                        "rerank_max_candidates": 24}
    # 本机是否有可用 CUDA：决定「auto」实际落到 GPU 还是 CPU
    qa_cuda = False
    try:
        import torch as _torch
        qa_cuda = bool(_torch.cuda.is_available())
    except Exception:
        qa_cuda = False

    return render_template(
        "config_center/ai_config.html",
        current_module_id="config_center",
        ai_mode=ai_mode,
        ai_module_enabled=module_enabled,
        ai_features=features,
        ai_tasks=tasks,
        ai_logs=ai_logs,
        ai_log_total=ai_log_total,
        vector_stats=vector_stats,
        book_tree=book_tree,
        disk_resources=disk_resources,
        config_text=config_text,
        ai_cfg=ai_cfg,
        ai_deps=ai_deps,
        ai_backends=ai_backends,
        ai_model_status=ai_model_status,
        qa_speed=qa_speed,
        qa_cuda=qa_cuda,
    )


# ============================================================
# 用户管理 API
# ============================================================
@bp.route("/api/user/add", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_add():
    from core.db_base import get_user_by_account
    user = _require_admin()
    account = (request.form.get("account") or "").strip()
    name = sanitize_text(request.form.get("name") or "", max_len=50)
    role = (request.form.get("role") or "user").strip()
    permissions = request.form.getlist("permissions")
    if not account or len(account) < 2:
        return fail("账号至少2个字符")
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_]+$", account):
        return fail("账号仅支持字母/数字/下划线")
    if get_user_by_account(account):
        return fail("账号已存在")
    if role not in ("user", "admin"):
        role = "user"
    # 创建管理员账号 = 提权，仅超管可为；否则 admin 能无限造 admin，分级就没意义了
    if role == "admin" and not _is_superadmin(user):
        return fail("仅超级管理员可创建管理员账号", code=403)
    uid = add_user(account, name or account, "123456", role, permissions, user.get("id"))
    add_log(user["id"], "user_add", target_type="user", target_id=uid,
            detail={"account": account, "name": name}, ip=get_client_ip())
    return ok({"id": uid}, msg=f"用户 {account} 创建成功，初始密码：123456")


@bp.route("/api/user/resetpwd", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_resetpwd():
    from core.db_base import update_user_password
    user = _require_admin()
    uid = (request.form.get("id") or "").strip()
    if not uid:
        return fail("参数缺失")
    update_user_password(uid, "123456")
    add_log(user["id"], "user_resetpwd", target_type="user", target_id=uid, ip=get_client_ip())
    return ok(msg="密码已重置为 123456")


@bp.route("/api/user/toggle", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_toggle():
    from core.db_base import toggle_user_active
    user = _require_admin()
    uid = (request.form.get("id") or "").strip()
    is_active = request.form.get("is_active", "1") == "1"
    if not uid:
        return fail("参数缺失")
    toggle_user_active(uid, is_active)
    status_text = "启用" if is_active else "禁用"
    add_log(user["id"], "user_toggle", target_type="user", target_id=uid,
            detail={"status": status_text}, ip=get_client_ip())
    return ok(msg=f"用户已{status_text}")


@bp.route("/api/user/delete", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_delete():
    from core.db_base import get_user_by_id
    user = _require_admin()
    uid = (request.form.get("id") or "").strip()
    if not uid:
        return fail("参数缺失")
    target = get_user_by_id(uid)
    if not target:
        return fail("用户不存在", code=404)
    if uid == user.get("id"):
        return fail("不能删除当前登录账号", code=403)
    t_role = target.get("role")
    # 超级管理员账号一律不可删（避免把唯一的超管删光后无人可管理管理员）
    if t_role == "superadmin":
        return fail("超级管理员账号不可删除", code=403)
    # 原来管理员一律不可删；现放开给超管，普通管理员仍不能删管理员
    if t_role == "admin" and not _is_superadmin(user):
        return fail("仅超级管理员可删除管理员账号", code=403)
    conn = get_db()
    cur = conn.cursor()
    # 联动清理该用户的孤儿数据（外键未强制开启时避免残留）
    # ⚠️ experience_posts / feedback_items 声明了 FOREIGN KEY(user_id)
    # REFERENCES users(id)，而 db_base 每个连接都 PRAGMA foreign_keys=ON ——
    # 漏清这两张表会让下面的 DELETE FROM users 直接抛
    # "FOREIGN KEY constraint failed"，接口 500 且用户删不掉。
    # user_likes / user_collects / private_categories 虽无外键，但同属用户私有域，
    # 一并清掉免留孤儿。
    for tbl in ("notes", "reviews", "favorites", "reading_records", "logs", "ai_logs",
                "experience_posts", "feedback_items",
                "user_likes", "user_collects", "private_categories"):
        try:
            cur.execute(f"DELETE FROM {tbl} WHERE user_id=?", (uid,))
        except Exception:
            pass
    cur.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    add_log(user["id"], "user_delete", target_type="user", target_id=uid,
            detail={"account": target.get("account")}, ip=get_client_ip())
    return ok(msg=f"用户 {target.get('account')} 已删除")


@bp.route("/api/user/updateperms", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_updateperms():
    from core.db_base import update_user_permissions
    user = _require_admin()
    uid = (request.form.get("id") or "").strip()
    permissions = request.form.getlist("permissions")
    if not uid:
        return fail("参数缺失")
    update_user_permissions(uid, permissions)
    add_log(user["id"], "user_updateperms", target_type="user", target_id=uid,
            detail={"permissions": permissions}, ip=get_client_ip())
    return ok(msg="权限更新成功")


@bp.route("/api/user/import", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_user_import():
    """批量导入用户（CSV / XLSX，表头：账号, 名字, 角色）"""
    import csv
    import io
    from core.db_base import add_user, get_user_by_account
    user = _require_admin()
    if "file" not in request.files:
        return fail("未上传文件")
    f = request.files["file"]
    if not f.filename:
        return fail("未选择文件")
    filename = f.filename.lower()
    rows_data, fieldnames = [], []
    try:
        if filename.endswith(".xlsx"):
            try:
                import openpyxl
            except ImportError:
                return fail("缺少 openpyxl 依赖，请先安装：pip install openpyxl")
            wb = openpyxl.load_workbook(io.BytesIO(f.stream.read()))
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows:
                return fail("Excel 文件为空")
            fieldnames = [str(c).strip() if c is not None else f"列{i+1}" for i, c in enumerate(all_rows[0])]
            for row in all_rows[1:]:
                row_dict = {}
                for i, val in enumerate(row):
                    if i < len(fieldnames):
                        row_dict[fieldnames[i]] = str(val).strip() if val is not None else ""
                rows_data.append(row_dict)
            wb.close()
        elif filename.endswith(".csv"):
            stream = io.StringIO(f.stream.read().decode("utf-8-sig"), newline=None)
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames or []
            for row in reader:
                rows_data.append(dict(row))
        else:
            return fail("仅支持 .csv 或 .xlsx 文件")
    except Exception as e:
        return fail(f"文件解析失败: {e}")
    required_fields = ["账号", "名字", "角色"]
    if fieldnames and not all(x in fieldnames for x in required_fields):
        return fail(f"表头必须包含: {', '.join(required_fields)}，当前表头: {', '.join(fieldnames)}")
    success, failed, errors = 0, 0, []
    for idx, row in enumerate(rows_data, start=2):
        account = str(row.get("账号", "")).strip()
        name = str(row.get("名字", "")).strip()
        role = str(row.get("角色", "user")).strip().lower()
        if not account:
            failed += 1; errors.append(f"第{idx}行: 账号为空"); continue
        if role not in ("user", "admin"):
            role = "user"
        # 与单个新增保持一致：导入管理员角色也属提权，仅超管可为
        if role == "admin" and not _is_superadmin(user):
            failed += 1
            errors.append(f"第{idx}行: 仅超级管理员可导入管理员账号")
            continue
        if get_user_by_account(account):
            failed += 1; errors.append(f"第{idx}行: 账号 {account} 已存在"); continue
        try:
            add_user(account, name or account, "123456", role, [], user.get("id"))
            success += 1
        except Exception as e:
            failed += 1; errors.append(f"第{idx}行: {e}")
    add_log(user["id"], "user_import", detail={"success": success, "failed": failed}, ip=get_client_ip())
    msg = f"导入完成: 成功 {success} 条"
    if failed > 0:
        msg += f", 失败 {failed} 条"
    return ok({"success": success, "failed": failed, "errors": errors[:10]}, msg=msg)


@bp.route("/api/logs", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_logs():
    _require_admin()
    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", 50, type=int) or 50
    per_page = min(per_page, 200)
    result = get_logs(per_page, (page - 1) * per_page)
    return ok({"logs": result["logs"], "total": result["total"], "page": page, "per_page": per_page})


# ============================================================
# AI 配置操作 API（管理员）
# ============================================================
# 重建索引进度（进程内单例；重建为后台线程执行，避免 HTTP 请求长时间阻塞）
_REBUILD_STATE = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "skipped": 0,
    "failed": 0,
    "errors": [],
    "started_at": None,
    "finished_at": None,
}
_REBUILD_LOCK = threading.Lock()


def _rebuild_worker(books, task_id=None, reset_index_first=False, admin_id=None):
    """后台逐本重建向量索引（外壳：保证 running 标志必定复位）。

    复位必须在 finally 里做：主体一旦抛出未捕获异常（线程启动失败、被中断、
    未覆盖的运行时错误），running 会永远是 True，之后 api_ai_rebuild_index
    一律返回 409「重建任务正在运行中」，整个索引重建功能只能靠重启进程恢复。
    """
    try:
        _rebuild_worker_body(books, task_id=task_id,
                             reset_index_first=reset_index_first, admin_id=admin_id)
    except Exception as e:
        print(f"❌ 索引重建线程异常终止：{e}")
        try:
            import traceback as _tb
            _tb.print_exc()
        except Exception:
            pass
    finally:
        with _REBUILD_LOCK:
            _REBUILD_STATE["running"] = False
            _REBUILD_STATE["finished_at"] = time.strftime("%H:%M:%S")


def _rebuild_worker_body(books, task_id=None, reset_index_first=False, admin_id=None):
    """后台逐本重建向量索引（主体，异常由 _rebuild_worker 统一兜底复位）。

    reset_index_first=True：逐本重建**前**先整体清空索引（映射+磁盘+内存缓存）。
    仅「全量重建(force=1)」使用。原因：本函数是逐本 trigger_rebuild_for_book，
    只删该书映射、store() 从 idx.ntotal 追加；不清空的话上一轮全部向量会变成孤儿
    （实测 80/164、孤儿 48.8%，映射 faiss_index 起始为 80 说明是追加）。
    选择性索引（book_ids）**绝不能**置 True，否则会丢掉未选中书的向量。

    跳过 LLM 摘要（with_summary=False）：7B 模型在 CPU 上每本要几十秒，
    21 本书会拖到十几分钟，足以让触发请求的浏览器/网关超时。
    摘要可由图书详情页的「重新生成摘要」单独触发。

    进度会同步写入 ai_tasks 表：只放内存的话，刷新页面或换个会话就看不到，
    用户会误以为任务没跑，也无法回溯哪些书成功、哪些失败。

    reset_index_first=True 时，书本循环结束后还会调用 experience_hub 的
    reindex_all_posts() 把经验帖（exp_ 命名空间）向量补回 —— 详见该处注释。
    """
    _exp_note, _exp_fail = "", 0   # 经验帖补回结果（用于最终回显，杜绝静默丢失）
    total = max(1, len(books))

    def _sync_db():
        """把当前进度写回 ai_tasks，让「异步任务」列表实时可见。"""
        if not task_id:
            return
        try:
            pct = int(_REBUILD_STATE["done"] * 100 / total)
            update_ai_task(
                task_id,
                status="running",
                progress=pct,
                message=f"已完成 {_REBUILD_STATE['done']}/{len(books)}："
                        f"成功 {_REBUILD_STATE['ok']}，跳过 {_REBUILD_STATE['skipped']}，"
                        f"失败 {_REBUILD_STATE['failed']}",
            )
        except Exception:
            pass

    # 先清理孤儿索引数据（图书已删除但分块残留），避免其污染指纹并拖慢重建
    try:
        if _try_load_ai_center():
            from modules.ai_center.internal import vector_store
            vector_store.purge_orphan_chunks()
    except Exception:
        pass

    # 全量重建：整体清空索引一次，保证下方逐本 store() 从 ntotal=0 开始，
    # 不再把上一轮向量追加成孤儿（详见函数 docstring）。
    if reset_index_first:
        try:
            if _try_load_ai_center():
                from modules.ai_center.internal import vector_store
                vector_store.reset_index()
        except Exception:
            pass

    for b in books:
        bid = b.get("id")
        try:
            ok_, res = trigger_rebuild_for_book(bid, with_summary=False)
            with _REBUILD_LOCK:
                _REBUILD_STATE["done"] += 1
                if ok_:
                    if isinstance(res, str) and res.startswith("skip:"):
                        _REBUILD_STATE["skipped"] += 1
                    else:
                        _REBUILD_STATE["ok"] += 1
                else:
                    _REBUILD_STATE["failed"] += 1
                    if len(_REBUILD_STATE["errors"]) < 10:
                        _REBUILD_STATE["errors"].append(f"{bid}: {res}")
        except Exception as e:
            with _REBUILD_LOCK:
                _REBUILD_STATE["done"] += 1
                _REBUILD_STATE["failed"] += 1
                if len(_REBUILD_STATE["errors"]) < 10:
                    _REBUILD_STATE["errors"].append(f"{bid}: {e}")
        _sync_db()

    # 经验帖索引补回：
    # 上面的 reset_index() 清空的是**整个 mode** 的映射（clear_vector_mappings 只按 mode
    # 过滤、不过滤 book_id），经验帖以 book_id="exp_<id>" 存在同一张映射表与同一个 faiss
    # 索引里，因此会被一并清空；而本函数只遍历 books 表重建图书 —— 经验帖不在 books 表，
    # 于是向量被清空后无人补回，保守问答从此检索不到任何经验内容，且全程无报错。
    # 按「谁清空谁负责补回」的原则，仅在确实清空过索引（reset_index_first）时补回；
    # 增量 / 选择性索引没有清空动作，经验帖索引本来就完好，无需重刷。
    if reset_index_first:
        try:
            from modules.experience_hub.routes import reindex_all_posts
            _et, _eok, _efail = reindex_all_posts()
            _exp_fail = _efail
            if _et:
                _exp_note = (f"经验帖索引补回 {_eok}/{_et}"
                             + (f"，失败 {_efail}" if _efail else ""))
            else:
                # 经验帖表为空或读取失败：若此前确有 exp_ 向量被清，这里无法补回，
                # 必须显式提示，不能再像旧版那样「静默丢失」。
                _exp_note = "经验帖索引补回 0 篇（经验帖表为空或读取失败，请检查 experience_posts 表）"
            print(f"🔄 全量重建后续：{_exp_note}")
            try:
                if task_id:
                    update_ai_task(
                        task_id, status="running",
                        message=f"图书重建完成；{_exp_note}",
                    )
            except Exception:
                pass
        except Exception as e:
            _exp_fail += 1
            _exp_note = f"经验帖索引补回失败：{e}"
            print(f"⚠️ 全量重建后补回经验帖索引失败：{e}")
        # 经验帖补回失败 → 写入操作日志，避免「静默丢向量」无人知晓
        if _exp_fail > 0 and admin_id:
            try:
                add_log(admin_id, "rebuild_exp_restore_failed",
                        target_type="index", detail={"note": _exp_note}, ip="")
            except Exception:
                pass

    # 全部跑完后刷新索引标记（数据指纹），避免被误判过期
    try:
        if _try_load_ai_center():
            from modules.ai_center.internal import vector_store
            vector_store.refresh_marker()
    except Exception:
        pass

    # 落库最终结果（成功/跳过/失败明细），便于事后回溯
    # 注：running 标志的复位已移到 _rebuild_worker 的 finally 中统一处理。
    if task_id:
        try:
            errs = list(_REBUILD_STATE["errors"])
            summary = {
                "total": len(books),
                "ok": _REBUILD_STATE["ok"],
                "skipped": _REBUILD_STATE["skipped"],
                "failed": _REBUILD_STATE["failed"],
                "errors": errs,
            }
            msg = (f"成功 {summary['ok']} 本，跳过 {summary['skipped']} 本，"
                   f"失败 {summary['failed']} 本")
            if errs:
                msg += "：" + "；".join(errs[:3])
            # 经验帖补回结果一并回显（无论成功/失败）——禁止静默丢失向量
            if _exp_note:
                msg += f"；{_exp_note}"
                summary["exp_restore"] = _exp_note
            update_ai_task(
                task_id,
                status="done" if (summary["failed"] == 0 and _exp_fail == 0) else "failed",
                progress=100,
                message=msg,
                result=summary,
            )
        except Exception:
            pass


def _rebuild_empty_library(admin_id=None, skipped=0):
    """书库已清空时执行「全量重建」的收尾：清孤儿分块 + 索引置空 + 补回经验帖。

    为什么需要它：
        原实现在 `if not books:` 处直接 return，导致**书全删后「全量重建」变成空操作** ——
        `purge_orphan_chunks()` 与 `reset_index()` 都在后台 worker 里，worker 压根没启动。
        实测后果：全部图书删除后仍残留 130 行 `document_chunks`、50 行 `ai_parent_chunks`、
        faiss 索引 ntotal=157 而有效映射仅 3（孤儿率 98%），只能手工进库清理。
        现在即便没有可重建的图书，也执行一次真实的清理收尾。

    ⚠️ 经验帖必须补回：index 是按 mode 整体清空的，经验帖（book_id="exp_<id>"）与图书
    共用同一索引与映射表，而它们不在 books 表、不会被任何图书循环重建 —— 不补回就是
    「静默丢向量」（回归 `_t_rebuild_exp.py` 守的就是这条）。
    """
    purged, saved, exp_note = 0, False, ""
    try:
        if _try_load_ai_center():
            from modules.ai_center.internal import vector_store
            purged, saved = vector_store.empty_index()
            try:
                from modules.experience_hub.routes import reindex_all_posts
                _et, _eok, _efail = reindex_all_posts()
                if _et:
                    exp_note = ("经验帖索引补回 %d/%d" % (_eok, _et)
                                + ("，失败 %d" % _efail if _efail else ""))
                else:
                    exp_note = "经验帖索引补回 0 篇（经验帖表为空或读取失败）"
            except Exception as _e:
                exp_note = "经验帖索引补回失败：%s" % _e
                if admin_id:
                    try:
                        add_log(admin_id, "rebuild_exp_restore_failed",
                                target_type="index", detail={"note": exp_note}, ip="")
                    except Exception:
                        pass
            # 经验帖写入后 faiss 已变更，再刷一次 marker 保证指纹与落盘一致
            try:
                vector_store.refresh_marker()
            except Exception:
                pass
    except Exception as e:
        return fail("清空空书库索引失败：%s" % e, code=500)

    if admin_id:
        try:
            add_log(admin_id, "AI 索引清空重建", target_type="index",
                    detail={"purged_chunks": purged, "saved": saved, "note": exp_note},
                    ip=get_client_ip())
        except Exception:
            pass
    msg = "书库为空：已清理孤儿分块 %d 行并将索引重置为空" % purged
    if saved:
        msg += "（索引已落盘）"
    if exp_note:
        msg += "；" + exp_note
    return ok({"total": 0, "skipped": skipped, "purged": purged}, msg=msg)


def _parse_book_ids(raw):
    """解析前端传来的 book_ids，兼容三种形态：
      - JSON 数组（application/json 提交，或数组被 JSON.stringify 成字符串塞进 FormData）
      - 逗号分隔的 id 字符串
      - 已经是 list/tuple
    统一返回去空白后的非空字符串列表；解析不出就返回空列表（=「未指定」，走原逻辑）。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, (list, tuple)):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                return []
            return []
        return [x.strip() for x in s.split(",") if x.strip()]
    return []


@bp.route("/api/ai/rebuild_index", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_rebuild_index():
    """触发图书向量索引重建（后台异步执行，立即返回；前端轮询 rebuild_status 看进度）。

    book_ids=[...] → 选择性索引（只重建指定的书，与 force 无关）
    force=1        → 全量重建（所有书推倒重来）
    force=0/缺省   → 增量重建（只处理未索引 / 上次失败 / 文件已变更的书）

    三种模式互斥优先级：book_ids > force > 增量。
    不传 book_ids 时行为与改动前完全一致，不影响现有「索引新书/全量重建」按钮。
    """
    _require_admin()
    admin_user = g.get("current_user") or {}
    admin_id = admin_user.get("id")
    try:
        if not _try_load_ai_center():
            return fail("AI 模块未启用，无法重建索引", code=400)

        # 参数兼容表单与 JSON（doAction 发的是 FormData）
        data = request.get_json(silent=True) or {}
        if not data and request.form:
            data = request.form.to_dict()
        force = str(data.get("force") or "0").lower() in ("1", "true", "yes", "on")

        # ── 选择性索引：只重建前端勾选的书 ──
        # 前端的「一级/二级/单本/多本」四种粒度已统一展开成 book_ids 列表，
        # 后端无需感知分类结构，保持单一职责。
        requested_ids = _parse_book_ids(data.get("book_ids"))

        all_books = get_all_books() or []
        if requested_ids:
            id_set = set(requested_ids)
            books = [b for b in all_books if b.get("id") in id_set]
            skipped_already = len(all_books) - len(books)
            mode_text = "选择性索引"
            skip_text = f"（未选中的 {skipped_already} 本已忽略）" if skipped_already else ""
            if not books:
                return fail("所选图书均不存在，请刷新页面后重试", code=404)
        else:
            # 与原逻辑完全一致：force=1 全量，否则增量
            books = all_books if force else (get_books_need_indexing(force=False) or [])
            skipped_already = len(all_books) - len(books)
            mode_text = "全量重建" if force else "增量索引"
            skip_text = f"（已索引且未变更的 {skipped_already} 本已跳过）" if skipped_already else ""
            if not books:
                #force=1 且书库为空时**不能直接短路**。
                # 原逻辑在此 return，导致 worker 不启动、purge_orphan_chunks 与
                # reset_index 都不执行 —— 书全删后「全量重建」是唯一清理入口，
                # 却成了空操作（实测残留 130 行分块 / 50 行父块 / 154 个孤儿槽位）。
                if force:
                    return _rebuild_empty_library(admin_id, skipped_already)
                return ok({"total": 0, "skipped": skipped_already},
                          msg="没有需要索引的图书（已全部索引且文件未变更）")

        with _REBUILD_LOCK:
            if _REBUILD_STATE.get("running"):
                return fail("重建任务正在运行中，请等待其完成后再试", code=409)
            _REBUILD_STATE.update({
                "running": True,
                "total": len(books),
                "done": 0,
                "ok": 0,
                "skipped": 0,
                "failed": 0,
                "errors": [],
                "started_at": time.strftime("%H:%M:%S"),
                "finished_at": None,
            })
        task_id = "t_" + uuid.uuid4().hex[:10]
        try:
            create_ai_task(task_id, "rebuild_index")
            update_ai_task(task_id, status="running", progress=0,
                           message=f"{mode_text}：共 {len(books)} 本{skip_text}")
        except Exception:
            pass
        # 仅 force=1 全量重建才先清空索引；book_ids 选择性索引 / 增量索引不清空。
        _reset_first = bool(force) and not requested_ids
        threading.Thread(target=_rebuild_worker,
                         args=(books, task_id, _reset_first, admin_id),
                         daemon=True).start()
        msg = f"已在后台开始{mode_text} {len(books)} 本书{skip_text}"
        return ok({"total": len(books), "skipped": skipped_already, "task_id": task_id}, msg=msg)
    except Exception as e:
        return fail(f"重建失败: {e}", code=500)


@bp.route("/api/ai/rebuild_status", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_rebuild_status():
    """查询重建索引进度。"""
    with _REBUILD_LOCK:
        st = {k: v for k, v in _REBUILD_STATE.items()}
    return ok(st)


@bp.route("/api/ai/toggle_module", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_toggle_module():
    """切换 ai_center 模块启用状态（写 modules.yaml，需重启生效）。"""
    _require_admin()
    enabled = request.form.get("enabled") or (request.json.get("enabled") if request.is_json else None)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ("1", "true", "yes", "on")
    enabled = bool(enabled)
    cfg_path = os.path.join(BASE_DIR, "config", "modules.yaml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            data = {}
        modules = data.get("modules")
        if not isinstance(modules, dict):
            modules = {}
            data["modules"] = modules
        entry = modules.get("ai_center")
        if not isinstance(entry, dict):
            entry = {}
        entry["enabled"] = bool(enabled)
        if "sort_order" not in entry:
            entry["sort_order"] = 0
        if "ai_mode" not in entry:
            entry["ai_mode"] = "light"
        modules["ai_center"] = entry
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        try:
            add_log(g.current_user["id"], "AI 模块切换", detail={"enabled": enabled}, ip=get_client_ip())
        except Exception:
            pass
        return ok({"enabled": enabled, "restart_required": True},
                  msg=("已启用 AI 模块，请重启服务生效" if enabled else "已禁用 AI 模块，请重启服务生效"))
    except Exception as e:
        return fail(f"修改失败: {e}", code=500)


@bp.route("/api/ai/clear_logs", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_clear_logs():
    """清空 AI 操作日志。"""
    _require_admin()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_logs")
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        try:
            add_log(g.current_user["id"], "清空 AI 日志", detail={"deleted": deleted}, ip=get_client_ip())
        except Exception:
            pass
        return ok({"deleted": deleted}, msg=f"已清空 {deleted} 条 AI 日志")
    except Exception as e:
        return fail(f"清空失败: {e}", code=500)


# ============================================================
# AI 配置热切换 / 模型与后端配置（均写 modules.yaml，立即刷新内存缓存）
# ============================================================
@bp.route("/api/ai/set_mode", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_mode():
    _require_admin()
    mode = (request.form.get("mode") or (request.json.get("mode") if request.is_json else None) or "light")
    try:
        cfg = set_ai_mode(mode)
        try:
            add_log(g.current_user["id"], "AI 模式切换", detail={"mode": cfg.get("ai_mode")}, ip=get_client_ip())
        except Exception:
            pass
        # 索引方案B：切换模式即校验目标模式索引；缺失/过期则后台懒重建（无需每次手动重建）
        rebuild_hint = ""
        try:
            from modules.ai_center.internal import vector_store as _vs
            _vs.unload_index()  # 清掉旧模式缓存索引
            ready, reason = _vs.ensure_mode_ready(mode, lazy=True)
            if not ready:
                rebuild_hint = "（该模式索引缺失或已过期，正在后台自动重建，稍后自动可用）"
        except Exception as e:
            rebuild_hint = f"（索引状态检查失败: {e}）"
        return ok({"ai_mode": cfg.get("ai_mode"), "enabled": cfg.get("enabled")},
                  msg=f"AI 模式已切换为「{cfg.get('ai_mode')}」，已热加载（无需重启）{rebuild_hint}")
    except Exception as e:
        return fail(f"切换失败: {e}", code=500)


@bp.route("/api/ai/set_security", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_security():
    _require_admin()
    enabled = request.form.get("enabled") or (request.json.get("enabled") if request.is_json else None)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ("1", "true", "yes", "on")
    enabled = bool(enabled)
    try:
        cfg = set_security_lock(enabled)
        try:
            add_log(g.current_user["id"], "AI 安全锁", detail={"security_lock": enabled}, ip=get_client_ip())
        except Exception:
            pass
        return ok({"security_lock": cfg.get("security_lock")},
                  msg=("已开启涉密安全锁定：未授权用户无法读取正文" if enabled else "已关闭涉密安全锁定"))
    except Exception as e:
        return fail(f"设置失败: {e}", code=500)


@bp.route("/api/ai/set_qa_speed", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_qa_speed():
    """保存「问答加速」可选步骤开关（HyDE / 二阶重排 / 引用校验 / 校验异步 / 重排设备 / 候选数）。

    参数经服务端白名单与取值范围校验后才落盘，避免非法值写坏配置。
    这些开关每次问答实时读取，**保存即生效、无需重启**；
    例外是 rerank_device——重排模型加载后不会再迁移设备，需重启才生效。
    """
    _require_admin()
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.form.to_dict() if request.form else {}

    def _as_bool(key, default=True):
        v = data.get(key, None)
        if v is None:
            return default
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    patch = {
        "hyde": _as_bool("hyde"),
        "rerank": _as_bool("rerank"),
        "citation_check": _as_bool("citation_check"),
        "grounding_async": _as_bool("grounding_async"),
    }
    dev = str(data.get("rerank_device") or "auto").strip().lower()
    patch["rerank_device"] = dev if dev in ("auto", "cuda", "cpu") else "auto"
    try:
        n = int(data.get("rerank_max_candidates", 24))
    except Exception:
        n = 24
    patch["rerank_max_candidates"] = n if 1 <= n <= 64 else 24

    try:
        from core.ai_config_store import set_qa_speed
        cfg = set_qa_speed(patch)
        try:
            add_log(g.current_user["id"], "AI 问答加速配置",
                    detail={"qa_speed": patch}, ip=get_client_ip())
        except Exception:
            pass
        off = [k for k in ("hyde", "rerank", "citation_check") if not patch[k]]
        tail = ("（已关闭：%s）" % "、".join(off)) if off else "（全部开启）"
        return ok(cfg, msg="问答加速配置已保存并立即生效%s" % tail)
    except Exception as e:
        return fail(f"设置失败: {e}", code=500)


@bp.route("/api/ai/set_qa_mode_category", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_qa_mode_category():
    """设置某分类的问答档位（public / general / precise）；mode 传空 = 清除该设置，恢复继承。

    档位决定该分类下图书的：相关性阈值 min_score、OCR 扫描页数上限。
    分类路径只允许「一级」或「一级/二级」；历史脏数据那种多层重复路径一律拒收。
    保存即写回 modules.yaml 并热刷新缓存，**无需重启**。
    """
    _require_admin()
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.form.to_dict() if request.form else {}
    cat_path = str(data.get("cat_path") or "").strip()
    mode = str(data.get("mode") or "").strip().lower()
    if not cat_path:
        return fail("缺少分类路径", code=400)

    try:
        from core.ai_config_store import set_qa_mode_by_category
        res = set_qa_mode_by_category(cat_path, mode)
        try:
            add_log(g.current_user["id"], "分类问答档位",
                    detail={"cat_path": cat_path, "mode": mode or "(继承)"},
                    ip=get_client_ip())
        except Exception:
            pass
        if mode:
            _label = {"public": "公共档", "general": "普通档", "precise": "精确档"}.get(mode, mode)
            tip = ""
            if res.get("removed"):
                tip = "（顺带清理了 %d 条无效旧配置）" % len(res["removed"])
            return ok(res, msg="「%s」已设为%s，立即生效%s" % (cat_path, _label, tip))
        return ok(res, msg="「%s」已恢复继承上级 / 默认档位" % cat_path)
    except ValueError as e:
        return fail(str(e), code=400)
    except Exception as e:
        return fail(f"设置失败: {e}", code=500)


@bp.route("/api/ai/set_model_paths", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_model_paths():
    _require_admin()
    light_emb = request.form.get("light_emb") or (request.json.get("light_emb") if request.is_json else "") or ""
    full_emb = request.form.get("full_emb") or (request.json.get("full_emb") if request.is_json else "") or ""
    local_llm = request.form.get("local_llm") or (request.json.get("local_llm") if request.is_json else "") or ""
    paths = {"light_emb": light_emb, "full_emb": full_emb, "local_llm": local_llm}
    paths = {k: v for k, v in paths.items() if v}
    try:
        cfg = set_ai_model_paths(paths)
        try:
            add_log(g.current_user["id"], "AI 模型路径", detail=paths, ip=get_client_ip())
        except Exception:
            pass
        return ok({"model_paths": cfg.get("model_paths")}, msg="模型路径已保存（立即生效，重启后加载）")
    except Exception as e:
        return fail(f"保存失败: {e}", code=500)


@bp.route("/api/ai/set_backend", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_set_backend():
    _require_admin()
    backend = request.form.get("backend") or (request.json.get("backend") if request.is_json else None) or "off"
    url = request.form.get("url") or (request.json.get("url") if request.is_json else "") or ""
    model = request.form.get("model") or (request.json.get("model") if request.is_json else "") or ""
    api_key = request.form.get("api_key") or (request.json.get("api_key") if request.is_json else "") or ""
    model_path = request.form.get("model_path") or (request.json.get("model_path") if request.is_json else "") or ""
    backend_cfg = {}
    if backend == "ollama":
        backend_cfg = {"url": url or "http://localhost:11434", "model": model or "qwen2.5:7b"}
    elif backend == "dashscope":
        backend_cfg = {"url": url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                       "model": model or "qwen-plus", "api_key": api_key}
    elif backend == "vllm":
        backend_cfg = {"url": url or "http://localhost:8000/v1",
                       "model": model or "", "api_key": api_key}
    elif backend == "transformers":
        backend_cfg = {"model_path": model_path or "models/"}
    elif backend == "llama_cpp":
        backend_cfg = {"model_path": model_path or ""}
    try:
        cfg = set_ai_backend(backend, backend_cfg)
        try:
            add_log(g.current_user["id"], "AI 推理后端", detail={"backend": backend}, ip=get_client_ip())
        except Exception:
            pass
        # 保存后探活：远端/云端后端填错时立刻提示，避免「保存成功、首次提问才失败」。
        # 探活失败【不阻断】保存 —— 后端可能只是临时离线，配置本身仍应写进去。
        probe_note = ""
        probe_ok = None
        if backend in ("ollama", "dashscope", "vllm"):
            try:
                from modules.ai_center.internal.qa import probe_backend_connectivity
                probe_ok, detail = probe_backend_connectivity(backend, backend_cfg)
                probe_note = ("；✅ 连接测试通过" if probe_ok
                              else f"；⚠️ 连接测试未通过：{detail}")
            except Exception as e:
                probe_ok = False
                probe_note = f"；⚠️ 连接测试异常：{e}"
        return ok({"backend": cfg.get("backend"), cfg.get("backend"): cfg.get(backend, {}),
                   "probe_ok": probe_ok},
                  msg=f"AI 推理后端已设置为「{backend}」，立即生效{probe_note}")
    except Exception as e:
        return fail(f"设置失败: {e}", code=500)


@bp.route("/api/ai/compress_index", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_ai_compress_index():
    _require_admin()
    try:
        result = clean_orphan_vectors()
        try:
            add_log(g.current_user["id"], "AI 索引压缩", detail=result, ip=get_client_ip())
        except Exception:
            pass
        return ok(result, msg=f"已清理孤儿向量 {result.get('deleted_orphan', 0)} 条、失效向量 {result.get('deleted_invalid', 0)} 条")
    except Exception as e:
        return fail(f"压缩失败: {e}", code=500)


# ============================================================
# 图书级 AI 接口（通过 core.ai_interface 调用，故障隔离）
# 仅要求登录，与图书详情页的调用行为一致（任意登录用户可用）
# ============================================================
@bp.route("/api/ai/summary/<book_id>", methods=["POST"])
@login_required
@module_exception_guard("config_center")
def api_ai_summary(book_id):
    if not is_ai_enabled():
        return fail("AI功能已关闭", code=503)
    # 按图书资源权限校验：受限图书不可被越权摘要
    book = get_book_by_id(book_id)
    if not book:
        return fail("图书不存在", code=404)
    if not user_can_view_book(book, g.current_user):
        return fail("无权访问该书内容", code=403)
    from core.db_base import get_document_chunks
    chunks = get_document_chunks(book_id=book_id)
    text = "\n\n".join([c.get("chunk_text", "") for c in chunks[:100]])
    if not text:
        try:
            from modules.ai_center.internal.parser import extract_text_from_file
            book = get_book_by_id(book_id)
            path = (book or {}).get("savepath") or ""
            if not path and book:
                path, _ = resolve_file_path(book.get("path") or "")
            if path and os.path.exists(path):
                text = extract_text_from_file(path) or ""
        except Exception:
            text = ""
    if not text:
        return fail("该书暂无可读文本，请稍后再试（AI索引构建中）")
    force = request.args.get("force", type=bool) or False
    if request.method == "POST":
        force = True
    ok_, result = generate_book_summary(book_id, text, force=force)
    if not ok_:
        return fail(str(result), code=500)
    try:
        update_book_ai_fields(book_id, summary=result)
    except Exception:
        pass
    try:
        add_ai_log(g.current_user["id"], "book_summary", target_type="book", target_id=book_id,
                   detail={"force": force, "len": len(result)}, ip=get_client_ip())
    except Exception:
        pass
    return ok({"summary": result})


@bp.route("/api/ai/ask", methods=["POST"])
@login_required
@module_exception_guard("config_center")
def api_ai_ask():
    if not is_ai_enabled():
        return fail("AI功能已关闭", code=503)
    # 图书详情页（book_detail.html）用的是 FormData，这里必须同时兼容表单与 JSON。
    # 原先只读 get_json()，表单提交会拿到空 dict，直接报「缺少图书ID」，
    # 导致图书详情页的 AI 提问完全不可用。
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = request.form.to_dict()
    book_id = (data.get("book_id") or "").strip()
    question = (data.get("question") or "").strip()
    if not book_id:
        return fail("缺少图书ID")
    if not question:
        return fail("问题不能为空")
    # 按图书资源权限校验：受限图书不可被越权问答
    book = get_book_by_id(book_id)
    if not book:
        return fail("图书不存在", code=404)
    if not user_can_view_book(book, g.current_user):
        return fail("无权访问该书内容", code=403)
    top_k = int(data.get("top_k") or 5)
    ok_, result = ask_book_question(book_id, question, top_k=top_k)
    try:
        add_ai_log(g.current_user["id"], "book_qa", target_type="book", target_id=book_id,
                   detail={"q": question[:300], "ok": ok_}, ip=get_client_ip())
    except Exception:
        pass
    if not ok_:
        return fail(result.get("answer") if isinstance(result, dict) else "AI服务异常", code=500)
    return ok(result or {})


# ============================================================
# 权限管理（三层级：一级分类 / 二级分类 / 单本图书；支持指定用户可见）
# ============================================================
@bp.route("/permissions", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def permissions_page():
    rules = get_all_resource_permission_details()  # [{resource_type, resource_id, permission_level, allowed_users}]
    # 解析可读资源名称
    cats = get_all_categories() or []
    cat_map = {c["id"]: c for c in cats}
    books = get_all_books() or []
    book_map = {b["id"]: b for b in books}
    for r in rules:
        rid = r.get("resource_id")
        if r["resource_type"] == "cat1":
            r["resource_name"] = (cat_map.get(rid) or {}).get("name", rid)
            r["type_label"] = "一级分类"
        elif r["resource_type"] == "cat2":
            r["resource_name"] = (cat_map.get(rid) or {}).get("name", rid)
            r["type_label"] = "二级分类"
        else:
            r["resource_name"] = (book_map.get(rid) or {}).get("name", rid)
            r["type_label"] = "单本图书"
        r["allowed_users"] = r.get("allowed_users") or []
    # 下拉候选：分类无 level 列，用 parent_id 区分一级/二级
    cat1_list = [c for c in cats if not c.get("parent_id")]
    cat2_list = [c for c in cats if c.get("parent_id")]
    users = get_all_users() or []
    user_map = {}
    for u in users:
        u.pop("password_hash", None)
        user_map[u["id"]] = u.get("name") or u.get("account") or u["id"]
    for r in rules:
        r["allowed_user_names"] = [user_map.get(uid, uid) for uid in (r.get("allowed_users") or [])]
    return render_template(
        "config_center/permissions.html",
        current_module_id="config_center",
        rules=rules,
        cat1_list=cat1_list,
        cat2_list=cat2_list,
        books=books,
        users=users,
    )


@bp.route("/api/permissions", methods=["GET"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_get_resource_perms():
    items = get_all_resource_permission_details()
    return ok({"items": items, "total": len(items)})


@bp.route("/api/permission/save", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_save_resource_perm():
    data = request.get_json(silent=True) or {}
    perms = data.get("permissions") or []
    #原先边校验边写库，第 N+1 条校验失败时直接 return，
    # 前 N 条却已经落库 —— 权限停在「改了一半」的状态，前端只看到一句报错。
    # 改为两段式：先全量校验通过，再统一写入。
    items = []
    for p in perms:
        rid = p.get("resource_id")
        if not rid:
            continue
        level = p.get("permission_level") or "public"
        rtype = p.get("resource_type") or "book"
        allowed = p.get("allowed_users") or []
        if level == "selected" and not allowed:
            return fail("指定用户可见时至少选择一名用户", code=400)
        if level not in ("public", "login", "admin", "selected"):
            level = "public"
        items.append((rid, level, rtype, allowed))
    saved = 0
    for rid, level, rtype, allowed in items:
        set_resource_permission(rid, level, rtype, allowed_users=allowed)
        saved += 1
    deleted = data.get("deleted") or []
    for d in deleted:
        if isinstance(d, dict):
            delete_resource_permission(d.get("resource_id"), d.get("resource_type") or "book")
        else:
            delete_resource_permission(d, "book")
    try:
        add_log(g.current_user["id"], "修改资源权限", detail={"saved": saved, "deleted": len(deleted)}, ip=get_client_ip())
    except Exception:
        pass
    return ok({"saved": saved, "deleted": len(deleted)}, msg=f"已保存 {saved} 条，删除 {len(deleted)} 条")


@bp.route("/api/permission/delete", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_delete_resource_perm():
    data = request.get_json(silent=True) or {}
    rtype = data.get("resource_type") or "book"
    rid = data.get("resource_id")
    if not rid:
        return fail("缺少资源ID", code=400)
    delete_resource_permission(rid, rtype)
    try:
        add_log(g.current_user["id"], "删除资源权限", detail={"type": rtype, "id": rid}, ip=get_client_ip())
    except Exception:
        pass
    return ok(msg="已删除该权限规则")


@bp.route("/api/module_access/save", methods=["POST"])
@login_required
@permission_required("config_access")
@module_exception_guard("config_center")
def api_save_module_access():
    """保存模块访问级别（全局设置，位于用户管理页）。body: {access: [{module_id, min_level, allowed_users}]}"""
    data = request.get_json(silent=True) or {}
    access = data.get("access") or []
    # 同 api_save_resource_perm：先全量校验再统一写入，避免中途 return
    # 留下「一部分模块已改、一部分没改」的半成品状态。
    items = []
    for a in access:
        mid = a.get("module_id")
        level = a.get("min_level") or "public"
        allowed = a.get("allowed_users") or []
        if not mid:
            continue
        if level == "selected" and not allowed:
            return fail(f"模块 [{mid}] 设为「选择用户可看」时至少选择一名用户", code=400)
        items.append((mid, level, allowed))
    saved = 0
    for mid, level, allowed in items:
        set_module_access(mid, level, allowed_users=allowed)
        saved += 1
    try:
        add_log(g.current_user["id"], "修改模块访问权限", detail={"saved": saved}, ip=get_client_ip())
    except Exception:
        pass
    return ok({"saved": saved}, msg=f"已保存 {saved} 个模块的访问级别")

