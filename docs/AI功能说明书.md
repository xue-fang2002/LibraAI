# AI 功能说明书

> 适用范围：本 Flask 图书管理系统的全部 AI 能力（问答、摘要、向量检索、索引管理、模型/后端切换）。
> 面向读者：系统管理员（配置与运维）、普通用户（使用问答/摘要）、以及需要在业务模块里接入 AI 的开发者。
> 配套文档：`docs/给AI写新模块的提示文档.md`（开发者写模块时必读）。

---

## 1. 概述

系统内置 AI 智能能力，由 `modules/ai_center` 提供，框架自动接入各业务模块（首页、图书馆），并通过**配置中心**集中管理。

- **首页（默认落地页 `/home`）**：全局 AI 问答，可自由提问。
- **图书详情页（`/book/detail/<id>`）**：针对单本书的「内容摘要」与「基于本书问答」。
- **配置中心 → AI 配置（`/config/ai`，仅管理员）**：切换 AI 模式、推理后端、模型路径、安全锁、重建/压缩索引、查看依赖与日志。

所有 AI 能力都做了**故障隔离**：AI 模块不可用时，图书检索、上传等基础功能仍正常，只是 AI 相关 UI 自动隐藏。

---

## 2. AI 功能清单

| 功能 | 入口位置 | 依赖的系统模式 | 说明 |
|------|----------|----------------|------|
| 全局 AI 问答 | 首页 `/home` | 非 `off` | 自由提问，支持「思维模式」切换，保留会话历史 |
| 思维模式（发散/保守） | 首页问答界面 | 任意 | 发散＝自由发挥；保守＝仅依据已索引资料 |
| 图书内容问答 | 图书详情页「AI 问答」 | `full` | 针对单本书提问，返回答案 + 参考片段 |
| 图书摘要 | 图书详情页「内容摘要」 | `light` / `full` | 自动生成，可手动「重新生成摘要/标签」 |
| 向量检索与索引 | 后台 | `full` | 图书被切分→向量化→存入 FAISS，供问答检索 |
| 索引重建 / 压缩 | 配置中心 AI 配置 | `light` / `full` | 重建全部图书索引、清理孤儿/失效向量 |
| 涉密安全锁 | 配置中心 AI 配置 | 全部 | 开启后禁止 AI 读取图书正文 |

---

## 3. AI 系统模式（总开关）

`ai_mode` 决定"哪些 AI 功能可用"，取值：

- **`off`** —— AI 整体关闭。首页显示「AI 已关闭，请前往系统配置启用」；图书详情页的 AI 区块隐藏。
- **`light`（轻量）** —— 仅摘要 / 标签 / 检索。不提供深度问答（单书问答会提示需 full 模式）。
- **`full`（完整）** —— 问答 + 深度分析 + 向量检索，全部可用。

> ⚠️ 注意：**系统模式 ≠ 思维模式**。系统模式控制"功能开不开"，思维模式只控制"回答怎么答"（见第 4 节）。两者互不影响。

---

## 4. 思维模式（divergent / conservative）

仅影响回答方式，**不影响功能可用性**。

- **🌀 发散（divergent）**：模型用自己的知识自由回答，适合开放性、创意类问题。
- **📚 保守（conservative）**：先检索已索引的图书资料，仅依据资料回答；若资料库里找不到相关内容，会提示「切换到发散模式自由提问」或「先重建索引」。

两种设置方式：
1. **每次临时切换**：首页问答界面底部有「🌀 发散 / 📚 保守」按钮，只对当前这条问题生效。
2. **全局默认值**：`config/modules.yaml` 的 `ai_center.chat_thinking_mode` 字段（默认 `divergent`），对所有未显式指定 mode 的问答生效。

---

## 5. 推理后端与「换模型」操作

AI 的"大脑"叫**推理后端**，支持四种，可**热切换、无需重启服务**（切换后首次推理才会真正加载新模型）：

| 后端 | 说明 | 所需依赖 | 需填写的参数 |
|------|------|----------|--------------|
| `llama_cpp` | 本地 GGUF 模型（当前默认） | `llama-cpp-python` + 模型文件 | 模型路径（如 `models/qwen2.5-7b-instruct-q4_k_m.gguf`） |
| `ollama` | 本地 Ollama HTTP 服务 | `requests` + `openai` | 服务地址（默认 `http://localhost:11434`）+ 模型名（如 `qwen2.5:7b`） |
| `dashscope` | 阿里云百炼云端 API | `requests` + `openai` | API Key + 模型名（如 `qwen-plus`） |
| `transformers` | HuggingFace 原生加载 | `torch` + `sentence_transformers` + `faiss` | 模型路径 |

### 5.1 给管理员的操作步骤（换模型 / 换后端）

1. 用管理员账号登录（默认账号 `admin`，初始密码见首次启动的控制台输出 / `config/.init_admin_pwd`）。
2. 进入 **系统配置 → AI 配置**（路径 `/config/ai`）。
3. **选择 AI 模式**：`off` / `light` / `full`。
4. **选择推理后端**：`llama_cpp` / `ollama` / `dashscope` / `transformers`。
5. **填写对应参数**（按上表）：
   - llama_cpp → 模型路径
   - ollama → 服务地址 + 模型名
   - dashscope → API Key + 模型名
   - transformers → 模型路径
6. （可选）设置**嵌入模型路径** `light_emb` / `full_emb`，其向量维度必须与模式匹配（light=512，full=1024），否则检索会错乱。
7. （可选）开启 **涉密安全锁**，禁止 AI 读取图书正文。
8. 点保存 → **立即生效（热加载），无需重启**。
9. 若需要的是「彻底启用/禁用整个 AI 模块」（注意是模块启停，不是模式切换），改完开关后**必须重启服务**才生效。

### 5.2 配置中心 AI 配置页还提供

- **重建全部图书索引**：异步遍历所有图书，重新切分 + 向量化（换模型/换嵌入模型后必做）。
- **压缩索引**：清理孤儿向量、失效向量，释放空间。
- **依赖探测与状态**：显示 `torch` / `faiss` / `llama_cpp` / `openai` 等是否安装、模型文件是否存在，未就绪会提示先补依赖。
- **AI 任务队列 + 操作日志**：查看重建进度与历史操作。
- **配置文件原始内容**：可直接查看 `modules.yaml` / `app.yaml` 当前值。

---

## 6. 配置文件（config/modules.yaml）

AI 配置集中在 `modules:` → `ai_center:` 块，示例（当前系统实际值）：

```yaml
modules:
  ai_center:
    enabled: true
    sort_order: 0
    ai_mode: light                 # off / light / full
    security_lock: false           # 涉密安全锁
    backend: llama_cpp             # off/llama_cpp/ollama/dashscope/transformers
    chat_thinking_mode: divergent  # divergent / conservative
    model_paths:
      light_emb: models/bge-small-zh-v1.5      # 轻量模式嵌入模型
      full_emb:  models/bge-large-zh-v1.5      # 完整模式嵌入模型
      local_llm:  models/LFM2.5-2.6B-Q4_K_M.gguf  # 本地大模型
    ollama:
      url: http://localhost:11434
      model: qwen2.5:7b
    dashscope:
      url: https://dashscope.aliyuncs.com/compatible-mode/v1
      model: qwen-plus
      api_key: sk-xxx
    transformers:
      model_path: models/
    llama_cpp:
      model_path: models/qwen2.5-7b-instruct-q4_k_m.gguf
```

> 通过配置中心 UI 修改以上字段会写回此文件并**立即刷新内存缓存**（缓存 TTL 5 分钟，热切换接口已强制刷新）。手动改文件后若想立即生效，重启服务即可。

---

## 7. 依赖与模型准备

不同后端需要的 Python 依赖不同，缺失时 UI 会提示，不会崩溃：

- `llama_cpp`：`llama-cpp-python`
- `ollama` / `dashscope`：`requests` + `openai`
- `transformers`：`torch` + `sentence_transformers` + `faiss`

本机 `models/` 目录已预置模型（可替换）：
- 嵌入模型：`bge-small-zh-v1.5`（light）、`bge-large-zh-v1.5`（full）
- 本地大模型：`LFM2.5-2.6B-Q4_K_M.gguf`（llama_cpp 默认）

---

## 8. 常见问题 / 排错

- **首页显示「AI 已关闭」**：去「系统配置 → AI 配置」把模式切到 `light` 或 `full`。
- **换后端/换模型后没反应**：后端切换是热加载，但模型在**首次推理**时才真正加载；同时确认对应依赖已安装、模型路径/文件确实存在（看 AI 配置页的依赖探测）。
- **单书问答提示「本书尚未建立索引」**：去「系统配置 → AI 配置 → 重建索引」；换过嵌入模型后也必须重建。
- **保守模式答非所问或说没资料**：该模式只依据已索引内容，请先重建索引，或切到「发散」自由提问。
- **改了 AI 模块 enable/disable 不生效**：模块的启用/禁用开关需要**重启服务**；而模式、后端、路径、安全锁的改动都是热加载、无需重启。
- **刚手动改了 modules.yaml 但 AI 没读到新值**：配置有 5 分钟缓存。通过配置中心 UI 改会强制刷新；手动改文件后重启服务或等 5 分钟即可。

---

## 9. 接口速查（开发者集成用）

业务模块请统一调用 `core.ai_interface`，**不要**直接 import `ai_center.internal.*`。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/home` | GET | 首页 AI 问答界面 |
| `/home/api/chat` | POST | `{question, mode}` → `{answer}` |
| `/home/api/chat/clear` | POST | 清空当前会话历史 |
| `/config/api/ai/summary/<book_id>` | POST | 生成/获取图书摘要 → `{summary}` |
| `/config/api/ai/ask` | POST | `{book_id, question, top_k}` → `{answer, sources}` |
| `/config/api/ai/set_mode` | POST | `{mode}` 热切换 off/light/full |
| `/config/api/ai/set_backend` | POST | `{backend, url, model, api_key, model_path}` 热切换后端 |
| `/config/api/ai/set_model_paths` | POST | `{light_emb, full_emb, local_llm}` 保存模型路径 |
| `/config/api/ai/set_security` | POST | `{enabled}` 涉密安全锁 |
| `/config/api/ai/rebuild_index` | POST | 异步重建全部图书索引 |
| `/config/api/ai/compress_index` | POST | 清理孤儿/失效向量 |
| `/config/api/ai/toggle_module` | POST | `{enabled}` 模块启停（**需重启**） |

**调用示例（业务模块内）：**

```python
from core.ai_interface import global_ai_chat, ask_book_question, generate_book_summary, is_ai_enabled

if is_ai_enabled():
    ok_, ans = global_ai_chat("介绍一下量子计算", mode="divergent")
    ok_, res = ask_book_question(book_id, "这本书的核心观点是什么？", top_k=5)
    ok_, summary = generate_book_summary(book_id, book_text, force=False)
```

---

## 10. 一句话总结

AI = **模式开关（off/light/full）** 决定能用什么，加 **思维开关（发散/保守）** 决定怎么答，加 **推理后端（4 选 1）** 决定用哪个模型；日常配置都在「系统配置 → AI 配置」里热切换，只有模块整体启停才需重启。
