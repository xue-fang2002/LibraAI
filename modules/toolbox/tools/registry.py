"""工具自动发现（与 automation/flows 同一套思路：目录即清单）。

约定：
    modules/toolbox/tools/<分类>/<工具>.py

每个工具模块需提供模块级常量与入口函数：

    TOOL_ID  : str   全局工具 id，必须与 tools-config.js 里的 key 完全一致
                     （如 "image_compress"、"document_pdf_merge"）
    run(temp_id, params) -> Response     统一执行入口，产物写 temp_id/out

可选的展示/编排元数据（后续给前端配置与 ai_agent 自动生成能力用，当前不影响执行）：
    CATEGORY / LABEL / ICON / PARAMS / NEEDS_FILES

发现失败（导入错误 / 缺 TOOL_ID）的模块会被跳过并打到 stderr，不影响其它工具 ——
这样某个工具的可选依赖缺失（如 rembg / Tesseract）不会拖垮整个工具箱。
"""
import os
import sys
import importlib

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG = "modules.toolbox.tools"

_CACHE = None


def _scan():
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    tools = {}
    if os.path.isdir(_TOOLS_DIR):
        for cat in sorted(os.listdir(_TOOLS_DIR)):
            cat_dir = os.path.join(_TOOLS_DIR, cat)
            # 跳过 _common.py / registry.py 等非分类项（下划线开头）
            if cat.startswith("_") or cat.startswith("."):
                continue
            if not os.path.isdir(cat_dir) or not os.path.isfile(os.path.join(cat_dir, "__init__.py")):
                continue
            for fn in sorted(os.listdir(cat_dir)):
                if not fn.endswith(".py") or fn.startswith("_"):
                    continue
                mod_name = fn[:-3]
                try:
                    mod = importlib.import_module(f"{_PKG}.{cat}.{mod_name}")
                except Exception as e:
                    print(f"[toolbox.tools] 跳过 {cat}/{mod_name}: {e}", file=sys.stderr)
                    continue
                tool_id = getattr(mod, "TOOL_ID", None)
                if not tool_id:
                    continue
                tools[str(tool_id)] = mod
    _CACHE = tools
    return tools


def get(tool_id):
    """按工具 id 取模块；不存在返回 None（调用方负责回落到旧 handler_map）。"""
    return _scan().get(str(tool_id or ""))


def all_tools():
    """返回 {tool_id: module}。"""
    return _scan()


def reload():
    """清缓存重新扫描（开发期新增工具后不必重启进程）。"""
    global _CACHE
    _CACHE = None
    return _scan()
