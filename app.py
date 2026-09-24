"""
Flask 应用入口。
启动命令：python app.py
开发模式下自动：
  1. 加载 config/app.yaml 与 config/modules.yaml
  2. 初始化数据库（SQLite，懒建表/列）
  3. 通过插件扫描器注册所有模块 Blueprint
  4. 挂载全局登录/登出/404/500
  5. 启动 AI 后台任务调度器（light/full 模式）
"""
import os
import sys
import secrets

# ---------- 保证项目根在 PATH ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Flask, request, redirect, url_for, render_template, session, g, jsonify, send_from_directory, abort

# ---------- Core imports ----------
from core.config_loader import load_app_config, load_modules_config
from core.response import fail as _fail, ok
from core.db_base import (
        init_db_once, get_all_categories, ensure_tables_and_columns, add_log,
        get_user_by_account, verify_password, build_current_user,
        update_last_login, update_user_password, update_user_name,
        user_can_access_module, get_db, user_can_view_book,
    )
from core.plugin_scan import PluginScanner
from core.exceptions import global_exception_handler, module_exception_guard
from core.auth import login_required, SESSION_USER_KEY, need_login_response, load_global_user
from core.ai_interface import ensure_ai_scheduler, is_ai_enabled
from common.utils import get_client_ip, now_str, is_safe_path


# ---------- SECRET_KEY 持久化 ----------
# 之前兑底是每次启动 secrets.token_hex(16)，重启即全员掉线。
# 现在首次启动生成后写入 config/.secret_key（已入 .gitignore），后续启动复用；
# 配置/环境变量显式提供时优先于本地文件。
_SECRET_FILE = os.path.join(BASE_DIR, "config", ".secret_key")

def _load_or_create_secret_key() -> str:
    try:
        if os.path.exists(_SECRET_FILE):
            with open(_SECRET_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        # 0600 权限（Unix）；Windows 下 os.chmod 作用有限但无害
        fd = os.open(_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        return key
    except Exception:
        return secrets.token_hex(32)  # 极端情况下退回原行为，不阻塞启动


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_relative_config=False,
    )

    # ---------- 配置 ----------
    app_cfg = load_app_config() or {}
    mod_cfg = load_modules_config() or []
    _app_section = app_cfg.get("app") or {}
    # 优先级：环境变量 > app.yaml > 本地持久化密钥文件（config/.secret_key，自动生成一次后复用）
    SECRET_KEY = os.environ.get("SECRET_KEY") or _app_section.get("secret_key")
    if not SECRET_KEY:
        SECRET_KEY = _load_or_create_secret_key()
    DEBUG = _app_section.get("debug", False)
    HOST = _app_section.get("host", "0.0.0.0")
    PORT = int(_app_section.get("port", 5000))

    app.config.update(
        SECRET_KEY=SECRET_KEY,
        DEBUG=DEBUG,
        JSON_AS_ASCII=False,
        TEMPLATES_AUTO_RELOAD=True,
        MAX_CONTENT_LENGTH=600 * 1024 * 1024,   # 600MB 上传上限
    )
    app.config["_app_cfg"] = app_cfg
    app.config["_mod_cfg"] = mod_cfg

    # ---------- 统一日志（#26） ----------
    from core.logger import setup_logging, get_logger
    _app_logger = setup_logging()
    app.logger.setLevel(_app_logger.level)

    # ---------- 请求级连接兜底关闭（#12） ----------
    from core.db_base import close_request_connections
    app.teardown_appcontext(close_request_connections)

    # ---------- 数据库：建库 + 补表 + 补列 ----------
    init_db_once()
    try:
        ensure_tables_and_columns()
    except Exception as e:
        _app_logger.warning(f"数据库结构修正时出问题：{e}")

    # ---------- 插件扫描：注册模块 Blueprint + 生成菜单 ----------
    scanner = PluginScanner()
    try:
        registered = scanner.register_blueprints(app)
        _app_logger.info(f"PluginScanner 完成。已加载 {len(registered)} 个模块：{registered}")
    except Exception as e:
        _app_logger.exception("插件扫描注册失败")
        raise

    # 注册完成后，聚合各模块自报的权限点（权限定义去中心化，新增模块无需改动 db_base）
    try:
        from core.db_base import refresh_module_permissions
        refresh_module_permissions()
    except Exception as e:
        print(f"[WARN] 聚合模块权限失败：{e}")

    # 确定默认跳转模块（已启用的第一个模块；优先 homepage 作为首页）
    enabled = scanner.get_enabled_modules()
    default_module = (next((m["module_id"] for m in enabled if m["module_id"] == "homepage"), None)) \
                     or (enabled[0]["module_id"] if enabled else "homepage")
    # 默认跳转的端点 & 前缀（直接用前缀+endpoint 组合，避免请求外调用 url_for 的问题）
    info_map = {m["module_id"]: m for m in enabled}
    _dm_info = info_map.get(default_module, {"route_prefix": "/", "url_default_endpoint": "index"})
    _default_prefix = (_dm_info.get("route_prefix") or "/").rstrip("/") or "/"
    _default_ep = _dm_info.get("url_default_endpoint", "index")
    _default_endpoint = f"{default_module}.{_default_ep}"
    scanner.default_module = default_module  # 兼容旧引用
    default_module_ref = default_module

    # ---------- 注入全局变量（模板 / g ） ----------
    @app.before_request
    def _before_req():
        g.base_dir = BASE_DIR
        g.app_cfg = app_cfg
        g.mod_cfg = mod_cfg
        g.scanner = scanner
        g.app_name = (app_cfg.get("brand") or {}).get("name") or (app_cfg.get("app") or {}).get("name") or ""
        # current_module_id：优先从 g.current_module_id，否则从 request.endpoint 的 blueprint 名推断
        req_endpoint = request.endpoint or ""
        bp_name = req_endpoint.split(".")[0] if "." in req_endpoint else None
        g.current_module_id = getattr(g, "current_module_id", None) or bp_name
        # 已并入「工具箱 Hub」的子模块（chart / office_tools / toolbox）侧栏没有独立入口，
        # 归一化成其归属导航项（toolbox_hub），否则侧栏任何一项都拿不到 active，
        # 黑夜模式下表现为「导航栏选中灯光整个消失」。仅影响侧栏高亮标识，不改变业务与路由。
        g.current_module_id = PluginScanner.HUB_PARENT_MODULE.get(g.current_module_id, g.current_module_id)
        # CSRF 同源校验（POST/PUT/DELETE 必须带 AJAX 头或同源 Referer，否则拦截）
        _csrf_resp = load_global_user()
        if _csrf_resp is not None:
            return _csrf_resp
        uid = session.get(SESSION_USER_KEY)
        g.current_user = None
        g.is_admin = False
        if uid:
            user = build_current_user(uid)
            if user and not user.get("inactive"):
                g.current_user = user
                g.is_admin = user.get("role") in ("admin", "superadmin")
            else:
                session.pop(SESSION_USER_KEY, None)

    @app.context_processor
    def _inject_ctx():
        menu_items = []
        module_urls = {}
        user = getattr(g, "current_user", None)
        try:
            raw_items = scanner.build_menu_items()
            menu_items = [it for it in raw_items if user_can_access_module(user, it.get("module_id"))]
        except Exception:
            menu_items = []
        try:
            module_urls = scanner.build_module_urls()
        except Exception:
            module_urls = {}
        return {
            "app_name": g.app_name,
            "menu_items": menu_items,
            "module_urls": module_urls,
            "current_user": g.current_user,
            "is_admin": g.is_admin,
            "current_module_id": g.current_module_id,
            "default_module": default_module_ref,
        }

    # ---------- 自定义 Jinja 过滤器：fromjson（日志/权限 JSON 字段展示用） ----------
    import json as _json

    @app.template_filter("fromjson")
    def _fromjson_filter(value, default=None):
        try:
            return _json.loads(value)
        except Exception:
            return default if default is not None else {}

    # ---------- 全局错误处理 ----------
    global_exception_handler(app)

    # ---------- 登录 / 登出 ----------
    # 开放重定向拦截：只允许站内单斜杠路径，拦截 "//evil.com" 协议相对跳转
    def _sanitize_next_url(next_url: str) -> str:
        next_url = (next_url or "").strip()
        if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
            return ""
        return next_url

    # ---- 登录防爆破：同一账号+IP 失败 5 次锁定 10 分钟 ----
    import threading as _th
    _login_fail_lock = _th.Lock()
    _login_fails = {}   # key -> {"count": n, "locked_until": ts}
    _LOGIN_MAX_FAILS = 5
    _LOGIN_LOCK_SECONDS = 600

    def _login_lock_key(account: str) -> str:
        return f"{account}|{get_client_ip()}"

    def _login_locked_until(key: str):
        with _login_fail_lock:
            rec = _login_fails.get(key)
            lu = (rec or {}).get("locked_until") or 0
            import time as _time
            if lu > _time.time():
                return lu
            if rec and lu and lu <= _time.time():
                _login_fails.pop(key, None)  # 锁定过期，清除记录
            return 0

    def _login_fail_hit(key: str):
        import time as _time
        with _login_fail_lock:
            rec = _login_fails.setdefault(key, {"count": 0, "locked_until": 0})
            rec["count"] += 1
            if rec["count"] >= _LOGIN_MAX_FAILS:
                rec["locked_until"] = _time.time() + _LOGIN_LOCK_SECONDS
                rec["count"] = 0

    def _login_success_clear(key: str):
        with _login_fail_lock:
            _login_fails.pop(key, None)

    @app.route("/login", methods=["GET"])
    @module_exception_guard("core")
    def login_view():
        if g.current_user:
            return redirect(url_for(_default_endpoint))
        try:
            from core.db_base import db_query
            db_query("SELECT 1")   # 轻量探活，确保库 OK（原来用 get_all_categories 全表查询充当）
        except Exception:
            pass
        return render_template(
            "login.html",
            app_name=g.app_name,
            next_url=_sanitize_next_url(request.args.get("next") or ""),
        )

    @app.route("/api/login", methods=["POST"])
    @module_exception_guard("core")
    def api_login():
        data = request.get_json(silent=True) or {}
        account = (data.get("account") or "").strip()
        password = data.get("password") or ""
        if not account or not password:
            return _fail("请输入账号和密码")
        lock_key = _login_lock_key(account)
        locked_until = _login_locked_until(lock_key)
        if locked_until:
            remain = int(locked_until - __import__("time").time()) // 60 + 1
            try: add_log(None, "登录失败-触发锁定", detail={"account":account}, ip=get_client_ip())
            except Exception: pass
            return _fail(f"失败次数过多，账号已临时锁定，请约 {remain} 分钟后再试", code=429)
        user = get_user_by_account(account)
        if not user:
            _login_fail_hit(lock_key)
            try: add_log(None, "登录失败-账号不存在", detail={"account":account}, ip=get_client_ip())
            except Exception: pass
            return _fail("账号或密码错误")
        if user.get("inactive"):
            try: add_log(user["id"], "登录失败-账号禁用", ip=get_client_ip())
            except Exception: pass
            return _fail("该账号已被禁用，请联系管理员")
        if not verify_password(user, password):
            _login_fail_hit(lock_key)
            try: add_log(user["id"], "登录失败-密码错误", ip=get_client_ip())
            except Exception: pass
            return _fail("账号或密码错误")
        _login_success_clear(lock_key)
        session[SESSION_USER_KEY] = user["id"]
        session.permanent = True
        try:
            update_last_login(user["id"], ip=get_client_ip())
        except Exception:
            pass
        try: add_log(user["id"], "登录成功", ip=get_client_ip())
        except Exception: pass
        next_url = _sanitize_next_url(data.get("next") or "")
        if next_url:
            return jsonify({"code": 200, "msg": "ok", "data": {"redirect": next_url}})
        return jsonify({"code": 200, "msg": "ok", "data": {"redirect": url_for(_default_endpoint)}})

    @app.route("/logout", methods=["GET"])
    def logout_view():
        try:
            if g.current_user:
                add_log(g.current_user["id"], "退出登录", ip=get_client_ip())
        except Exception: pass
        session.pop(SESSION_USER_KEY, None)
        return redirect(url_for(_default_endpoint))

    # ---------- 个人中心：修改密码 / 修改名字（从原图书系统登录头像菜单迁移而来） ----------
    @app.route("/api/account/change-password", methods=["POST"])
    @module_exception_guard("core")
    @login_required
    def api_account_change_password():
        user = g.current_user
        old_pwd = (request.form.get("old_pwd") or "").strip()
        new_pwd = (request.form.get("new_pwd") or "").strip()
        confirm = (request.form.get("confirm_pwd") or "").strip()
        if not old_pwd or not new_pwd or not confirm:
            return _fail("请填写完整信息")
        if new_pwd != confirm:
            return _fail("两次输入的新密码不一致")
        if len(new_pwd) < 6:
            return _fail("新密码至少 6 位")
        if not verify_password(user, old_pwd):
            return _fail("原密码错误")
        update_user_password(user["id"], new_pwd)
        try:
            add_log(user["id"], "修改密码", ip=get_client_ip())
        except Exception:
            pass
        return ok(msg="密码修改成功")

    @app.route("/api/account/change-name", methods=["POST"])
    @module_exception_guard("core")
    @login_required
    def api_account_change_name():
        user = g.current_user
        new_name = (request.form.get("name") or "").strip()
        if not new_name:
            return _fail("名字不能为空")
        if len(new_name) > 32:
            return _fail("名字过长（最多 32 字）")
        update_user_name(user["id"], new_name)
        try:
            add_log(user["id"], "修改名字", ip=get_client_ip())
        except Exception:
            pass
        return ok(msg="名字修改成功")

    # ---------- 根路径跳转到默认模块 ----------
    @app.route("/", methods=["GET"])
    def root():
        return redirect(url_for(_default_endpoint))

    # ---------- 静态文件：上传目录 ----------
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    @app.route("/uploads/<path:filepath>")
    @login_required
    def serve_upload(filepath):
        full_path = os.path.normpath(os.path.join(UPLOAD_DIR, filepath))
        # 边界保护：只允许用户落在上传目录内
        if not is_safe_path(full_path, UPLOAD_DIR):
            return abort(404)
        # 命中图书源文件时，按资源权限校验（admin/selected 级别不向普通登录用户泄露）
        book = None
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM books WHERE savepath=?", (full_path,))
            row = cur.fetchone(); conn.close()
            if row:
                book = dict(row)
        except Exception:
            pass
        if book is not None:
            user = g.get("current_user")
            if not user_can_view_book(book, user):
                if user:
                    return abort(403)
                return need_login_response(request.path)
        if not os.path.exists(full_path):
            return abort(404)
        # XSS 防护：可以在浏览器内渲染为可执行文档的类型（html/svg 等），
        # 一律强制下载，避免同源内嵌渲染窃取登录态 Cookie。
        ext = os.path.splitext(full_path)[1].lstrip(".").lower()
        if ext in ("html", "htm", "svg", "xml", "xhtml"):
            return send_from_directory(UPLOAD_DIR, filepath, as_attachment=True)
        return send_from_directory(UPLOAD_DIR, filepath)

    # ---------- 全局健康检查 ----------
    @app.route("/api/health")
    def health():
        modules_map = (mod_cfg or {}).get("modules", {}) if isinstance(mod_cfg, dict) else {}
        modules_list = [{"id": mid, "enabled": bool(m.get("enabled"))} for mid, m in modules_map.items()] if modules_map else []
        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {
                "app": g.app_name,
                "time": now_str(),
                "ai": {
                    "enabled": is_ai_enabled(),
                },
                "modules": modules_list,
            },
        })

    # ---------- 初始化 AI 调度器（模式开启时） ----------
    with app.app_context():
        try:
            ensure_ai_scheduler()
            if is_ai_enabled():
                try:
                    from core.ai_interface import get_ai_mode
                    print(f"[AI] 已启用模式: {get_ai_mode().upper()}，默认模块: {default_module_ref}")
                except Exception:
                    print(f"[AI] 已启用，默认模块: {default_module_ref}")
        except Exception as e:
            print(f"[AI] 初始化调度器失败（不影响核心功能）：{e}")

    return app, HOST, PORT, DEBUG


if __name__ == "__main__":
    app, HOST, PORT, DEBUG = create_app()
    print("=" * 70)
    print(f"  ✅ 服务启动: http://{HOST}:{PORT}  (debug={DEBUG})")
    print("  🔐 管理员账号 admin：首次启动自动创建，初始密码见下方 [INIT] 输出")
    print("  🧭 首页为「AI 问答」，左侧菜单切换模块")
    print("=" * 70)
    # 确保首次启动自动创建 admin 账号（懒加载）
    # 密码来源：环境变量 ADMIN_INIT_PASSWORD，否则随机生成（开源版不再内置固定口令）
    try:
        from core.db_base import create_default_admin
        _init_pwd = create_default_admin()
        if _init_pwd:
            print(f"[INIT] 已创建超级管理员 admin，初始密码: {_init_pwd}")
            print("[INIT] ⚠️ 请登录后立即修改密码（密码同时写入 config/.init_admin_pwd，勿外传）")
    except Exception as e:
        print(f"[INIT] 创建默认admin账号失败：{e}")
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)
