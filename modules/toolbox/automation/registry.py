"""
流程注册表：自动扫描 flows/ 目录，发现所有符合契约的流程模块。

每个流程模块需定义模块级常量（见 base.py 契约）并暴露 run(work_dir, params)。
发现失败（导入错误 / 缺 FLOW_ID）的模块会被跳过，不影响其它流程。
"""

import os
import sys
import importlib
import traceback

# flows/ 目录：私有流程存放处（已被 .gitignore 忽略）
FLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flows")


def get_flows():
    """返回已发现的流程元数据列表，供前端渲染工具卡片。"""
    flows = []
    if not os.path.isdir(FLOW_DIR):
        return flows
    for fn in sorted(os.listdir(FLOW_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        mod_name = fn[:-3]
        try:
            mod = importlib.import_module(
                f"modules.toolbox.automation.flows.{mod_name}"
            )
        except Exception as e:
            # 流程模块自身依赖缺失（如未装 playwright）时跳过，不阻塞框架，
            # 但把错误打到 stderr，方便排查为什么分类里看不到工具。
            print(f"[toolbox.automation] 跳过流程 {mod_name}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            continue
        flow_id = getattr(mod, "FLOW_ID", None)
        if not flow_id:
            continue
        flows.append(
            {
                "id": flow_id,
                "name": getattr(mod, "FLOW_NAME", mod_name),
                "icon": getattr(mod, "FLOW_ICON", "🤖"),
                "desc": getattr(mod, "FLOW_DESC", ""),
                "params": getattr(mod, "FLOW_PARAMS", []),
                "accept": getattr(mod, "FLOW_ACCEPT", None),
                "stream": bool(getattr(mod, "FLOW_STREAM", False)),
            }
        )
    return flows


def get_flow(flow_id):
    """按 flow_id 加载流程模块；不存在或加载失败返回 None。"""
    if not flow_id or not os.path.isdir(FLOW_DIR):
        return None
    try:
        mod = importlib.import_module(
            f"modules.toolbox.automation.flows.{flow_id}"
        )
    except Exception:
        return None
    if getattr(mod, "FLOW_ID", None) != flow_id:
        return None
    return mod
