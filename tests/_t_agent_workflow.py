"""回归：AI 任务中心 × 工作自动化流程（workflow 连接器）。

覆盖：
  1. WorkflowConnector 自动发现 flows/（开箱示例 workflow.example 必现）
  2. 关键词路由命中（不依赖 LLM）
  3. registry 汇总 / modules_summary 包含 workflow
  4. launcher 门禁：流程不存在 404、单实例锁 409
  5. 权限过滤 _cap_allowed：无 workflow 权限的用户不可见、不可跑

⚠️ 开源友好（重要）：
  本测试**只依赖随仓库发布的通用示例流程** flows/example_flow.py（无任何私有信息）。
  私有流程位于 flows/ 但被 .gitignore 忽略、不入库，
  因此相关断言改为「本地存在才校验，否则跳过」，且不硬编码任何私有流程名——
  克隆仓库后回归不会因缺私有流程而失败，公开仓库里也不会出现私有流程名称。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

Results = []
Skipped = []


def chk(name, ok, detail=""):
    Results.append((name, bool(ok), detail))
    print(("✅" if ok else "❌") + f" {name}" + (f"  [{detail}]" if detail and not ok else ""))


def skip(name, why=""):
    """记录跳过的断言（非失败）：依赖本地私有流程的校验在开源环境下不应判失败。"""
    Skipped.append((name, why))
    print(f"⏭  {name}" + (f"  [{why}]" if why else ""))


def main():
    app = create_app()[0]

    # ---------- 1. 连接器自动发现（只依赖开箱示例流程） ----------
    from modules.ai_agent.connectors.workflow import WorkflowConnector
    caps = WorkflowConnector().capabilities()
    chk("workflow 连接器发现至少 1 个流程（开箱示例）", len(caps) >= 1, str(len(caps)))
    ex = next((c for c in caps if c.cid == "workflow.example_flow"), None)
    chk("包含开箱示例 workflow.example_flow", ex is not None)
    if ex:
        chk("执行方式为 async", ex.execution == "async", ex.execution)
        chk("示例流程无需上传文件（FLOW_ACCEPT 为空）", ex.needs_files is False)
        chk("handler 指向流程 id", ex.handler == "example_flow", ex.handler)
        pnames = [p.name for p in ex.params]
        chk("参数含 user/pwd/wait_sec",
            all(n in pnames for n in ("user", "pwd", "wait_sec")), str(pnames))

    # ---------- 1b. 本地私有流程：存在才校验，不存在则跳过 ----------
    # 不硬编码任何私有流程 id/名称（那些不入库），只用「非开箱示例」的通用契约来校验，
    # 保证公开仓库里不出现任何私有流程名。
    others = [c for c in caps if c.handler != "example_flow"]
    if others:
        bad = [c.cid for c in others
               if c.handler != str(c.cid).split("workflow.", 1)[-1]]
        chk("（本地私有流程）handler 与 flow id 一致", not bad, str(bad))
        chk("（本地私有流程）执行方式均为 async",
            all(c.execution == "async" for c in others))
    else:
        skip("（本地私有流程）额外契约断言", "未安装私有流程（开源仓库预期如此）")

    # ---------- 2. registry 汇总 ----------
    from modules.ai_agent import registry
    all_caps = registry.all_capabilities()
    chk("all_capabilities 含 workflow 模块", any(c.module == "workflow" for c in all_caps))
    chk("get_capability('workflow.example_flow') 可取",
        registry.get_capability("workflow.example_flow") is not None)
    summary = {s["module"] for s in registry.modules_summary()}
    chk("modules_summary 含 workflow", "workflow" in summary)

    # ---------- 3. 关键词路由（不依赖 LLM） ----------
    from modules.ai_agent import planner
    module, source = planner.route_module("帮我跑一下示例流程")
    chk("「帮我跑一下示例流程」路由到 workflow", module == "workflow",
        f"module={module} source={source}")
    chk("路由来源为 keyword（未走 LLM）", source == "keyword", source)

    # ---------- 4. launcher 门禁 ----------
    from modules.toolbox.automation.launcher import launch_flow
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ok, code, msg = launch_flow("no_such_flow", td, {})
        chk("不存在的流程 → (False, 404)", (not ok) and code == 404, f"{code} {msg}")

        lock = os.path.join(td, "run.lock")
        open(lock, "w").close()
        # 用开箱示例流程触发锁检查（锁检查先于 Popen，且不依赖任何私有流程）
        ok, code, msg = launch_flow("example_flow", td, {})
        chk("单实例锁 → (False, 409)", (not ok) and code == 409, f"{code} {msg}")

    # ---------- 5. 权限过滤 ----------
    from modules.ai_agent.routes import _cap_allowed
    tb_cap = next(c for c in all_caps if c.module == "toolbox")
    with app.test_request_context():
        from flask import g as _g
        _g.current_user = {"id": 1, "role": "admin", "account": "admin"}
        chk("admin 对 workflow 能力放行", _cap_allowed(ex) is True)
        chk("admin 对普通能力放行", _cap_allowed(tb_cap) is True)
        _g.current_user = {"id": 2, "role": "user", "account": "someone"}
        chk("无权限用户对 workflow 能力拦截", _cap_allowed(ex) is False)
        chk("无权限用户对普通能力放行", _cap_allowed(tb_cap) is True)

    # ---------- 汇总 ----------
    failed = [r for r in Results if not r[1]]
    extra = f"，跳过 {len(Skipped)} 项" if Skipped else ""
    print(f"\n===== {len(Results) - len(failed)}/{len(Results)} 通过{extra} =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
