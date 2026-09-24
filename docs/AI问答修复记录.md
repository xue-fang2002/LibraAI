# AI 问答修复记录（2026-08-29）

> 本次为**实修复 + 实测验证**，不是建议清单。每一项都列出了根因、改动、验证结果。
> 验证环境：`ai_mode=full`、`backend=llama_cpp`（qwen2.5-7b-instruct-q4_k_m）、CPU 推理。

---

## 一、重建索引完全用不了（核心故障）

用户反馈「重建索引功能根本就用不了」。排查出 **4 层原因叠加**，逐层修复。

### 原因 1：嵌入模型根本加载不了（决定性根因）

- 现象：`vector_data/` 下有 `index_full.meta.json`（记 21 本书/452 分块），却**没有 `index_full.faiss`**。
- 报错：`❌ 向量模型[full]加载失败: __init__() missing 1 required positional argument: 'word_embedding_dimension'`
- 根因：`models/bge-large-zh-v1.5/` 缺 **`1_Pooling/` 子目录**，而 `modules.json` 引用了它。
  Pooling 层拿不到 `word_embedding_dimension` 就构造失败 → `get_model('full')` 返回 None
  → `encode()` 返回 None → 所有向量化被跳过 → faiss 永远写不出来。
  （对照 `bge-small-zh-v1.5` 有 `1_Pooling`，所以 light 模式一直正常。）
- 修复：新增 `models/bge-large-zh-v1.5/1_Pooling/config.json`（`word_embedding_dimension: 1024`）。
- 验证：`✅ 向量模型[full]加载完成，维度: 1024`；`encode` 输出 `(1, 1024)`，与 `FULL_EMBEDDING_DIM=1024` 一致。

### 原因 2：重建是同步阻塞的，且每本书都要跑一次 7B 摘要

- `api_ai_rebuild_index` 遍历所有书同步调用 `submit_rebuild_task`，而该函数内部串行做
  解析 → 分块 → 向量化 → **LLM 摘要**。7B 模型在 CPU 上每本几十秒，21 本要十几分钟，
  HTTP 请求必然超时，表现为「点了没反应 / 用不了」。
- 修复：
  - 重建改为**后台线程**执行，接口立即返回；
  - 新增 `GET /config/api/ai/rebuild_status` 进度查询；
  - `submit_rebuild_task(book_id, with_summary=False)`：批量重建跳过 LLM 摘要
    （摘要可由图书详情页「重新生成摘要」单独触发）。
- 验证：触发耗时 **2.16 秒**（原来会阻塞到超时）。

### 原因 3：失败被静默吞掉，还留下「假就绪」的 marker

- `rebuild_all()` 无论成功与否都写 marker，造成
  **「marker 说有 21 本书，faiss 里一个向量都没有」** → `is_mode_ready()` 返回 True
  → 检索恒为空且永不重建；而 `submit_rebuild_task` 向量化失败时仍返回成功。
- 修复：
  - `rebuild_all()`：`success > 0` 才写 marker，否则**删除 marker** 并打印失败书目；
  - `store()`：落盘失败返回 0（原来是"写入了但文件没落地"的错位）；
  - `submit_rebuild_task()`：向量化/写入失败一律 `return None`，并打印完整堆栈（原来只 print str(e)）；
  - `ai_interface.trigger_rebuild_for_book()`：区分失败与跳过（`skip:` 前缀）。
- 验证：重建结果 `ok=4, skipped=2, failed=0`，失败数从 1 降到 **0**。

### 原因 4：孤儿分块让重建去向量化上万条无效文本

- `books` 表只有 6 本，但 `document_chunks` 有 20 个 book_id，**16 个是孤儿**（书已删除、分块残留），
  其中 `b_9767a4ba` 一个就占 16206 个分块。
- 修复：
  - 新增 `vector_store.purge_orphan_chunks()`，清理 `document_chunks` / `ai_parent_chunks` /
    `ai_vector_mappings` 中书已不存在的记录；`rebuild_all` 与重建 API 开头均调用；
  - `_index_fingerprint()` 改为 `INNER JOIN books`，只统计有效书。
- 验证：分块从 **16322 行（20 个 book_id / 16 个孤儿）→ 25 行 / 0 孤儿**；
  marker 准确记录 `book_count=4, chunk_count=25`。

### 原因 5（修复过程中发现）：PDF 数学符号让编码整批失败

- 报错：`TextEncodeInput must be Union[TextInputSequence, ...]`
- 根因：PDF 抽取文本含 **Unicode 代理项**（如数学字母 `\ud835`）与 Private Use Area 字符
  （Symbol 字体映射，如 `\uf0ce`），Rust tokenizers 直接抛异常，实测 10 个分块里 3 个因此失败。
- 修复（`embedder.py`）：
  - 新增 `_sanitize_text()`：剔除代理项 + 丢弃无法 utf-8 编码的字符；
  - `encode()` 输入统一清洗；
  - 新增 `_encode_one_by_one()` 降级：批量失败时逐条编码，失败条目填零向量以保持数量对齐
    （零向量内积为 0，不会误命中）。
- 验证：清洗后 10 条**全部编码成功** `(10, 1024)`，零失败；该书从"整本失败"变为正常索引。

### 重建索引最终结果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 触发响应 | 阻塞到超时 | **2.16 秒**立即返回 |
| 成功 / 跳过 / 失败 | 3 / 2 / **1** | **4 / 2 / 0** |
| `index_full.faiss` | 不存在 | **352 KB** |
| 失败是否可见 | 静默吞掉 | 进度接口返回错误书目 |

---

## 二、AI 问答本体

| 问题 | 修复 | 验证 |
|------|------|------|
| **多轮上下文失效**（history 从未传给模型） | 新增 `_build_messages(prompt, history)`（限 6 轮、单条 1500 字）；四个后端 `generate` 加 `history` 参数；`llm_generate` 透传；`global_chat` 两处调用补 `history` | 追问「它和经典计算最大区别？」→ 正确回答「量子计算最大的区别在于…」，**PASS** |
| **思考过程泄漏**（答案开头大段独白） | `SYSTEM_PROMPT` 去掉负面指令（负面指令反被模型复述，是独白来源）；llama_cpp 加 `stop=["\n用户：","\nUser:"]`；新增 `_strip_musing()` 按句剥离开头独白（限前 8 句，清理后为空则回退原文） | 答案直接是「量子计算是利用量子比特的叠加与纠缠…」，**PASS** |
| **并发锁 5 秒即放弃** | `_infer_lock.acquire(timeout=5)` → `180`（单次推理实测 15–60s，5s 会把并发请求误报成「模型未响应」） | 代码级修复；串行与排队均正常 |
| **索引未就绪时静默失败** | 保守检索前校验 `is_mode_ready()`，未就绪则触发后台重建并提示「索引尚未就绪（原因），已后台重建」 | 逻辑已生效，不再误报「未找到」 |
| **全局问答不返回来源** | 新增 `_pack_sources()`（book_id / score / text，且过滤空片段）；`routes.api_chat` 透传；首页前端渲染「📌 参考片段」 | 返回 3 条真实来源（如《人工智能之机器学习…》） |
| **保守模式资料过长导致生成返回空** | 每条资料截断 600 字符（避免超出 n_ctx=4096） | 保守模式现能正常作答 |

> 注：修复初期曾把「让我构思」加入 stop 词，结果保守模式下整段回答被截断成空
> （长 prompt 更容易触发模型先写"构思"），已移除，并补了「清理后为空则回退」的保护。

---

## 三、顺带修掉的一个既有 Bug

**图书详情页的 AI 提问完全不可用**：后端 `api_ai_ask` 用 `request.get_json()` 读参数，
而前端 `book_detail.html` 发的是 `FormData` → 永远读到空 → 固定返回 `400 缺少图书ID`。

- 修复：`get_json(silent=True) or {}` 为空时回退读 `request.form.to_dict()`。
- 验证：`code: 200`，答案「这本书主要讲述了个体心理学理论，包括生活意义、心灵与肉体…」，
  参考片段 1 条且文本非空（此前恒为空白，因为 `ask_question_about_book` 的 sources 缺 `text` 字段，已一并补上）。

---

## 四、实测通过项（回归勿破坏）

- 未登录调用 `/home/api/chat` → `code 401 请先登录后查看`
- 空问题 / 超长（>2000 字）→ 正确 400
- 保守模式命中资料并附来源；图书问答正常作答
- 首页渲染、登录、模块降级均正常

---

## 四之二、异步任务没有记录 / 看不到成败（补充修复）

用户反馈：点「重建向量索引」后，页面下方「异步任务」列表没有任何记录，也不知道哪些成功哪些失败。

- **根因 1（我引入的）**：新增的后台重建只把进度放在进程内全局变量 `_REBUILD_STATE`，**没写 `ai_tasks` 表**。
  页面列表读的是 `get_ai_tasks()`，所以永远看不到；刷新页面或换会话后进度就丢了。
- **根因 2（既有 Bug，更隐蔽）**：`ai_tasks` 表**缺 `book_id` 和 `message` 两列**，
  而 `create_ai_task()` 要写 `book_id`、`update_ai_task()` 要写 `message` → 两个函数一直插入失败
  （异常被 `logger.exception` 吞掉，表面无感）。
  原因：库里的 `ai_tasks` 是**旧版本建的表**（有 `payload/total/error/user_id/started_at/finished_at`），
  而当前 `db_base.py` 的 `CREATE TABLE IF NOT EXISTS` 定义的是另一套列；表已存在就不会重建，
  于是两者长期不匹配。**这解释了为什么任务记录停在 8 月 14 日之后再无新增。**
- 修复：
  1. `db_base` 建表后用 `_add_column_if_missing` 双向补齐
     （`book_id`/`message`/`payload`/`total`/`error`/`user_id`/`started_at`/`finished_at`）；
  2. 重建触发时 `create_ai_task(task_id, "rebuild_index")`；每本书处理完
     `update_ai_task(progress=百分比, message=实时计数)`；结束时写入状态 `done`/`failed`
     与结果明细（成功/跳过/失败 + 错误书目）。
- 验证：`ai_tasks` 记录数 7 → 8，任务记录显示
  `状态 done / 进度 100% / 信息"成功 4 本，跳过 2 本，失败 0 本" / 结果明细 JSON`。

---

## 四之三、「回答牛头不对马嘴」是索引的原因吗？—— 是，实测确认

用三类问题做对照实验（同一环境）：

| 场景 | 问题 | 结果 |
|------|------|------|
| A 书中有正文 | 集成学习中随机森林的基本原理？ | **答得准确专业**（"Bagging 的一种应用，有放回抽样…"），命中真实正文 score≈0.49 |
| B 只有元数据、无正文的书 | 《自卑与超越》核心观点？ | **答案看似合理但属编造**；来源只有元数据（书名=、SS号=）和**无关的 AdaBoost 片段** |
| C 通用知识（不依赖索引） | 用一句话解释光合作用？ | **答对**（9.1 秒），模型本身能力正常 |

**结论**：是索引（内容）的原因，不是模型能力问题。
- A 证明：只要索引里有真正文，回答就准确；
- C 证明：模型本身没问题（7B 能答对通用知识）；
- B 证明：索引里没正文时，模型会用先验知识"编得像真的"，而来源其实是元数据 + 跨书凑出的无关片段。

**索引的真实状况**：库里 6 本书，仅 4 本被索引、共 25 个分块。
- 《08_1集成学习》17 块 / 16214 字符（有正文）
- 《01_机器学习概述》3 块、《05_Logistic_Softmax》4 块（有正文）
- **《自卑与超越》只有 1 块，内容是 PDF 元数据**（`[General Information] 书名=… 作者=… SS号=…`）
  —— 扫描件 PDF 无文本层，正文抽不出来。

**根因链条**：扫描件 PDF 无文本层 → 正文抽取失败 → 索引里只剩元数据 →
可检索内容太少 → 检索只能拿不相关片段凑数 → 模型据此编造 → 表现为「牛头不对马嘴」。

**可选改进（按性价比）**：
1. **相关性阈值**：命中低于阈值（如 0.35）即判"资料中没有"，不再拿无关片段凑数、也不让模型硬答。（改动小、见效快）
2. **过滤元数据噪声**：识别 `[General Information]` / `书名=` / `SS号=` 这类 PDF 元数据分块，不入库。
3. **标记无正文的书**：解析后正文过短就在 AI 配置页标「无文本层（疑似扫描件）」，提示需要 OCR。
4. **OCR（治本）**：扫描件需 OCR 才有正文，需引入 OCR 依赖，成本较高。

---

## 五、未做（性能优化，非功能缺陷，按需再做）

| 项 | 现状 | 建议 |
|----|------|------|
| 无流式输出 | 单次 15–60 秒干等「思考中…」 | 改 SSE 逐字输出，首字即可见 |
| 首请求冷启动 | 首个请求 >20s（重型依赖首次导入） | 启动预热 + 前端「正在初始化」提示 |
| 全 CPU 推理 | `embedder.py` 硬编码 `device="cpu"` | 有 GPU 时启用 `n_gpu_layers` |
| 模型偏重 | 7B Q4 单次 15–60s | 库内 `LFM2.5-2.6B-Q4_K_M.gguf`（1.6G）可作「快速」档 |

> 每次改动后都需**重启服务**（Flask 非 debug）。

---

# 六、2026-08-31 五项改进（用户指定顺序，逐项完成并验证）

执行原则：一次做一项，验证通过再做下一项。

## 6.1 重建改增量 + 双按钮（已完成）

- `books` 表新增 `indexed_hash`（上次成功索引的文件哈希）。
- 新增 `get_books_need_indexing(force=False)`（只返回未索引 / 上次失败 / 文件哈希变化的书）
  与 `mark_book_indexed(book_id, file_hash)`。
- **配套迁移**（关键）：加列后把已 `done` 且有分块的书的 `indexed_hash` 初始化为 `file_hash`，
  否则第一次点增量仍会把全部书重做一遍。
- `api_ai_rebuild_index` 支持 `force`（form/json 双兼容）；前端拆成
  「🔄 索引新书（增量）」与「♻️ 全量重建」两个按钮。

| 验证项 | 结果 |
|--------|------|
| 增量挑选 | 10 本中只挑 6 本（跳过 4 本已 done 未变更） |
| 触发耗时 | **2.31 秒**（原来会阻塞到超时） |
| 执行结果 | `ok=4, skipped=2, failed=0` |
| 幂等性 | 再次触发自动降为 2 本 |

## 6.2 OCR 缓存（已完成，含 OCR 接入）

> 前置：原 `parser.py` 从未调用 OCR，故本项把「接入 OCR + 做缓存」一起完成。

- 新建 `ocr_cache(file_hash PK, text, pages, engine)` 表 + `get_ocr_cache` / `save_ocr_cache`。
- `parser.py` 改为两级：**文本层优先 → 判定为扫描件才 OCR**。
  判定依据 `_looks_scanned()`：正文 < 500 字符，或只剩 `[General Information]` / `SS号=` 等元数据。
- OCR 复用已有的 `deps.get_ocr_engine()`（rapidocr）+ `deps.get_fitz()`（150 DPI 渲染），
  限页数走 `modules.yaml` 的 `ai_center.ocr_max_pages`，**默认 20**。

| 验证项 | 结果 |
|--------|------|
| OCR 触发 | 《自卑与超越》文本层仅 757 字符元数据 → 自动 OCR 20 页，得 **13035 字符** |
| 缓存命中 | 第 2 次调用 **0.21 秒**（首次 557.5 秒），加速约 **2595 倍** |
| 端到端 | 清掉 indexed_hash 后增量重建（3 本，2.3 分钟）：该书 **1 块/757 字符 → 13 块/14352 字符** 真正文 |

**性能警示**：首次 OCR 实测约 **28 秒/页**（20 页 ≈ 9.3 分钟），
比早期单页抽样（9–15 秒）慢，疑似服务进程占用 CPU 所致。
因此**限页数与缓存都不可省**；若嫌慢可把 `ocr_max_pages` 调小。

## 6.3 当前索引概况（第 2 项完成后）

10 本书，8 本已索引：

- 机器学习课件 3 本：3 / 4 / 17 块
- QIWIP 制度文件 4 份：95 / 18 / 19 / 46 块（共 13 万+ 字符）
- 自卑与超越：13 块 / 14352 字符（OCR 所得）
- 未索引 2 本：商学院一期管理培训课件（failed）、诊所看病单（pending），两者 `file_hash` 为空，文件疑似缺失

## 6.4 文档档位配置（公共/一般/精确）（已完成）

档位决定两件事：**OCR 扫描页数** 与 **回答严格度（相关性阈值）**。

- `config.py` 新增：
  - `QA_MODES = ("public", "general", "precise")`；
  - `_DEFAULT_OCR_PAGES`（public/general=12、precise=0 即整本全扫）；
  - `_DEFAULT_MIN_SCORE`（public=0.30 / general=0.40 / precise=0.45）；
  - `get_qa_mode_by_category(cat1, cat2)`：先匹配「cat1/cat2」再匹配「cat1」再退回全局默认；
  - `get_book_qa_mode(book)`：优先级 **单本 `books.qa_mode` > 分类配置 > 全局默认**；
  - `get_min_score(qa_mode)` / `get_ocr_max_pages_for_mode(qa_mode)`。
- `modules.yaml` 新增 `ai_center.qa_mode` 块：
  - `default: general`；
  - `ocr_max_pages`: public/general=12、precise=0；
  - `min_score`: public=0.30、general=0.40、precise=0.45；
  - `by_category`: `"文件资料/制度文件": precise`（QIWIP 4 份制度文件自动精确档，整本 OCR + 最严阈值）。

## 6.5 拒绝机制（相关性阈值）（已完成并实测）

**目标**：命中分数低于该书档位阈值即判「资料中没有」，宁可说找不到，也绝不拿无关片段凑数让模型编造。

- 全局问答 `global_chat`：保守检索后 `hits = _filter_by_score(hits, get_min_score(None))`（默认档 general 0.40）。
- 图书级问答 `ask_question_about_book`：**补全**按该书档位过滤——
  取 `get_book_qa_mode(book)` → `get_min_score(mode)` → `_filter_by_score` → 命中为空则返回
  「本书已索引的资料中未找到与问题相关的内容。可换个问法，或确认索引是否覆盖了相关章节」。
- 阈值依据：`bge-large` 归一化内积下，相关命中约 0.47~0.55、无关命中约 0.36~0.42，
  general 取 0.40 留安全余量；精确档更严（0.45），公共档更松（0.30）。

| 验证项（2026-08-31，full 模式，b_072b3cb4《自卑与超越》） | 命中 score | 阈值 | 结果 |
|------|------|------|------|
| 无关：「番茄炒蛋做米其林」 | 0.32 | 0.40 | **拒绝**（资料中没有）✓ |
| 弱相关：「这本书的作者是谁」 | 0.36 | 0.40 | **拒绝**（宁可说找不到，不答错）✓ |
| 真相关：「阿德勒 个体心理学 自卑」 | 0.50 | 0.40 | **通过** → 生成答案，来源《自卑与超越》P9（score 0.5185）✓ |

> 实测确认：阈值 0.40 能正确拦掉跨书/弱相关凑数，又放行真正命中，达到「答错的代价高于答不出」的预期。
