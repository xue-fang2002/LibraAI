"""Homepage 首页模块路由。

作为整个系统的默认首页，提供AI全局智能问答界面。
其他模块也可以通过 core.ai_interface 调用AI功能。
"""
import time
import json

from flask import render_template, request, jsonify, session, g, Response, stream_with_context

from modules.homepage import bp
from core.auth import login_required
from core.ai_interface import global_ai_chat, global_ai_chat_stream, is_ai_enabled, get_ai_features
from core.response import ok, fail
from core.db_base import add_log, add_ai_log, get_chat_history, append_chat_history, clear_chat_history
from core.exceptions import module_exception_guard
from common.utils import get_client_ip

# 简单内存限流（防滥用）：每 IP 每分钟最多 30 次全局问答
_CHAT_RATE = {}
_CHAT_RATE_LIMIT = 30
_CHAT_RATE_WINDOW = 60


def _chat_rate_ok(key):
    now = time.time()
    # 顺带清理过期条目 / 空 key，防止字典无限增长
    for k in [k for k, v in _CHAT_RATE.items() if not v or now - v[-1] >= _CHAT_RATE_WINDOW]:
        _CHAT_RATE.pop(k, None)
    hits = _CHAT_RATE.get(key, [])
    hits = [t for t in hits if now - t < _CHAT_RATE_WINDOW]
    if len(hits) >= _CHAT_RATE_LIMIT:
        _CHAT_RATE[key] = hits
        return False
    hits.append(now)
    _CHAT_RATE[key] = hits
    return True

# 对话历史：已登录用户存服务端 chat_histories 表（按用户隔离，重启不丢，
# cookie 不再携带历史明文与签名）；未登录时退回 session 兜底
_HISTORY_KEY = "homepage_chat_history"
_HISTORY_SCOPE = "homepage"


def _history_uid():
    user = g.get("current_user")
    return (user or {}).get("id") if isinstance(user, dict) else None


def _get_history(max_rounds: int = 20):
    uid = _history_uid()
    if uid is None:
        h = session.get(_HISTORY_KEY, [])
        if not isinstance(h, list):
            h = []
        return h[-max_rounds:]
    return get_chat_history(uid, max_rounds=max_rounds, scope=_HISTORY_SCOPE)


def _append_history(role: str, content: str):
    uid = _history_uid()
    if uid is None:
        h = session.get(_HISTORY_KEY, [])
        if not isinstance(h, list):
            h = []
        h.append({"role": role, "content": content})
        session[_HISTORY_KEY] = h[-40:]
        return
    append_chat_history(uid, role, content, scope=_HISTORY_SCOPE)


@bp.route("/", methods=["GET"])
@module_exception_guard("homepage")
def index():
    """首页：AI问答界面。"""
    ai = get_ai_features()
    history = _get_history()
    return render_template(
        "homepage/index.html",
        current_module_id="homepage",
        ai=ai,
        history=history,
    )


@bp.route("/api/chat", methods=["POST"])
@login_required
@module_exception_guard("homepage")
def api_chat():
    """全局AI问答接口（AJAX）。"""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return fail("问题不能为空")
    if len(question) > 2000:
        return fail("问题过长（最多2000字符）")
    if not _chat_rate_ok(get_client_ip()):
        return fail("请求过于频繁，请稍后再试", code=429)

    if not is_ai_enabled():
        return fail("AI功能已关闭，请在系统配置中启用", code=503)

    # 思维模式下发：divergent（发散）/ conservative（保守，仅依据资料）
    mode = payload.get("mode") or None
    # 速度档位（每问可选）：与流式接口保持一致，未传则回落全局配置
    _apply_speed_profile(payload.get("profile") or None)

    # 保留历史上下文
    history = _get_history()
    ok_, result = global_ai_chat(question, history=history, mode=mode, user=g.get("current_user"))
    user = g.get("current_user")
    uid = user.get("id") if user else None

    try:
        add_ai_log(
            uid, "homepage_chat",
            detail={"question": question[:300], "ok": ok_, "ip": get_client_ip()},
            ip=get_client_ip(),
            mode=result.get("mode") if isinstance(result, dict) else None,
        )
    except Exception:
        pass

    if not ok_:
        return fail(result.get("answer") if isinstance(result, dict) else "AI暂时不可用", code=500)

    answer = result.get("answer", "") if isinstance(result, dict) else str(result)
    # 保守模式下回传引用来源（书名/片段/相似度），供前端展示，提升答案可追溯性
    sources = result.get("sources", []) if isinstance(result, dict) else []
    # 写入当前会话历史
    _append_history("user", question)
    _append_history("assistant", answer)
    return ok({"answer": answer, "sources": sources})


def _apply_speed_profile(profile):
    """应用「每问速度档位」（请求级覆盖 AI 各可选步骤开关）。

    惰性 import：跨模块引用必须放在函数内，否则触发模块加载器 reload 陷阱（路由丢失）。
    """
    if not profile:
        return None
    try:
        from modules.ai_center.internal import config as _ai_cfg
        if profile in _ai_cfg.get_speed_profile_names():
            _ai_cfg.set_speed_profile(profile)
            return profile
    except Exception:
        pass
    return None


@bp.route("/api/chat/stream", methods=["POST"])
@login_required
@module_exception_guard("homepage")
def api_chat_stream():
    """全局 AI 问答（流式 SSE 接口）。

    与原 /api/chat（整段返回）并存：旧接口保持不动，供前端在流式失败时回退。
    本接口返回 text/event-stream，逐条下发 SSE 事件（sources / delta / done / answer / error）。
    历史写入与 AI 日志在收到 done/answer 事件后于服务端完成（与 api_chat 一致）。
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return fail("问题不能为空")
    if len(question) > 2000:
        return fail("问题过长（最多2000字符）")
    if not _chat_rate_ok(get_client_ip()):
        return fail("请求过于频繁，请稍后再试", code=429)
    if not is_ai_enabled():
        return fail("AI功能已关闭，请在系统配置中启用", code=503)

    # 思维模式下发：mixed（混合标注）/ strict（严格依据资料）
    mode = payload.get("mode") or None
    # 速度档位（每问可选）：high / balanced，未传则回落全局配置
    # （fast 档已删除，未知档位在 set_speed_profile 内安全回落全局）
    profile = payload.get("profile") or None

    history = _get_history()
    user = g.get("current_user")
    uid = user.get("id") if user else None

    def _commit(final_answer, final_mode):
        """写历史 + 日志（与 api_chat 一致），确保对话上下文持续可用。"""
        try:
            if final_answer:
                _append_history("user", question)
                _append_history("assistant", final_answer)
            add_ai_log(
                uid, "homepage_chat",
                detail={"question": question[:300], "stream": True, "ip": get_client_ip()},
                ip=get_client_ip(),
                mode=final_mode,
            )
        except Exception:
            pass

    def event_stream():
        # 档位覆盖放在生成器内设置：流式期间上下文一定有效（响应已开始后才迭代）
        _apply_speed_profile(profile)
        full_answer = None
        final_sources = []
        final_mode = mode or "mixed"
        committed = False
        try:
            for ev in global_ai_chat_stream(question, history=history, mode=mode, user=user):
                if not isinstance(ev, dict):
                    continue
                t = ev.get("type")
                if t == "done" or t == "answer":
                    full_answer = ev.get("answer") or ev.get("text")
                    final_sources = ev.get("sources", []) or []
                    final_mode = ev.get("mode", final_mode)
                    # 关键：答案一定稿就立刻落历史/日志。引用校验已改为异步
                    # （done 之后还会再发 grounding 事件），若仍等到流结束才写，
                    # 用户紧接着追问时上下文还没进去。
                    if not committed:
                        committed = True
                        _commit(full_answer, final_mode)
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "message": f"流式输出异常: {e}"}, ensure_ascii=False) + "\n\n"
        finally:
            # 兜底：异常路径下没写成时补一次
            if not committed:
                _commit(full_answer, final_mode)

    resp = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    return resp


@bp.route("/api/chat/clear", methods=["POST"])
@module_exception_guard("homepage")
def api_clear_history():
    """清空当前用户的聊天历史（服务端表）。"""
    uid = _history_uid()
    if uid is None:
        session[_HISTORY_KEY] = []
    else:
        clear_chat_history(uid, scope=_HISTORY_SCOPE)
    return ok()
