# -*- coding: utf-8 -*-
"""经验贴两处修复的回归验证（无第三方依赖，标准库 urllib）。
A. 正文首行缩进：detail 模板改用 {{- post.content -}} 后，渲染 HTML 中正文应紧贴
   <div> 起始，不再被 Jinja 模板自身的缩进空格污染；用户自有缩进应保留。
B. 重复发布：同一用户 + 完全相同 (标题/分类/正文) 的并发提交，窗口期内只落库一条。
"""
import json
import os
import sqlite3
import threading
import urllib.request
import urllib.error
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:5005"
DB = "book_manager.db"
# 管理员密码：优先读环境变量（开源版初始密码是随机生成的），未设置时回落旧默认值
ADMIN_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD") or "admin123"

results = []
def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL"), "-", name)

def make_opener():
    cj = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(opener, method, url, data=None, headers=None):
    body = None
    h = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
    if headers:
        h.update(headers)
    if data is not None:
        body = data.encode("utf-8")
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with opener.open(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw

def login(opener):
    st, body = req(opener, "POST", f"{BASE}/api/login",
                   json.dumps({"account": "admin", "password": ADMIN_PASSWORD}))
    assert st == 200 and '"code":200' in body, f"login failed: {st} {body[:200]}"
    return True

def create(opener, payload):
    st, body = req(opener, "POST", f"{BASE}/experience/api/create", json.dumps(payload))
    try:
        j = json.loads(body)
    except Exception:
        j = {}
    return st, j

def get_raw(opener, url):
    # 手动跟随 308（urllib 默认不跟 308）
    st, body = req(opener, "GET", url)
    if st == 308 and "location" in {}:
        pass
    return st, body

def fetch_follow(opener, url):
    seen = 0
    while seen < 5:
        st, body = req(opener, "GET", url)
        if st in (301, 302, 303, 307, 308):
            loc = None
            # 从原始 response 取 Location
            # urllib 在 308 抛错，上面已捕获；这里仅处理自动跟随的情况
            break
        return st, body
    return st, body

# ---------- 准备 ----------
op = make_opener()
login(op)

# 预热：先发一条不同标题的帖，触发模型加载（避免并发测试被首call延迟干扰）
warm = {"title": "TEST_WARMUP_预热勿删", "category": "测试", "content": "warmup", "tags": "", "is_public": True}
ws, wj = create(op, warm)
print("warmup:", ws, wj.get("msg"))
check(ws == 200, "warmup 发布成功(模型已加载)")

# ---------- A. 缩进修复 ----------
content_a = "第一行没有缩进的文字。\n第二行也没有缩进。\n  第三行带了两个空格缩进，应被保留。\n第四行。"
pa = {"title": "TEST_INDENT_缩进验证", "category": "测试", "content": content_a, "tags": "", "is_public": True}
st_a, ja = create(op, pa)
check(st_a == 200, "A 发布缩进测试帖成功")
pid_a = ja.get("data", {}).get("id") if isinstance(ja.get("data"), dict) else None
if pid_a:
    st_d, html = req(op, "GET", f"{BASE}/experience/{pid_a}")
    # 取 detail-content 区块内的原始 HTML
    import re
    m = re.search(r'<div class="detail-content"[^>]*>(.*?)</div>', html, re.S)
    if m:
        inner = m.group(1)
        # 模板空格应被 {{- -}} 消除：正文不以空格/换行开头，且用户自有缩进保留
        no_lead_ws = not inner.startswith((" ", "\n", "\t"))
        user_indent_kept = "  第三行带了两个空格缩进" in inner
        check(no_lead_ws, "A 渲染 HTML 正文无模板前导空白(缩进已修复)")
        check(user_indent_kept, "A 用户自有两空格缩进被保留")
        print("   [debug] inner(首60):", repr(inner[:60]))
    else:
        check(False, "A 未找到 detail-content 区块")
else:
    check(False, "A 未拿到帖子 id")

# ---------- B. 并发重复发布 ----------
title_b = "TEST_CONCURRENT_并发去重"
content_b = "并发测试正文，内容完全一致。"
pb = {"title": title_b, "category": "测试", "content": content_b, "tags": "", "is_public": True}

ids = []
lock = threading.Lock()
def worker():
    s, j = create(op, pb)
    with lock:
        if isinstance(j.get("data"), dict):
            ids.append((s, j.get("data", {}).get("id")))

threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads: t.start()
for t in threads: t.join(timeout=120)

distinct_ids = set(i for _, i in ids if i)
print("   [debug] 5 个响应返回的 id:", ids)
check(len(distinct_ids) == 1, "B 5 个并发请求只产生 1 个新帖(id 唯一)")

# DB 层复核：该标题实际行数
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cnt = conn.execute("SELECT COUNT(*) c FROM experience_posts WHERE title=?", (title_b,)).fetchone()["c"]
conn.close()
check(cnt == 1, f"B DB 复核：标题='{title_b}' 实际行数={cnt}（应为1）")

# ---------- 清理测试数据 ----------
try:
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM experience_posts WHERE title LIKE 'TEST_%'")
    c.commit()
    c.close()
    print("已清理 TEST_ 前缀测试帖")
except Exception as e:
    print("清理失败(可手动删 TEST_ 帖子):", e)

# ---------- 汇总 ----------
passed = sum(1 for ok, _ in results if ok)
print(f"\n==== 结果: {passed}/{len(results)} PASS ====")
for ok, name in results:
    print(("  OK " if ok else "  XX ") + name)
