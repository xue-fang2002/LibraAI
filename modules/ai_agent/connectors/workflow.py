"""工作自动化流程连接器（私有 RPA 能力自动发现）。

设计目标：**加流程 = 在 modules/toolbox/automation/flows/ 下丢一个 .py
（定义 FLOW_ID/FLOW_NAME/FLOW_PARAMS…），本连接器无需任何改动即可把该
流程暴露给 AI 任务中心**。能力从 automation.registry.get_flows() 动态生成，
与 workflow 模块页面（/workflow）共用同一份流程元数据，零登记。

执行方式统一 async：RPA 流程是慢任务（逐行填报、浏览器自动化），
走 ai_agent 的后台线程 + 进度轮询，进度直接复用流程自身外抛的
run.log / progress.json / done.flag 三件套。

⚠️ 必须惰性导入 registry（调用期才触达 toolbox 包），理由同 toolbox.py
顶部的 reload 陷阱说明。
"""
import re
from typing import Any, Dict, List

from .base import Connector, Capability, ParamSpec, EXEC_ASYNC

# 常见动词后缀：去掉后得到名词主干（"发票上传流程"→"发票上传"），口语命中率更高
_NAME_SUFFIXES = ("填报", "填写", "处理", "生成", "导出", "下载", "转换", "上传")


def _derive_keywords(name: str, flow_id: str, desc: str) -> List[str]:
    """从流程元数据派生路由关键词：名称 + 名词主干 + ASCII 词 + id 碎片。"""
    out: List[str] = []
    def _add(k):
        k = str(k).strip()
        if k and k not in out:
            out.append(k)

    if name:
        _add(name)
        for suf in _NAME_SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf):
                _add(name[: -len(suf)])
                break
    # ASCII 词（OA / Excel / PDF…）：关键词匹配统一 lower，大小写不敏感
    for w in re.findall(r"[A-Za-z0-9]+", f"{name} {desc}"):
        _add(w.lower())
    for piece in str(flow_id).split("_"):
        _add(piece.lower())
    _add("自动化")
    _add("流程")
    _add("工作流")
    return out[:12]


def _convert_param(p: Dict[str, Any]) -> ParamSpec:
    """把 FLOW_PARAMS 的 dict 转成 ParamSpec。

    流程参数的 type 可能是 text/password/number/textarea 等：
    password/textarea 统一降级为 text（密码建议用户在计划卡里手填）；
    默认值取 value，提示取 tip。
    """
    ptype = (p.get("type") or "text").lower()
    if ptype not in ("number", "bool", "enum"):
        ptype = "text"
    return ParamSpec(
        name=p.get("name"),
        label=p.get("label", p.get("name", "")),
        type=ptype,
        required=bool(p.get("required", False)),
        options=list(p.get("options", []) or []),
        default=p.get("value"),
        hint=p.get("tip", ""),
    )


class WorkflowConnector(Connector):
    module_id = "workflow"
    display_name = "工作自动化流程"

    def capabilities(self) -> List[Capability]:
        # 惰性导入：调用期（应用已启动、plugin_scan 扫描完毕）才触达 toolbox 包
        from modules.toolbox.automation.registry import get_flows

        caps: List[Capability] = []
        for f in get_flows():
            flow_id = str(f.get("id") or "")
            if not flow_id:
                continue
            name = f.get("name") or flow_id
            desc = f.get("desc") or f"{name}（工作自动化流程）"
            caps.append(Capability(
                cid=f"workflow.{flow_id}",
                label=name,
                desc=desc,
                module="workflow",
                execution=EXEC_ASYNC,
                params=[_convert_param(p) for p in (f.get("params") or [])],
                needs_files=bool(f.get("accept")),
                target="/workflow",
                handler=flow_id,   # 异步执行器按此 key 找到流程
                keywords=_derive_keywords(name, flow_id, desc),
            ))
        return caps
