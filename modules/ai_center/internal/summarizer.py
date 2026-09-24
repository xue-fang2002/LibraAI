"""图书摘要生成（依赖本地 LLM，失败时回退为抽取式摘要）。"""
import re

from .config import get_ai_mode


def summarize_text(text, book_id=None, force: bool = False):
    """
    为给定文本生成中文摘要。
    优先用 LLM 生成；LLM 不可用或失败时，回退为基于关键句抽取的轻量摘要。

    Args:
        text: 待摘要文本（通常由调用方拼好）
        book_id: 图书ID（便于日志/后续扩展）
        force: 是否强制重算
    Returns:
        摘要字符串
    """
    if not text or len(text.strip()) < 5:
        return ""

    # 优先 LLM 生成
    try:
        from .qa import llm_generate
        mode = get_ai_mode()
        if mode in ("light", "full"):
            prompt = (
                f"请用300字以内概括以下文档的核心内容，突出主题、关键要点与适用场景，使用中文：\n\n"
                f"{text[:6000]}"
            )
            s = llm_generate(prompt, max_tokens=400, temperature=0.2)
            if s and len(s.strip()) >= 8:
                return s.strip()[:400]
    except Exception as e:
        print(f"⚠️ LLM摘要失败，降级为抽取式: {e}")

    # 回退：抽取前若干完整句子
    sents = re.split(r'(?<=[。！？!?])', text)
    sents = [s.strip() for s in sents if s.strip()]
    return "".join(sents[:6])[:400]
