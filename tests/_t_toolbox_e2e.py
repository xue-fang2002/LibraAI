"""E2E：登录后遍历「工具箱 Hub」首页全部卡片链接，确认没有 404 / 「页面不存在」。
同时校验 /toolbox/ 与 /tools/document 正常渲染、无 console/pageerror。
"""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5005"
# 管理员密码：优先读环境变量（开源版初始密码是随机生成的），未设置时回落旧默认值
ADMIN_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD") or "admin123"
errors = []
notes = []
bad_res = []

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context()
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("response", lambda r: bad_res.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    # 登录
    pg.goto(f"{BASE}/login", wait_until="domcontentloaded")
    pg.fill("#account", "admin")
    pg.fill("#pwd", ADMIN_PASSWORD)
    pg.click('button[type="submit"]')
    pg.wait_for_load_state("load")
    pg.wait_for_timeout(1200)
    notes.append(f"login -> {pg.url}")

    # Hub 首页
    resp = pg.goto(f"{BASE}/tools/", wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    notes.append(f"/tools/ -> {resp.status if resp else '?'}")
    hrefs = pg.eval_on_selector_all(
        "a.hub-card",
        "els => els.map(e => e.href).filter(h => h && h.startsWith('http'))",
    )
    # 去重、只看本机、排除登出链接（会把会话踢掉，导致后续页面误判 401）
    hrefs = sorted({h for h in hrefs if h.startswith(BASE) and "/logout" not in h})
    print(f"[Hub] 卡片/链接数 = {len(hrefs)}")

    checked = 0
    for h in hrefs:
        try:
            r = pg.goto(h, wait_until="domcontentloaded", timeout=20000)
            body = pg.content()
            notfound = "页面不存在" in body
            status = r.status if r else "?"
            flag = "ERR" if (status >= 400 or notfound) else "OK "
            if flag == "ERR":
                errors.append(f"{h} -> HTTP {status} notfound={notfound}")
            print(f"  [{flag}] {status} {h}")
            checked += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{h} -> 异常 {e}")
            print(f"  [ERR] {h} -> {e}")

    # 关键页面单测
    for u in ["/toolbox/", "/tools/document"]:
        r = pg.goto(f"{BASE}{u}", wait_until="domcontentloaded")
        pg.wait_for_timeout(800)
        body = pg.content()
        nf = "页面不存在" in body
        print(f"  [{'ERR' if ((r and r.status >= 400) or nf) else 'OK '}] {r.status if r else '?'} {u} (notfound={nf})")
        if (r and r.status >= 400) or nf:
            errors.append(f"{u} -> {r.status if r else '?'} notfound={nf}")

    b.close()

print("\n=== 汇总 ===")
for n in notes:
    print("  ", n)
if bad_res:
    print("全流程 >=400 资源：")
    for x in sorted(set(bad_res)):
        print("   -", x)
if errors:
    print(f"发现 {len(errors)} 个问题：")
    for e in errors:
        print("   -", e)
    sys.exit(1)
print(f"全部 OK（检查 {checked} 个链接 + 2 个关键页），无 404、无 console error")
