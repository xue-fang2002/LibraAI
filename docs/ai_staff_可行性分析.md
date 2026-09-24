# 「AI 员工」模块 · 运维助手 可行性与复杂度分析

> 评估日期：2026-09-21 · 仅分析，未改任何业务代码
> 目标：侧边栏新增模块「AI 员工」，首个员工「运维助手」——读项目文件、查数据库、分析日志、定位参数/代码位置。

---

## 一、结论先行

**可行，但必须换实现思路。** 不能做「把整个项目读进来喂给模型」，只能做「检索式 + 只读工具调用」的运维助手。

- 整体复杂度：**中高**（M 级），完整落地约 3–5 人日；**P0 最小可用版约 1.5–2 人日**。
- 最大风险不是「做不出来」，而是 **7B 远程模型 + 串行推理锁 → 一次排查要几分钟并阻塞全站 AI 问答**。
- 建议：**新建独立模块 `ai_staff`（/staff）**，不塞进 `ai_agent`（后者是「一句话触发一个工具」的任务形态，不适合多轮排查对话）。

---

## 二、现状事实（本次实测，非估算）

| 项 | 实测值 | 对方案的影响 |
|---|---|---|
| Python 代码 | modules 23,006 行 + core 3,577 + common 292 ≈ **2.7 万行** | 全文塞上下文 ≈ 百万级 token，**不可行**，必须检索 |
| 前端 | js+css 16,950 行，html 42 个 | 同上；且前端定位需求（如某按钮在哪个 html）需单独索引 |
| 数据库 | `book_manager.db` **310 MB / 30 张表** | 只能只读 + 白名单 + LIMIT，禁止任意 SQL |
| 日志 | `logs/app.log`（RotatingFileHandler，5MB×3，格式 `时间 [级别] logger | 消息`）+ 根目录 `_server*.log` | 格式规整、易解析；**但当前日志覆盖很薄**（只有模块加载等少数 INFO），要真正「分析日志」需先补齐埋点 |
| LLM | ollama @ 10.50.9.88，qwen2.5-7b；`llm_generate()` 同步阻塞、timeout 120s、**串行推理锁** | 单次调用几十秒；多轮 = 分钟级；**会独占推理锁** |
| Function Calling | `llm_generate(prompt, max_tokens, temperature, timeout, history, stream)` **无 tools 参数** | 需自实现 ReAct 文本协议（Thought/Action/Observation），7B 格式遵从率一般，需强约束+重试+兜底 |
| 侧栏机制 | `plugin_scan` 扫 `modules/<id>/__init__.py` 的 `module_info` + `config/modules.yaml` | 新增模块成本很低（骨架 ≈ 0.5 天，可照抄 `workflow`） |
| 权限机制 | `module_access` + `user_can_access_module`，workflow 已做 before_request 门禁范式 | 运维助手属高危能力，**默认仅管理员**，可直接复用 workflow 的门禁写法 |

---

## 三、架构选型

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 挂到 ai_agent 当连接器 | 在 `/agent` 加若干能力 | 复用最多、改动最小 | 侧栏入口仍是「AI 任务」；单轮工具调用形态，不适合「提问→查→再查→总结」的排查链路 | ✗ |
| B. 新建独立模块 `ai_staff`（/staff） | 独立页面 + 独立会话 + 独立权限 | 符合「AI 员工」产品形态；权限独立收口；后续可加更多员工 | 需新写页面与会话流 | ✓ **推荐** |
| C. 塞进 toolbox / workflow | 当工具或流程 | 省事 | 语义错位（那是工具/RPA，不是会思考的员工）；且 workflow 是私有 RPA 专用 | ✗ |

**推荐 B**：`modules/ai_staff/`，内部**惰性 import** `modules.ai_center.internal.qa.llm_generate`（遵守项目「跨模块调用一律函数内 import」铁律，规避 reload 陷阱）。

---

## 四、运维助手的四个工具（能力边界）

| 工具 | 能力 | 实现要点 | 复杂度 |
|---|---|---|---|
| T1 代码检索 | 定位「某个参数/函数/配置项在哪个文件第几行」 | ① grep 全文（ripgrep 风格，排除黑名单目录）② Python 用 `ast` 抽 def/class/赋值建符号索引（秒级、无需模型）③ 可选：后续加本地 bge-small 语义检索 | 中 |
| T2 文件读取 | 打开指定文件片段 | 路径必须在项目根内；按行区间返回；单次上限（如 300 行 / 8k 字符）；跳过二进制 | 低 |
| T3 数据库只读查询 | 查表结构、行数、抽样数据 | sqlite3 `mode=ro` 只读连接；**仅允许单条 SELECT**；强制 LIMIT；语句超时；禁 PRAGMA 写/ATTACH/多语句 | 中（风险高） |
| T4 日志分析 | 按时间窗/级别/traceback 聚合 | 解析 `logs/app.log` + `_server*.log`；按级别计数、ERROR 聚类、traceback 提取 | 低—中 |

**明确不做（P0/P1 都不做）**：写文件、改配置、执行 shell、重启服务、删数据、任意 SQL。

---

## 五、复杂度分级与工期

| 子任务 | Effort | 说明 |
|---|---|---|
| ① 模块骨架：__init__ + yaml 两处 + 侧栏入口 + 权限门禁 | 低 | 照抄 `modules/workflow/`，0.5 天 |
| ② 会话页面 + SSE 流式（展示思考/工具调用过程） | 中 | 可复用首页问答的气泡样式 |
| ③ 代码符号索引 + 检索 | 中 | ast + grep，1 天 |
| ④ 只读 SQL 工具 + 安全护栏 | 中 | 0.5 天，需最谨慎 |
| ⑤ 日志解析聚合 | 低—中 | 0.5 天 |
| ⑥ ReAct 编排 + 轮次上限 + 超时 + 中断 | 中—高 | 1–2 天，难点在 7B 格式遵从 |
| ⑦ 审计日志 + 权限收口 | 中 | 0.5 天 |

**P0 合计 ≈ 1.5–2 天（①②③④⑤ 简化版 + ⑥ 单轮版 + ⑦）**，完整版 3–5 天。

---

## 六、风险与护栏（红线）

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 远程 7B 串行推理锁 | 运维助手跑几分钟 → **全站 AI 问答被卡住** | 异步任务 + 队列 + 可中断；单会话轮次上限（≤6）；全局并发上限（如同时仅 1 个运维任务） |
| 任意文件读取 | 泄露 DB / 模型权重 / 配置密钥 | 路径白名单（项目根内）+ 黑名单（`_pw_browsers/`、`models/`、`ai_vector_store/`、`*.db`、`__pycache__`、`.git`）+ 管理员门禁 |
| 任意 SQL | 拖库 / 写坏数据 | 只读连接 + 仅单条 SELECT + 强制 LIMIT + 超时中断 + 关键字黑名单 |
| 上下文爆炸 | 巨慢且必失败 | 检索优先、只回传片段、硬截断 |
| 模型幻觉（编造文件/行号） | 错误结论误导排查 | 工具返回必须带真实路径+行号；答案强制引用证据；检索为空时明确回答「没找到」 |
| 日志覆盖不足 | 「分析日志」无米下锅 | 前置项：为关键链路补 `get_logger()` 埋点（可作为独立 P1 任务） |
| 项目既有坑 | 新路由 404 / 改了不生效 | 遵守记忆铁律：杀净 app.py 再起、验 5005 单监听、跨模块惰性 import |

---

## 七、分批次落地计划

**P0（最小可用，建议先做这批）**
1. 新建 `modules/ai_staff/`（`__init__.py` / `routes.py` / `tools.py` / `planner.py` / `templates/ai_staff/index.html`）
2. `config/modules.yaml` + `modules.yaml.example` 同步：`ai_staff: enabled/sort_order/public:false`
3. 权限门禁：before_request 复用 workflow 范式，默认 `level=admin`
4. 工具：T1 grep 检索 + T2 片段读取 + T3 只读 SELECT + T4 日志聚合（全部只读）
5. 编排：ReAct 单轮（模型输出 JSON `{tool, args}` → 执行 → 回灌 → 生成答案），失败兜底为「直接列出 grep 命中」

**P0 验收标准（必须逐条演示）**
- 问「`rerank_max_candidates` 这个参数在哪个文件、第几行、默认值是多少」→ 给出真实路径+行号+默认值
- 问「users 表有几条记录、有哪些字段」→ 给出真实数字与字段名
- 问「最近一天有哪些 ERROR 日志」→ 给出条目与时间
- 无权限用户访问 `/staff` → forbidden；普通用户侧栏看不到该入口

**P1**：多轮（≤6 轮）+ 中间过程流式展示 + ast 符号索引加速 + 审计表 + 可中断 / 并发控制；同时补日志埋点。
**P2**：本地 bge-small 语义检索（独立 faiss 命名空间，绝不混进图书索引）、定时巡检、把「AI 员工」做成可配置的多员工（运维/数据/文档）。

---

## 八、需要你拍板的 3 件事

1. **查库是「自由 SELECT（只读护栏内）」还是「预置查询模板」？** 前者灵活、后者更安全。建议先 P0 走只读自由 SELECT + LIMIT 500，观察后再收紧。
2. **是否给运维助手单独配一个本地后端（llama_cpp）？** 避免长任务占用远程 ollama 推理锁拖垮全站问答。代价是本地 7B 推理慢。
3. **UI 形态**：聊天式（像首页问答，附带工具调用过程折叠面板）还是工单式（提交问题 → 后台跑 → 结果页）？长任务建议聊天式 + 异步。

---

## 九、可直接复制给另一个 AI 的落地指令（P0）

```
在 D:\xianmufile（Flask 插件化项目）新增模块 ai_staff，严格按下列约束实现 P0：

【必须遵守的项目铁律】
1. 跨模块调用一律在函数内惰性 import，禁止模块顶层 import 兄弟模块包（会触发 plugin_scan reload 陷阱导致路由全丢）。
2. 改前端一律改根目录 static/（url_for('static') 只指向根 static/）。
3. 暗色选择器只认 body.dark；明亮写 body:not(.dark)[data-light-theme]。
4. 装饰器顺序：@bp.route → @module_exception_guard(...) → @login_required。
5. 新增模块需同时改 config/modules.yaml 与 config/modules.yaml.example。
6. 页面外壳 .wrap 用 min-height:100%；聊天区用 flex + overflow-y:auto + min-height:0。

【文件】
- modules/ai_staff/__init__.py：定义 bp + module_info（module_id="ai_staff", display_name="AI 员工", icon 自选, route_prefix="/staff", url_default_endpoint="index", blueprint=bp），末尾 from . import routes。
- modules/ai_staff/routes.py：before_request 门禁（复用 modules/workflow/routes.py 的 _ensure_access_seed + user_can_access_module 范式，默认 level=admin）；GET / 渲染页面；POST /api/chat 走同步推理（先不做流式）；SSE /api/stream/<task_id> 输出过程。
- modules/ai_staff/tools.py：四个只读工具
    grep_code(pattern, path_glob, max_hits)       → 返回 [{file, line_no, text}]
    read_file(path, start_line, end_line)         → 片段，上限 300 行 / 8k 字符
    query_db(sql)                                 → 仅允许单条 SELECT，强制 LIMIT 500，sqlite3 只读连接(URI mode=ro)，超时 5s
    read_logs(hours, level, keyword, max_lines)   → 解析 logs/app.log 格式 "时间 [级别] logger | 消息"
  路径安全：全部解析成绝对路径后必须位于项目根内；黑名单目录 _pw_browsers / models / ai_vector_store / __pycache__ / .git；禁读 *.db、*.gguf、*.safetensors、*.bin。
- modules/ai_staff/planner.py：唯一调 LLM 处，惰性 import modules.ai_center.internal.qa.llm_generate；ReAct 单轮，要求模型只输出 JSON {"tool": "...", "args": {...}} 或 {"answer": "..."}；解析失败则直接用 grep 结果兜底。

【验收】
1. 启动前先杀光所有 app.py 进程，再起一个，确认 5005 只有单监听（双进程会让新路由随机 404）。
2. 管理员登录后可看到侧栏「AI 员工」，普通用户看不到、直连 /staff 返回 forbidden。
3. 三条问句必须给出带真实路径/行号/数字的证据答案；查不到时明确回答「没找到」。
4. 提交前 py_compile 门禁；只 git add 明确路径，禁止 git rm -r 与 reset --hard。
```
