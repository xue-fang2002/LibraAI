# AI 问答剩余问题修复指南（逐步操作）

> 适用状态：重启后「未登录可调用」已修复（P1-4 关闭）。
> 以下为重启后仍然存在的问题，按**建议执行顺序**排列，每步含改动位置、可直接粘贴的代码、验证方法。
> 除第 0 步外均为 `modules/ai_center/internal/qa.py` 一个文件的局部改动（第 5 步涉及 2 个额外文件），互不耦合，可单独做、单独验。

---

## 第 0 步｜不动代码：重建索引，先让「保守思维」能用

**现象**：`modules/ai_center/vector_data/` 下有 `index_full.meta.json`（记录 21 本书 / 452 个分块）却没有 `index_full.faiss`。
当前 `ai_mode=full` → `get_index()` 找不到文件就建**空索引**（`ntotal=0`）→ `query()` 恒返回 `[]` → 任何保守模式提问都回「未在现有资料中找到相关内容」。

**操作**：配置中心 → AI 中心 → **重建索引**（21 本书 452 分块，bge-large 在 CPU 上编码，需要一些时间）。

**验证（关键）**：
```bash
ls -la modules/ai_center/vector_data/
# 必须看到 index_full.faiss 出现（当前只有 index_full.meta.json）
```

**若重建后 `index_full.faiss` 仍未生成**，说明重建流程本身有问题（`rebuild_all()` 在 faiss 落盘前就写了 marker，marker 说"有"但实际没有）。
此时看服务端日志有无 `❌ 后台懒重建索引[full] 失败`，并按第 3 步给静默失败加提示，避免继续误导用户。

---

## 第 1 步｜修多轮上下文（P0，收益最大）

**问题**：`global_chat` 发散分支 `llm_generate(question, …)` **从未使用 `history`**，各后端也只构造 `[system, user]` 两轮 messages。
session 存了 20 轮历史，却一次都没喂给模型 —— 追问必然答非所问。

**改动 1** — 在 `qa.py` 的 `SYSTEM_PROMPT` 定义之后新增：

```python
_MAX_HISTORY_ROUNDS = 6  # 最多带入 6 轮历史，防 n_ctx 溢出


def _build_messages(prompt, history=None):
    """把对话历史拼成多轮 messages；无历史时退化为单轮。"""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in (history or [])[-(_MAX_HISTORY_ROUNDS * 2):]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:1500]})
    msgs.append({"role": "user", "content": prompt})
    return msgs
```

**改动 2** — 四个后端 `generate` 函数签名加 `history=None`，并用 `_build_messages` 替换原 messages 构造：

| 函数 | 改法 |
|------|------|
| `_generate_llama_cpp` | `def _generate_llama_cpp(llm, prompt, max_tokens, temperature, history=None):` 并把 `messages = [{"role":"system",…},{"role":"user",…}]` 换成 `messages = _build_messages(prompt, history)` |
| `_generate_ollama` | 签名加 `history=None`；`"messages": _build_messages(prompt, history),` |
| `_generate_dashscope` | 同上 |
| `_generate_transformers` | 签名加 `history=None`；`messages = _build_messages(prompt, history)`（后面 `apply_chat_template` 不用改） |

**改动 3** — `llm_generate()` 增加透传：

```python
def llm_generate(prompt, max_tokens=512, temperature=0.3, timeout_seconds=120, history=None):
    ...
    generator = _BACKENDS[backend]["generate"]
    return _strip_musing(_strip_think(generator(llm, prompt, max_tokens, temperature, history)))
```

**改动 4** — `global_chat` 两处调用补 `history`：

```python
answer = llm_generate(question, max_tokens=600, temperature=0.7, history=history)   # 发散分支
answer = llm_generate(prompt,   max_tokens=600, temperature=0.2, history=history)   # 保守分支
```

**验证**：连续问「量子计算是什么？」→「它和经典计算最大的区别是什么？」。
修复前第二问会答「没有提供具体上下文，不知道'它'指什么」；修复后应能正确指代量子计算。

---

## 第 2 步｜清掉思考过程独白（P0）

**根因**：现有 `SYSTEM_PROMPT` 里的「请直接给出最终答案，不要输出任何思考过程、分析步骤…」是**负面指令**，反而诱导模型先复述一遍指令
（实测模型输出「我需要直接给出最终答案，不要输出思考过程…」）。而 `_strip_think()` 只剥 `<think>` 标签，对这种自然语言独白完全无效。

**改动 1** — 简化 `SYSTEM_PROMPT`（去掉负面表述，只留正面指令）：

```python
SYSTEM_PROMPT = (
    "你是图书管理系统的智能助手。"
    "用中文简洁、专业地直接回答用户问题，不要寒暄。"
)
```

**改动 2** — llama_cpp 推理加 `stop`，从源头截断：

```python
resp = llm.create_chat_completion(
    messages=messages,
    max_tokens=max_tokens,
    temperature=temperature,
    stop=["\n用户：", "\nUser:", "让我构思"],
    stream=False,
)
```

**改动 3** — 后处理兜底（放在 `_strip_think` 之后）：

```python
_MUSING_HEAD = re.compile(
    r"^\s*(用户|使用者)(要求|询问|问|说|提到)|"
    r"^\s*(我|让我们?)(需要|应当|应该|先)|"
    r"^\s*让我(构思|想想|先)|^\s*首先[,，]?(我|让我们?)"
)


def _strip_musing(text):
    """剥离答案开头的复述/构思独白，只保留正式回答。"""
    if not text:
        return text
    import re as _re
    head = _re.compile(
        r"^\s*(用户|使用者)(要求|询问|问|说|提到)|"
        r"^\s*(我|让我们?)(需要|应当|应该|先)|"
        r"^\s*让我(构思|想想|先)|^\s*首先[,，]?(我|让我们?)"
    )
    paras = [p for p in text.split("\n") if p.strip()]
    start = 0
    for i, p in enumerate(paras[:4]):
        if head.match(p.strip()):
            start = i + 1
        else:
            break
    return "\n".join(paras[start:]).strip() if start else text.strip()
```

> 保守性原则：只在**开头**匹配到独白模式时才剥离，且只剥前 4 段内，避免误伤正常回答。

**验证**：问「用一句话介绍量子计算」，返回应直接是答案正文，不再出现「用户要求…我需要…让我构思…」。

---

## 第 3 步｜索引未就绪时给明确提示，别再说「未找到」（P0 静默失败）

**问题**：索引缺失时用户看到「未在现有资料中找到相关内容」，会以为图书馆里没这本书。

**改动** — `global_chat` 保守分支，检索前加就绪校验：

```python
    ctx = ""
    hits = []
    try:
        from . import vector_store, embedder
        ready, reason = vector_store.is_mode_ready(None)
        if not ready:
            vector_store.ensure_mode_ready(None, lazy=True)
            return {
                "answer": f"资料索引尚未就绪（{reason}），已在后台开始重建，请稍候再试。",
                "mode": mode,
                "sources": [],
            }
        qv = embedder.encode_one(question)
        if qv is not None:
            hits = vector_store.query(None, qv, top_k=5) or []
            if hits:
                ctx = "\n\n".join(f"[资料 {i+1}] {h['content']}" for i, h in enumerate(hits))
    except Exception as e:
        print(f"⚠️ 保守模式检索失败: {e}")
```

**验证**：把 `index_full.faiss` 临时改名 → 保守模式提问应提示「索引尚未就绪…已后台重建」，而不是「未找到」。

---

## 第 4 步｜并发推理锁改排队（P1）

**问题**：`qa.py:366` `_infer_lock.acquire(timeout=5)`，而实测单次推理 15.9s / 45.1s / 60.5s。
5 秒内来的第二个请求直接返回 `None` → 前端显示「模型未响应，请检查 AI 后端是否已加载」，实为排队超时被误报。

**改动**：

```python
    if not _infer_lock.acquire(timeout=180):   # 原为 5
        print("⚠️ LLM推理排队超时")
        return None
```

**验证**：开两个窗口同一秒各发一问，第二个应排队等第一个完成后正常作答，而不是秒回「模型未响应」。

---

## 第 5 步｜回传并展示引用来源（P2）

**问题 A**：全局问答 `sources` 恒为空，前端也不展示；而图书详情页 `book_qa` 有「📌 参考片段」，两者不一致。

**改动 A1** — `qa.py` 加打包函数，并让保守分支返回真实 sources：

```python
def _pack_sources(hits):
    return [
        {
            "book_id": h.get("book_id"),
            "score": round(float(h.get("score", 0.0)), 4),
            "text": (h.get("content") or "")[:200],
        }
        for h in (hits or [])
    ]
```
保守分支成功返回处改为：`return {"answer": answer.strip(), "mode": mode, "sources": _pack_sources(hits)}`

**改动 A2** — `modules/homepage/routes.py` 的 `api_chat` 透传：

```python
return ok({"answer": answer, "sources": result.get("sources", [])})
```

**改动 A3** — `modules/homepage/templates/homepage/index.html` 的 `send()` 内：

```js
if (data.code === 200) {
    const d = data.data || {};
    appendMsg('ai', d.answer || '（无响应内容）');
    if (d.sources && d.sources.length) {
        const s = d.sources.map((x, i) => `[${i + 1}] ${String(x.text || '').slice(0, 80)}…`).join('\n');
        appendMsg('ai', '📌 参考片段：\n' + s);
    }
}
```
（`appendMsg` 内部已做 `<` 转义，安全。）

**问题 B**：`ask_question_about_book` 返回 `{book_id, score}` 但**没有 `text`**，而 `book_detail.html:569` 渲染 `s.text` → 参考片段恒为空白。

**改动 B** — `qa.py:448`：

```python
"sources": [
    {"book_id": h["book_id"], "score": h["score"], "text": (h.get("content") or "")[:200]}
    for h in hits
],
```

**验证**：重建索引后，保守模式问库内问题 → 答案下方应出现「📌 参考片段：[1] …」；图书详情页提问也应看到真实片段而非空白。

---

## 可选第 6 步｜性能与体验（不急，但影响观感）

| 项 | 现状 | 建议 |
|----|------|------|
| 无流式输出 | 全程「⏳ 思考中…」，最长干等 60s | 改 SSE 逐字输出，首字即可见 |
| 首请求冷启动 | 首个请求 >20s（重型依赖首次导入） | 启动时预热导入；前端给「正在初始化 AI 引擎」提示 |
| 全 CPU 推理 | `embedder.py:62` 硬编码 `device="cpu"` | 有 GPU 时启用 `n_gpu_layers`，embedding 一并走 GPU |
| 模型偏重 | 7B Q4 单次 15–60s | 库内已有 `LFM2.5-2.6B-Q4_K_M.gguf`（1.6G）可作「快速」档 |

---

## 建议执行顺序

1. **第 0 步**（运维，零风险）→ 让保守模式先能用
2. **第 1 步** 多轮上下文 → 决定「能不能连续聊」
3. **第 2 步** 清思考过程 → 决定「答案干不干净」
4. **第 3 步** 索引提示 + **第 4 步** 并发锁 → 消除误导与误报
5. **第 5 步** 来源回传 → 提升可信度
6. 第 6 步按需

**每步做完都重启服务**（Flask 非 debug，改 `routes.py` / `qa.py` 后必须手动重启才生效）。
