"""
AI 公共接口层（Core 暴露给各业务模块调用）。

设计目的：
  book_lib、homepage 等业务模块**只依赖这里的函数**，不直接 import ai_center 内部实现，
  做到解耦 + 故障隔离：AI 模块挂掉时，图书模块基础功能仍可用。

所有函数都有降级兜底：当 ai_center 不可用或 modules.yaml 中 ai_mode=off 时，
返回 False/None/空列表，调用方据此隐藏 AI UI。
"""

from typing import Any, Dict, List, Optional, Tuple
from .config_loader import get_ai_mode_from_config, is_module_enabled

_AI_CENTER_AVAILABLE = False
_AI_CENTER_ERROR: Optional[str] = None

# ==============================================================
# 尝试引入 ai_center 内部实现（懒加载，失败则降级）
# ==============================================================
def _try_load_ai_center():
    global _AI_CENTER_AVAILABLE, _AI_CENTER_ERROR
    if _AI_CENTER_AVAILABLE:
        return True
    if not is_module_enabled("ai_center"):
        _AI_CENTER_ERROR = "模块未启用"
        return False
    try:
        from modules.ai_center.internal import (
            qa as _qa,
            vector_store as _vector_store,
            summarizer as _summarizer,
            chunker as _chunker,
            embedder as _embedder,
            tasks as _tasks,
            config as _ai_config,
        )
        globals().update({
            "_qa": _qa,
            "_vector_store": _vector_store,
            "_summarizer": _summarizer,
            "_chunker": _chunker,
            "_embedder": _embedder,
            "_tasks": _tasks,
            "_ai_config": _ai_config,
        })
        _AI_CENTER_AVAILABLE = True
        return True
    except Exception as e:
        _AI_CENTER_ERROR = str(e)
        return False


# ==============================================================
# 基础查询：模式 & 特性
# ==============================================================
def get_ai_mode() -> str:
    """返回 AI 模式：off / light / full。关闭时返回 off。"""
    if not is_module_enabled("ai_center"):
        return "off"
    return get_ai_mode_from_config()


def is_ai_enabled() -> bool:
    """AI 功能是否启用（非 off 且模块可用）。"""
    return get_ai_mode() != "off"


def get_ai_features() -> Dict[str, Any]:
    """返回前端可用的 AI 特性开关，用于显隐 UI。"""
    mode = get_ai_mode()
    base = {"mode": mode}
    if mode == "off":
        return base
    base.update({
        # light 模式(bge-small + LLM)后端同样支持向量检索与问答，
        "summary": mode in ("light", "full"),
        "vector_search": mode in ("light", "full"),
        "qa": mode in ("light", "full"),
        "rebuild": mode in ("light", "full"),
    })
    return base


def get_available_backends() -> List[str]:
    if not _try_load_ai_center():
        return []
    try:
        return list(_ai_config.get_available_backends())
    except Exception:
        return []


# ==============================================================
# 图书 AI 能力
# ==============================================================
def generate_book_summary(book_id: str, book_text: str, force: bool = False) -> Tuple[bool, str]:
    """
    为图书生成摘要。
    返回: (成功?, 摘要文本/错误信息)
    """
    if not is_ai_enabled():
        return False, "AI已关闭"
    if not _try_load_ai_center():
        return False, f"AI模块不可用: {_AI_CENTER_ERROR}"
    try:
        summary = _summarizer.summarize_text(book_text, book_id=book_id, force=force)
        return True, summary or ""
    except Exception as e:
        return False, str(e)


def ask_book_question(book_id: str, question: str, top_k: int = 5) -> Tuple[bool, Dict[str, Any]]:
    """
    针对某本图书提问。
    返回: (成功?, {answer, sources, ...})
    """
    # light 模式同样支持单书问答，仅 off 时拦截（与 generate_book_summary 一致）。
    if not is_ai_enabled():
        return False, "AI已关闭"
    if not _try_load_ai_center():
        return False, {"answer": f"AI模块不可用: {_AI_CENTER_ERROR}", "sources": []}
    try:
        result = _qa.ask_question_about_book(book_id, question, top_k=top_k)
        return True, result or {"answer": "", "sources": []}
    except Exception as e:
        return False, {"answer": f"AI异常: {e}", "sources": []}


def global_ai_chat(question: str, history: Optional[List[Dict]] = None, mode: Optional[str] = None, user: Optional[Dict] = None) -> Tuple[bool, Dict[str, Any]]:
    """
    全局 AI 问答（不限定图书，首页模块用）。
    mode: 思维模式下发（divergent 发散 / conservative 保守），None 时由 qa.global_chat 按配置决定。
    user: 当前登录用户（dict），透传给 qa.global_chat 做图书级权限过滤。
    """
    ai_mode = get_ai_mode()
    if ai_mode == "off":
        return False, {"answer": "AI 已关闭，请在配置中启用。", "mode": ai_mode}
    if not _try_load_ai_center():
        return False, {"answer": f"AI模块不可用: {_AI_CENTER_ERROR}", "mode": ai_mode}
    try:
        result = _qa.global_chat(question, history=history or [], mode=mode, user=user)
        result = result or {"answer": "", "mode": ai_mode}
        result.setdefault("mode", ai_mode)
        return True, result
    except Exception as e:
        return False, {"answer": f"AI异常: {e}", "mode": ai_mode}


def global_ai_chat_stream(question: str, history: Optional[List[Dict]] = None, mode: Optional[str] = None, user: Optional[Dict] = None):
    """全局 AI 问答（流式版），yield SSE 事件 dict。

    与 global_ai_chat 的区别：不返回 dict，而是逐条 yield 事件
    （sources / delta / done / answer / error），由路由层封装成 text/event-stream。
    前置的开关/可用判定在此完成，业务异常由调用方（路由）兜底为 error 事件。
    """
    ai_mode = get_ai_mode()
    if ai_mode == "off":
        yield {"type": "error", "message": "AI 已关闭，请在配置中启用。"}
        return
    if not _try_load_ai_center():
        yield {"type": "error", "message": f"AI模块不可用: {_AI_CENTER_ERROR}"}
        return
    try:
        for ev in _qa.global_chat_stream(question, history=history or [], mode=mode, user=user):
            yield ev
    except Exception as e:
        yield {"type": "error", "message": f"AI异常: {e}"}


# ==============================================================
# 向量 & 索引
# ==============================================================
def invalidate_vectors_for_book(book_id: str) -> bool:
    """删除/修改图书时，标记向量失效。"""
    try:
        from .db_base import invalidate_vector_mappings_by_book
        invalidate_vector_mappings_by_book(book_id)
        return True
    except Exception:
        return False


def trigger_rebuild_for_book(book_id: str, with_summary: bool = True) -> Tuple[bool, str]:
    """触发某本图书的重建索引任务。

    返回 (是否成功, task_id 或错误信息)。
    submit_rebuild_task 返回 None 表示**失败**（向量化/写入未成功）；
    返回 "skip:xxx" 表示因无文件/无文本内容/涉密锁定而跳过，不算失败。
    批量重建时传 with_summary=False 可跳过 LLM 摘要，大幅缩短耗时。
    """
    if not is_ai_enabled():
        return False, "AI已关闭"
    if not _try_load_ai_center():
        return False, f"AI模块不可用: {_AI_CENTER_ERROR}"
    try:
        task_id = _tasks.submit_rebuild_task(book_id, with_summary=with_summary)
        if task_id is None:
            return False, "索引失败（向量化或写入索引未成功）"
        return True, task_id
    except Exception as e:
        return False, str(e)


def get_vector_stats() -> Dict[str, Any]:
    if not _try_load_ai_center():
        return {}
    try:
        return _vector_store.get_stats()
    except Exception:
        return {}


# ==============================================================
# 调度器
# ==============================================================
def ensure_ai_scheduler():
    """启动 AI 后台任务调度器（light/full 模式）。"""
    if not is_ai_enabled():
        return None
    if not _try_load_ai_center():
        return None
    try:
        return _tasks.ensure_scheduler_for_mode()
    except Exception:
        return None
