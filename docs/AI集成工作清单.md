# AI 集成工作清单（按优先级排序）

> 生成时间：2026-09-15（2026-09-16 更新：P0 完成、AI 任务中心第一版落地）
> 范围：AI 任务入口 → 工具箱/办公/图表/导航的能力调度 → 多步自动执行
> 排序原则：**先安全、后地基、再功能** —— 上层 AI 集成每往前一步，底层缺口的代价就翻一倍。

> ⚠️ **路线变更（2026-09-16）**：原计划的「首页 AI 问答 + 任务执行按钮」改为
> **独立的 `ai_agent` 模块**（用户拍板）。首页问答保持原样不动。
> 理由是任务类交互（计划卡/进度/回执/历史）与问答流式气泡的 UI 需求完全不同，
> 混在一起两边都别扭；独立模块也能避免意图分类拖慢默认问答链路。

---

## 批次总览

| 批次 | 主题 | 做完能达到什么 |
|---|---|---|
| P0 | 安全与可用性底线 | 系统不再有裸奔接口，AI 不会因为后端抖动直接崩给用户 |
| P1 | 结构地基 + 主线入口 | 首页成为真正的统一调度入口，工具箱可持续扩展 |
| P2 | 能力扩展 | 覆盖导航深链、工具箱接入、结构化输出、多步编排 |
| P3 | 远期 / 需谨慎评估 | 真正的自主循环与网页自动化 |

---

## P0 · 安全与可用性底线 —— ✅ 已完成（2026-09-15，commit `f29e49c` / `P0-2`）

> 开工前已做 git 备份：tag `backup-before-p0-20260915` → commit `753ce24`（改动前状态完整保留）。
> 回滚方式：`git checkout backup-before-p0-20260915 -- <path>`，或 `git revert <commit>`。

### 1. ✅ 补回缺失的登录校验 —— 已完成
- **工作量**：低
- **涉及文件**：`modules/office_tools/routes.py`（18 条路由全部无 `@login_required`）、`modules/chart/routes.py:13`（装饰器被注释掉）
- **为什么排第一**：现在靠侧边栏可见性做软隔离，直接打 URL 就能用。首页一旦成为所有能力的总入口，等于把这两套能力暴露给每一个能打开首页的人。这是**做任何其他事之前必须先关的门**。
- **注意**：补之前先确认是否存在依赖匿名访问的前端调用，避免误伤现有功能。
- **完成标志**：未登录直接请求 `/office_tools/api/xxx`、`/chart/ai/chat` 返回 `need_login`。
- **实测结果**（会话隔离的双态验证，21 个 AJAX 端点 + 3 个页面）：
  - 未登录：AJAX 全部 `code=401 need_login`，页面 HTTP 401
  - 已登录：全部通过（多为 `code=400` 业务参数校验，说明确实走进业务逻辑）
  - 对照 `/book/`（公共页）两态均 200，无误伤
- **改动**：`modules/office_tools/routes.py` 18 条路由 + `modules/chart/routes.py` 3 处（`/`、`/ai/render`、`/ai/chart`）。
- ⚠️ **踩坑记录**：这里用 curl 验收时不能用 HTTP 状态码判定 —— 项目约定 AJAX 未登录返回的是 **HTTP 200 + body `{"code":401,"need_login":true}`**，只有页面请求才是 HTTP 401。判定要读 body 的 `code` 字段。

### 2. ✅ `get_llm()` 增加后端自动 fallback —— 已完成
- **工作量**：中
- **涉及文件**：`modules/ai_center/internal/qa.py:609` `get_llm()`、以及 `llm_generate()` 的调用路径
- **现状**：`get_llm()` 加载失败直接 `return None`，上层只回一句"模型未响应"，不会自动切到已配置的其他后端。
- **为什么排第二**：后面所有 AI 编排（意图路由、计划生成、多步执行）都是**串行多次推理**，一次推理失败就是整条任务链崩。没有 fallback，越复杂的功能越脆。
- **建议**：按已配置后端列表做健康探测（已有 `probe_backend_connectivity`）→ 有序降级 → 记录实际使用后端供前端展示。
- **完成标志**：主动停掉首选后端，问答自动切到备用后端并正常返回。
- **实现要点**（`modules/ai_center/internal/qa.py`）：
  - `get_llm()` 改为按候选顺序尝试，失败即降级；候选顺序 `ollama → dashscope → llama_cpp → transformers`（本地重量级后端排最后）
  - 新增 `_try_load_backend()`：**先轻量探活再加载**，避免每次提问都为不可达后端付出数十秒的模型加载代价
  - 失败后端进冷却表（300s）；降级态每 300s 回探一次首选后端，恢复后自动切回（打印 `✅ 首选后端 [x] 已恢复，自动切回`）
  - 新增 `get_active_backend()`：返回**实际生效**后端。`llm_generate()` 与 `get_prompt_budget()` 改用它 —— 否则自动降级后会用 A 后端的 `generate` 去驱动 B 后端的实例，必然报错
  - `config.py` 新增 `get_backend_config(backend_key)` / `get_backend_probe_cfg()`；`get_llm_backend_config()` 改为复用前者的等价包装（已验证输出与改造前逐键一致）
  - `get_current_backend_info()` 增加 `active_backend` / `degraded` 字段 —— ⚠️ **该函数目前没有任何调用方，字段已备好但未接前端**，需要时再接线
- **实测结果**：注入假后端验证 7/7 通过（降级、generate 跟随、冷却、缓存复用、回切、全失败返回 None、预算跟随）。真实环境 `get_llm_backend_config()` 与改造前输出完全一致；当前 `backend=ollama`（10.50.9.88）正常加载且**未降级**；端到端真实问答 84s 返回正确答案。
- ⚠️ **行为变化（需知晓）**：过去首选后端挂掉会秒返回 None（"模型未响应"）；现在会继续尝试候选，**最坏情况要等本地模型加载失败**，首次降级请求会明显变慢。若不接受本地兜底，把 `qa.py` 的 `_BACKEND_FALLBACK_ORDER` 里的 `llama_cpp` / `transformers` 删掉即可。

---

## P1 · 结构地基 + 主线入口

### 4/5. ✅ AI 任务中心模块（`ai_agent`）第一版 —— 已落地
- **commit**：`d6d80cd`（2026-09-16）
- **入口**：侧边栏「⚡ AI 任务」，路由前缀 `/agent`；`modules.yaml` 需 `ai_agent.enabled: true`
  （真实 `modules.yaml` 不入库，模板 `modules.yaml.example` 已同步；`DEFAULT_PUBLIC_MODULES` 已加，普通用户可见）
- **文件划分**（刻意拆细，避免再出现千行单文件）：
  | 文件 | 职责 |
  |---|---|
  | `registry.py` | 能力注册表：连接器注册与查询，对外只暴露 5 个函数 |
  | `store.py` | 任务持久化：建表 / 状态机 / CRUD |
  | `planner.py` | 意图路由与计划生成（**全模块只有这里调 LLM**） |
  | `executor.py` | 原地执行器（目前只有图表这一条分支） |
  | `routes.py` | 只做参数校验与响应封装，业务全下沉 |
  | `connectors/*.py` | 每个业务模块一个文件，声明能力与执行方式 |
  | `static/ai_agent/agent.js` + `components/` | 主控制器 + 计划卡 / 任务列表两个纯渲染组件 |
- **能力注册表**：共 21 项（工具箱 9 / 办公 11 / 图表 1，导航从 `nav_items` 表动态生成）
  - 执行方式三态：`inplace`（后端直接跑完）/ `jump`（带参跳转）/ `link`（只给链接）
  - 新增一个业务模块只需：写一个连接器文件 + 在 `connectors/__init__.py` 注册一行
- **意图路由**：两级（粗分类模块 → 精筛能力）；**关键词能命中就跳过第一次 LLM 调用**
  - 实测：「把图片压缩到 60% 质量」16.7s（关键词命中，仅 1 次推理）；「画各月销售额柱状图」13.2s
- **已实测通过**：图表原地执行全链路 —— 规划 18.4s + 执行 18.5s，产出 PNG 26KB，可正常访问
- ⚠️ **踩坑**：任务表必须叫 `agent_tasks`，**不能叫 `ai_tasks`** —— 库里已有一张同名的 ai_center 任务表
  （字段是 task_type/progress/book_id），`CREATE TABLE IF NOT EXISTS` 遇到同名表会静默跳过，
  接着所有查询都报 `no column named query`。已在 `store.py` 注释里写明。

**第一版没做的事（诚实清单）**：
- 跳转过去后**参数不会自动填充** —— 工具箱 `?tool=xxx`、办公 `?tab=xxx` 的 URL 参数目前没人读（即 P2 任务 7）
- 导航连接器还是纯链接（深链见 P2 任务 6）
- 任务产物没有过期清理机制
- 工具箱只覆盖 9 个高频能力，不是全量 35 个（避免前后端两份配置不同步，见该文件顶部注释）

---

### 3. 工具箱注册表改造：装饰器注册 + 按领域拆分
- **工作量**：中
- **涉及文件**：`modules/toolbox/handlers.py`（1180 行，`handler_map` 手工维护 35 项）
- **为什么排在首页集成之前**：否则后续为 AI 新增的每个工具，都长在一个会持续恶化的结构上。改完反而更省事。
- **做法**：`@register_tool("image_compress")` 装饰器自动注册；按领域拆 `handlers/image.py`、`handlers/pdf.py`、`handlers/text.py`；`dispatch()` 只保留查表逻辑，函数体不动。
- **风险**：低（纯结构重组，不改任何 handler 内部逻辑）。
- **完成标志**：`handler_map` 硬编码字典消失，新增一个工具只需写函数 + 加装饰器。

### 4. ~~首页统一入口（方案 A：意图路由 + 计划卡 + 带参跳转）~~ —— 已改路线，见上方 `ai_agent`
- **状态**：❌ 不再执行。改为独立的 `ai_agent` 模块，首页问答保持原样不动。
- 以下内容仅作历史记录留存：
- **工作量**：中
- **依赖**：任务 1、2
- **涉及文件**：`modules/homepage/routes.py`（新增路由）、`modules/homepage/templates/homepage/index.html`、新增前端脚本
- **要做**：
  - 在「混合标注 / 严格依据资料」之外，独立增加「任务执行」开关（**两个维度，不做成同一行互斥按钮**）。
  - 后端新增意图路由接口：两级路由（先粗分问答/工具/办公/图表，再在子类里精筛），每级只给模型 5~10 个候选，**禁止全量塞 46 个能力**。
  - 命中后生成计划卡片预览 → 带参数跳转到对应模块 → 自动切工具/切 tab/填表单。
  - 工具结果用**卡片式回执**（计划 + 状态 + 下载链接），不要塞进纯文本对话气泡。
- **不做**：原地执行（见 P2 任务 9）、AI 意图分类进入默认问答链路。
- **关键约束**：`llm_generate` 同步阻塞 + `n_ctx=4096`，意图分类必须是用户主动触发，且每次注入的工具清单要有上限。
- **完成标志**：首页输入一句话 → 出计划卡 → 跳转后目标工具已自动选好、参数已填好。

### 5. ✅ AI 图表原地执行试点 —— 已完成（落在 `ai_agent/executor.py`）
- **实现**：`_run_chart()` 复用 `modules/chart/ai_chart.py` 的 `build_chart_prompt / parse_chart_spec / render_spec`，
  产物落 `static/ai_agent/out/`（已在 .gitignore），前端在计划卡里直接渲染 PNG。
- **实测**：规划 18.4s + 执行 18.5s，出图 26KB，URL 可访问。
- **下一步**：若要把工具箱也做成原地执行，照 `_run_chart` 加一个分支即可（前提是打通 P2 任务 9 的后端执行管道）。

### 5b. ✅ 办公异步执行 + 进度轮询 + 文件选择 UI —— 已完成（四象限的最后一块）
- **新增执行模式 `EXEC_ASYNC`**：`inplace` 之外，慢任务（办公自动化）走后台线程 + 前端轮询。
- **新增文件**：
  - `modules/ai_agent/progress.py` —— 文件化进度（`temp/ai_agent_tasks/<id>/progress.json`），
    刻意不落 DB，避免 SQLite 多线程写竞争。
  - `modules/ai_agent/office_runner.py` —— 11 个办公能力 handler，复用 `office_tools.service`
    （该层本就是路径驱动的）。统一约定：工作区文件复制进 `work/`，控制用 Excel 自动识别。
- **链路**：确认 → `run_async` 起线程（显式带 `user_id`/`task_id`）→ 产物收进 `out/` →
  前端轮询 `/agent/api/tasks/<id>/progress` → 完成后经授权路由下载。
- **多用户（公司级每人独立）已验**：alice 的工作区文件 bob 拿不到（`run_async` 直接拒），
  产物与下载路由一律先校验任务归属。
- **⚠️ 部署注意**：`Excel 转 PDF` / `单元格替换` 依赖 `win32com`（Windows + 已装 Office）。
  容器化到 Linux 需换 LibreOffice headless 后端 —— 决策已隔离在 `office_runner`，换后端只动那一个文件。
- **踩坑**：`office_tools.service` 的 `batch_create_folders` 第三个参数 `target_dirs` 是
  **目录列表**（`for d in target_dirs`），传单个字符串会被按字符迭代，静默什么都不建。已改为 `[work]`。

---

## P2 · 能力扩展

### 6. 网址导航升级为「深链」
- **工作量**：低
- **涉及文件**：`modules/nav_hub/routes.py`、`nav_items` 表
- **做法**：`ALTER TABLE nav_items` 增加 `url_template` / `params`(JSON) / `example` 三列（SQLite 向后兼容，现有数据不动）。AI 只负责从用户的话里抽参数拼 URL，吐出一个可直接点的链接。
- **为什么排这么前**：性价比最高——不用碰 DOM、不用托管登录态、模型负担极小，却能覆盖大量"去某个系统带条件查/导出"的需求。
- **明确不做**：服务端通用 RPA（见任务 12）。

### 7. 工具箱带参跳转接回首页
- **工作量**：中
- **依赖**：任务 3、4
- **做法**：首页计划卡 → 跳 `/tools` → 自动选中工具 + 填参数；带文件需求时提示从工作区取用。
- **完成标志**：首页说"把图片压到 60% 转 PNG"，跳转后工具已自动就绪。

### 8. 工具描述标准化 → 启用 function calling
- **工作量**：中
- **涉及文件**：`qa.py` 的 `_generate_llama_cpp()`（补 `tools=` 参数）、`_generate_ollama()`（body 补 `tools` 字段）、`ai_assistant.js` 的 `buildToolList()`
- **前提认知**：**底层早已支持**，只是代码没用。不用换模型、不用加依赖。
- **价值**：摆脱"靠 prompt 求模型吐合法 JSON"，输出由 schema 约束，准确率明显提升。
- **风险**：`n_ctx=4096`，tools schema 全量注入会撑爆上下文 → 必须配合每轮动态注入子集（复用任务 4 的两级路由）。

### 9. 后端执行层：工作区 → temp → dispatch
- **工作量**：中 ~ 高
- **涉及文件**：新增 `modules/toolbox/executor.py`，复用已有 `workspace.py` 持久文件池与 `handlers.dispatch()`
- **价值**：打通后才能真正原地执行工具箱任务；也是多步编排的前提。
- **为什么不在 P1**：投入大、收益要等 P2 编排落地才显现。**建议等首页集成跑通、确认用户真的高频使用后再投**。

### 10. L2 多步编排（一次输出 `steps[]`，后端串行执行）
- **工作量**：中
- **依赖**：任务 9
- **做法**：让模型一次性输出步骤数组（含 `depends_on`），后端按依赖串行跑，某步失败即停并回报已完成部分。
- **为什么优先于 ReAct**：只有一次推理，不受上下文窗口挤压、不会死循环、天然适配"加工流水线"类需求，性价比远高于自主循环。

---

## P3 · 远期 / 需谨慎评估

### 11. L3 自主循环（ReAct）
- **工作量**：高
- **依赖**：任务 9、10，以及 `n_ctx` 提升
- **限制**：只允许只读/幂等工具，最多 3 步，每步结果实时推给前端。**本地 7B/9B 模型的可靠性天花板必须事先接受。**

### 12. 特定流程的定制 RPA 脚本（非通用网页 Agent）
- **工作量**：按流程计
- **前提**：先把通用需求都用深链（任务 6）吃掉；剩下非做不可的高频流程，才针对**具体两三个**写死 Playwright 脚本。
- **明确不做**：通用网页自主导航。`n_ctx=4096` 装不下网页上下文，且模型规格不足以支撑可靠决策。

### 13. 清理与依赖补齐（非阻塞）
- 工作区 `_trash` 过期清理
- 根目录遗留诊断文件（`_flask_*.log`、`_server*.log`、`_theme_shots/`、`_verify_shots/` 等）——**需你确认后再删，不自行处理**
- 可选依赖：`rembg`（去背景）、Tesseract OCR（环境变量 `TESSERACT_CMD`）

---

## 已核实：不需要重做的一项

- **「config_center 全量重建静默清除经验帖向量」**：已在代码中修复。`modules/config_center/routes.py:640` 的 `_rebuild_worker()` 带 `reset_index_first` 分支，清空索引后会调用 experience_hub 的 `reindex_all_posts()` 补回 `exp_` 命名空间，注释与逻辑齐备。**本清单未纳入此项。**

---

## 硬约束备忘（写代码时必须记住）

| 约束 | 影响 |
|---|---|
| `n_ctx=4096`（`qa.py:165`） | 所有 prompt 必须做预算控制，禁止全量注入工具清单 |
| `llm_generate` 同步阻塞、timeout 120s | 编排是串行推理，必须有超时与中断设计 |
| 工具执行层在前端 DOM / 内存态 | 办公自动化（`OTState.uploadedFiles`）无法后端接管，只能跳转执行 |
| `TOOLBOX_CONFIG`、`OTState` 是 `const` 不挂 window | 跨文件引用必须裸标识符或加 `typeof` 守卫 |
| 改 `.py` 必须重启；改模板无需重启 | `TEMPLATES_AUTO_RELOAD=True` 已开 |
| 删文件一律 `rm -f` 单文件，**禁止 `git rm -r`** | 历史事故教训 |
