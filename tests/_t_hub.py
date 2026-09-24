"""验证 P2：Hub 卡片配置化（endpoint 解析 + yaml 覆盖 + 回落）。"""
import sys
sys.path.insert(0, r"D:\xianmufile")

from flask import Flask

import core.config_loader as CL
from modules.toolbox_hub import zones as Z

app = Flask(__name__)
app.add_url_rule("/chart/", "chart.index", lambda: "")
app.add_url_rule("/tools/document", "toolbox_hub.document", lambda: "")
app.add_url_rule("/office_tools/", "office_tools.index", lambda: "")

checks = []


def chk(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- %s" % (extra,)) if extra and not cond else ""))


# 注意：url_for 在「仅 app_context」下会因缺 SERVER_NAME 而失败，
# 真实路由是在 request context 里执行的，所以这里用 test_request_context 模拟。
with app.test_request_context("/"):
    print("=== 1. 回落：yaml 未配 zones 时用 DEFAULT_ZONES ===")
    CL.get_config("modules.toolbox_hub.zones", None)
    zs = Z.get_zones()
    chk("返回 9 个分区", len(zs) == 9, len(zs))
    titles = [z["title"] for z in zs]
    for t in ["图表工具", "文档处理", "办公自动化", "图片处理",
              "文本工具", "识别工具", "系统工具", "计算换算", "压缩打包"]:
        chk("包含分区「%s」" % t, t in titles)

    print("\n=== 2. endpoint: 解析成真实 URL（不是硬编码路径）===")
    by_title = {z["title"]: z for z in zs}
    chk("图表工具 -> /chart/?hub=1", by_title["图表工具"]["url"] == "/chart/?hub=1",
        by_title["图表工具"]["url"])
    chk("文档处理 -> /tools/document",
        by_title["文档处理"]["url"] == "/tools/document",
        by_title["文档处理"]["url"])
    chk("办公自动化 -> /office_tools/?hub=1",
        by_title["办公自动化"]["url"] == "/office_tools/?hub=1",
        by_title["办公自动化"]["url"])
    chk("图片处理 原样保留 hash 路由",
        by_title["图片处理"]["url"] == "/toolbox?hub=1#/image",
        by_title["图片处理"]["url"])

    print("\n=== 3. 角标结构化（kind 决定样式）===")
    badges = by_title["文档处理"]["badges"]
    chk("文档处理 3 个角标", len(badges) == 3, badges)
    chk("角标含 todo 类型", any(b["kind"] == "todo" for b in badges), badges)

    print("\n=== 4. yaml 覆盖生效（模拟加第 9 个区）===")
    fake = [{"icon": "🎬", "title": "视频处理", "desc": "剪辑 / 转码",
             "badges": [{"text": "新", "kind": "ok"}],
             "url": "/toolbox?hub=1#/video"}]
    orig = CL.get_config
    CL.get_config = lambda path, default=None: (fake if path == "modules.toolbox_hub.zones"
                                                else orig(path, default))
    # zones 模块里用的是 from ... import get_config，需改模块内引用
    Z.get_config = CL.get_config
    zs2 = Z.get_zones()
    chk("yaml 配置优先（只剩 1 个区）", len(zs2) == 1 and zs2[0]["title"] == "视频处理", zs2)
    Z.get_config = orig
    CL.get_config = orig

    print("\n=== 5. 容错：坏数据不会让整页挂掉 ===")
    Z.get_config = lambda path, default=None: [{"no_title": 1}, "garbage"] if path.endswith("zones") else orig(path, default)
    chk("无 title / 非 dict 条目被跳过", Z.get_zones() == [], Z.get_zones())
    Z.get_config = orig

print()
fails = [n for n, c in checks if not c]
print("通过 %d / %d" % (len(checks) - len(fails), len(checks)))
if fails:
    print("失败项:", fails)
