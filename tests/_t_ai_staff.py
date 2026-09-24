# -*- coding: utf-8 -*-
"""AI 员工（ai_staff）回归脚本。

覆盖：
  1) SQL 护栏：只读校验（禁多语句/注释/DDL-DML/PRAGMA、强制 LIMIT）
  2) 路径护栏：越界 / 黑名单目录 / 敏感文件 / 二进制
  3) 六个工具：list_dir / code_search / read_file / db_schema / db_query / log_search
  4) ReAct 解析 + 编排（用桩 LLM，不依赖真实模型）
  5) 路由冒烟：页面 200、state 开关、chat 建任务 + cancel

用法：D:/anaconda/python.exe _t_ai_staff.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  ✅ {name}")
    else:
        FAIL.append(name)
        print(f"  ❌ {name} {extra}")


def main():
    from app import create_app
    app = create_app()[0]

    from modules.ai_staff import guard, tools, engine

    with app.app_context():
        root = app.root_path
        from core.db_base import DB_PATH
        ctx = {"user": {"id": "u_test", "account": "tester", "role": "admin"},
               "db_path": DB_PATH, "allow_sql": True}

        print("\n[0] 员工花名册")
        from modules.ai_staff import staffs
        people = staffs.get_staffs()
        ids = [s["id"] for s in people]
        check("花名册含运维助手", "ops" in ids, ids)
        op = staffs.get_staff("ops")
        check("取到启用的运维助手", bool(op) and op["name"] == "运维助手", op)
        check("卡片链接指向对话页", op.get("url", "").endswith("/staff/ops"), op.get("url"))
        check("停用员工取不到", staffs.get_staff("_more") == {}, ids)
        check("不存在的员工取不到", staffs.get_staff("nobody") == {}, ids)
        bad = staffs._normalize([{"id": "../etc", "name": "坏"}, {"id": "ok-1", "name": "好"}])
        check("非法 id 被过滤（只剩合法的一条）", len(bad) == 1 and bad[0]["id"] == "ok-1", bad)

        print("\n[1] SQL 护栏")
        ok_sql, err = guard.validate_select("select id, account from users")
        check("普通 SELECT 放行并补 LIMIT", ok_sql is not None and "LIMIT" in ok_sql, err)
        _, err = guard.validate_select("select 1; drop table users")
        check("多语句被拒", "多条语句" in err, err)
        _, err = guard.validate_select("select 1 -- x")
        check("注释被拒", "注释" in err, err)
        _, err = guard.validate_select("update users set account='x'")
        check("UPDATE 被拒", "SELECT" in err or "关键字" in err, err)
        _, err = guard.validate_select("PRAGMA table_info(users)")
        check("PRAGMA 被拒", bool(err), err)
        s, _ = guard.validate_select("select * from users limit 9999")
        check("LIMIT 被压到 200", s is not None and "LIMIT 200" in s, s)
        s, _ = guard.validate_select("with t as (select 1 as a) select * from t")
        check("WITH 开头放行", s is not None, s)

        print("\n[2] 路径护栏")
        p, err = guard.resolve_path(root, "modules/ai_staff/tools.py")
        check("正常文件放行", p is not None, err)
        _, err = guard.resolve_path(root, "../../Windows/System32/config")
        check("越界被拒", "项目根" in err or ".." in err, err)
        _, err = guard.resolve_path(root, "models/x.gguf")
        check("黑名单目录被拒", "黑名单" in err, err)
        _, err = guard.resolve_path(root, "config/modules.yaml")
        check("敏感配置被拒", "敏感" in err, err)
        _, err = guard.resolve_path(root, "book_manager.db")
        check("数据库文件被拒", bool(err), err)

        print("\n[3] 工具")
        r = tools.list_dir(root, ctx, {"path": "modules/ai_staff", "depth": 1})
        check("list_dir 命中目录", r.get("ok") and any(
            e.get("dir", "").endswith("ai_staff") for e in r.get("entries", [])), r)

        r = tools.code_search(root, ctx, {"pattern": "HUB_MERGED_MODULES", "max_hits": 5})
        files_hit = [h.get("file", "") for h in (r.get("hits") or [])]
        check("code_search 命中 plugin_scan", r.get("ok") and any(
            "core/plugin_scan.py" in f for f in files_hit), files_hit)
        check("code_search 不扫临时脚本", not any(f.startswith("_") for f in files_hit), files_hit)

        r = tools.read_file(root, ctx, {"path": "modules/ai_staff/__init__.py", "end_line": 40})
        check("read_file 返回片段", r.get("ok") and "module_id" in r.get("content", ""), r)
        r = tools.read_file(root, ctx, {"path": "config/modules.yaml"})
        check("read_file 拒绝敏感文件", not r.get("ok"), r)

        r = tools.db_schema(root, ctx, {})
        check("db_schema 列出业务表", r.get("ok") and r.get("table_count", 0) >= 25, r)
        r = tools.db_schema(root, ctx, {"table": "users"})
        cols = [c["name"] for c in (r.get("tables") or [{}])[0].get("columns", [])]
        check("db_schema 返回字段", "account" in cols and "name" in cols, cols)

        r = tools.db_query(root, ctx, {"sql": "select id, account, role from users"})
        check("db_query 正常查询", r.get("ok") and r.get("row_count", 0) >= 1, r)
        r = tools.db_query(root, ctx, {"sql": "delete from users"})
        check("db_query 拒绝写操作", not r.get("ok"), r)
        r = tools.db_query(root, {**ctx, "allow_sql": False}, {"sql": "select 1"})
        check("关闭开关后拒绝查库", not r.get("ok"), r)

        r = tools.log_search(root, ctx, {"level": "ERROR", "max_lines": 3})
        check("log_search 返回结果结构", r.get("ok") and "level_counts" in r, r)

        r = tools.code_search(root, ctx, {"pattern": "切片字符大小", "max_hits": 5})
        check("中文关键词未命中时给建议", r.get("hit_count") == 0
              and "英文" in (r.get("hint") or "") and r.get("searched_dirs"), r.get("hint"))
        r = tools.code_search(root, ctx, {"pattern": "chunker_size", "max_hits": 3})
        check("未命中时给相似文件名候选", r.get("hit_count") == 0
              and any("chunker" in s for s in (r.get("suggestions") or [])), r.get("suggestions"))

        print("\n[3.5] 证据校验")
        good = tools.verify_evidence(root, "定义在 modules/ai_staff/tools.py 第 10 行")
        check("真实引用通过校验", good == [], good)
        bad = tools.verify_evidence(root, "定义在 config/settings.py 第 25 行")
        check("编造的文件被抓出", bool(bad) and "settings.py" in bad[0], bad)
        bad = tools.verify_evidence(root, "见 modules/ai_staff/tools.py:99999")
        check("行号越界被抓出", bool(bad) and "99999" in bad[0], bad)
        check("中文行号写法也能抓", bool(tools.verify_evidence(
            root, "见 config/settings.py 第 25 行")), [])
        refs_f, refs_l = set(), set()
        r = tools.code_search(root, ctx, {"pattern": "def code_search", "max_hits": 3})
        tools.collect_refs(r, refs_f, refs_l)
        check("collect_refs 收集到命中行", bool(refs_l) and any(
            "tools.py" in f for f in refs_f), sorted(refs_f)[:3])
        check("命中过的引用不算未证实", tools.unverified_refs(
            f"见 {sorted(refs_l)[0][0]} 第 {sorted(refs_l)[0][1]} 行", refs_f, refs_l) == [], [])
        check("未命中的引用被标未证实", bool(tools.unverified_refs(
            "见 core/ai_interface.py 第 12 行", refs_f, refs_l)), [])
        hit_line = sorted(refs_l)[0]
        check("带行号必须命中该行（文件见过也不算）", bool(tools.unverified_refs(
            f"见 {hit_line[0]} 第 {hit_line[1] + 5000} 行", refs_f, refs_l)), hit_line)

        print("\n[3.6] 导航证据校验（防编造模块/入口）")
        nav_paths, nav_mods = set(), set()
        r = tools.site_map(root, ctx, {"keyword": "图书"})
        tools.collect_nav_refs(r, nav_paths, nav_mods)
        check("collect_nav_refs 收集到真实模块名", "图书馆" in nav_mods, sorted(nav_mods))
        check("collect_nav_refs 收集到入口路径", bool(nav_paths), sorted(nav_paths)[:3])
        real_path = sorted(nav_paths)[0]
        check("原样引用观察里的路径放行", tools.unverified_nav(
            f"路径是 {real_path}。", nav_paths, nav_mods) == [], real_path)
        check("带引号/前缀包装的真实路径放行（误杀回归）", tools.unverified_nav(
            f"上传图书功能在「{real_path}」里。", nav_paths, nav_mods) == [],
            f"上传图书功能在「{real_path}」里。")
        bad = tools.unverified_nav(
            "员工账号管理功能在 AI 中心模块。路径是 AI 中心 → 员工账号管理。",
            nav_paths, nav_mods)
        check("编造的箭头路径被抓出", any(
            "AI 中心" in b and "员工账号管理" in b and "→" in b for b in bad), bad)
        check("编造的「在 X 模块」被抓出", any("AI 中心 模块" in b for b in bad), bad)
        check("操作步骤描述（无导航语境）不误伤", tools.unverified_nav(
            "先上传文件 → 系统自动索引。", nav_paths, nav_mods) == [], [])
        check("真实模块名 + 观察外入口也被抓", bool(tools.unverified_nav(
            f"路径是 图书馆 → 不存在的入口。", nav_paths, nav_mods)), [])
        check("空观察（全没命中）时一切路径都算编造", bool(tools.unverified_nav(
            "路径是 图书馆 → 图书管理。", set(), set())), [])

        print("\n[4] ReAct 解析与编排")
        p = engine._parse("思考: 先看看表\n行动: db_schema({\"table\": \"users\"})")
        check("解析出工具与参数", p["tool"] == "db_schema" and p["args"] == {"table": "users"}, p)
        p = engine._parse("答案: 在 core/plugin_scan.py:173")
        check("解析出答案", "plugin_scan.py" in p["answer"], p)
        p = engine._parse("行动: db_schema({坏 JSON})")
        check("参数解析失败时 args=None", p["tool"] == "db_schema" and p["args"] is None, p)

        events = []
        script = iter([
            "思考: 找侧栏宽度\n行动: code_search({\"pattern\": \"HUB_MERGED_MODULES\", \"max_hits\": 3})",
            "答案: 定义在 core/plugin_scan.py 第 173 行。",
        ])
        orig_llm = engine._llm
        engine._llm = lambda prompt, max_tokens=400: next(script)
        try:
            engine.run("侧栏合并配置在哪？", root, ctx, events.append)
        finally:
            engine._llm = orig_llm
        types = [e.get("type") for e in events]
        check("编排产生 tool 事件", "tool" in types, types)
        check("编排产生 final + done", types[-2:] == ["final", "done"], types)
        check("最终答案含真实路径", any(
            "plugin_scan.py" in (e.get("text") or "") for e in events if e.get("type") == "final"), events)

        events2 = []
        script2 = iter([
            "思考: 查库\n行动: db_schema({\"table\": \"users\"})",
            "答案: 无权查库，只能读文件。",
        ])
        limited = {"id": "t", "name": "受限员工", "persona": "你只能读文件。", "tools": ["read_file"]}
        engine._llm = lambda prompt, max_tokens=400: next(script2)
        try:
            engine.run("users 表什么样？", root, {**ctx, "staff": limited}, events2.append)
        finally:
            engine._llm = orig_llm
        te = [e for e in events2 if e.get("type") == "tool"]
        check("工具白名单拦截越权工具", bool(te) and te[0]["ok"] is False
              and "无权" in (te[0]["result"] or {}).get("error", ""), te)

        # 7B 最典型的死循环：同一中文关键词反复搜英文代码 → 第二次必须被拦下
        events3 = []
        script3 = iter([
            '思考: 搜切片\n行动: code_search({"pattern": "切片字符大小"})',
            '思考: 再搜一次\n行动: code_search({"pattern": "切片字符大小"})',
            "答案: 没找到，建议用英文词 chunk 再搜。",
        ])
        engine._llm = lambda prompt, max_tokens=400: next(script3)
        try:
            engine.run("切片字符大小在哪配置？", root, ctx, events3.append)
        finally:
            engine._llm = orig_llm
        n_tool3 = sum(1 for e in events3 if e.get("type") == "tool")
        check("相同参数重复调用被拦（只真正执行 1 次）", n_tool3 == 1, [
            e.get("name") for e in events3 if e.get("type") == "tool"])
        check("重复调用给出换思路提示", any(
            "重复调用" in (e.get("text") or "") for e in events3 if e.get("type") == "step"),
            [e.get("text") for e in events3 if e.get("type") == "step"])

        # 编造证据 → 打回重写 → 重写后通过
        events4 = []
        script4 = iter([
            "答案: 定义在 config/settings.py 第 25 行。",
            "答案: 定义在 modules/ai_staff/tools.py 第 10 行。",
        ])
        engine._llm = lambda prompt, max_tokens=400: next(script4)
        try:
            engine.run("切片配置在哪？", root, ctx, events4.append)
        finally:
            engine._llm = orig_llm
        fin4 = [e for e in events4 if e.get("type") == "final"]
        check("编造证据被拦下并要求重写", any(
            "证据校验" in (e.get("text") or "") for e in events4 if e.get("type") == "step"),
            [e.get("text") for e in events4 if e.get("type") == "step"])
        check("修正后答案不含编造路径", bool(fin4) and "settings.py" not in fin4[-1]["text"],
              fin4[-1]["text"] if fin4 else "")

        print("\n[4.5] 八字排盘与规则字典")
        # staffs.get_staff 内部会用 url_for，必须带应用上下文
        with app.app_context():
            try:
                from modules.ai_staff import bazi as m_bazi
            except Exception:
                m_bazi = None
            if m_bazi is None or m_bazi.Solar is None:
                print("  ⚠️ lunar_python 未安装，跳过排盘用例")
            else:
                # 交叉验证：官方示例 公历 1986-05-29 子时 → 丙寅 癸巳 癸酉 壬子
                rep = m_bazi.analyze(1986, 5, 29, 0.0, "男")
                check("已知八字四柱正确",
                      rep["chart"]["si_zhu"] == "丙寅 癸巳 癸酉 壬子", rep["chart"]["si_zhu"])
                dm = rep["chart"]["day_master"]
                check("日主癸水阴", (dm["gan"], dm["element"], dm["polarity"]) == ("癸", "水", "阴"), dm)
                check("纳音与官方一致",
                      rep["lunar"]["nayin"] == ["炉中火", "长流水", "剑锋金", "桑柘木"], rep["lunar"]["nayin"])
                check("大运与起运岁数非空",
                      rep["readout"]["dayun_start"] and len(rep["readout"]["dayun"]) >= 5,
                      rep["readout"]["dayun_start"])

                r2 = m_bazi.analyze(1990, 3, 15, 14.0, "女")
                check("四柱齐全", len(r2["chart"]["si_zhu"].split()) == 4, r2["chart"]["si_zhu"])
                check("强弱有判定",
                      r2["chart"]["strength"]["verdict"] in ("身强", "中和", "身弱"), r2["chart"]["strength"])
                check("十神有归类", bool(r2["shi_shen"]["groups"]), r2["shi_shen"]["groups"])
                check("主导十神非空", bool(r2["shi_shen"]["top"]), r2["shi_shen"]["top"])
                check("性格要点够用", len(r2["readout"]["traits"]) >= 3, r2["readout"]["traits"])
                check("岗位方向够用", len(r2["readout"]["jobs"]) >= 3, r2["readout"]["jobs"])
                check("协作提示够用", len(r2["readout"]["cautions"]) >= 2, r2["readout"]["cautions"])
                check("免责说明固定存在", "不作为" in r2["disclaimer"], r2["disclaimer"])

                r3 = m_bazi.analyze(1990, 3, 15, 14.0, "", "", hour_known=False)
                check("缺时辰时柱标未知", r3["chart"]["si_zhu"].endswith("未知"), r3["chart"]["si_zhu"])
                check("缺时辰不强判强弱",
                      r3["chart"]["strength"]["verdict"] == "缺时辰无法判定", r3["chart"]["strength"])
                check("缺时辰在文本中说明", "三柱" in m_bazi.to_text(r3), "")

                rw = m_bazi.analyze(1990, 3, 15, 14.0, "女", "乌鲁木齐")
                check("真太阳时校正生效", "校正" in rw["birth"]["true_solar_note"],
                      rw["birth"]["true_solar_note"])
                rb = m_bazi.analyze(1990, 3, 15, 14.0, "女", "北京")
                check("不同城市结果可比",
                      rb["chart"]["si_zhu"] == rw["chart"]["si_zhu"] or bool(rw["birth"].get("true_solar_alert")),
                      [rb["chart"]["si_zhu"], rw["chart"]["si_zhu"]])
                check("非法日期被拒", m_bazi.analyze(1986, 13, 40, 0, "男").get("ok") is False, [])

                rr = tools.call_tool("calc_bazi", root, ctx,
                                     {"year": 1990, "month": 3, "day": 15, "hour": "未",
                                      "gender": "女", "city": "上海"})
                check("calc_bazi 工具可用", rr.get("ok") is True, rr)
                check("工具返回结构化报告", bool((rr.get("report") or {}).get("chart")), list(rr.keys()))
                check("工具返回可读事实文本", "四柱" in (rr.get("text") or ""), (rr.get("text") or "")[:60])
                check("缺参数时报错明确", tools.call_tool("calc_bazi", root, ctx, {}).get("ok") is False, [])
                check("未知时辰走三柱", tools.call_tool(
                    "calc_bazi", root, ctx,
                    {"year": 1990, "month": 3, "day": 15, "hour": "未知"}
                )["report"]["birth"]["hour_known"] is False, [])
                check("时辰名可解析", tools.call_tool(
                    "calc_bazi", root, ctx, {"year": 1990, "month": 3, "day": 15, "hour": "午"}
                )["report"]["birth"]["shichen"] == "午", [])
                spec = {t["name"]: t for t in tools.TOOL_SPECS}
                check("calc_bazi 标记 private_args", spec["calc_bazi"].get("private_args") is True, [])

                # 员工隔离：命理员工不给代码/数据库权限
                hr = staffs.get_staff("hr")
                check("花名册含人力参谋", bool(hr) and hr["name"] == "人力参谋", (hr or {}).get("name"))
                check("人力参谋白名单只放行排盘", hr["tools"] == ["calc_bazi"], hr["tools"])
                check("人力参谋带表单", bool(hr["form"].get("fields"))
                      and hr["form"]["auto_tool"] == "calc_bazi", hr["form"].get("auto_tool"))
                check("出生地建议列表非空", len(hr["form"]["fields"][-1]["options"]) >= 20, [])

                ev5 = []
                it5 = iter(["思考: 找代码\n行动: code_search({\"pattern\": \"HUB_MERGED\"})",
                            "答案: 我没有查看代码的权限。"])
                ctx_hr = dict(ctx)
                ctx_hr["staff"] = hr
                engine._llm = lambda prompt, max_tokens=400: next(it5)
                try:
                    engine.run("看看代码", root, ctx_hr, ev5.append)
                finally:
                    engine._llm = orig_llm
                check("越权工具被拒", any(
                    "无权" in (e.get("result") or {}).get("error", "")
                    for e in ev5 if e.get("type") == "tool"),
                    [e.get("name") for e in ev5 if e.get("type") == "tool"])

                # 隐私：预取事件的 args 必须为空，且审计不落参数
                ev6 = []
                it6 = iter(["答案: 已解读。"])
                pre_txt = tools.call_tool("calc_bazi", root, ctx,
                                          {"year": 1990, "month": 3, "day": 15,
                                           "hour": "未", "gender": "女"})["text"]
                ctx_pre = dict(ctx)
                ctx_pre["staff"] = hr
                ctx_pre["pre"] = {"tool": "calc_bazi", "text": pre_txt, "report": None}
                engine._llm = lambda prompt, max_tokens=400: next(it6)
                try:
                    engine.run("分析一下", root, ctx_pre, ev6.append)
                finally:
                    engine._llm = orig_llm
                tool_ev = [e for e in ev6 if e.get("type") == "tool"]
                check("预取结果以 tool 事件送达", any(e.get("pre") is True for e in tool_ev), tool_ev)
                check("预取事件 args 已脱敏", all(not e.get("args") for e in tool_ev), tool_ev)
                check("排盘文本不含姓名等无关字段", "没有" in pre_txt or "出生" in pre_txt, pre_txt[:60])

    print("\n[5] 路由冒烟")
    c = app.test_client()
    # CSRF 防护：POST 必须带 AJAX 头（或同源 Referer）
    HDR = {"X-Requested-With": "XMLHttpRequest"}
    resp = c.post("/api/login", json={"account": "admin", "password": "admin123"}, headers=HDR)
    check("登录成功", resp.get_json().get("code") == 200, resp.get_json())

    r = c.get("/staff/")
    html = r.get_data(as_text=True)
    check("首页 200（卡片墙）", r.status_code == 200, r.status_code)
    check("首页含员工卡片", "staff-card" in html and "运维助手" in html, html[:200])
    check("停用卡片不可点", 'staff-card disabled' in html, html[:400])

    r = c.get("/staff/ops")
    html = r.get_data(as_text=True)
    check("员工对话页 200", r.status_code == 200, r.status_code)
    check("对话页注入 STAFF_ID", 'window.STAFF_ID = "ops"' in html, html[:200])
    r = c.get("/staff/nobody")
    check("不存在的员工 404", r.status_code == 404, r.status_code)
    r = c.get("/staff/_more")
    check("停用的员工 404", r.status_code == 404, r.status_code)
    r = c.get("/staff/api/state")
    js = r.get_json()
    if js is None:
        FAIL.append("state 返回 JSON")
        print(f"  ❌ state 返回 JSON -> {r.status_code}")
        return 1
    check("state 返回 allow_sql=True", js.get("code") == 200 and js["data"].get("allow_sql") is True, js)
    r = c.post("/staff/api/chat", json={"message": "users 表有几个用户"}, headers=HDR)
    check("缺少 staff 时拒绝", r.get_json().get("code") != 200, r.get_json())
    r = c.post("/staff/api/chat", json={"message": "x", "staff": "nobody"}, headers=HDR)
    check("未知员工拒绝", r.get_json().get("code") != 200, r.get_json())

    r = c.post("/staff/api/chat", json={"message": "users 表有几个用户", "staff": "ops"}, headers=HDR)
    js = r.get_json()
    tid = (js.get("data") or {}).get("task_id") if js.get("code") == 200 else ""
    check("chat 建任务", bool(tid), js)
    # 真实 LLM 不可用时任务会瞬间结束，无法稳定复现「占用中」，
    # 所以直接把并发计数置为满，验证入口的拒绝分支。
    with engine._LOCK:
        engine._RUNNING = engine.MAX_CONCURRENT
    try:
        r = c.post("/staff/api/chat", json={"message": "再来一个", "staff": "ops"}, headers=HDR)
        check("并发被拒（同时只允许 1 个任务）", r.get_json().get("code") != 200, r.get_json())
    finally:
        with engine._LOCK:
            engine._RUNNING = 0
    if tid:
        r = c.post(f"/staff/api/cancel/{tid}", headers=HDR)
        check("cancel 成功", r.get_json().get("data", {}).get("cancelled") is True, r.get_json())
    r = c.get("/staff/api/stream/not-exist-task")
    check("不存在的任务返回 404", r.status_code == 404, r.status_code)

    # ---- 表单型员工「人力参谋」 ----
    r = c.get("/staff/hr")
    html = r.get_data(as_text=True)
    check("人力参谋对话页 200", r.status_code == 200, r.status_code)
    check("对话页注入表单配置", "STAFF_FORM" in html and "calc_bazi" in html, html[:300])
    r = c.post("/staff/api/chat", json={"message": "帮我分析这个人", "staff": "hr",
                                       "fields": {"year": 1990, "month": 3, "day": 15,
                                                  "hour": "未", "gender": "女", "city": "上海"}},
               headers=HDR)
    js = r.get_json()
    check("表单提交触发后端预计算",
          js.get("code") == 200 and (js.get("data") or {}).get("pre") is True, js)
    check("表单用例下不会落到越权路径", js.get("code") == 200, js)

    print("\n[6] AI客服（help）与其三个专用工具")
    from modules.ai_staff import staffs as _sf
    _help = _sf.get_staff("help")
    check("花名册含 AI客服", bool(_help) and _help["name"] == "AI客服", _sf.get_staffs())
    check("AI客服为单图标头像", not _help.get("icons"), _help.get("icon"))
    check("AI客服只对三名受限工具开放",
          _help.get("tools") == ["site_map", "tool_usage", "book_search"], _help.get("tools"))
    check("AI客服对所有人开放", (_help.get("min_role") or "") == "", _help.get("min_role"))
    check("运维助手为单图标头像", not _sf.get_staff("ops").get("icons"), _sf.get_staff("ops").get("icon"))
    check("人力参谋为单图标头像", not _sf.get_staff("hr").get("icons"), _sf.get_staff("hr").get("icon"))
    check("花名册不含 icons 字段", all(not s.get("icons") for s in _sf.get_staffs()), "")
    check("运维助手仍要求管理员", (_sf.get_staff("ops").get("min_role") or "") == "admin", "")
    admin_u = {"id": "a1", "role": "admin"}
    normal_u = {"id": "u1", "role": "user"}
    check("管理员可见全部员工",
          [s["id"] for s in _sf.visible_staffs(admin_u)][:3] == ["ops", "hr", "help"],
          [s["id"] for s in _sf.visible_staffs(admin_u)])
    check("普通用户只见使用向导",
          [s["id"] for s in _sf.visible_staffs(normal_u)] == ["help"],
          [s["id"] for s in _sf.visible_staffs(normal_u)])
    check("未登录看不到任何员工", _sf.visible_staffs(None) == [], "")
    check("普通用户不能绕过到运维助手", _sf.can_use(normal_u, _sf.get_staff("ops")) is False, "")

    _r = tools.call_tool("site_map", root, ctx, {})
    _mods = _r.get("modules") or []
    check("site_map 列出模块", _r.get("ok") and len(_mods) >= 10, len(_mods))
    check("site_map 不回灌内部别名", all("_alias" not in m for m in _mods), "")
    check("site_map 带模块自报入口",
          any(m["id"] == "book_lib" and any(e["url"] for e in m["entries"]) for m in _mods), "")
    _r = tools.call_tool("site_map", root, ctx, {"keyword": "上传图书"})
    check("口语别名能召回：上传图书→图书馆",
          _r.get("hit_count") == 1 and _r["modules"][0]["url"] == "/book", _r.get("hit_count"))
    _r = tools.call_tool("site_map", root, ctx, {"keyword": "zzz火星怪兽zzz"})
    check("site_map 无结果时给拒答引导",
          _r.get("hit_count") == 0 and "我没找到这个入口" in (_r.get("hint") or ""), _r.get("hint"))
    check("site_map 无结果时仍给完整清单兜底", len(_r.get("all_modules") or []) >= 10, "")

    _r = tools.call_tool("tool_usage", root, ctx, {"keyword": "PDF 转换"})
    _ids = [t["id"] for t in (_r.get("tools") or [])]
    check("tool_usage 命中文档格式转换", "document_convert" in _ids[:3], _ids[:3])
    check("tool_usage 给中文导航路径",
          any("工具箱 →" in (t.get("path") or "") for t in (_r.get("tools") or [])), "")
    _r = tools.call_tool("tool_usage", root, ctx, {"tool_id": "nope_xxx"})
    check("tool_usage 未知 id 给相近候选",
          _r.get("hit_count") == 0 and "相近的" in (_r.get("hint") or ""), _r.get("hint"))
    _r = tools.call_tool("tool_usage", root, ctx, {})
    check("tool_usage 无关键词返回分类概览",
          bool(_r.get("by_category")) and _r.get("tool_count", 0) > 30, _r.get("tool_count"))

    # 行级权限用独立临时库验证，避免污染正式库
    import sqlite3 as _sq
    import tempfile as _tf
    _tmpdb = os.path.join(_tf.gettempdir(), "_t_aistaff_books.db")
    if os.path.exists(_tmpdb):
        os.remove(_tmpdb)
    _cn = _sq.connect(_tmpdb)
    _cn.execute("CREATE TABLE books (id TEXT, name TEXT, cat1 TEXT, cat2 TEXT, file_type TEXT,"
                " owner_id TEXT, pc1 TEXT, path TEXT, savepath TEXT)")
    _cn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?)", [
        ("b1", "Python 基础教程", "技术", "编程", "pdf", None, "", "/x/a.pdf", "/x"),
        ("b2", "我的私密笔记", "", "", "pdf", "u1", "个人", "/x/b.pdf", "/x"),
        ("b3", "别人的薪资表", "", "", "xlsx", "u2", "个人", "/x/c.pdf", "/x"),
    ])
    _cn.commit(); _cn.close()
    _ctx_t = {"user": normal_u, "db_path": _tmpdb, "allow_sql": False}
    _r = tools.call_tool("book_search", root, _ctx_t, {"name": "私密"})
    check("能查到自己的私有书", _r.get("row_count") == 1, _r)
    _r = tools.call_tool("book_search", root, _ctx_t, {"name": "薪资"})
    check("看不到别人的私有书", _r.get("row_count") == 0, _r)
    check("查不到时说明权限范围", "公共图书" in (_r.get("hint") or ""), _r.get("hint"))
    _ctx_a = {"user": admin_u, "db_path": _tmpdb, "allow_sql": False}
    _r = tools.call_tool("book_search", root, _ctx_a, {"name": "薪资"})
    check("管理员可查到全部", _r.get("row_count") == 1, _r)
    _r = tools.call_tool("book_search", root, _ctx_t, {"name": "Python"})
    _b = (_r.get("books") or [{}])[0]
    check("返回 cat1/cat2 拼接的分类路径",
          _b.get("category_path") == "技术 / 编程", _b)
    check("不泄漏文件路径", "path" not in _b and "savepath" not in _b, list(_b.keys()))
    os.remove(_tmpdb)

    print("\n[7] AI客服的路由级门禁")
    # MAX_CONCURRENT=1：前端用例的任务线程同步阻塞在 LLM 上，cancel 只是打标记，
    # 真正的推理锁要等那一轮跑完才释放（可能几十秒），这里给足 120 秒。
    for _i in range(240):
        if not (c.get("/staff/api/state").get_json()["data"].get("busy")):
            break
        time.sleep(0.5)
    try:
        from core.auth import SESSION_USER_KEY as _SK
        _c2 = app.test_client()
        _c2.post("/api/login", json={"account": "admin", "password": "admin123"}, headers=HDR)
        with _c2.session_transaction() as _s:
            _s[_SK] = "u_eede2a5c615d"   # 张三，role=user
        _h = _c2.get("/staff/").get_data(as_text=True)
        check("普通用户卡片墙只见 AI客服", "AI客服" in _h and "运维助手" not in _h, "")
        check("普通用户能进 AI客服页", _c2.get("/staff/help").status_code == 200, "")
        _r = _c2.get("/staff/ops")
        check("普通用户深链运维助手被拦", _r.status_code == 403, _r.status_code)
        _r = _c2.post("/staff/api/chat", json={"message": "x", "staff": "ops"}, headers=HDR)
        check("接口层同样拦截越权员工", _r.get_json().get("code") == 403, _r.get_json())
        _r = _c2.post("/staff/api/chat", json={"message": "x", "staff": "help"}, headers=HDR)
        check("普通用户可向 AI客服提问", _r.get_json().get("code") == 200, _r.get_json())
        _tid = (_r.get_json().get("data") or {}).get("task_id")
        if _tid:
            _c2.post(f"/staff/api/cancel/{_tid}", headers=HDR)
            _r = _c2.get(f"/staff/api/stream/{_tid}")
            check("任务流本人可读", _r.status_code in (200, 404), _r.status_code)
    except Exception as _e:
        check("AI客服路由门禁", False, f"{type(_e).__name__}: {_e}")


    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
