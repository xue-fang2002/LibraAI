"""AI 依赖检测与懒加载。

所有可选第三方依赖（faiss、sentence-transformers、llama-cpp-python、PyMuPDF、python-docx 等）
均通过此模块统一探测。缺失时返回 None 并提供友好提示，不抛异常。

这样设计的目的是：**未安装任何 AI 依赖时，图书管理系统仍能正常启动并使用全部基础功能**。
"""
import importlib
import os

# 缓存检测结果
_dep_cache = {}


def clear_dep_cache():
    """清除依赖检测缓存（用于配置变更后强制重新检测）"""
    global _dep_cache
    _dep_cache = {}


def _try_import(module_name, force_refresh=False):
    """尝试导入模块，成功返回模块对象，失败返回None。
    force_refresh=True 时跳过缓存重新检测。"""
    if not force_refresh and module_name in _dep_cache:
        return _dep_cache[module_name]
    try:
        mod = importlib.import_module(module_name)
        _dep_cache[module_name] = mod
        return mod
    except Exception:
        _dep_cache[module_name] = None
        return None


# ---------- 核心AI依赖 ----------
def has_faiss_dep(force_refresh=False):
    return (_try_import("faiss", force_refresh=force_refresh) is not None
            or _try_import("faiss_cpu", force_refresh=force_refresh) is not None)


def get_faiss():
    if not has_faiss_dep():
        return None
    return _try_import("faiss") or _try_import("faiss_cpu")


def has_numpy_dep(force_refresh=False):
    return _try_import("numpy", force_refresh=force_refresh) is not None


def get_numpy():
    return _try_import("numpy")


def has_embedder_dep(force_refresh=False):
    """是否安装了sentence-transformers"""
    return _try_import("sentence_transformers", force_refresh=force_refresh) is not None


def get_sentence_transformers():
    return _try_import("sentence_transformers")


def has_llm_dep(force_refresh=False):
    """是否安装了llama-cpp-python（用于本地量化大模型推理）"""
    return _try_import("llama_cpp", force_refresh=force_refresh) is not None


def get_llama_cpp():
    return _try_import("llama_cpp")


def has_requests_dep(force_refresh=False):
    """是否安装了requests库（用于 Ollama/DashScope 后端）"""
    return _try_import("requests", force_refresh=force_refresh) is not None


def get_requests():
    return _try_import("requests")


def has_transformers_dep(force_refresh=False):
    """是否安装了transformers库（用于 Transformers 后端）"""
    return _try_import("transformers", force_refresh=force_refresh) is not None


def get_transformers():
    return _try_import("transformers")


# ---------- 文档解析依赖 ----------
def has_parser_pdf(force_refresh=False):
    return _try_import("fitz", force_refresh=force_refresh) is not None  # PyMuPDF


def get_fitz():
    return _try_import("fitz")


def has_parser_office(force_refresh=False):
    """是否安装了Office文档解析库"""
    return (_try_import("docx", force_refresh=force_refresh) is not None
            or _try_import("openpyxl", force_refresh=force_refresh) is not None
            or _try_import("pptx", force_refresh=force_refresh) is not None)


def get_docx():
    return _try_import("docx")  # python-docx


def get_openpyxl():
    return _try_import("openpyxl")


def get_pptx():
    return _try_import("pptx")  # python-pptx


def has_ocr_dep(force_refresh=False):
    """OCR依赖：优先rapidocr-onnxruntime（轻量），其次paddleocr，最后ddddocr"""
    return (_try_import("rapidocr_onnxruntime", force_refresh=force_refresh) is not None
            or _try_import("paddleocr", force_refresh=force_refresh) is not None
            or _try_import("ddddocr", force_refresh=force_refresh) is not None)


def get_ocr_engine():
    """获取OCR引擎实例（懒加载，单例）
    返回 (engine, engine_type) 元组，engine_type 为 'rapid'/'paddle'/'dddd'
    """
    global _ocr_engine
    if "_ocr_engine" in _dep_cache:
        return _dep_cache["_ocr_engine"]
    engine = None
    engine_type = None
    try:
        rapid = _try_import("rapidocr_onnxruntime")
        if rapid is not None:
            engine = rapid.RapidOCR()
            engine_type = "rapid"
        else:
            paddle = _try_import("paddleocr")
            if paddle is not None:
                engine = paddle.PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
                engine_type = "paddle"
            else:
                dddd = _try_import("ddddocr")
                if dddd is not None:
                    engine = dddd.DdddOcr(show_ad=False)
                    engine_type = "dddd"
    except Exception as e:
        print(f"⚠️ OCR引擎初始化失败: {e}")
        engine = None
    result = (engine, engine_type) if engine else (None, None)
    _dep_cache["_ocr_engine"] = result
    return result


def has_jieba_dep(force_refresh=False):
    return _try_import("jieba", force_refresh=force_refresh) is not None


def get_jieba():
    return _try_import("jieba")


def has_dxf_dep(force_refresh=False):
    return _try_import("ezdxf", force_refresh=force_refresh) is not None


def get_ezdxf():
    return _try_import("ezdxf")


# ---------- 异步任务调度依赖 ----------
def has_apscheduler_dep(force_refresh=False):
    return _try_import("apscheduler", force_refresh=force_refresh) is not None


def get_apscheduler():
    return _try_import("apscheduler")


# ---------- 依赖状态汇总（供前端展示） ----------
def get_dep_status(force_refresh=False):
    """返回所有依赖的安装状态字典。默认使用缓存，force_refresh=True时强制重新检测。

    注意：sentence_transformers/transformers 等大型库的强制重新导入非常慢（5秒+），
    因此默认使用缓存结果，只在用户手动点击"刷新状态"时才强制刷新。
    """
    return {
        # Web框架
        "flask": _try_import("flask", force_refresh=force_refresh) is not None,
        "jinja2": _try_import("jinja2", force_refresh=force_refresh) is not None,
        # 核心AI
        "faiss": has_faiss_dep(force_refresh=force_refresh),
        "numpy": has_numpy_dep(force_refresh=force_refresh),
        "sentence_transformers": has_embedder_dep(force_refresh=force_refresh),
        "llama_cpp": has_llm_dep(force_refresh=force_refresh),
        "transformers": has_transformers_dep(force_refresh=force_refresh),
        "requests": has_requests_dep(force_refresh=force_refresh),
        # 文档解析
        "PyMuPDF(fitz)": has_parser_pdf(force_refresh=force_refresh),
        "python-docx": _try_import("docx", force_refresh=force_refresh) is not None,
        "openpyxl": _try_import("openpyxl", force_refresh=force_refresh) is not None,
        "python-pptx": _try_import("pptx", force_refresh=force_refresh) is not None,
        "pillow": _try_import("PIL", force_refresh=force_refresh) is not None,
        # OCR（任一即可）
        "rapidocr_onnxruntime": _try_import("rapidocr_onnxruntime", force_refresh=force_refresh) is not None,
        "paddleocr": _try_import("paddleocr", force_refresh=force_refresh) is not None,
        "ddddocr": _try_import("ddddocr", force_refresh=force_refresh) is not None,
        # 辅助
        "jieba": has_jieba_dep(force_refresh=force_refresh),
        "ezdxf": has_dxf_dep(force_refresh=force_refresh),
        "apscheduler": has_apscheduler_dep(force_refresh=force_refresh),
        "httpx": _try_import("httpx", force_refresh=force_refresh) is not None,
    }


def check_model_file(path):
    """检查模型文件/目录是否存在"""
    if not path:
        return False
    return os.path.exists(path)


def get_missing_for_mode(mode):
    """返回指定模式下缺失的依赖列表（用于配置页提示）"""
    missing = []
    if mode == "off":
        return missing

    # light模式必需
    if not has_faiss_dep(force_refresh=True):
        missing.append("faiss-cpu（向量索引）")
    if not has_numpy_dep(force_refresh=True):
        missing.append("numpy")
    if not has_embedder_dep(force_refresh=True):
        missing.append("sentence-transformers（向量化模型）")
    if not has_parser_pdf(force_refresh=True):
        missing.append("PyMuPDF（PDF解析）")
    if not has_parser_office(force_refresh=True):
        missing.append("python-docx/openpyxl/python-pptx（Office解析）")

    if mode == "full":
        if not has_llm_dep(force_refresh=True):
            missing.append("llama-cpp-python（llama_cpp 后端）")
        if not has_requests_dep(force_refresh=True):
            missing.append("requests（ollama/dashscope 后端）")
        if not has_transformers_dep(force_refresh=True):
            missing.append("transformers + torch（transformers 后端，可选）")

    return missing
