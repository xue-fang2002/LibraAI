"""工具箱连接器（能力自动发现）。

设计目标：**加工具 = 在 tools/<分类>/ 下丢一个 .py（定义 TOOL_ID/LABEL/NEEDS_FILES/
PARAMS），本连接器无需任何改动即可把该工具暴露给 AI 任务中心**。

能力从 `modules.toolbox.tools.registry` 动态生成，每个工具模块可携带的元数据：

    必填（P0 已统一）：
        TOOL_ID      str   全局工具 id（如 "image_compress"）
        LABEL        str   中文名
        NEEDS_FILES  bool  是否需要用户上传文件
        PARAMS       list  参数定义（[{name,label,type,required,options,default,hint}, ...]）

    可选（用于微调 AI 行为，不写则走安全默认值）：
        AI_EXECUTION  str   inplace / jump / async
                       默认：无文件工具 → inplace（后端直接跑，结果回对话）；
                             有文件工具 → jump（跳到工具箱页执行，避免长任务阻塞请求）；
                             但 _INPLACE_FILE_TOOLS 白名单里的「快工具」允许原地执行。
        AI_DESC       str   给 LLM 的一句话说明（不写 → "{LABEL}（{分类}工具）"）
        AI_KEYWORDS   list 粗分类/精选命中的关键词（不写 → 由 LABEL + TOOL_ID 英文碎片自动派生）
        AI_HANDLER    str   原地执行时的后端处理器 key（不写 → TOOL_ID）

执行方式语义见 connectors/base.py。inplace/async 工具会带上 handler，由
executor._run_toolbox 经 handlers.dispatch 调起，复用工具箱全部代码、零额外封装。
"""
from typing import Any, Dict, List

from .base import Connector, Capability, ParamSpec, EXEC_INPLACE, EXEC_JUMP, EXEC_ASYNC

# ⚠️ 必须惰性导入 registry，切勿提到模块顶层。
# plugin_scan 按字母序遍历模块，ai_agent 排在 toolbox 之前；若本模块在导入期就
# `import modules.toolbox.*`，会把 modules.toolbox 提前写进 sys.modules，导致
# 轮询到 toolbox 时命中 plugin_scan 的 importlib.reload 分支 —— 而 reload 只会
# 重建 __init__ 里的空 Blueprint、不会重新执行已缓存的 routes 子模块，最终
# toolbox 蓝图一条路由都不剩（/toolbox 404）。惰性到调用期导入即可规避。


# 分类中文名：用于自动生成的 desc / 关键词派生
_CATEGORY_CN = {
    "image": "图片",
    "document": "文档",
    "text": "文本",
    "recognition": "识别",
    "system": "系统",
    "automation": "自动化",
}

# 有文件、但「处理快、无模型下载」、允许直接原地执行的工具白名单。
# 不在此列的「有文件」工具默认 jump（跳工具箱页执行），避免长任务阻塞请求；
# 想让新工具原地执行，只需在其模块里写 AI_EXECUTION = "inplace" 即可。
_INPLACE_FILE_TOOLS = {
    "image_compress", "image_convert", "image_thumbnail", "image_watermark",
    "image_edit", "image_filter", "image_merge", "image_exif",
    "document_pdf_merge", "document_pdf_split", "document_pdf_extract_text",
    "document_pdf_extract_image", "document_pdf_to_image", "document_pdf_rotate",
    "document_pdf_watermark", "system_rename",
    "recognition_qrcode_scan", "recognition_ocr_image", "recognition_ocr_pdf",
    "recognition_ocr_screenshot",
}


def _derive_keywords(label: str, tool_id: str, category: str) -> List[str]:
    """从已有元数据派生关键词：LABEL + 分类中文 + TOOL_ID 英文碎片。

    不追求穷尽，只覆盖「用户最可能说的关键词」，让两级路由能命中即可；
    工具若写 AI_KEYWORDS 会完全覆盖这里的派生结果。
    """
    out: List[str] = []
    if label:
        out.append(label)
    cn = _CATEGORY_CN.get(category)
    if cn:
        out.append(cn)
    # TOOL_ID 形如 image_compress / document_pdf_merge → 拆出英文碎片
    for piece in str(tool_id).split("_"):
        piece = piece.strip().lower()
        if piece and piece not in out:
            out.append(piece)
    return out[:8]


def _convert_param(p: Dict[str, Any]) -> Dict[str, Any]:
    """把工具 PARAMS 的 dict 转成 ParamSpec 构造参数。

    工具侧 type 可能含 textarea（前端多行输入），统一降级成 text。
    """
    ptype = (p.get("type") or "text").lower()
    if ptype == "textarea":
        ptype = "text"
    return {
        "name": p.get("name"),
        "label": p.get("label", p.get("name", "")),
        "type": ptype,
        "required": bool(p.get("required", False)),
        "options": list(p.get("options", []) or []),
        "default": p.get("default"),
        "hint": p.get("hint", ""),
    }


def _cap_from_tool(mod, tool_id: str) -> Capability:
    category = getattr(mod, "CATEGORY", "") or ""
    needs_files = bool(getattr(mod, "NEEDS_FILES", False))

    # 执行方式：优先用工具显式声明；否则按
    # 「无文件→原地，有文件→默认跳转，但快工具白名单里允许原地」的规则。
    execution = getattr(mod, "AI_EXECUTION", None)
    if not execution:
        if not needs_files:
            execution = EXEC_INPLACE
        elif tool_id in _INPLACE_FILE_TOOLS:
            execution = EXEC_INPLACE
        else:
            execution = EXEC_JUMP

    # handler：原地/异步执行需要后端处理器 key，默认 TOOL_ID
    handler = getattr(mod, "AI_HANDLER", None)
    if handler is None:
        handler = tool_id if execution in (EXEC_INPLACE, EXEC_ASYNC) else ""

    label = getattr(mod, "LABEL", tool_id) or tool_id
    cn = _CATEGORY_CN.get(category, "")
    desc = getattr(mod, "AI_DESC", None) or (f"{label}（{cn}工具）" if cn else label)

    keywords = getattr(mod, "AI_KEYWORDS", None)
    if not keywords:
        keywords = _derive_keywords(label, tool_id, category)

    params = [ParamSpec(**_convert_param(p)) for p in (getattr(mod, "PARAMS", []) or [])]

    return Capability(
        cid=f"toolbox.{tool_id}",
        label=label,
        desc=desc,
        module="toolbox",
        execution=execution,
        params=params,
        needs_files=needs_files,
        target=f"/toolbox?tool={tool_id}",
        handler=handler,
        keywords=list(keywords),
    )


class ToolboxConnector(Connector):
    module_id = "toolbox"
    display_name = "工具箱"

    def capabilities(self) -> List[Capability]:
        # 惰性导入：调用期（应用已启动、plugin_scan 扫描完毕）才触达工具箱包，
        # 避免导入期提前缓存 modules.toolbox（见文件顶部说明）。
        from modules.toolbox.tools import registry as _tools_registry

        caps: List[Capability] = []
        for tool_id, mod in _tools_registry.all_tools().items():
            try:
                caps.append(_cap_from_tool(mod, str(tool_id)))
            except Exception as e:
                # 单个工具元数据异常不影响其它工具；打到 stderr 便于排查
                print(f"⚠️ [ai_agent] 工具 {tool_id} 生成能力失败: {e}")
        return caps
