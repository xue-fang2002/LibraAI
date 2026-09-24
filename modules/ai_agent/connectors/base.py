"""连接器的数据契约。

一个「能力（Capability）」= AI 可调度的原子动作。
一个「连接器（Connector）」= 某个业务模块对外暴露的一组能力 + 执行方式。

执行方式 execution 只有四种，界面按它决定交互：
  inplace  后端可直接跑完，结果当场回到对话里（目前只有 AI 图表）
  async    后端起后台线程慢慢跑，前端轮询进度（慢任务 / 需占用 Office 等）
  jump     需要跳到目标模块页面执行（带参数自动填表），可回写结果
  link     只生成一个 URL 让用户点开（导航深链），不产生服务端动作
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


EXEC_INPLACE = "inplace"
EXEC_ASYNC = "async"
EXEC_JUMP = "jump"
EXEC_LINK = "link"

VALID_EXECUTIONS = (EXEC_INPLACE, EXEC_ASYNC, EXEC_JUMP, EXEC_LINK)


@dataclass
class ParamSpec:
    """能力的一个参数。type 决定前端怎么渲染、计划怎么校验。"""
    name: str
    label: str
    type: str = "text"          # text / number / bool / enum
    required: bool = False
    options: List[str] = field(default_factory=list)
    default: Any = None
    hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Capability:
    """一项可被 AI 调度的能力。"""
    cid: str                     # 全局唯一 id，如 "toolbox.image_compress"
    label: str                   # 中文名
    desc: str                    # 一句话说明（会进 LLM prompt，写清楚点）
    module: str                  # 归属模块 id
    execution: str = EXEC_JUMP
    params: List[ParamSpec] = field(default_factory=list)
    needs_files: bool = False    # 是否需要用户输入文件
    target: str = ""             # 深链 URL 模板 / 目标页面路由
    # 后端 dispatch 用的处理器 key（如 "recognition_qrcode_gen"）。
    # 只在 inplace 执行时内部使用，**不进 LLM prompt 也不返给前端**。
    handler: str = ""
    keywords: List[str] = field(default_factory=list)   # 粗分类用的关键词

    def __post_init__(self):
        if self.execution not in VALID_EXECUTIONS:
            raise ValueError(f"未知执行方式: {self.execution}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cid": self.cid,
            "label": self.label,
            "desc": self.desc,
            "module": self.module,
            "execution": self.execution,
            "needs_files": self.needs_files,
            "target": self.target,
            "params": [p.to_dict() for p in self.params],
        }

    def to_prompt_dict(self) -> Dict[str, Any]:
        """给 LLM 的精简摘要：不放前端渲染用的字段，省 token。"""
        out = {
            "id": self.cid,
            "name": self.label,
            "desc": self.desc,
            "params": [
                {"name": p.name, "label": p.label, "type": p.type,
                 "required": p.required, **({"enum": p.options} if p.options else {})}
                for p in self.params
            ],
        }
        if self.needs_files:
            out["needs_files"] = True
        return out


class Connector:
    """连接器基类。子类只需实现 capabilities()。"""

    module_id: str = ""
    display_name: str = ""

    def capabilities(self) -> List[Capability]:
        raise NotImplementedError

    def build_link(self, cap: Capability, params: Dict[str, Any]) -> str:
        """link / jump 型能力：把参数填进 target 模板，产出可跳转地址。

        模板占位符用 {param_name}；缺参数时该段留空而不是抛错。
        """
        target = cap.target or ""
        if not target or "{" not in target:
            return target
        out = target
        for key, val in (params or {}).items():
            out = out.replace("{" + key + "}", str(val))
        # 未填充的占位符整段去掉，避免 ?a=&b= 这种脏 URL
        import re
        out = re.sub(r"\{[a-zA-Z_]+\}", "", out)
        return out.rstrip("?&=")
