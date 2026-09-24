"""回归：模块路由注册 —— 带 Blueprint 的模块即使被提前导入，也不能丢路由。

背景（本次事故根因）：
plugin_scan.scan() 对「已在 sys.modules 里的模块」会 importlib.reload()。而
importlib.reload 只重新执行包的 __init__.py —— 会新建一个空 Blueprint；包内
`from . import routes` 因 routes 子模块已在 sys.modules 中不会重新执行，于是
新 Blueprint 上一条路由都没有，整个模块的入口变成 404。

触发链：ai_agent（字母序在 toolbox 之前）的连接器在导入期 import 了
modules.toolbox.tools.registry → modules.toolbox 被提前缓存 → 轮到 toolbox 时
命中 reload → toolbox 蓝图被清空 → /toolbox 404、/tools/document 500。

本脚本用 `--pre-import` 预导入全部模块包来复现该路径，断言每个模块的路由
仍然注册、toolbox.index 仍在。两种模式都必须全绿。
"""
import sys

PRE = "--pre-import" in sys.argv

# module_id -> route_prefix（当 entry 路由落在该前缀下即视为该模块注册成功）
MODULES = {
    "homepage": "/home",
    "book_lib": "/book",
    "experience_hub": "/experience",
    "chart": "/chart",
    "toolbox_hub": "/tools",
    "office_tools": "/office_tools",
    "workflow": "/workflow",
    "toolbox": "/toolbox",
    "nav_hub": "/nav_hub",
    "feedback": "/feedback",
    "ai_agent": "/agent",
    "config_center": "/config",
}

if PRE:
    for m in MODULES:
        try:
            __import__(f"modules.{m}")
        except Exception as e:  # noqa: BLE001
            print(f"  (pre-import modules.{m} 失败: {e})")

import app as a  # noqa: E402

_r = a.create_app()
app = _r[0] if isinstance(_r, tuple) else _r
rules = list(app.url_map.iter_rules())
endpoints = {x.endpoint for x in rules}
routed = {x.rule for x in rules}

ok = 0
fail = 0
mode = "PRE-IMPORT" if PRE else "NORMAL"
print(f"=== [{mode}] 模块路由注册检查 ===")

for mid, prefix in MODULES.items():
    has = any(r.startswith(prefix) for r in routed)
    if has:
        ok += 1
        print(f"  [OK] {mid:15s} {prefix}")
    else:
        fail += 1
        print(f"  [!!] {mid:15s} {prefix}  -> 0 条路由（蓝图被清空）")

key = "toolbox.index" in endpoints
print(f"  [{'OK' if key else '!!'}] toolbox.index {'存在' if key else '缺失'}")
ok += 1 if key else 0
fail += 0 if key else 1

# 附带：toolbox 的路由条数（本次事故中被清成 0）
tb_n = sum(1 for e in endpoints if e.startswith("toolbox."))
print(f"  [{'OK' if tb_n > 5 else '!!'}] toolbox.* 路由条数 = {tb_n}")
ok += 1 if tb_n > 5 else 0
fail += 0 if tb_n > 5 else 1

print(f"\n[{mode}] 通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
