"""
Book Library 图书模块路由（前缀 /book）。

- 保留原图书系统的全部核心功能：浏览、搜索、详情、笔记、读后感、收藏、
  阅读进度、下载、批量管理、分类排序、资源权限配置。
- AI 功能通过 core.ai_interface 调用，AI 模块故障时图书功能不受影响。
- 故障隔离：所有路由装饰了 module_exception_guard 捕获本模块异常。
"""
import os
import re
import time
import threading
from datetime import datetime
from flask import (
    render_template, request, jsonify, abort, g, send_file,
    redirect, url_for, session,
)

from modules.book_lib import bp
from modules.book_lib import db_ops
from core.auth import login_required, permission_required, need_login_response
from core.response import ok, fail
from core.db_base import (
    add_log, get_book_by_id, filter_books_by_permission,
    set_resource_permission, delete_resource_permission, get_all_resource_permissions,
    user_can_view_book, get_book_effective_level, has_permission,
    get_all_categories, get_all_books, get_all_users,
)
from core.exceptions import module_exception_guard, PermissionDeniedException
from core.ai_interface import (
    is_ai_enabled, get_ai_features, generate_book_summary, ask_book_question,
    trigger_rebuild_for_book, get_vector_stats,
)
from common.utils import (
    get_client_ip, sanitize_text, compute_file_hash,
    get_upload_save_path, resolve_file_path, extract_original_filename,
    allowed_file, BASE_DIR, UPLOAD_FOLDER,
    is_allowed_upload_path, is_safe_path, ALLOWED_EXTENSIONS,
)

# ===== 模块初始化：确保默认分类 + 数据库字段完整 =====
try:
    db_ops.ensure_schema()
    db_ops.ensure_default_categories()
except Exception:
    pass


# ============================================================
# 渲染上下文：给模板注入 AI 特性开关（所有 book_lib 页面都能用）
# ============================================================
@bp.app_context_processor
def _book_ai_context():
    return {"book_ai": get_ai_features()}


# ============================================================
# 页面路由
# ============================================================
@bp.route("/", methods=["GET"])
@module_exception_guard("book_lib")
def index():
    user = g.get("current_user")
    data = db_ops.list_books_tree(user)
    can_private_book = bool(user) and has_permission(user, "private_book")
    return render_template(
        "book_lib/index.html",
        current_module_id="book_lib",
        books=data["tree"],
        total_books=data["total_books"],
        total_categories=data["total_categories"],
        cat1_list=data["cat1_list"],
        cat2_map=data["cat2_map"],
        can_private_book=can_private_book,
    )


# ---- /book/config（管理员配置中心：图书管理 + 分类管理） ----
@bp.route("/config", methods=["GET"])
@login_required
@module_exception_guard("book_lib")
def config():
    user = g.get("current_user")
    if not user or user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可访问配置中心")
    from core.db_base import get_user_permissions
    cats = get_all_categories()
    books = get_all_books()
    users = get_all_users() or []
    # 一级分类（排除"未分类"之外的排序）
    cat1_list = [c for c in cats if not c.get("parent_id")]
    cat1_list.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    nav_first = [c.get("name") for c in cat1_list]
    # layout: {c1_name: [c2_name, ...]}
    layout = {}
    cat2_map = {}
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
                cat2_map.setdefault(parent_name, []).append(c.get("name") or "")
                layout.setdefault(parent_name, []).append(c.get("name") or "")
    # 管理员拥有全部权限
    user_perms = get_user_permissions(user) if user else []
    if user and user.get("role") in ("admin", "superadmin"):
        user_perms = [
            "book_add", "book_edit", "book_delete", "book_batch_delete",
            "book_batch_move", "book_batch_add", "c1_add", "c1_edit",
            "c1_delete", "c1_move", "c2_add", "c2_edit", "c2_delete",
            "c2_move", "file_upload", "private_book", "user_manage", "view_all_records",
            "config_access", "admin",
        ]
    is_admin = True
    return render_template(
        "book_lib/config.html",
        current_module_id="book_lib",
        # 统计卡片
        total_books=len(books),
        total_categories=len(cat1_list),
        total_users=len(users),
        # 配置中心数据（对齐参照模板 JS 契约）
        cfg={"books": books, "layout": layout, "nav_first": nav_first, "cat_colors": cat_colors},
        cat1_list=cat1_list,
        cat2_map=cat2_map,
        nav_first=nav_first,
        layout=layout,
        cat_colors=cat_colors,
        cat_id_map=cat_id_map,
        user_perms=user_perms,
        is_admin=is_admin,
        all_cat="全部",
    )


@bp.route("/my_reading", methods=["GET"])
@login_required
@module_exception_guard("book_lib")
def my_reading():
    from core.db_base import (
        get_user_notes, get_user_reviews, get_user_reading_history, get_user_favorites,
    )
    user = g.current_user
    reading_list = get_user_reading_history(user_id=user["id"], limit=500)
    notes_list = get_user_notes(user_id=user["id"])
    reviews_list = get_user_reviews(user_id=user["id"])
    favs = get_user_favorites(user_id=user["id"])
    # 统计卡片所需：reading/finished/unread 计数
    stats = {"reading": 0, "finished": 0, "unread": 0}
    for h in reading_list:
        s = (h.get("status") or "unread")
        if s == "reading":
            stats["reading"] += 1
        elif s == "finished":
            stats["finished"] += 1
        else:
            stats["unread"] += 1
    return render_template(
        "book_lib/my_reading.html",
        current_module_id="book_lib",
        # ===== 对齐 book_manager_v3 模板数据契约 =====
        stats=stats,
        favorite_count=len(favs),
        favorites=favs,
        history=reading_list,
        notes=notes_list,
        reviews=reviews_list,
        ai_features={"reading_analysis": False},
    )


@bp.route("/my_books", methods=["GET"])
@login_required
@module_exception_guard("book_lib")
def my_books():
    """我的私有图书：列出当前用户拥有且设为私有的资料（仅自己与管理员可见）。"""
    user = g.current_user
    if not has_permission(user, "private_book"):
        raise PermissionDeniedException("无私人图书权限")
    books = db_ops.get_my_books(user["id"], private_only=True)
    # 补充生效权限标签，便于前端展示
    for b in books:
        b["effective_permission"] = get_book_effective_level(b)
    # 个人分类（一/二级），仅本人
    pcats = db_ops.get_private_categories(user["id"])
    pcat1 = [c for c in pcats if not c.get("parent_id")]
    pcat2_map = {}
    for c in pcats:
        if c.get("parent_id"):
            pcat2_map.setdefault(str(c["parent_id"]), []).append(c)
    # 全局分类（上传时可选择挂到共享分类下）
    cats = get_all_categories() or []
    gcat1 = [c for c in cats if not c.get("parent_id")]
    gcat1.sort(key=lambda c: (c.get("sort_order") or 0, c.get("name") or ""))
    gcat2_map = {}
    for c in cats:
        if c.get("parent_id"):
            parent = next((p for p in cats if str(p.get("id")) == str(c.get("parent_id"))), None)
            if parent:
                gcat2_map.setdefault(parent.get("name") or "", []).append(c.get("name") or "")
    return render_template(
        "book_lib/my_books.html",
        current_module_id="book_lib",
        books=books,
        total=len(books),
        private_count=sum(1 for b in books if b.get("pc1")),
        pcat1=pcat1,
        pcat2_map=pcat2_map,
        gcat1=gcat1,
        gcat2_map=gcat2_map,
    )


# ============================================================
# 私人图书馆：私有上传 + 个人分类（private_categories）
# 权限统一走 private_book；与全局分类 / 公开库完全隔离，不改全局任何行为
# ============================================================
@bp.route("/api/private/upload", methods=["POST"])
@login_required
@permission_required("private_book")
@module_exception_guard("book_lib")
def api_private_upload():
    """私人图书馆上传：仅需 private_book 权限（不需要 file_upload/book_add），上传即私有。

    分类二选一（scope）：
      global  → 写入 cat1/cat2（全局分类）：本人在全局图书馆对应分类下可见，私有页也可见
      private → 写入 pc1/pc2（个人分类）：仅在「我的私有图书」显示，不进全局分类树
    """
    from core.db_base import update_book
    user = g.current_user
    files_list = request.files.getlist("files") or []
    if not files_list:
        return fail("请选择要上传的文件")
    scope = (request.form.get("scope") or "global").strip().lower()
    c1 = sanitize_text((request.form.get("cat1") or "").strip(), max_len=30) or "未分类"
    c2 = sanitize_text((request.form.get("cat2") or "").strip(), max_len=30) or "未分类"
    p1 = sanitize_text((request.form.get("pc1") or "").strip(), max_len=30)
    p2 = sanitize_text((request.form.get("pc2") or "").strip(), max_len=30)
    if scope not in ("global", "private"):
        scope = "global"
    if scope == "private" and not p1:
        return fail("请选择个人分类")
    # 文件落盘目录：个人分类用 pc，全局分类用 c
    dir1, dir2 = (p1 or "未分类", p2 or "未分类") if scope == "private" else (c1, c2)
    added_ids = []
    msgs = []
    for f in files_list:
        fname = f.filename or ""
        if not fname:
            continue
        if not allowed_file(fname):
            msgs.append(f"跳过（不允许的扩展名）: {fname}")
            continue
        save_path, rel_path = get_upload_save_path(fname, dir1, dir2)
        try:
            f.save(save_path)
        except Exception as e:
            msgs.append(f"保存失败 {fname}: {e}")
            continue
        try:
            file_ext = fname.rsplit('.', 1)[1].lower()
        except Exception:
            file_ext = ""
        try:
            file_size = os.path.getsize(save_path)
        except Exception:
            file_size = 0
        if file_size > 500 * 1024 * 1024:
            try:
                os.remove(save_path)
            except Exception:
                pass
            msgs.append(f"文件过大: {fname}")
            continue
        file_hash = compute_file_hash(save_path)
        name = os.path.splitext(fname)[0]
        bid = db_ops.add_book(
            name=name, cat1=c1, cat2=c2, path=rel_path,
            upload_by=user.get("account") or user.get("id"),
            owner_id=user.get("id"),
            file_hash=file_hash, savepath=save_path,
            file_ext=file_ext, file_size=file_size,
        )
        # 个人分类写入 pc1/pc2（list_books_tree 会据此排除，不进全局树）
        if scope == "private":
            try:
                update_book(bid, pc1=p1, pc2=p2)
            except Exception:
                pass
        # 一律私有（selected + allowed_users=[owner]）
        db_ops.set_book_visibility(bid, "private", owner_id=user.get("id"))
        added_ids.append(bid)
        try:
            add_log(user["id"], "上传私有资料", target_type="book", target_id=bid,
                    detail={"filename": fname, "size": file_size, "scope": scope},
                    ip=get_client_ip())
        except Exception:
            pass
    db_ops.clear_categories_layout_cache()
    if is_ai_enabled():
        for bid in added_ids:
            try:
                threading.Thread(target=trigger_rebuild_for_book, args=(bid,), daemon=True).start()
            except Exception:
                pass
    return ok({"added": len(added_ids), "ids": added_ids, "msgs": msgs})


@bp.route("/api/pcat/list", methods=["GET"])
@login_required
@permission_required("private_book")
@module_exception_guard("book_lib")
def api_pcat_list():
    """列出当前用户的个人分类（一/二级）。"""
    user = g.current_user
    return ok({"items": db_ops.get_private_categories(user["id"])})


@bp.route("/api/pcat/add", methods=["POST"])
@login_required
@permission_required("private_book")
@module_exception_guard("book_lib")
def api_pcat_add():
    """新增个人分类。parent_id 为空=一级，否则=挂在指定一级下的二级。"""
    user = g.current_user
    data = request.get_json(silent=True) or request.form
    name = sanitize_text((data.get("name") or "").strip(), max_len=30)
    parent_id = (data.get("parent_id") or "").strip() or None
    if not name:
        return fail("分类名称不能为空")
    cid, err = db_ops.add_private_category(user["id"], name, parent_id=parent_id)
    if err:
        return fail(err)
    try:
        add_log(user["id"], "新增个人分类", target_type="category", target_id=cid,
                detail={"name": name, "parent_id": parent_id}, ip=get_client_ip())
    except Exception:
        pass
    return ok({"id": cid})


@bp.route("/api/pcat/rename", methods=["POST"])
@login_required
@permission_required("private_book")
@module_exception_guard("book_lib")
def api_pcat_rename():
    """个人分类改名（仅本人）。"""
    user = g.current_user
    data = request.get_json(silent=True) or request.form
    cid = (data.get("id") or "").strip()
    name = sanitize_text((data.get("name") or "").strip(), max_len=30)
    if not cid or not name:
        return fail("参数不完整")
    ok_, err = db_ops.rename_private_category(cid, user["id"], name)
    if not ok_:
        return fail(err or "改名失败")
    return ok()


@bp.route("/api/pcat/delete", methods=["POST"])
@login_required
@permission_required("private_book")
@module_exception_guard("book_lib")
def api_pcat_delete():
    """删除个人分类（连带其下二级）。挂在其中的书不会删除，仅失去归属。"""
    user = g.current_user
    data = request.get_json(silent=True) or request.form
    cid = (data.get("id") or "").strip()
    if not cid:
        return fail("缺少分类ID")
    ok_, err = db_ops.delete_private_category(cid, user["id"])
    if not ok_:
        return fail(err or "删除失败")
    return ok()


@bp.route("/detail/<book_id>", methods=["GET"])
@module_exception_guard("book_lib")
def book_detail(book_id):
    from core.db_base import (
        get_user_notes, get_user_reviews, get_user_favorites, has_permission,
    )
    user = g.get("current_user")
    ai = get_ai_features()
    book = get_book_by_id(book_id)
    if not book:
        return abort(404)
    is_admin = bool(user and user.get("role") in ("admin", "superadmin"))
    can_view = user_can_view_book(book, user)
    if not can_view:
        if user:
            raise PermissionDeniedException("该图书仅指定用户/管理员可访问")
        return need_login_response(request.path)
    book["effective_permission"] = get_book_effective_level(book)
    # 笔记（公开 + 自己写的）
    notes = get_user_notes(book_id=book_id)
    me_notes = [n for n in notes if user and n.get("user_id") == user.get("id")]
    other_notes = [n for n in notes if not (user and n.get("user_id") == user.get("id"))]
    # 读后感
    reviews = get_user_reviews(book_id=book_id)
    me_reviews = [r for r in reviews if user and r.get("user_id") == user.get("id")]
    # 修：原写法是 r.get("user_id") == r.get("id")，拿「读后感作者」和「读后感自身主键」
    # 比较，结果 other_reviews 恒等于全部读后感（自己写的也混在里面）。
    other_reviews = [r for r in reviews if not (user and r.get("user_id") == user.get("id"))]
    # 我的收藏
    my_fav = False
    if user:
        favs = get_user_favorites(user_id=user["id"], book_id=book_id)
        my_fav = bool(favs)
    # 阅读状态
    my_reading_status = None
    if user:
        from core.db_base import get_user_reading_history
        lst = get_user_reading_history(user_id=user["id"], book_id=book_id, limit=1)
        if lst: my_reading_status = lst[0].get("status")
    avg_rating = 0
    if reviews:
        ratings = [r.get("rating") or 0 for r in reviews]
        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 1)
    can_edit = bool(user and has_permission(user, "book_edit"))
    can_delete = bool(user and has_permission(user, "book_delete"))
    can_upload = bool(user and has_permission(user, "file_upload"))
    can_config_perm = bool(user and has_permission(user, "config_access"))
    return render_template(
        "book_lib/book_detail.html",
        current_module_id="book_lib",
        # ===== 对齐 book_manager_v3 模板数据契约 =====
        book=book,
        read_count=0,            # 框架未单独统计阅读人数，占位
        reading_count=0,
        reading_record={"status": my_reading_status} if my_reading_status else None,
        is_favorited=my_fav,
        my_review=me_reviews[0] if me_reviews else None,
        notes=notes,
        reviews=reviews,
        ai_features={
            "auto_summary": bool(ai.get("summary")),
            "similar_books": False,   # 框架未暴露相似书端点
            "qa": bool(ai.get("qa")),
            "long_doc_summary": False,
            "note_polish": False,
        },
        is_admin=is_admin,
        user_perms=[],
        current_user=user,
    )


# ============================================================
# 文件下载 / 在线预览（安全）
# ============================================================
@bp.route("/download/<book_id>", methods=["GET"])
@module_exception_guard("book_lib")
def download_book(book_id):
    book = get_book_by_id(book_id)
    if not book:
        return abort(404)
    user = g.get("current_user")
    if not user_can_view_book(book, user):
        if user:
            return abort(403)
        return need_login_response(request.path)
    full_path = None
    err = None
    if book.get("savepath") and os.path.exists(book.get("savepath")):
        # savepath 是绝对路径，此前完全不做范围校验 —— 只要能把 books.savepath
        # 写成任意路径，/download/<id> 就是一个任意文件下载器。
        if is_safe_path(os.path.abspath(book["savepath"]), UPLOAD_FOLDER):
            full_path = book["savepath"]
    if not full_path:
        #resolve_file_path 的范围是整个 BASE_DIR，过宽；
        # 收紧到 UPLOAD_FOLDER（不限制扩展名，避免误伤历史数据）
        full_path, err = is_allowed_upload_path(book.get("path") or "")
    if not full_path:
        return f"文件不存在：{err or ''}", 404
    try:
        download_name = extract_original_filename(full_path) or book.get("name") or os.path.basename(full_path)
    except Exception:
        download_name = book.get("name") or os.path.basename(full_path)
    try:
        add_log(user["id"] if user else None, "下载图书", target_type="book", target_id=book_id, ip=get_client_ip(),
                detail={"filename": download_name})
    except Exception:
        pass
    return send_file(full_path, as_attachment=True, download_name=download_name)


@bp.route("/view/<book_id>", methods=["GET"])
@module_exception_guard("book_lib")
def view_online(book_id):
    """只允许图片/PDF/TXT 等可预览类型直接在线预览；其他类型转为下载。"""
    book = get_book_by_id(book_id)
    if not book: return abort(404)
    user = g.get("current_user")
    if not user_can_view_book(book, user):
        if user:
            return abort(403)
        return need_login_response(request.path)
    full_path = None
    if book.get("savepath") and os.path.exists(book.get("savepath")):
        # 同 download_book：savepath 为绝对路径，必须先确认落在上传目录内
        if is_safe_path(os.path.abspath(book["savepath"]), UPLOAD_FOLDER):
            full_path = book["savepath"]
    if not full_path:
        full_path, err = is_allowed_upload_path(book.get("path") or "")
    if not full_path:
        return f"文件不存在：{err or ''}", 404
    ext = os.path.splitext(full_path)[1].lower()
    preview_exts = {".txt", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".csv", ".log"}
    download_name = extract_original_filename(full_path) or book.get("name")
    if ext in preview_exts:
        try:
            add_log(user["id"] if user else None, "在线预览图书", target_type="book", target_id=book_id, ip=get_client_ip())
        except Exception:
            pass
        return send_file(full_path, download_name=download_name)
    return send_file(full_path, as_attachment=True, download_name=download_name)


# ============================================================
# 搜索
# ============================================================
@bp.route("/api/search", methods=["GET"])
@module_exception_guard("book_lib")
def api_search():
    kw = (request.args.get("kw") or "").strip()
    kw = sanitize_text(kw, max_len=50)
    user = g.get("current_user")
    all_books = filter_books_by_permission([], user) if not kw else []
    if kw:
        from core.db_base import get_all_books
        all_books = get_all_books()
        all_books = filter_books_by_permission(all_books, user)
        low = kw.lower()
        result = []
        for b in all_books:
            text = f"{b.get('name','')} {b.get('cat1','')} {b.get('cat2','')} {b.get('tags','')} {b.get('summary','')}".lower()
            if low in text:
                result.append(b)
        all_books = result
    return ok({"items": all_books[:500], "total": len(all_books)})


# ============================================================
# 图书 CRUD API
# ============================================================
@bp.route("/api/upload", methods=["POST"])
@login_required
@permission_required("file_upload")
@module_exception_guard("book_lib")
def api_upload():
    user = g.current_user
    files_list = request.files.getlist("files") or []
    if not files_list:
        return fail("请选择要上传的文件")
    cat1 = (request.form.get("cat1") or "").strip() or "未分类"
    cat2 = (request.form.get("cat2") or "").strip() or "未分类"
    cat1 = sanitize_text(cat1, max_len=30)
    cat2 = sanitize_text(cat2, max_len=30)
    visibility = (request.form.get("visibility") or "public").strip().lower()
    if visibility == "private" and not has_permission(user, "private_book"):
        return fail("无权限将图书设为私有")
    added_ids = []
    msgs = []
    for f in files_list:
        fname = f.filename or ""
        if not fname:
            continue
        if not allowed_file(fname):
            msgs.append(f"跳过（不允许的扩展名）: {fname}")
            continue
        save_path, rel_path = get_upload_save_path(fname, cat1, cat2)
        try:
            f.save(save_path)
        except Exception as e:
            msgs.append(f"保存失败 {fname}: {e}")
            continue
        try:
            file_ext = fname.rsplit('.', 1)[1].lower()
        except Exception:
            file_ext = ""
        try:
            file_size = os.path.getsize(save_path)
        except Exception:
            file_size = 0
        if file_size > 500 * 1024 * 1024:
            try: os.remove(save_path)
            except Exception: pass
            msgs.append(f"文件过大: {fname}")
            continue
        file_hash = compute_file_hash(save_path)
        name = os.path.splitext(fname)[0]
        bid = db_ops.add_book(
            name=name, cat1=cat1, cat2=cat2, path=rel_path,
            upload_by=user.get("account") or user.get("id"),
            owner_id=user.get("id"),
            file_hash=file_hash,
            savepath=save_path, file_ext=file_ext, file_size=file_size,
        )
        added_ids.append(bid)
        # 私人图书馆：标记私有则仅 owner + 管理员可见
        if visibility == "private":
            db_ops.set_book_visibility(bid, "private", owner_id=user.get("id"))
        try:
            add_log(user["id"], "上传图书", target_type="book", target_id=bid,
                    detail={"filename": fname, "size": file_size, "cat1": cat1, "cat2": cat2},
                    ip=get_client_ip())
        except Exception:
            pass
    db_ops.clear_categories_layout_cache()
    # 若 AI 启用则异步触发摘要/索引
    if is_ai_enabled():
        for bid in added_ids:
            #改为后台线程异步触发索引/摘要，避免扫描件在请求线程内
            # 同步跑完解析+OCR+向量化而超时（原注释写"异步"实为同步）。
            try:
                threading.Thread(target=trigger_rebuild_for_book, args=(bid,), daemon=True).start()
            except Exception:
                pass
    return ok({"added": len(added_ids), "ids": added_ids, "msgs": msgs})


@bp.route("/api/edit", methods=["POST"])
@login_required
@permission_required("book_edit")
@module_exception_guard("book_lib")
def api_edit():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("id") or "").strip()
    if not book_id:
        return fail("缺少图书ID")
    name = (data.get("name") or "").strip()
    if not name:
        return fail("名称不能为空")
    update_fields = {
        "name": sanitize_text(name, max_len=200),
        "cat1": sanitize_text((data.get("cat1") or data.get("c1") or "").strip(), max_len=30) or "未分类",
        "cat2": sanitize_text((data.get("cat2") or data.get("c2") or "").strip(), max_len=30) or "未分类",
    }
    # AI 问答档位（public/general/precise）：单本覆盖分类自动判定。
    # 前端传 "auto" 或留空/非法值 → 清空单本覆盖，交回分类自动判定；合法档位直接写入。
    qa_mode = (data.get("qa_mode") or "").strip().lower()
    if qa_mode:
        update_fields["qa_mode"] = "" if qa_mode == "auto" or qa_mode not in ("public", "general", "precise") else qa_mode
    if data.get("sort_order") is not None:
        try: update_fields["sort_order"] = int(data.get("sort_order"))
        except Exception: pass
    from core.db_base import update_book
    update_book(book_id, **update_fields)
    # 可见性切换（私人图书馆）：公开/私有
    vis = (data.get("visibility") or "").strip().lower()
    if vis in ("public", "private"):
        bk = get_book_by_id(book_id)
        owner = (bk or {}).get("owner_id") or g.current_user.get("id")
        # 归属校验：可见性决定「谁能看见这本书」，改动它必须是本人或管理员。
        # 原先只对 private 做权限校验、public 分支完全不校验归属 —— 任何持有
        # book_edit 的人都能把别人（含管理员）设为私有的书一键改成公开。
        _cur_id = str((g.current_user or {}).get("id") or "")
        _is_admin = (g.current_user or {}).get("role") in ("admin", "superadmin")
        if owner and _cur_id and str(owner) != _cur_id and not _is_admin:
            return fail("只能修改自己上传的图书的可见性", code=403)
        if vis == "private" and not has_permission(g.current_user, "private_book"):
            return fail("无权限将图书设为私有")
        db_ops.set_book_visibility(book_id, vis, owner_id=owner)
    try:
        add_log(g.current_user["id"], "编辑图书", target_type="book", target_id=book_id,
                detail=update_fields, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    return ok()


@bp.route("/api/delete", methods=["POST"])
@login_required
@permission_required("book_delete")
@module_exception_guard("book_lib")
def api_delete():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("id") or "").strip()
    if not book_id:
        return fail("缺少图书ID")
    ok_, msg = db_ops.delete_book(book_id)
    if not ok_:
        return fail(msg or "删除失败")
    db_ops.clear_categories_layout_cache()
    return ok()


# ============================================================
# 孤儿文件回收
# 两步式上传（/api/upload_only 只存盘、/api/batch_add 才入库）会留下
# 从未进入 books 表的文件，任何删除操作都够不着它们。这里按磁盘事实对账，
# 提供「先看后删」的回收入口，默认只读扫描，清理必须显式传入待删清单。
# ============================================================
@bp.route("/api/orphan_scan", methods=["GET", "POST"])
@login_required
@permission_required("book_delete")
@module_exception_guard("book_lib")
def api_orphan_scan():
    """只读扫描：列出 uploads 下未被 books 表引用的孤儿文件。"""
    user = g.current_user or {}
    if user.get("role") not in ("admin", "superadmin"):
        return fail("仅管理员可扫描孤儿文件", code=403)
    try:
        from modules.book_lib import orphan
    except Exception as e:
        return fail("孤儿扫描模块不可用: %s" % e, code=500)
    try:
        min_age = int(request.values.get("min_age") or orphan.DEFAULT_MIN_AGE)
    except Exception:
        min_age = orphan.DEFAULT_MIN_AGE
    items = orphan.scan_orphan_files(min_age_sec=max(0, min_age))
    summ = orphan.summary(items)
    return ok(data={
        "items": [{
            "rel": i["rel"], "name": i["name"], "size": i["size"],
            "mtime_str": i["mtime_str"],
        } for i in items],
        "summary": summ,
        "min_age_sec": max(0, min_age),
    })


@bp.route("/api/orphan_purge", methods=["POST"])
@login_required
@permission_required("book_delete")
@module_exception_guard("book_lib")
def api_orphan_purge():
    """清理孤儿文件。必须显式传入 paths（绝对路径清单），支持 dry_run 预演。"""
    user = g.current_user or {}
    if user.get("role") not in ("admin", "superadmin"):
        return fail("仅管理员可清理孤儿文件", code=403)
    data = request.get_json(silent=True) or request.form
    paths = data.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return fail("缺少待清理文件清单", code=400)
    dry = str(data.get("dry_run") or "").lower() in ("1", "true", "yes")
    try:
        from modules.book_lib import orphan
    except Exception as e:
        return fail("孤儿扫描模块不可用: %s" % e, code=500)
    deleted, failed, freed, skipped = orphan.purge_orphan_files(
        paths,
        user_id=user.get("id"),
        ip=get_client_ip(),
        dry_run=dry,
    )
    return ok(data={
        "deleted": deleted, "failed": failed, "skipped": skipped,
        "freed_bytes": freed, "freed_mb": round(freed / 1024 / 1024, 2),
        "dry_run": dry,
    })


@bp.route("/api/book/move_order", methods=["POST"])
@login_required
@permission_required("book_edit")
@module_exception_guard("book_lib")
def api_book_move_order():
    data = request.get_json(silent=True) or request.form
    bid = (data.get("id") or "").strip()
    direction = (data.get("direction") or "").strip()
    if not bid or direction not in ("up", "down"):
        return fail("参数错误")
    from core.db_base import move_book_order
    done = move_book_order(bid, direction)
    if not done:
        return fail("无法移动（已是首位或末尾）")
    try:
        book = get_book_by_id(bid)
        add_log(g.current_user["id"], "图书排序", target_type="book", target_id=bid,
                detail={"direction": direction, "name": (book or {}).get("name", "")}, ip=get_client_ip())
    except Exception: pass
    return ok()


@bp.route("/api/book/move_edge", methods=["POST"])
@login_required
@permission_required("book_edit")
@module_exception_guard("book_lib")
def api_book_move_edge():
    data = request.get_json(silent=True) or request.form
    bid = (data.get("id") or "").strip()
    direction = (data.get("direction") or "").strip()
    if not bid or direction not in ("top", "bottom"):
        return fail("参数错误")
    from core.db_base import move_book_to_edge
    done = move_book_to_edge(bid, direction)
    if not done:
        return fail("更新失败")
    try:
        book = get_book_by_id(bid)
        add_log(g.current_user["id"], "图书排序", target_type="book", target_id=bid,
                detail={"direction": direction, "name": (book or {}).get("name", "")}, ip=get_client_ip())
    except Exception: pass
    return ok()


@bp.route("/api/batch_move", methods=["POST"])
@login_required
@permission_required("book_batch_move")
@module_exception_guard("book_lib")
def api_batch_move():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or request.form.getlist("ids") or []
    cat1 = sanitize_text((data.get("cat1") or request.form.get("c1") or "").strip(), max_len=30) or "未分类"
    cat2 = sanitize_text((data.get("cat2") or request.form.get("c2") or "").strip(), max_len=30) or "未分类"
    n = db_ops.batch_move_books(ids, cat1, cat2)
    db_ops.clear_categories_layout_cache()
    try:
        add_log(g.current_user["id"], "批量移动图书",
                detail={"new_cat1": cat1, "new_cat2": cat2, "count": n}, ip=get_client_ip())
    except Exception: pass
    return ok({"moved": n})


@bp.route("/api/batch_delete", methods=["POST"])
@login_required
@permission_required("book_batch_delete")
@module_exception_guard("book_lib")
def api_batch_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or request.form.getlist("ids") or []
    ok_count, fail_count = db_ops.batch_delete_books(ids, user_id=g.current_user.get("id"), ip=get_client_ip())
    db_ops.clear_categories_layout_cache()
    return ok({"deleted": ok_count, "failed": fail_count})


# ============================================================
# 配置中心：图书列表 + 单本新增（带路径，不走上传）
# ============================================================
@bp.route("/api/books_list", methods=["GET"])
@login_required
@module_exception_guard("book_lib")
def api_books_list():
    user = g.get("current_user")
    if not user or user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可访问")
    books = get_all_books()
    perm_map = get_all_resource_permissions()
    for b in books:
        b["effective_permission"] = perm_map.get(f"book:{b.get('id')}", "public")
        b["is_private"] = (b["effective_permission"] == "selected")
        b.pop("savepath", None)  # 不向前端泄露服务端绝对路径
    return ok({"books": books, "total": len(books)})


@bp.route("/api/book/add", methods=["POST"])
@login_required
@permission_required("book_add")
@module_exception_guard("book_lib")
def api_book_add():
    data = request.get_json(silent=True) or request.form
    name = sanitize_text((data.get("name") or "").strip(), max_len=200)
    if not name:
        return fail("名称不能为空")
    c1 = sanitize_text((data.get("c1") or "").strip(), max_len=30) or "未分类"
    c2 = sanitize_text((data.get("c2") or "").strip(), max_len=30) or "未分类"
    path = (data.get("path") or "").strip()
    if not path:
        return fail("路径不能为空")
    #路径白名单。原先只校验非空，配合 /download/<id> 可把
    # book_manager.db、config/app.yaml 当成图书下载出去（send_file 直接落盘）。
    _full, _perr = is_allowed_upload_path(path, allowed_exts=ALLOWED_EXTENSIONS)
    if _perr:
        return fail("文件路径不合法：%s" % _perr)
    user = g.current_user
    visibility = (data.get("visibility") or "public").strip().lower()
    if visibility == "private" and not has_permission(user, "private_book"):
        return fail("无权限将图书设为私有")
    bid = db_ops.add_book(
        name=name, cat1=c1, cat2=c2, path=path,
        upload_by=user.get("account") or user.get("id"),
        owner_id=user.get("id"),
    )
    if visibility == "private":
        db_ops.set_book_visibility(bid, "private", owner_id=user.get("id"))
    try:
        add_log(user["id"], "新增图书", target_type="book", target_id=bid,
                detail={"name": name, "cat1": c1, "cat2": c2}, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    return ok({"id": bid})


@bp.route("/api/upload_only", methods=["POST"])
@login_required
@permission_required("file_upload")
@module_exception_guard("book_lib")
def api_upload_only():
    """仅上传文件（不创建图书记录），返回路径供新增资料弹窗使用。"""
    user = g.current_user
    files_list = request.files.getlist("files") or []
    if not files_list:
        return fail("请选择要上传的文件")
    cat1 = (request.form.get("c1") or "").strip() or "未分类"
    cat2 = (request.form.get("c2") or "").strip() or "未分类"
    results = []
    for f in files_list:
        fname = f.filename or ""
        if not fname:
            continue
        if not allowed_file(fname):
            results.append({"code": 400, "name": fname, "msg": "不允许的扩展名"})
            continue
        save_path, rel_path = get_upload_save_path(fname, cat1, cat2)
        try:
            f.save(save_path)
        except Exception as e:
            results.append({"code": 500, "name": fname, "msg": f"保存失败: {e}"})
            continue
        try:
            file_size = os.path.getsize(save_path)
        except Exception:
            file_size = 0
        if file_size > 500 * 1024 * 1024:
            try: os.remove(save_path)
            except Exception: pass
            results.append({"code": 400, "name": fname, "msg": "文件过大"})
            continue
        results.append({"code": 200, "name": fname, "path": rel_path})
    try:
        add_log(user["id"], "上传文件(配置中心)", detail={"count": len(results)}, ip=get_client_ip())
    except Exception:
        pass
    return ok({"results": results})


@bp.route("/api/book/batchadd", methods=["POST"])
@login_required
@permission_required("book_batch_add")
@module_exception_guard("book_lib")
def api_book_batchadd():
    """批量入库：根据 names + paths 创建图书记录。"""
    user = g.current_user
    c1 = sanitize_text((request.form.get("c1") or "").strip(), max_len=30) or "未分类"
    c2 = sanitize_text((request.form.get("c2") or "").strip(), max_len=30) or "未分类"
    visibility = (request.form.get("visibility") or "public").strip().lower()
    if visibility == "private" and not has_permission(user, "private_book"):
        return fail("无权限将图书设为私有")
    names = request.form.getlist("names") or []
    paths = request.form.getlist("paths") or []
    if not names or len(names) != len(paths):
        return fail("参数不匹配")
    added_ids = []
    for name, path in zip(names, paths):
        name = sanitize_text(name, max_len=200)
        if not name or not path:
            continue
        bid = db_ops.add_book(
            name=name, cat1=c1, cat2=c2, path=path,
            upload_by=user.get("account") or user.get("id"),
            owner_id=user.get("id"),
        )
        added_ids.append(bid)
        if visibility == "private":
            db_ops.set_book_visibility(bid, "private", owner_id=user.get("id"))
    try:
        add_log(user["id"], "批量入库图书", detail={"count": len(added_ids), "c1": c1, "c2": c2}, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    if is_ai_enabled():
        for bid in added_ids:
            #改为后台线程异步触发索引/摘要，避免扫描件在请求线程内
            # 同步跑完解析+OCR+向量化而超时（原注释写"异步"实为同步）。
            try:
                threading.Thread(target=trigger_rebuild_for_book, args=(bid,), daemon=True).start()
            except Exception:
                pass
    return ok({"added": len(added_ids), "ids": added_ids})


# ============================================================
# 配置中心：分类 CRUD（一级/二级分类 新增/编辑/删除/排序）
# ============================================================
@bp.route("/api/category/add", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_category_add():
    user = g.current_user
    if user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可操作分类")
    data = request.get_json(silent=True) or request.form
    name = sanitize_text((data.get("name") or "").strip(), max_len=30)
    if not name:
        return fail("分类名称不能为空")
    parent_id = (data.get("parent_id") or "").strip() or None
    cats = get_all_categories()
    # 重名检查
    for c in cats:
        if c.get("name") == name and str(c.get("parent_id") or "") == str(parent_id or ""):
            return fail("分类名称已存在")
    # 计算 sort_order
    siblings = [c for c in cats if str(c.get("parent_id") or "") == str(parent_id or "")]
    sort_order = len(siblings)
    cid = db_ops.add_category(name, parent_id=parent_id, sort_order=sort_order, color="#6c5ce7")
    try:
        add_log(user["id"], "新增分类", target_type="category", target_id=cid,
                detail={"name": name, "parent_id": parent_id}, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    return ok({"id": cid})


@bp.route("/api/category/edit", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_category_edit():
    user = g.current_user
    if user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可操作分类")
    data = request.get_json(silent=True) or request.form
    cat_id = (data.get("id") or "").strip()
    if not cat_id:
        return fail("缺少分类ID")
    name = sanitize_text((data.get("name") or "").strip(), max_len=30)
    color = (data.get("color") or "").strip() or None
    if not name:
        return fail("名称不能为空")
    # 查找原分类
    cats = get_all_categories()
    cat = next((c for c in cats if str(c.get("id")) == str(cat_id)), None)
    if not cat:
        return fail("分类不存在")
    old_name = cat.get("name")
    is_c1 = not cat.get("parent_id")
    fields = {"name": name, "color": color}
    db_ops.update_category(cat_id, **fields)
    # 同步更新图书表中的 cat1/cat2
    parent_name = ""
    if not is_c1:
        parent_name = next(
            (c.get("name") for c in cats
             if str(c.get("id")) == str(cat.get("parent_id"))), "") or ""
    conn = db_ops.get_db()
    cur = conn.cursor()
    try:
        if is_c1:
            cur.execute("UPDATE books SET cat1=? WHERE cat1=?", (name, old_name))
        else:
            # 二级分类必须限定在同一父分类下：不同一级分类下允许存在同名二级
            # （如「制度」下和「标准」下都有「规范」），只按 cat2=? 匹配会把
            # 另一个父分类下的书一并改名 → 分类统计与筛选错位。
            cur.execute("UPDATE books SET cat2=? WHERE cat2=? AND cat1=?",
                        (name, old_name, parent_name))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        add_log(user["id"], "编辑分类", target_type="category", target_id=cat_id,
                detail={"old_name": old_name, "new_name": name}, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    return ok()


@bp.route("/api/category/delete", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_category_delete():
    user = g.current_user
    if user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可操作分类")
    data = request.get_json(silent=True) or request.form
    cat_id = (data.get("id") or "").strip()
    if not cat_id:
        return fail("缺少分类ID")
    cats = get_all_categories()
    cat = next((c for c in cats if str(c.get("id")) == str(cat_id)), None)
    if not cat:
        return fail("分类不存在")
    if cat.get("name") == "未分类":
        return fail("未分类不可删除")
    ok_ = db_ops.delete_category(cat_id, user=user, ip=get_client_ip())
    if not ok_:
        return fail("删除失败")
    db_ops.clear_categories_layout_cache()
    return ok()


@bp.route("/api/category/move", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_category_move():
    user = g.current_user
    if user.get("role") not in ("admin", "superadmin"):
        raise PermissionDeniedException("仅管理员可操作分类")
    data = request.get_json(silent=True) or request.form
    cat_id = (data.get("id") or "").strip()
    direction = (data.get("direction") or "up").strip()
    if not cat_id:
        return fail("缺少分类ID")
    if direction not in ("up", "down"):
        return fail("方向参数无效")
    db_ops.move_category(cat_id, direction)
    try:
        add_log(user["id"], "移动分类", target_type="category", target_id=cat_id,
                detail={"direction": direction}, ip=get_client_ip())
    except Exception:
        pass
    db_ops.clear_categories_layout_cache()
    return ok()




# ============================================================
# 阅读 / 笔记 / 读后感 / 收藏
# ============================================================
@bp.route("/api/reading_status", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_reading_status():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("book_id") or "").strip()
    status = (data.get("status") or "reading").strip().lower()
    if not book_id: return fail("缺少图书ID")
    if status not in ("reading", "finished", "wishlist", "dropped"):
        status = "reading"
    progress = data.get("progress")
    try: progress = int(progress) if progress is not None else None
    except Exception: progress = None
    db_ops.upsert_reading(g.current_user["id"], book_id, status, progress)
    try:
        add_log(g.current_user["id"], "更新阅读状态", target_type="book", target_id=book_id,
                detail={"status": status, "progress": progress}, ip=get_client_ip())
    except Exception: pass
    return ok()


@bp.route("/api/note/add", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_add_note():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("book_id") or "").strip()
    content = (data.get("content") or "").strip()
    is_public = data.get("is_public", True)
    if not book_id: return fail("缺少图书ID")
    if not content: return fail("内容不能为空")
    nid = db_ops.add_note(g.current_user["id"], book_id, content, bool(is_public))
    try:
        add_log(g.current_user["id"], "添加笔记", target_type="book", target_id=book_id,
                detail={"len": len(content)}, ip=get_client_ip())
    except Exception: pass
    return ok({"id": nid})


@bp.route("/api/review/add", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_add_review():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("book_id") or "").strip()
    content = (data.get("content") or "").strip()
    rating = data.get("rating", 5)
    try: rating = int(rating)
    except Exception: rating = 5
    is_public = data.get("is_public", True)
    if not book_id: return fail("缺少图书ID")
    if not content: return fail("内容不能为空")
    rid = db_ops.add_review(g.current_user["id"], book_id, content, rating, bool(is_public))
    try:
        add_log(g.current_user["id"], "添加读后感", target_type="book", target_id=book_id, ip=get_client_ip())
    except Exception: pass
    return ok({"id": rid})


@bp.route("/api/favorite/toggle", methods=["POST"])
@login_required
@module_exception_guard("book_lib")
def api_favorite_toggle():
    data = request.get_json(silent=True) or request.form
    book_id = (data.get("book_id") or "").strip()
    if not book_id: return fail("缺少图书ID")
    added = db_ops.toggle_favorite(g.current_user["id"], book_id)
    try:
        add_log(g.current_user["id"], "收藏/取消收藏", target_type="book", target_id=book_id,
                detail={"added": added}, ip=get_client_ip())
    except Exception: pass
    return ok({"is_favorite": added})


