# AI 问答功能测试报告与改进建议

- 测试时间：2026-08-29
- 测试对象：`homepage` 模块「AI 智能问答」（首页全局问答）+ 相关 AI 链路
- 测试方式：对已在运行的实例（`:5005`）做真实 HTTP 端到端测试，含未登录拦截、登录、两种思维模式、边界输入、多轮追问
- 环境：Python 3.9（anaconda）/ Flask / llama_cpp 0.3.34 / 模型 `qwen2.5-7b-instruct-q4_k_m.gguf`（4.4G）/ 当前 `ai_mode: full`、`backend: llama_cpp`、`chat_thinking_mode: divergent`

> **接口路径提示**：首页问答蓝图前缀为 `/home`（`module_info.route_prefix`），真实接口是
> `POST /home/api/chat`、`POST /home/api/chat/clear`、页面 `GET /home/`。
> 前端用 `url_for("homepage.api_chat")` 生成，路径正确；**直接调 `/api/chat` 会 404**。

---

## 一、测试用例与结果

| # | 用例 | 期望 | 实际 | 耗时 | 结论 |
|---|------|------|------|------|------|
| 1 | 未登录调 `/home/api/chat` | 401 未登录 | **200 并正常生成回答** | 23.5s | ❌ 鉴权未拦截 |
| 1b | 对照：未登录调 `/api/account/change-name` | 401 | 401 `need_login:true` | 0.2s | ✅ 鉴权机制本身正常 |
| 2 | 管理员登录（`admin` + 初始密码） | 200 | 200 `ok` | 2.6s | ✅ |
| 3 | 访问首页 `/home/` | 200 | 200 | 2.1s | ✅ |
| 4 | 发散：一句话介绍量子计算 | 简洁中文回答 | 200，但**开头大段内心独白** | 15.9s | ⚠️ 输出污染 |
| 5 | 发散：Python 装饰器举例 | 简洁专业回答 | 200，但**含"让我构思一下回答：1.…"** | 45.1s | ⚠️ 输出污染 + 慢 |
| 6 | 保守：诺奖（库外内容） | 命中不到则明确提示 | 200「未在现有资料中找到…」 | 4.6s | ✅ 降级文案清晰 |
| 7 | 保守：图书馆里有哪些书 | 应命中已有 21 本书 | 200「未在现有资料中找到…」 | 3.5s | ❌ 索引缺失致恒不命中 |
| 8 | 空问题 | 400 | 400「问题不能为空」 | 2.1s | ✅ |
| 9 | 超长问题（2001 字） | 400 | 400「问题过长（最多2000字符）」 | 2.1s | ✅ |
| 10 | 发散：追问"它和经典计算最大区别？" | 结合上文作答 | **模型答"没有提供上下文，不知道'它'指什么"** | 60.5s | ❌ 多轮上下文失效 |

---

## 二、缺陷清单（按严重度）

### P0-1 思考过程泄漏，答案被推理独白污染

- 现象：发散模式 3 次回答均以模型内心独白开头，例如
  > "用户要求用一句话介绍量子计算。我需要简洁专业地用中文回答。…我需要用一句话来概括。量子计算是一种…"
  > "The user is reporting '测试未登录'… I need to respond appropriately…"（中英混杂）
- 根因：`modules/ai_center/internal/qa.py:337` 的 `_strip_think()` 只剥离 `<think>…</think>` 标签。
  而 qwen2.5-instruct 并未使用标签，而是**直接用自然语言输出推理**，正则完全失效。
- 影响：用户看到冗长啰嗦的独白，专业感尽失；与 SYSTEM_PROMPT「不要输出任何思考过程」的约定直接冲突。
- 修复建议：
  1. `qa.py` 增加后处理：剥离开头"用户…/我…/让我…/首先需要…"式独白段（命中即截断到第一个正式段落）；
  2. 推理侧用 `stop` 词表（如 `"\n\n用户："`、`"让我构思"`）提前截断；
  3. 若走 `create_chat_completion`，检查是否可显式关闭思考；
  4. 保留 `_strip_think` 作为兜底，但**不要只依赖它**。

### P0-2 多轮对话上下文完全失效（"伪多轮"）

- 现象：第 10 轮追问"它和经典计算最大的区别是什么？"，模型回复
  > "用户问的是'它和经典计算最大的区别是什么？'，但没有提供具体的上下文…我不知道'它'指的是什么。"
- 根因（代码事实）：
  - `qa.py:392` 发散分支 `answer = llm_generate(question, …)` —— **`history` 参数从头到尾未被使用**；
  - `qa.py:350 llm_generate()` 各后端（`_generate_llama_cpp/ollama/dashscope/transformers`）只构造
    `[{"role":"system"}, {"role":"user"}]` **两轮 messages**，没有任何历史；
  - 保守分支同样只把当前 question 拼进 prompt。
- 于是前端渲染历史气泡、服务端 `session` 存了 20 轮（`homepage/routes.py:39-53`），
  但**从不喂给模型** —— 用户以为有记忆，实际每轮都是无状态单轮。
- 修复建议：`llm_generate()` 增加 `history` 参数，把 `history` 展开为多轮 messages
  （llama_cpp 的 `create_chat_completion` 原生支持）；按 `n_ctx=4096` 做长度截断，
  建议保留最近 6–8 轮，并对超长历史按 token 数而非轮数裁剪。

### P0-3 保守模式当前 100% 不可用，且是"静默失败"

- 现象：任何保守模式提问（包括库内内容"图书馆里有哪些书"）都返回「未在现有资料中找到相关内容」。
- 根因（实测文件系统）：
  - `modules/ai_center/vector_data/` 下**只有 `index_light.faiss` + `index_light.meta.json` + `index_full.meta.json`**，
    **`index_full.faiss` 不存在**；
  - 但 `index_full.meta.json` 却记录 `book_count: 21, chunk_count: 452, built_at: 2026-08-29T11:45`
    （说明今天重建过索引，marker 写了，faiss 文件却没留下）；
  - 当前 `ai_mode=full` → `vector_store.py:132 get_index()` 找不到文件 → 新建**空索引**
    （`idx.ntotal == 0`）→ `query()` 在 line 248 直接 `return []` → 恒定"未找到"。
- 坏在"静默"：用户会以为图书馆里没这本书，实际是索引文件缺失。
- 修复建议：
  1. `query()` 前先做 `is_mode_ready()` 校验，未就绪时触发 `ensure_mode_ready()` 懒重建，
     并给前端明确提示「索引重建中，请稍候再试」，**而不是"未找到内容"**；
  2. 排查 `rebuild_all()` 为何 marker 写入成功而 faiss 文件缺失（写 marker 时机 /
     `_save_index` 失败未中断 / 磁盘或路径问题）——`vector_store.py:497-550`；
  3. 建议 `rebuild_all` 在 faiss 落盘成功后再写 marker，避免"marker 说有、实际没有"的状态错位。

### P1-4 未登录可调用问答接口（鉴权未生效）

- 现象：未带任何 Cookie 的 `POST /home/api/chat` 返回 **200 并生成完整回答**（用例 1）；
  对照 `POST /api/account/change-name` 正确返回 401（用例 1b）—— **鉴权机制本身是好的**。
- 磁盘代码 `modules/homepage/routes.py:70-73` 明确有 `@login_required`
  （`@bp.route` → `@login_required` → `@module_exception_guard`，login_required 在外层，逻辑正确）。
- 推断：**运行中的服务进程仍在跑旧代码**。`modules/homepage/routes.py` 修改时间为今日 10:05，
  而项目约定"Flask 非 debug，改 `routes.py` 后必须手动重启"。
- 建议：**重启服务后复验**；若重启后仍未拦截，再排查 homepage 蓝图的模块加载 / 装饰器失效。
  未授权调用会绕过登录直接消耗算力，优先级按安全项处理。

### P1-5 并发推理锁 5 秒即放弃，多用户会互相踢掉

- `qa.py:366`：`if not _infer_lock.acquire(timeout=5): return None`
- 实测单次推理 15.9s / 45.1s / 60.5s，都远超 5s。
  → 第二个用户在 5 秒内提问会被**直接判失败**，返回"（模型未响应，请检查 AI 后端是否已加载）"，
  误导用户以为是后端挂了。前端 `send()` 里 `sendBtn.disabled` 只能防同一页面连点，
  防不住多标签页 / 多用户。
- 建议：改为排队等待（acquire timeout 提到与单次推理同量级，如 120s，或直接阻塞），
  并在等待超时时返回「当前排队人数较多，请稍候」而非"模型未响应"。

### P2-6 全局问答不返回、也不展示引用来源

- `qa.py:398-425` 保守分支检索到 `hits` 并拼进 `ctx`，但返回时 `sources: []`（恒空）；
  首页前端也未渲染来源。
- 对比：图书详情页 `book_detail.html:567-570` 有「📌 参考片段」展示 —— 两者不一致。
- 建议：`global_chat` 回传 sources（书名 / 片段 / 相似度），首页前端在气泡下方展示引用，
  提升答案可追溯性与可信度。

### P2-7 图书问答 sources 字段与前端期望不匹配（参考片段恒空白）

- `qa.py:448` 返回 `sources: [{"book_id", "score"}]`（**没有 text**）；
  而 `book_detail.html:569` 渲染 `s.text` → 参考片段永远显示空白。
- 建议：后端补 `text`（`h["content"]` 已有，取前 120–200 字即可）。

### P2-8 首次请求冷启动 >20s

- 用例 1 首个请求读超时（20s 未返回），后续请求均 2s 级。
- 推测为首次请求触发 `torch / sentence_transformers` 等重型依赖导入。
- 建议：启动时（或首个请求前）预热导入 / 预加载模型，避免第一个用户承担冷启动；
  前端对首请求给"正在初始化 AI 引擎"提示。

### P2-9 light 模式门限不一致

- `ai_interface.py:117 ask_book_question()` 要求 `ai_mode == "full"`；
  但 `global_ai_chat()`（line 133）只排除 `off` → light 模式下首页问答**仍会真实调用 LLM**。
- 首页模板 `index.html:228` 只按 `ai.mode == 'off'` 隐藏聊天框 → light 下仍渲染输入框，
  而 hero 标签却写着"轻量：仅摘要/检索"，语义矛盾。
- 建议：统一门限（全局问答也要求 full，或明确允许并在 UI 上如实标注）。

### P2-10 思维模式选择不持久化、与后端配置不同步

- `index.html:277` 默认高亮"发散"，不读取后端 `chat_thinking_mode`；切换后刷新即回退。
- 建议：初始值由后端 `ai` 上下文下发，并把选择存入 `session`/`localStorage`。

---

## 三、性能与体验改进建议

| 问题 | 现状 | 建议 |
|------|------|------|
| 响应慢 | 发散问答 15.9s / 45.1s / 60.5s（CPU 推理 7B Q4，`max_tokens=600`） | 降低 `max_tokens`（如 256–384）；提供"快速/深度"档位 |
| 无流式输出 | 全程"⏳ 思考中…"，最长干等 60s，无反馈 | 改为 SSE/流式逐字输出，首字即可见 |
| 全 CPU 推理 | `embedder.py:62` 硬编码 `device="cpu"`；llama_cpp `n_threads=cpu//2` | 有 GPU 时启用 `n_gpu_layers`；embedding 也走 GPU |
| 模型偏重 | 4.4G 7B 模型，CPU 上吞吐低 | 库内已有 `LFM2.5-2.6B-Q4_K_M.gguf`（1.6G），可作默认/快速模型 |
| 冷启动 | 首请求 >20s | 启动预热 + 前端初始化提示 |

---

## 四、表现良好的部分（回归时请勿破坏）

- 输入校验到位：空问题、超长（>2000）均正确返回 400，文案清晰。
- 保守模式无命中的降级文案友好，明确引导"切发散 / 去配置中心重建索引"。
- 限流 30 次/分钟/IP（`routes.py:24-33`）、AI 日志埋点完整。
- `vector_store` 的父子块归并、按模式（light/full）隔离索引、marker 指纹校验设计扎实。
- 模块降级设计（`ai_interface` 只依赖 core、AI 挂了不影响图书功能）有效性已验证。
- 前端 `appendMsg` 做了 `<` 转义，基础 XSS 防护到位（建议补全 `&`、`>`、`"`）。

---

## 五、建议修复顺序

1. **重启服务并复验登录拦截**（P1-4，安全项，成本最低）
2. **修多轮上下文**（P0-2，功能完整性，改动集中在 `llm_generate` + 各后端 messages）
3. **清思考过程**（P0-1，直接决定观感，后处理即可止血）
4. **修 full 索引缺失 + 静默失败提示**（P0-3，让保守模式真正可用）
5. **并发锁改排队 + 流式输出**（P1-5 / 性能，决定多用户可用性）
6. 补齐 sources 回传与展示、字段对齐、模式持久化等 P2 项
