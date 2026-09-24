"""
ai_center 内部实现包。

优雅降级原则：包可被正常 import，但内部各子模块在无模型 / 无依赖时，
函数调用会抛出清晰可读的 RuntimeError（由 core.ai_interface 的 try/except 捕获，
转换为对业务模块友好的失败返回值），绝不会让整个应用崩溃。
后续接入真实模型（sentence-transformers / llama.cpp / FAISS 等）时，
只需替换对应子模块的函数实现即可。
"""
from . import config, embedder, chunker, vector_store, summarizer, qa, tasks, parser  # noqa: F401
