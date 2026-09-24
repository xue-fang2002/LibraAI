"""本地大模型问答模块（支持多后端切换）。

支持的后端：
- llama_cpp: llama-cpp-python 加载 GGUF 模型（当前默认）
- ollama: 本地 Ollama HTTP 服务
- dashscope: 阿里云百炼云端 API
- transformers: HuggingFace Transformers 原生加载

设计原则：
- 业务层仅调用 llm_generate()，不关心底层实现
- 切换后端只需修改配置，无需重启服务
- 各后端独立封装，互不影响
"""
import os
import re
import json
import time
import threading

from . import deps
from .config import get_chat_thinking_mode
from .parser import extract_heading  #抽取章节标题，供来源合并与按目录结构总结
from . import reranker  #二阶重排（cross-encoder），模型缺失/失败时优雅降级

# 全局状态
_llm = None              # 当前后端的 LLM 实例/句柄
_llm_lock = threading.Lock()
_llm_backend_loaded = None  # 当前加载的后端类型（可能与配置中心选的不同：见自动降级）
_llm_config_loaded = None   # 加载该实例时的配置指纹（backend + 模型名 + 地址/密钥）
_infer_lock = threading.Lock()  # 推理串行锁
_infer_waiters = 0  # 正在排队等待推理锁的请求数（仅作排队提示）

def get_infer_queue_info():
    """返回推理队列状态：busy=是否有人正在生成，waiters=正在排队的请求数。"""
    return {"busy": _infer_lock.locked(), "waiters": max(_infer_waiters, 0)}

# ---- P0-2 后端自动降级 ----
# 候选顺序（首选排在最前，由 get_llm 动态拼）。本地重量级后端（llama_cpp / transformers）
# 加载代价高（数十秒起、占内存显存），因此排在服务型后端之后，作为最后的兜底。
# 若不接受本地兜底，直接从本元组中删除对应项即可。
_BACKEND_FALLBACK_ORDER = ("ollama", "dashscope", "vllm", "llama_cpp", "transformers")
_backend_fail_until = {}   # 后端 -> 冷却到期的单调时刻（探活/加载失败后短期内不再尝试）
_preferred_last_try = 0.0  # 降级状态下，上一次尝试首选后端的时间
_BACKEND_COOLDOWN_SEC = 300   # 单个后端失败后的冷却时长
_PREFERRED_RETRY_SEC = 300    # 降级状态下回切首选后端的重试间隔

# 面向最终用户的统一兜底文案。
# 刻意不暴露推理框架 / 模型名 / 服务地址等内部信息；
# 真正的失败原因只打到服务端日志（见 get_llm / llm_generate 中的 ⚠️ 输出）。
MODEL_NO_RESPONSE_MSG = "（模型未响应，请稍后重试或联系管理员）"


# 系统提示词
# 注意：刻意不写「不要输出思考过程/分析步骤」之类的负面指令。
# 实测（qwen2.5-instruct）负面指令反而被模型先复述一遍再作答，
# 导致答案开头出现「我需要直接给出最终答案，不要输出思考过程…」这类独白。
# 改为正面简洁指令，配合 stop 词与后处理清理。
SYSTEM_PROMPT = (
    "你是图书管理系统的智能助手。"
    "用中文简洁、专业地直接回答用户问题，不要寒暄，不要复述问题。"
)

# 多轮对话配置
_MAX_HISTORY_ROUNDS = 6        # 最多带入的历史轮数（1 轮 = user + assistant）
_MAX_HISTORY_CHARS = 1500      # 单条历史最大字符数，防止长文吃满 n_ctx

# ---------------------------------------------------------------------------
# prompt 上下文预算
# ---------------------------------------------------------------------------
# llama_cpp 以 n_ctx=4096 加载本地模型，而 _build_ctx 在命中 ≤2 时每条给到 8000 字，
# 叠加 6 轮 × 1500 字历史后，中文 prompt 轻松超过 1.5 万 token，远超上下文 ——
# 尾部「问题：…」被截断，表现为答非所问或直接空响应。
# 这里按后端上下文容量为「资料 / 历史」分别设上限；云后端容量大，实测不会触发裁剪，
# 行为与改动前一致。任一步取不到后端信息时返回 None = 不限制（保守，不改原有行为）。
_N_CTX_BY_BACKEND = {
    "llama_cpp": 4096,     # 与 _load_llama_cpp 的 n_ctx 保持一致
    "transformers": 4096,
    "ollama": 8192,
    "dashscope": 16384,
    "vllm": 32768,
}
_N_CTX_SAFETY = 256        # system prompt / 模板 / 分词误差的预留
_CHARS_PER_TOKEN = 1.5     # 中文保守估算：1 个 token ≈ 1.5 个汉字
_CTX_BUDGET_RATIO = 0.75   # 资料占比；其余留给历史与问题
_MIN_CHARS_PER_HIT = 200   # 单条资料的最小保留字数


def get_prompt_budget(max_tokens=512):
    """当前后端可用于「资料 + 历史 + 问题」的字符预算。

    返回 None 表示无法判断（此时不做任何裁剪，保持改动前的行为）。
    """
    try:
        # 用实际生效的后端而非配置值：降级后两者的上下文窗口可能不同
        # （例：llama_cpp 4096 → dashscope 16384），预算必须跟着实际走。
        n_ctx = _N_CTX_BY_BACKEND.get(get_active_backend())
    except Exception:
        return None
    if not n_ctx:
        return None
    try:
        usable = int(n_ctx) - int(max_tokens or 0) - _N_CTX_SAFETY
    except Exception:
        return None
    if usable <= 256:
        usable = max(256, int(n_ctx) // 2)
    return int(usable * _CHARS_PER_TOKEN)


def get_history_budget(max_tokens=512):
    """历史可用的字符预算；None 表示不限制。"""
    b = get_prompt_budget(max_tokens=max_tokens)
    if not b:
        return None
    return int(b * (1 - _CTX_BUDGET_RATIO))


def _build_messages(prompt, history=None):
    """把对话历史拼成多轮 messages；无历史时退化为单轮。

    各后端（llama_cpp / ollama / dashscope / transformers）都接受 OpenAI 风格
    messages 列表，统一在此构造，避免各后端各写一套导致历史丢失。

按后端上下文预算裁剪历史，**优先丢弃最早的轮次**
    （最近的对话对当前问题最有用），避免「历史 + 长资料」一起撑爆本地模型 n_ctx。
    云后端预算充足，不会触发裁剪。
    """
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    items = list(history or [])[-(_MAX_HISTORY_ROUNDS * 2):]
    hist_budget = get_history_budget()
    kept = []
    total = 0
    # 从最近一条往回保留，预算用尽即止
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        c = content[:_MAX_HISTORY_CHARS]
        if hist_budget is not None and total + len(c) > hist_budget:
            room = hist_budget - total
            if room > 80:   # 剩下的位置还放得下一小段，就保留该条尾部
                kept.append({"role": role, "content": c[-room:]})
            break
        kept.append({"role": role, "content": c})
        total += len(c)
    kept.reverse()
    msgs.extend(kept)
    msgs.append({"role": "user", "content": prompt})
    return msgs


def unload_llm():
    """卸载当前 LLM 释放内存"""
    global _llm, _llm_backend_loaded, _llm_config_loaded, _preferred_last_try
    with _llm_lock:
        _llm = None
        _llm_backend_loaded = None
        _llm_config_loaded = None
        # 归零让下次加载立即重试首选后端，而不是被降级重试间隔挡住
        _preferred_last_try = 0.0


# ============================================================
# 后端 1: llama_cpp (GGUF 本地模型)
# ============================================================
def _load_llama_cpp():
    """加载 llama-cpp-python 后端"""
    from .config import get_model_paths
    path = get_model_paths()["llm"]
    if not path or not os.path.exists(path):
        return None, "模型文件不存在: " + str(path)

    llama_cpp = deps.get_llama_cpp()
    if llama_cpp is None:
        return None, "llama-cpp-python 未安装"

    try:
        print(f"⏳ [llama_cpp] 加载模型: {path}")
        llm = llama_cpp.Llama(
            model_path=path,
            n_ctx=4096,
            n_threads=max(2, (os.cpu_count() or 4) // 2),
            verbose=False,
        )
        print("✅ [llama_cpp] 模型加载完成")
        return llm, None
    except Exception as e:
        print(f"❌ [llama_cpp] 加载失败: {e}")
        return None, str(e)


def _generate_llama_cpp(llm, prompt, max_tokens, temperature, history=None, stream=False):
    """llama_cpp 推理（支持多轮历史）。stream=True 时返回增量生成器。"""
    messages = _build_messages(prompt, history)
    try:
        if hasattr(llm, "create_chat_completion"):
            if stream:
                def _gen():
                    resp = llm.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        # 只拦截「模型继续替用户编对话」这类串台，不要拦截中文构思词：
                        # 保守模式 prompt 较长时模型更容易先写「让我构思」，会被整段截断成空回答。
                        stop=["\n用户：", "\nUser:"],
                        stream=True,
                    )
                    for chunk in resp:
                        try:
                            c = (chunk.get("choices") or [{}])[0].get("delta") or {}
                            delta = c.get("content") or ""
                        except Exception:
                            delta = ""
                        if delta:
                            yield delta
                return _gen()
            resp = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                # 只拦截「模型继续替用户编对话」这类串台，不要拦截中文构思词：
                # 保守模式 prompt 较长时模型更容易先写「让我构思」，会被整段截断成空回答。
                stop=["\n用户：", "\nUser:"],
                stream=False,
            )
            return resp["choices"][0]["message"]["content"]
        else:
            full_prompt = f"系统：{SYSTEM_PROMPT}\n用户：{prompt}\n助手："
            resp = llm(full_prompt, max_tokens=max_tokens, temperature=temperature, echo=False)
            return resp["choices"][0]["text"]
    except Exception as e:
        print(f"⚠️ [llama_cpp] 推理失败: {e}")
        return None


# ============================================================
# 后端 2: Ollama (本地 HTTP 服务)
# ============================================================
def _load_ollama():
    """检查 Ollama 服务是否可用"""
    from .config import get_backend_config
    import requests
    # 用 get_backend_config("ollama") 而非 get_llm_backend_config()：
    # 后者只在「当前选中后端就是 ollama」时才返回 url/model，
    # 后端自动降级（P0-2）需要在主后端不是 ollama 时也能正确加载它。
    cfg = get_backend_config("ollama")
    base_url = cfg.get("ollama_url", "http://localhost:11434")
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        if resp.status_code == 200:
            return {"type": "ollama", "base_url": base_url, "model": cfg.get("ollama_model", "")}, None
        else:
            return None, f"Ollama 服务异常 (HTTP {resp.status_code})"
    except ImportError:
        return None, "requests 库未安装，请运行: pip install requests"
    except Exception as e:
        return None, f"无法连接 Ollama 服务: {e}"


def _generate_ollama(llm, prompt, max_tokens, temperature, history=None, stream=False):
    """Ollama 推理（支持多轮历史）。stream=True 时返回增量生成器。"""
    import requests, json
    try:
        if stream:
            def _gen():
                resp = requests.post(
                    f"{llm['base_url']}/api/chat",
                    json={
                        "model": llm["model"],
                        "messages": _build_messages(prompt, history),
                        "stream": True,
                        # 关闭模型内部思考链：思考型模型（如 qwen3.5）默认会先生成上千 token
                        # 的内部独白，实测使单次响应从 ~9s 膨胀到 130~216s，并会突破 timeout。
                        # Ollama 对不识别的字段静默忽略，故老版本/非思考模型加此项均为 no-op。
                        "think": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": temperature,
                        }
                    },
                    timeout=180,
                    stream=True,
                )
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    delta = (obj.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if obj.get("done"):
                        break
            return _gen()
        resp = requests.post(
            f"{llm['base_url']}/api/chat",
            json={
                "model": llm["model"],
                "messages": _build_messages(prompt, history),
                "stream": False,
                # 同上：关闭思考链，避免 130~216s 的超长响应（实测降至 ~9s）
                "think": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                }
            },
            timeout=180
        )
        result = resp.json()
        return result.get("message", {}).get("content", "")
    except Exception as e:
        print(f"⚠️ [ollama] 推理失败: {e}")
        return None


# ============================================================
# 后端 3: 阿里云百炼 (DashScope)
# ============================================================
def _load_dashscope():
    """检查 DashScope API Key 是否配置"""
    from .config import get_backend_config
    cfg = get_backend_config("dashscope")
    api_key = cfg.get("dashscope_api_key", "")
    if not api_key:
        return None, "阿里云百炼 API Key 未配置"
    print("✅ [dashscope] API Key 已配置")
    llm = {"type": "dashscope", "api_key": api_key,
           "base_url": cfg.get("dashscope_url", ""),
           "model": cfg.get("dashscope_model", "")}
    # 三态：yaml 里显式配了才下发 enable_thinking；缺省完全不发送，
    # 以兼容会拒绝未知私有字段的 OpenAI 兼容厂商。
    if "dashscope_enable_thinking" in cfg:
        llm["enable_thinking"] = cfg["dashscope_enable_thinking"]
    return llm, None


def _generate_dashscope(llm, prompt, max_tokens, temperature, history=None, stream=False):
    """DashScope 推理（支持多轮历史）。stream=True 时返回增量生成器。"""
    import requests, json
    try:
        if stream:
            def _gen():
                body = {
                    "model": llm["model"],
                    "messages": _build_messages(prompt, history),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                }
                # 思考链开关：仅在 yaml 显式配置时下发，缺省不发送
                # （部分 OpenAI 兼容厂商会拒绝不认识的私有字段，见 _load_dashscope）
                if "enable_thinking" in llm:
                    body["enable_thinking"] = llm["enable_thinking"]
                resp = requests.post(
                    f"{llm['base_url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {llm['api_key']}",
                        "Content-Type": "application/json"
                    },
                    json=body,
                    timeout=120,
                    stream=True,
                )
                for line in resp.iter_lines():
                    if not line:
                        continue
                    s = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not s.startswith("data:"):
                        continue
                    payload = s[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    try:
                        delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    except Exception:
                        delta = ""
                    if delta:
                        yield delta
            return _gen()
        body = {
            "model": llm["model"],
            "messages": _build_messages(prompt, history),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if "enable_thinking" in llm:
            body["enable_thinking"] = llm["enable_thinking"]
        resp = requests.post(
            f"{llm['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm['api_key']}",
                "Content-Type": "application/json"
            },
            json=body,
            timeout=120
        )
        result = resp.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        elif "message" in result:
            print(f"⚠️ [dashscope] API 返回: {result.get('message')}")
            return None
        else:
            print(f"⚠️ [dashscope] 未知响应: {result}")
            return None
    except Exception as e:
        print(f"⚠️ [dashscope] 推理失败: {e}")
        return None


# ============================================================
# 后端 5: vLLM (OpenAI 兼容推理服务)
# ============================================================
def _load_vllm():
    """检查 vLLM 服务配置是否有效（默认无需 API Key）。

    与 dashscope 不同：vLLM 默认不开鉴权，api_key 可为空，因此不能像
    dashscope 那样「key 为空就拒绝加载」，只要求填了 url + model。
    """
    from .config import get_backend_config
    cfg = get_backend_config("vllm")
    base_url = (cfg.get("vllm_url") or "").strip()
    model = (cfg.get("vllm_model") or "").strip()
    if not base_url:
        return None, "vLLM 服务地址未配置"
    if not model:
        return None, "vLLM 模型名未配置"
    llm = {"type": "vllm", "base_url": base_url.rstrip("/"),
           "model": model, "api_key": cfg.get("vllm_api_key") or ""}
    return llm, None

def _generate_vllm(llm, prompt, max_tokens, temperature, history=None, stream=False):
    """vLLM 推理（OpenAI 兼容接口，支持多轮历史）。stream=True 时返回增量生成器。

    协议与 dashscope 完全一致（/chat/completions + SSE），仅 base_url / model / api_key
    不同；api_key 为空时不发送 Authorization 头（vLLM 默认不鉴权）。
    """
    import requests, json
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"
    try:
        if stream:
            def _gen():
                body = {
                    "model": llm["model"],
                    "messages": _build_messages(prompt, history),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                }
                resp = requests.post(
                    f"{llm['base_url']}/chat/completions",
                    headers=headers, json=body, timeout=120, stream=True,
                )
                for line in resp.iter_lines():
                    if not line:
                        continue
                    s = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not s.startswith("data:"):
                        continue
                    payload = s[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    try:
                        delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    except Exception:
                        delta = ""
                    if delta:
                        yield delta
            return _gen()
        body = {
            "model": llm["model"],
            "messages": _build_messages(prompt, history),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = requests.post(
            f"{llm['base_url']}/chat/completions",
            headers=headers, json=body, timeout=120,
        )
        result = resp.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        elif "message" in result:
            print(f"⚠️ [vllm] API 返回: {result.get('message')}")
            return None
        else:
            print(f"⚠️ [vllm] 未知响应: {result}")
            return None
    except Exception as e:
        print(f"⚠️ [vllm] 推理失败: {e}")
        return None

# ============================================================
# 后端 4: Transformers (HuggingFace)
# ============================================================
def _load_transformers():
    """加载 Transformers 模型"""
    from .config import get_model_paths
    path = get_model_paths()["llm"]
    if not path or not os.path.exists(path):
        return None, "模型文件不存在"

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
    except ImportError:
        return None, "transformers 或 torch 未安装，请运行: pip install transformers torch"

    try:
        print(f"⏳ [transformers] 加载模型: {path}")
        tokenizer = AutoTokenizer.from_pretrained(path)
        model_kwargs = {}
        if torch.cuda.is_available():
            model_kwargs["device_map"] = "auto"
            model_kwargs["torch_dtype"] = torch.float16
            model_kwargs["load_in_4bit"] = True
        else:
            model_kwargs["device_map"] = "cpu"
            model_kwargs["torch_dtype"] = torch.float32

        model = AutoModelForCausalLM.from_pretrained(path, **model_kwargs)
        model.eval()
        print("✅ [transformers] 模型加载完成")
        return {"type": "transformers", "model": model, "tokenizer": tokenizer}, None
    except Exception as e:
        print(f"❌ [transformers] 加载失败: {e}")
        return None, str(e)


def _generate_transformers(llm, prompt, max_tokens, temperature, history=None, stream=False):
    """Transformers 推理（支持多轮历史）。stream=True 时返回增量生成器。"""
    import torch
    try:
        tokenizer = llm["tokenizer"]
        model = llm["model"]
        messages = _build_messages(prompt, history)
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        if stream:
            try:
                from transformers import TextIteratorStreamer
            except Exception:
                TextIteratorStreamer = None
            if TextIteratorStreamer is not None:
                from threading import Thread
                streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
                gen_kwargs = dict(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    do_sample=temperature > 0,
                    pad_token_id=tokenizer.eos_token_id,
                    streamer=streamer,
                )

                _err = {"msg": None}

                def _run():
                    try:
                        with torch.no_grad():
                            model.generate(**gen_kwargs)
                    except Exception as e:
                        _err["msg"] = str(e)
                        print(f"⚠️ [transformers] 流式生成线程异常: {e}")
                    finally:
                        #必须显式结束 streamer。
                        # 若 generate 抛异常而这里不 end()，消费侧 `for piece in streamer`
                        # 会永久阻塞 —— 调用方一直持有 _infer_lock 不放，整个 AI 问答瘫痪。
                        try:
                            streamer.end()
                        except Exception:
                            pass

                t = Thread(target=_run, daemon=True)
                t.start()

                def _gen():
                    try:
                        for piece in streamer:
                            if piece:
                                yield piece
                        if _err["msg"]:
                            print(f"⚠️ [transformers] 流式生成失败: {_err['msg']}")
                    finally:
                        # 客户端提前断开时同样要收尾：结束 streamer 并给线程一个
                        # 有限的等待时间，避免它永远占着推理锁与显存。
                        try:
                            streamer.end()
                        except Exception:
                            pass
                        try:
                            t.join(timeout=5)
                        except Exception:
                            pass
                return _gen()
            # TextIteratorStreamer 不可用：退化为整段作为单个增量
            def _gen_full():
                with torch.no_grad():
                    outputs = model.generate(
                        **inputs, max_new_tokens=max_tokens, temperature=temperature,
                        do_sample=temperature > 0, pad_token_id=tokenizer.eos_token_id,
                    )
                generated_ids = outputs[0][inputs.input_ids.shape[1]:]
                out = tokenizer.decode(generated_ids, skip_special_tokens=True)
                if out:
                    yield out
            return _gen_full()
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated_ids = outputs[0][inputs.input_ids.shape[1]:]
        return tokenizer.decode(generated_ids, skip_special_tokens=True)
    except Exception as e:
        print(f"⚠️ [transformers] 推理失败: {e}")
        return None


# ============================================================
# 后端注册表（策略模式）
# ============================================================
_BACKENDS = {
    "llama_cpp": {"load": _load_llama_cpp, "generate": _generate_llama_cpp},
    "ollama": {"load": _load_ollama, "generate": _generate_ollama},
    "dashscope": {"load": _load_dashscope, "generate": _generate_dashscope},
    "vllm": {"load": _load_vllm, "generate": _generate_vllm},
    "transformers": {"load": _load_transformers, "generate": _generate_transformers},
}


def _llm_config_fingerprint(backend):
    """构建「后端 + 模型名 + 服务地址/密钥」的配置指纹。

    原先 get_llm() 的缓存键只有 backend 字符串，导致只改模型名或 ollama 地址时
    缓存不失效 —— 进程会继续拿着旧模型名/旧地址干活，且没有任何报错（静默错误）。
    这里把模型名、服务地址、密钥一并纳入指纹，任一变化都触发重新加载，
    使配置中心保存后即时生效，不必重启 Flask。

    注意：指纹可能包含 API Key，**切勿打印到日志**。
    计算失败时返回 None，表示「不参与判断」，退化为原先只比 backend 的行为。
    """
    try:
        from .config import get_llm_backend_config
        cfg = get_llm_backend_config()
        keys = ("llm_model", "ollama_url", "ollama_model",
                "dashscope_url", "dashscope_model", "dashscope_api_key",
                "dashscope_enable_thinking",
                "vllm_url", "vllm_model", "vllm_api_key")
        return "|".join([str(backend)] + [str(cfg.get(k, "")) for k in keys])
    except Exception:
        return None


def _short_conn_err(e):
    """把 requests 的原始异常压成一句人话（原始堆栈动辄 300 字符，不适合做提示文案）。"""
    s = str(e)
    for key, desc in (("NameResolutionError", "域名无法解析"),
                      ("getaddrinfo failed", "域名无法解析"),
                      ("ConnectTimeout", "连接超时"),
                      ("ReadTimeout", "读取超时"),
                      ("Connection refused", "连接被拒绝"),
                      ("Max retries exceeded", "连接失败")):
        if key in s:
            return desc
    return s[:120]


def probe_backend_connectivity(backend, backend_cfg=None):
    """后端连通性探活：让「填错」在配置中心保存那一刻就暴露，而不是等首次提问才发现。

    返回 (ok: bool, detail: str)。
    只发最小请求（1 个 token、内容 "hi"），**不携带任何图书正文**，无数据外泄风险。

    注意：detail 可能含厂商原始报错，属管理员视角，不要直接展示给普通用户。
    """
    import requests
    cfg = backend_cfg or {}
    try:
        if backend == "ollama":
            base = (cfg.get("url") or "http://localhost:11434").rstrip("/")
            model = cfg.get("model") or "qwen2.5:7b"
            try:
                r = requests.get(f"{base}/api/tags", timeout=5)
            except Exception as e:
                return False, f"无法连接 Ollama 服务（{base}）：{_short_conn_err(e)}"
            if r.status_code != 200:
                return False, f"Ollama 服务异常（HTTP {r.status_code}）"
            try:
                names = [m.get("name", "") for m in (r.json().get("models") or [])]
            except Exception:
                names = []
            if not names:
                return False, "该 Ollama 服务上没有任何模型，请先执行 ollama pull"
            if model not in names:
                return False, f"模型「{model}」不存在（该服务可用：{', '.join(names)}）"
            return True, f"服务可达，模型「{model}」已就绪"

        if backend == "dashscope":
            base = (cfg.get("url") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
            model = cfg.get("model") or "qwen-plus"
            key = (cfg.get("api_key") or "").strip()
            if not key:
                return False, "未填写 API Key"
            try:
                r = requests.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 1, "temperature": 0, "stream": False},
                    timeout=20,
                )
            except Exception as e:
                return False, f"无法连接服务地址（{base}）：{_short_conn_err(e)}"
            if r.status_code == 200:
                return True, f"服务可达，模型「{model}」响应正常"
            snippet = (r.text or "").strip()[:200]
            if r.status_code in (401, 403):
                return False, f"API Key 无效或无权限（HTTP {r.status_code}）"
            if r.status_code == 404:
                return False, "服务地址不正确（HTTP 404）：请确认地址末尾不含 /chat/completions"
            if r.status_code == 429:
                return False, "触发限流或账号余额不足（HTTP 429）"
            return False, f"HTTP {r.status_code}：{snippet}"

        if backend == "vllm":
            base = (cfg.get("url") or "http://localhost:8000/v1").rstrip("/")
            model = cfg.get("model") or ""
            key = (cfg.get("api_key") or "").strip()
            if not model:
                return False, "未填写模型名"
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            try:
                r = requests.post(
                    f"{base}/chat/completions",
                    headers=headers,
                    json={"model": model, "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 1, "temperature": 0, "stream": False},
                    timeout=20,
                )
            except Exception as e:
                return False, f"无法连接服务地址（{base}）：{_short_conn_err(e)}"
            if r.status_code == 200:
                return True, f"服务可达，模型「{model}」响应正常"
            snippet = (r.text or "").strip()[:200]
            if r.status_code == 404:
                return False, "服务地址不正确（HTTP 404）：请确认地址末尾不含 /chat/completions"
            return False, f"HTTP {r.status_code}：{snippet}"

        return True, "该后端无需探活"
    except Exception as e:
        return False, f"探活异常：{e}"


def _candidate_backends(preferred):
    """降级候选顺序：首选在前，其余按「代价低 → 高」排列，并剔除当前环境不可用的后端。"""
    try:
        from .config import get_available_backends
        avail = get_available_backends()
    except Exception:
        avail = list(_BACKENDS)
    rest = [b for b in _BACKEND_FALLBACK_ORDER if b != preferred]
    return [preferred] + [b for b in rest if b in avail]


def _try_load_backend(backend):
    """尝试加载一个后端。返回 (实例, 实际后端名, 错误信息)。

    先走轻量探活再真正加载：本地 GGUF / transformers 模型加载一次可能要数十秒，
    仅靠「加载失败」来判断可用性会让每次提问都付出这个代价。失败即写入冷却表。
    """
    if backend not in _BACKENDS:
        return None, None, f"未知后端类型: {backend}"
    if time.monotonic() < _backend_fail_until.get(backend, 0):
        return None, None, "冷却中（近期失败过）"

    try:
        from .config import get_backend_probe_cfg
        ok, detail = probe_backend_connectivity(backend, get_backend_probe_cfg(backend))
        if not ok:
            _backend_fail_until[backend] = time.monotonic() + _BACKEND_COOLDOWN_SEC
            return None, None, detail
    except Exception as e:
        # 探活自身异常不应阻断真实加载尝试（例如某些环境缺依赖）
        print(f"⚠️ [{backend}] 探活异常，仍尝试加载: {e}")

    try:
        instance, err = _BACKENDS[backend]["load"]()
    except Exception as e:
        instance, err = None, str(e)

    if instance is None:
        _backend_fail_until[backend] = time.monotonic() + _BACKEND_COOLDOWN_SEC
        return None, None, err or "加载失败"
    return instance, backend, None


def get_llm():
    """获取/加载 LLM 实例。

    P0-2 自动降级：主后端不可用时，按候选顺序自动切到其它已配置的后端，
    避免「配置的那一个挂掉 = 整个 AI 功能不可用」（此前只会回一句"模型未响应"）。

    降级状态会被记住并缓存；每隔 _PREFERRED_RETRY_SEC 秒重试一次首选后端，
    一旦恢复便自动切回，不会长期滞留在备用后端。
    """
    global _llm, _llm_backend_loaded, _llm_config_loaded, _preferred_last_try
    from .config import get_llm_backend
    preferred = get_llm_backend()

    with _llm_lock:
        # 1) 已有可用实例且配置未变：直接复用（处于降级态时同样复用）
        if _llm is not None and _llm_backend_loaded in _BACKENDS:
            fp_now = _llm_config_fingerprint(_llm_backend_loaded)
            if fp_now is None or _llm_config_loaded == fp_now:
                if _llm_backend_loaded == preferred:
                    return _llm
                # 降级态：到点才回探首选后端，避免每次提问都付出一轮探测开销
                if time.monotonic() - _preferred_last_try < _PREFERRED_RETRY_SEC:
                    return _llm
                _preferred_last_try = time.monotonic()
                inst, loaded, err = _try_load_backend(preferred)
                if inst is not None:
                    _llm = inst
                    _llm_backend_loaded = loaded
                    _llm_config_loaded = _llm_config_fingerprint(loaded)
                    print(f"✅ 首选后端 [{preferred}] 已恢复，自动切回")
                else:
                    print(f"⚠️ 首选后端 [{preferred}] 仍不可用（{err}），继续降级使用 "
                          f"[{_llm_backend_loaded}]")
                return _llm

        # 2) 清理旧实例后按候选顺序加载
        _llm = None
        _llm_backend_loaded = None
        _llm_config_loaded = None
        _preferred_last_try = time.monotonic()

        errors = []
        for cand in _candidate_backends(preferred):
            inst, loaded, err = _try_load_backend(cand)
            if inst is not None:
                _llm = inst
                _llm_backend_loaded = loaded
                _llm_config_loaded = _llm_config_fingerprint(loaded)
                if loaded != preferred:
                    print(f"⚠️ 后端 [{preferred}] 不可用，已自动降级到 [{loaded}]")
                return _llm
            errors.append(f"{cand}: {err}")

        print("⚠️ 所有后端均不可用（首选：" + str(preferred) + "）| " + " | ".join(errors))
        return None


def get_active_backend():
    """当前**实际生效**的推理后端。

    降级状态下它不等于配置中心选中的后端。前端展示与 llm_generate 选调度函数
    都必须以它为准，否则会用 A 后端的 generate 去驱动 B 后端的实例（必然报错）。
    """
    if _llm_backend_loaded in _BACKENDS:
        return _llm_backend_loaded
    try:
        from .config import get_llm_backend
        return get_llm_backend()
    except Exception:
        return "off"


def get_current_backend_info():
    """获取当前后端状态信息（供前端展示，敏感信息脱敏）"""
    from .config import get_llm_backend, get_llm_backend_config
    backend = get_llm_backend()
    config = get_llm_backend_config()

    safe_config = dict(config)
    api_key = safe_config.get("dashscope_api_key", "")
    if api_key and len(api_key) > 4:
        safe_config["dashscope_api_key"] = api_key[:4] + "****"
        safe_config["has_api_key"] = True
    elif api_key:
        safe_config["dashscope_api_key"] = "****"
        safe_config["has_api_key"] = True
    else:
        safe_config["has_api_key"] = False

    active = get_active_backend()
    info = {
        "backend": backend,
        # 自动降级后与配置值不同；前端据此提示「当前运行在备用后端」
        "active_backend": active,
        "degraded": bool(active and active != backend),
        "loaded": _llm is not None,
        "config": safe_config,
    }

    if backend == "llama_cpp":
        info["available"] = deps.has_llm_dep() and os.path.exists(config.get("llm_model", ""))
    elif backend == "ollama":
        info["available"] = deps.has_requests_dep()
    elif backend == "dashscope":
        info["available"] = bool(config.get("dashscope_api_key", ""))
    elif backend == "transformers":
        info["available"] = deps.has_transformers_dep() and os.path.exists(config.get("llm_model", ""))
    else:
        info["available"] = False

    return info


def _strip_think(text):
    """清除模型可能输出的 <think>...</think> 推理痕迹，保证答案干净。"""
    if not text:
        return text
    import re
    # 处理可能的多种写法：<think> <thinking> 等
    cleaned = re.sub(r"<\s*think[^>]*>.*?<\s*/\s*think\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 去掉残留的不成对标记
    cleaned = re.sub(r"<\s*think[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\s*/\s*think\s*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _strip_musing(text):
    """剥离答案开头的复述/构思独白，只保留正式回答。

    部分 instruct 模型（如 qwen2.5）会把「用户要求…我需要…让我构思…」这类推理
    过程**不带标签**地直接写在答案开头，`_strip_think` 对它无效。
    这里按句子从开头剥离，且只在前若干句内生效，避免误伤正常回答。
    """
    if not text:
        return text
    import re as _re
    head = _re.compile(
        r"^\s*(用户|使用者)(要求|询问|问了|说|提到|想知道|需要)"
        r"|^\s*让我(构思|想想|梳理|考虑)"
        r"|^\s*我(需要|应当|应该|要先|得先)"
        r"|^\s*(The user|User)\s+(is\s+)?(asking|asks|wants|needs|said|says)"
        r"|^\s*(I need to|Let me|I should|I will)\b"
    )
    parts = _re.split(r"(?<=[。！？!?\n])", text)
    i = 0
    while i < len(parts) and i < 8:
        s = parts[i].strip()
        if not s:
            i += 1
            continue
        if head.match(s):
            i += 1
            continue
        break
    if i == 0:
        # 未识别到独白：原样返回，绝不 strip —— 否则会把围栏语言后的换行
        # （```python\n）和表头与分隔行之间的换行（| a |\n| --- |）一起吞掉，
        # 导致前端 hljs 无法识别语言、marked 无法解析表格。
        return text
    # 识别到独白并剥离后：只去掉结果**最前面**的空白（独白与正文之间可能残留），
    # 保留末尾换行——那是代码围栏 / 表格的分隔换行，不能丢。
    return "".join(parts[i:]).lstrip()


# 通用 think 标签：<think> / </think> / <thinking> / </thinking> 及带属性的变体。
#流式分支原先用 seg.find() 硬编码了某个模型的专属闭合标签，
# 标准 </think> 永远匹配不到 → hold 无限堆积、思考之后的正式答案被整段吞掉。
# 改为通用正则后，旧标签同样能被匹配（它也符合 </?think[^>]*>），原模型行为不变。
_THINK_TAG_RE = re.compile(r"</?think[^>]*>", re.IGNORECASE)
# 跨块拼接时保留的尾部字符上限（须大于任何 think 标签长度，防止 hold 无限增长）
_HOLD_TAIL_LIMIT = 64


def _stream_clean(deltas):
    """流式逐块清洗：跨 chunk 处理 <think>...</think> 标签，并尽力剥离开头构思独白。

    输入 deltas 为文本增量迭代器，输出同为文本增量迭代器。仅在开头缓冲一小段以
    一次性判定是否 musing 独白，避免把「让我构思…」这类无标签推理泄漏到回答里；
    正常首句不受影响。思考标签可能跨多个增量块，这里做跨块拼接处理。

      - 标签识别改用通用正则 _THINK_TAG_RE（与非流式 _strip_think 对齐），
        不再依赖单一模型的专属闭合标签，标准 </think> 也能正确闭合。
      - 处于思考状态时只保留尾部碎片用于跨块拼接，不再把整段思考文本攒在 hold 里。
      - 移除原实现里 tail.startswith("/think") 那段失效分支：它是在
        find("<think")（开标签）的结果中找闭合标签，永远命中不了，
        且一旦命中会额外吞掉 7 个字符。
    """
    in_think = False
    hold = ""       # 跨 chunk 未闭合标签的暂存
    prefix = ""     # 开头缓冲（用于一次性判定 musing）
    started = False
    for delta in deltas:
        if not delta:
            continue
        seg = hold + delta
        hold = ""
        pieces = []
        i = 0
        n = len(seg)
        while i < n:
            m = _THINK_TAG_RE.search(seg, i)
            if m is None:
                rest = seg[i:]
                if in_think:
                    # 思考内容要丢弃；只留尾部碎片，供下一块拼出跨块的闭合标签
                    hold += rest
                    if len(hold) > _HOLD_TAIL_LIMIT:
                        hold = hold[-_HOLD_TAIL_LIMIT:]
                else:
                    # 尾部可能是被切断的半个标签（如 "</thi"），先暂存等下一块拼齐，
                    # 否则半个标签会作为正文泄漏给用户
                    cut = rest.rfind("<")
                    frag = rest[cut:].lower() if cut >= 0 else ""
                    if frag and ("<think".startswith(frag) or "</think".startswith(frag)):
                        pieces.append(rest[:cut])
                        hold += rest[cut:]
                    else:
                        pieces.append(rest)
                i = n
                break
            if not in_think:
                pieces.append(seg[i:m.start()])
            # in_think 时，标签之前的内容属于思考过程，直接丢弃
            tag = m.group(0)
            if tag.startswith("</") or tag.endswith("/>"):
                in_think = False        # 闭合标签 / 自闭合标签
            else:
                in_think = True
            i = m.end()
        text = "".join(pieces)
        if not text:
            continue
        if not started:
            prefix += text
            # 缓冲到出现句末或累积足够长，再判定开头 musing，避免误伤正常首句
            if len(prefix) < 80 and not re.search(r"[。！？!?；\n]", prefix):
                continue
            prefix = _strip_musing(prefix)
            if prefix:
                started = True
                yield prefix
            # 若 prefix 被整段剥离（纯 musing），丢弃并继续等待后续
            continue
        yield text
    # 收尾：不足 80 字且无句末的残留前缀，最后再判定一次
    if not started and prefix:
        prefix = _strip_musing(prefix)
        if prefix:
            yield prefix


def llm_generate(prompt, max_tokens=512, temperature=0.3, timeout_seconds=120, history=None, stream=False):
    """调用当前 LLM 后端生成文本。线程安全（串行推理）。

    stream=False（默认）：返回完整文本字符串（或 None），与改造前完全一致，
        其他调用方（summarizer / ask_question_about_book）无需改动。
    stream=True：返回一个增量生成器（yield 文本片段），整个生成期持有推理锁；
        调用方必须迭代该生成器，否则推理不会执行、锁也不会释放。失败时生成器
        不产出任何片段（需在业务层用「空答案」兜底）。

    Args:
        history: 对话历史 [{"role": "user"|"assistant", "content": str}]，
                 传入后各后端构造多轮 messages，实现真正的连续对话。
    """
    llm = get_llm()
    if llm is None:
        return None

    # 必须以「实际加载的后端」为准，而不是配置中心选的后端：
    # 自动降级后两者不同，用错调度函数会直接崩（P0-2）。
    backend = get_active_backend()

    if backend not in _BACKENDS:
        return None

    if not stream:
        # 非流式：整段返回（原逻辑）
        global _infer_waiters
        _infer_waiters += 1
        try:
            _acquired = _infer_lock.acquire(timeout=180)
        finally:
            _infer_waiters -= 1
        if not _acquired:
            print("⚠️ LLM推理排队超时（已有任务运行超过 180s）")
            return None
        try:
            generator = _BACKENDS[backend]["generate"]
            raw = generator(llm, prompt, max_tokens, temperature, history)
            cleaned = _strip_think(raw)
            stripped = _strip_musing(cleaned)
            # 清理后为空说明判断有误（把整段回答当成独白剥掉了），回退到清理前内容
            return stripped or cleaned
        finally:
            _infer_lock.release()

    # 流式：返回生成器，跨整个生成期持有推理锁
    def _gen():
        global _infer_waiters
        _infer_waiters += 1
        try:
            _acquired = _infer_lock.acquire(timeout=180)
        finally:
            _infer_waiters -= 1
        if not _acquired:
            print("⚠️ LLM推理排队超时（已有任务运行超过 180s）")
            return
        try:
            generator = _BACKENDS[backend]["generate"]
            deltas = generator(llm, prompt, max_tokens, temperature, history, stream=True)
            if not hasattr(deltas, "__iter__"):
                # 后端未真正流式：整段清洗后作为单个增量下发，保持接口一致
                raw = deltas
                cleaned = _strip_think(raw)
                stripped = _strip_musing(cleaned)
                if stripped:
                    yield stripped
                return
            yield from _stream_clean(deltas)
        except Exception as e:
            print(f"⚠️ [llm_generate] 流式推理异常: {e}")
            return
        finally:
            _infer_lock.release()
    return _gen()


# ============================================================
# 业务层入口（供 core.ai_interface 调用）
# ============================================================
#多轮追问改写 —— 历史此前只喂给 LLM 生成，检索仍用当前句，
# 导致「那它的第二条呢？」「上面那个呢？」这类追问几乎必然召回失败。
# 仅在「有历史 + 像追问」时调用一次轻量 LLM 把指代/省略补全成独立问句。
_FOLLOWUP_MARKERS = (
    "它", "他", "她", "这", "那", "此", "其", "上述", "上面", "以上", "刚才",
    "前者", "后者", "第二", "第三", "下一条", "上一条", "还有", "再问", "换个",
)


def _looks_like_followup(question):
    """粗略判断是否追问句（含指代/序数词且较短），避免对普通问句做无谓改写。"""
    q = (question or "").strip()
    if not q or len(q) > 40:
        return False
    return any(m in q for m in _FOLLOWUP_MARKERS)


def _rewrite_query(question, history):
    """结合历史把追问改写为独立完整问句，供**检索**使用。

    失败/超时/无历史/不像追问时原样返回，绝不阻断主流程。
    注意：改写结果只用于检索（向量编码、角色直取、分类/章节路由、弱支撑判定），
    展示给用户的仍是原始问题，避免用户看到「被改写过」的措辞。
    """
    if not history or not _looks_like_followup(question):
        return question
    try:
        items = list(history)[-6:]
        lines = []
        for it in items:
            if not isinstance(it, dict):
                continue
            role = it.get("role")
            content = (it.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            lines.append(("用户" if role == "user" else "助手") + "：" + content[:200])
        if not lines:
            return question
        convo = "\n".join(lines)
        prompt = (
            "下面是用户与助手的对话历史，最后是用户的新问题。\n"
            "新问题可能包含指代或省略（如「它」「第二条」「那这个呢」）。\n"
            "请把新问题改写成一个独立完整、脱离上下文也能理解的中文问句，"
            "补全被省略的主体和关键实体。\n"
            "只输出改写后的问句本身，不要解释、不要加任何多余内容。\n\n"
            f"对话历史：\n{convo}\n\n"
            f"新问题：{question}\n\n改写后的问句："
        )
        out = llm_generate(prompt, max_tokens=80, temperature=0.0)
        out = (out or "").strip().strip('"').strip("'")
        if not out or len(out) > 120:
            return question
        return out
    except Exception:
        return question


def _hyde_hypothetical(question):
    """用 LLM 生成一段假设性的「资料文本」，用于 HyDE 检索增强。失败/超时返回空字符串。"""
    try:
        prompt = (
            "你是一个资料检索助手。请根据下面的用户问题，写一段【假设性的】简短资料文本"
            "（2-4 句话，不超过 80 字），描述你认为能回答该问题的资料可能长什么样。"
            "只输出这段假设资料本身，不要解释、不要加引号。\n\n"
            f"问题：{question}\n\n假设资料："
        )
        out = llm_generate(prompt, max_tokens=80, temperature=0.3)
        out = (out or "").strip().strip('"').strip("'")
        if not out or len(out) > 200:
            return ""
        return out
    except Exception:
        return ""


def _encode_query(text, user=None):
    """把检索文本编码为查询向量。

    - 开启 HyDE 且 LLM 可用时：先用 LLM 生成一段假设性答案再嵌入，提升短 query 对长文档的召回；
    - LLM 不可用 / HyDE 关闭 / 生成失败时：回退为原始文本直接嵌入。
    仅查询侧生效，**绝不改动库内向量**，故无需重建索引；任何异常都安全降级为原始查询。
    """
    try:
        from . import embedder
        try:
            from .config import is_hyde_enabled, is_ai_enabled
            if not is_hyde_enabled() or not is_ai_enabled():
                return embedder.encode_one(text)
        except Exception:
            pass
        hyp = _hyde_hypothetical(text)
        if hyp:
            qv = embedder.encode_one(hyp)
            if qv is not None:
                return qv
        return embedder.encode_one(text)
    except Exception:
        try:
            from . import embedder
            return embedder.encode_one(text)
        except Exception:
            return None


#纯问候语识别（配合 _greeting_reply 使用）。
# 做法：按标点/空白切词后，逐个 token 核对问候词表——既兜住「你好，你能做什么」
# 这类组合问候，又保证整句里混入任何实词（如「介绍一下 Python」）就走正常检索。
_GREETING_WORDS = {
    "你好", "您好", "哈喽", "hello", "hi", "嗨", "嗨嗨",
    "在吗", "在么", "在不在", "你是谁", "你叫什么",
    "你能做什么", "你能干什么", "你会什么", "你会啥", "你能干啥", "你会做什么",
    "早上好", "上午好", "中午好", "下午好", "晚上好",
}
_GREETING_SPLIT_RE = re.compile(r"[\s，,。．、~～!！?？；;：:·]+")


def _verify_grounding(answer, sources, question, mode):
    """生成后校验答案是否被来源支撑（groundedness）。

    返回 (ok: bool, note: str)。ok=True 表示答案可信、来源可支撑。
    - 开关关闭、无答案、无来源、异常时一律视为通过（不阻断主流程）；
    - 仅当答案中存在资料完全找不到依据的明显编造时才判不通过。
    """
    try:
        from .config import is_citation_check_enabled
        if not is_citation_check_enabled():
            return True, ""
    except Exception:
        return True, ""
    if not answer or not sources:
        return True, ""
    parts = []
    for i, s in enumerate(sources[:6], 1):
        name = s.get("book_name") or ""
        sec = s.get("section_path") or s.get("section") or ""
        txt = (s.get("full_text") or s.get("text") or "")[:400]
        parts.append(f"[{i}] {name} {sec}\n{txt}")
    src_ctx = "\n\n".join(parts)
    prompt = (
        "你是答案可信度校验员。下面有「用户问题」「助手回答」「参考资料」。\n"
        "请判断：助手回答中是否存在【资料里完全找不到依据、属于凭空编造】的事实性陈述"
        "（如虚构的条款编号、日期、数字、人名、职责内容等）。\n"
        "仅当确实存在明显编造时才判「不通过」；合理推断或资料已有依据的表述都算「通过」。\n"
        "只输出一行结论：通过 / 不通过，并在冒号后用一句话说明原因。\n\n"
        f"用户问题：{question}\n\n"
        f"助手回答：{answer}\n\n"
        f"参考资料：\n{src_ctx}\n\n"
        "结论："
    )
    try:
        out = llm_generate(prompt, max_tokens=80, temperature=0.0)
        out = (out or "").strip()
    except Exception:
        return True, ""
    if not out:
        return True, ""
    ok = not out.startswith("不通过")
    note = out if len(out) <= 120 else out[:120]
    return ok, note


def _greeting_reply(question, mode):
    """问候/闲聊直答。

    问候语（你好 / 你是谁 / 你能做什么…）不是知识问题，绝不该进 RAG 检索——
    否则用问候语向量召回的全是不相干切片，模型会生成「资料内容与 XX 章节相关，
    无法回答」之类的莫名拒答（极速档下尤其明显）。
    仅匹配「整句全是问候词」的超短问句（按标点切词核对词表），宁漏勿误；
    返回 None 表示走正常检索。
    """
    q = (question or "").strip().lower()
    if not q or len(q) > 20:
        return None
    tokens = [t for t in _GREETING_SPLIT_RE.split(q) if t]
    if not tokens or any(t not in _GREETING_WORDS for t in tokens):
        return None
    if mode == "strict":
        return ("你好！我是 AI 问答助手，可以回答已索引资料范围内的问题。"
                "请直接输入你想了解的问题。")
    return ("你好！我是 AI 问答助手，可以帮你：\n"
            "- 📚 回答已索引图书资料中的问题（制度、技术文档、教学材料等）\n"
            "- 💡 结合通用知识补充与写作辅助（来源会透明标注）\n"
            "- 💻 解答编程等通用问题\n"
            "请直接输入你的问题。")


def global_chat(question, history=None, mode=None, user=None):
    """首页全局问答。

    mode:
      - "mixed"   混合标注（默认）：检索已索引资料并依据回答，资料未覆盖处用通用
                  知识/推断补充；回答中逐段标注来源——资料内容注明「📚 资料 [n]」
                  （对应下方资料编号），补充/推断注明「💡 补充」。全程透明、不编造。
      - "strict"  严格依据资料：仅根据已索引资料回答，不补充任何外部知识；资料
                  不足时直接说明「未在现有资料中找到相关内容」。

    历史值兼容：divergent → 归一为 mixed；conservative → 归一为 strict。

    user: 当前登录用户（dict）。传入后，检索命中会按图书/分类资源权限
          （resource_permissions）过滤，剔除当前用户无权查看的图书，避免受限书
          正文被任意登录用户通过全局问答读到。路由未登录时 user 为 None（@login_required
          已挡在网关外），此时受限书一律不可见，仅公开书可答。

    Returns: {"answer": str, "mode": str, "sources": list}
    """
    #涉密锁定不仅拦索引，也要拦问答——否则已索引正文仍可被读出，
    # 与「未授权用户无法读取正文」的提示语矛盾。与 tasks 共用 is_security_locked 判定。
    try:
        from .config import is_security_locked
        if is_security_locked():
            return {
                "answer": "当前已开启涉密锁定，AI 问答已禁用，无法读取图书正文。",
                "mode": mode or "mixed",
                "sources": [],
                "security_locked": True,
            }
    except Exception:
        pass
    mode = mode or get_chat_thinking_mode()
    # 兼容历史/前端传入的旧值
    if mode == "divergent":
        mode = "mixed"
    elif mode == "conservative":
        mode = "strict"
    history = history or []

    #问候/闲聊直答，不进检索（详见 _greeting_reply）
    _gr = _greeting_reply(question, mode)
    if _gr:
        return {"answer": _gr, "mode": mode, "sources": [], "greeting": True}

    # ---------- 检索（mixed / strict 都先检索；无资料时各自退化策略不同） ----------
    ctx = ""
    hits = []
    try:
        from . import vector_store, embedder
        # 索引未就绪：strict 直接提示；mixed 退化为通用知识回答（仍标注来源缺失）
        ready, reason = vector_store.is_mode_ready(None)
        if not ready:
            try:
                vector_store.ensure_mode_ready(None, lazy=True)
            except Exception:
                pass
            if mode == "strict":
                return {
                    "answer": f"资料索引尚未就绪（{reason}），已在后台开始重建，请稍候再试。",
                    "mode": mode,
                    "sources": [],
                }
            return _mixed_free(question, history, mode)
        #多轮追问改写 —— 用补全后的独立问句做检索，展示/生成仍用原始问题
        _q = _rewrite_query(question, history)
        qv = _encode_query(_q, user)
        if qv is None:
            if mode == "strict":
                return {
                    "answer": "向量模型未就绪，无法检索资料（请检查嵌入模型能否正常加载）。",
                    "mode": mode,
                    "sources": [],
                }
            return _mixed_free(question, history, mode)
        #具体岗位「X职责有哪些」类问题 —— 优先于向量召回直接子串命中
        # 章节原文（绕过脆弱的向量召回与「列全部分类」路由）。即使向量索引对该问题无命中，
        # 也能返回真实职责内容。通用机制，对所有书生效，不重建索引。
        _focus_title = ""
        #直查召回哨兵 —— 未走到对应分支时保持 None，
        # 供下方「跳过向量管线」守卫判断（_role 命中时 person/literal 也不会被赋值）。
        _person = _literal = None
        _role = _recall_role_content(_q, user)
        if _role:
            hits, _focus_title = _role
            ctx = _build_ctx(hits)
        else:
            #人员属性精准召回（姓名 + 工号/职务 等点查）。优先于通用 Excel
            # 字面召回，按「原子行内 姓名：<人名>」精确子串定位，杜绝稀释与误召回。
            _person = _recall_person_attr(_q, user)
            if _person:
                hits, _ = _person
                ctx = _build_ctx(hits)
                _ol = _book_outline_block(hits)
                if _ol:
                    ctx = ctx + "\n\n" + _ol
            else:
                #Excel 表块专属字面召回改为「加分」而非「接管」——
                # 结果暂存，下方与向量召回融合排序后统一使用。此前命中即独占跳过向量
                # 管线，无关表块（恰好含 query 某片段）会抢走正文资料（PDF 等）。
                _literal = _excel_table_recall(_q, user)
        #角色内容 / 人员属性直查命中即已确定 hits/ctx，跳过向量管线。
        # Excel 字面召回 _literal 改为与向量融合「加分」，
        # 不再参与此处的跳过判定（融合逻辑见 _merge_literal_hits）。
        if not (_role or _person):
            raw = vector_store.query(None, qv, top_k=vector_recall_k(), use_parent=False) or []
            # 过滤掉相关性过低的命中（全局问答用默认档位的阈值）
            try:
                from .config import get_min_score
                min_score = get_min_score(None)
            except Exception:
                min_score = 0.40
            # 粗筛宽进、精排严出：召回阶段只滤明显噪声，相关性判定交给二阶重排
            scored = _filter_by_score(raw, min(min_score, _RECALL_SCORE_FLOOR))
            # 一阶关键词去噪 + 二阶 cross-encoder 精排（不可用时自动回退一阶）
            ranked = _rerank_two_stage(_q, scored)
            if not ranked and not _literal:
                # 向量与字面两路皆无命中（不是权限问题）
                if mode == "strict":
                    return {
                        "answer": "未在现有资料中找到相关内容。可确认资料已入库"
                                  "（配置中心 → AI 中心 → 索引新书）。",
                        "mode": mode,
                        "sources": [],
                    }
                return _mixed_free(question, history, mode)
            # 权限过滤：剔除当前用户无权查看的图书命中。图书级问答已在路由层用
            # user_can_view_book 拦截；全局问答跨全部图书检索，必须在此补这一层，
            # 否则任何登录用户都能通过首页问答读到受限书正文与来源（书名/页码/片段）。
            visible = _filter_by_permission(ranked, user) if ranked else []
            # 字面召回「加分」：与向量命中合并去重后按综合分排序，不再独占接管
            hits = _merge_literal_hits(visible, _literal[0] if _literal else [])
            if not hits:
                return {
                    "answer": "你没有权限查看此书内容。如需访问，请联系管理员开通相应图书的查看权限。",
                    "mode": mode,
                    "sources": [],
                }
            if hits:
                #原本硬截 600 字符，对单本书（chunk 数量少）容易丢关键
                # 内容（如《诊所看病单》chunk 全文 804 字符含 CATATAN 备注，硬截会切掉后半段）。
                # 改为：命中数 ≤ 2 时不截断，让 LLM 看到完整资料；命中数 > 2 时仍按 600/条限
                # 制，避免 prompt 过长导致生成失败。
                ctx = _build_ctx(hits)
                #注入「章节结构」块（置于资料之后、问题之前，贴近生成，强制模型依据目录回答）
                _ol = _book_outline_block(hits)
                if _ol:
                    ctx = ctx + "\n\n" + _ol
    except Exception as e:
        print(f"⚠️ 检索失败: {e}")
        if mode == "strict":
            return {"answer": "检索资料时出错，暂无法基于资料回答。", "mode": mode, "sources": []}
        return _mixed_free(question, history, mode)

    #分类/目录类问题确定性作答（弱模型易把"岗位分类"答成"职责维度"，
    # 故直接依据文档大纲逐条列出，避免模型幻觉）。仅整段返回路径；流式路径见 global_chat_stream。
    # 具体岗位问题已被上方内容召回拦截（_focus_title 已置），不再走分类大纲。
    if _is_classification_question(_q) and not _focus_title:
        _det = _deterministic_outline_answer(hits, _q)
        if _det:
            return {"answer": _det, "mode": mode, "sources": _pack_sources(hits)}

    #章节定位精准召回 —— 问「某岗位/某章节的职责或内容」时向量召回会
    # 命中封面页，改为直接取该章节分块，确保模型看到的是正确章节（消除张冠李戴式答错）。
    # _focus_title 已在上方定义（角色内容召回优先），此处不再重置。
    if _is_section_lookup_question(_q) and not _is_classification_question(_q) and not _focus_title:
        _loc = _locate_section_hits(hits, _q, user)
        if _loc:
            hits, ctx, _focus_title = _loc

    _focus_note = ""
    if _focus_title:
        _focus_note = (
            f"\n【重要】以下问题询问的是「{_focus_title}」章节的内容，下方资料即该章节原文；"
            f"请【仅依据该章节】逐条罗列，不要混入其他章节或其他岗位的内容。\n\n"
        )

    #低置信拒答 —— 命中全是弱相关时，strict 明确说明资料支撑不足，
    # 不把弱相关片段当依据编造。
    _weak = (not _focus_title) and _is_weak_support(hits, _q)
    if _weak and mode == "strict":
        return {
            "answer": "未在资料中找到足够明确的相关内容。\n\n"
                      "（检索到的片段与问题相关性较低，不足以作为回答依据，故不作推断。）\n"
                      "可换个更具体的问法，或确认资料已入库（配置中心 → AI 中心 → 索引新书）。",
            "mode": mode,
            "sources": _pack_sources(hits),
        }
    _weak_note = _weak_support_notice() if _weak else ""

    # ---------- 生成 ----------
    if mode == "strict":
        prompt = (
            "请仅根据以下资料回答用户问题，不要编造资料之外的内容；"
            "若资料不足以回答，请明确说明。\n"
            #明确要求按资料原样罗列，避免表单类资料被 LLM 漏字段
            "请按资料原样逐项/逐字段罗列（包含双语字段对照），不要合并、不要省略字段名。\n"
            #strict 模式下，资料代码常因 PDF/DOCX 提取丢失缩进/代码块标记，
            # 导致回答平铺。允许模型仅恢复其标准语法缩进与 ``` 围栏（还原结构、不改内容），
            # 其余资料仍须原样——只影响含代码的回答，不波及非代码回答。
            "若资料中的代码因提取丢失缩进或代码块标记，可恢复为该语言标准语法的缩进并补充 ``` 语言 围栏；"
            "仅恢复结构与格式，不得增删或改写任何代码逻辑与含义；其余资料仍须原样。\n"
            #资料已带「章节标题」前缀，要求模型按目录层级组织，
            # 避免把"岗位分类"误答成"职责维度"
            "资料编号后标注的「章节标题」即该段所属章节，请依此层级组织答案；"
            "若下方提供「章节结构（目录大纲）」块，回答「分类 / 目录 / 有哪几个岗位 / 分几部分」类问题"
            "【必须】逐条列出该块中的章节标题作为答案框架，禁止从资料段落自行归纳为其他维度"
            "（如把岗位分类答成「安全管理 / 设备维护」）。\n\n"
            f"{_weak_note}{_focus_note}资料：\n{ctx}\n\n问题：{question}\n\n回答："
        )
        answer = llm_generate(prompt, max_tokens=600, temperature=0.2, history=history)
        if not answer:
            return {"answer": MODEL_NO_RESPONSE_MSG, "mode": mode, "sources": _pack_sources(hits)}
        #引用校验（groundedness）—— strict 模式下若答案被判定为资料无法支撑，
        # 退回保守回答，避免把模型编造的内容当结论输出；mixed 模式仅附校验说明、不降级。
        ok, note = _verify_grounding(answer, _pack_sources(hits), question, mode)
        if not ok and mode == "strict":
            return {
                "answer": "检索到的资料不足以支撑一个确切的回答，为避免误导不作推断。\n\n"
                          "（引用校验：答案部分内容在所提供的资料中未能找到依据。）",
                "mode": mode,
                "sources": _pack_sources(hits),
                "grounding": False,
                "grounding_note": note,
            }
        return {"answer": answer.strip(), "mode": mode, "sources": _pack_sources(hits),
                "grounding": ok, "grounding_note": (note if not ok else "")}

    # mixed：资料 + 通用知识补充，逐段标注来源
    prompt = (
        "请结合下方资料与你的通用知识回答用户问题，并严格按以下规则标注来源：\n"
        "1. 凡是直接依据下方资料给出的内容，在句末注明「📚 资料 [n]」，n 对应资料编号；\n"
        "2. 仅当你额外补充了资料中没有的常识或推断时，在该处注明「💡 补充」；"
        "纯粹依据资料的句子只标 📚，不要加 💡；\n"
        "3. 若资料与你的补充不一致，请明确说明差异；\n"
        "4. 不要编造资料中不存在的细节或来源；\n"
        #按资料原样逐项/逐字段罗列，表单类保留双语对照
        "5. 请按资料原样逐项/逐字段罗列（表单类资料含双语字段对照），不要合并、不要省略字段名。\n"
        "若资料中的代码因提取丢失缩进或代码块标记，可恢复为该语言标准语法的缩进并补充 ``` 语言 围栏；仅恢复结构与格式，不得增删或改写任何代码逻辑与含义；其余资料仍须原样。\n"
        #分类/目录类问题必须依据「章节结构（目录大纲）」块
        "6. 资料编号后标注的「章节标题」即该段所属章节；若下方提供「章节结构（目录大纲）」块，"
        "回答「分类 / 目录 / 有哪几个岗位 / 分几部分」类问题【必须】逐条列出该块中的章节标题作为答案框架，"
        "禁止从资料段落自行归纳为其他维度（如把岗位分类答成「安全管理 / 设备维护」）。\n"
        #同书已合并为一条资料，禁止模型逐条复述同一内容、堆叠编号
        "7. 同一条事实无论由几条资料支撑，都只陈述一遍：归纳合并后作答，句末只标最相关的 1 个编号；\n"
        "严禁把同一段内容重复输出多遍，严禁在一句末尾堆叠多个「📚 资料 [n]」。\n\n"
        f"{_weak_note}{_focus_note}资料：\n{ctx}\n\n问题：{question}\n\n回答："
    )
    answer = llm_generate(prompt, max_tokens=700,
                          temperature=(0.25 if _weak else 0.4), history=history)
    if not answer:
        return {"answer": MODEL_NO_RESPONSE_MSG, "mode": mode, "sources": _pack_sources(hits)}
    #引用校验（mixed 模式不降级，仅回传校验信号供前端展示置信度）
    ok, note = _verify_grounding(answer, _pack_sources(hits), question, mode)
    return {"answer": answer.strip(), "mode": mode, "sources": _pack_sources(hits),
            "grounding": ok, "grounding_note": (note if not ok else "")}


def global_chat_stream(question, history=None, mode=None, user=None):
    """首页全局问答（流式版）。yield SSE 事件 dict，供路由层封装成 text/event-stream。

    与 global_chat 逻辑完全一致（检索 / 相关性过滤 / 权限过滤 / mixed / strict），
    仅把最终生成改为逐块增量下发，并在生成前先把检索来源下发，提升长延迟下的体验。

    事件类型：
      - {"type":"sources","sources":[...]}            检索到的来源（生成前下发，供前端展示）
      - {"type":"status","text":"..."}               进度提示（可选，前端可忽略）
      - {"type":"delta","text":"..."}                逐块文本增量
      - {"type":"done","answer":full,"mode":m,
             "sources":[...]}                         结束（含完整答案，供服务端存历史）
      - {"type":"answer","text":...,"mode":m,
             "sources":[...]}                         即时单事件（无流式，如权限拦截 / 未命中）
      - {"type":"error","message":"..."}             出错
    """
    #涉密锁定同样拦流式问答（与 global_chat 一致）。
    try:
        from .config import is_security_locked
        if is_security_locked():
            yield {"type": "answer",
                   "text": "当前已开启涉密锁定，AI 问答已禁用，无法读取图书正文。",
                   "mode": mode or "mixed", "sources": [], "security_locked": True}
            return
    except Exception:
        pass
    mode = mode or get_chat_thinking_mode()
    # 兼容历史/前端传入的旧值
    if mode == "divergent":
        mode = "mixed"
    elif mode == "conservative":
        mode = "strict"
    history = history or []

    #问候/闲聊直答，不进检索（详见 _greeting_reply）
    _gr = _greeting_reply(question, mode)
    if _gr:
        yield {"type": "answer", "text": _gr, "mode": mode, "sources": [],
               "greeting": True}
        yield {"type": "done", "answer": _gr, "mode": mode,
               "sources": [], "grounding": None, "greeting": True}
        return

    # ---------- 检索（mixed / strict 都先检索；无资料时各自退化策略不同） ----------
    try:
        from . import vector_store, embedder
        # 索引未就绪：strict 直接提示；mixed 退化为通用知识回答（仍标注来源缺失）
        ready, reason = vector_store.is_mode_ready(None)
        if not ready:
            try:
                vector_store.ensure_mode_ready(None, lazy=True)
            except Exception:
                pass
            if mode == "strict":
                yield {"type": "answer", "text": f"资料索引尚未就绪（{reason}），已在后台开始重建，请稍候再试。",
                       "mode": mode, "sources": []}
                return
            yield from _stream_mixed_free(question, history, mode)
            return
        #多轮追问改写 —— 用补全后的独立问句做检索，展示/生成仍用原始问题
        _q = _rewrite_query(question, history)
        qv = _encode_query(_q, user)
        if qv is None:
            if mode == "strict":
                yield {"type": "answer", "text": "向量模型未就绪，无法检索资料（请检查嵌入模型能否正常加载）。",
                       "mode": mode, "sources": []}
                return
            yield from _stream_mixed_free(question, history, mode)
            return
        #具体岗位「X职责有哪些」类问题 —— 优先于向量召回直接子串命中
        # 章节原文。通用机制，对所有书生效，不重建索引。
        _focus_title = ""
        #直查召回哨兵 —— 未走到对应分支时保持 None，
        # 供下方「跳过向量管线」守卫判断（_role 命中时 person/literal 也不会被赋值）。
        _person = _literal = None
        _role = _recall_role_content(_q, user)
        if _role:
            hits, _focus_title = _role
            ctx = _build_ctx(hits)
        else:
            #人员属性精准召回（姓名 + 工号/职务 等点查）。优先于通用 Excel
            # 字面召回，按「原子行内 姓名：<人名>」精确子串定位，杜绝稀释与误召回。
            _person = _recall_person_attr(_q, user)
            if _person:
                hits, _ = _person
                ctx = _build_ctx(hits)
                _ol = _book_outline_block(hits)
                if _ol:
                    ctx = ctx + "\n\n" + _ol
            else:
                #Excel 字面召回改为「加分」而非「接管」，结果暂存待融合
                _literal = _excel_table_recall(_q, user)
        #角色内容 / 人员属性直查命中即已确定 hits/ctx，跳过向量管线。
        # Excel 字面召回 _literal 改为与向量融合「加分」，
        # 不再参与此处的跳过判定（融合逻辑见 _merge_literal_hits）。
        if not (_role or _person):
            raw = vector_store.query(None, qv, top_k=vector_recall_k(), use_parent=False) or []
            # 过滤掉相关性过低的命中（全局问答用默认档位的阈值）
            try:
                from .config import get_min_score
                min_score = get_min_score(None)
            except Exception:
                min_score = 0.40
            # 粗筛宽进、精排严出：召回阶段只滤明显噪声，相关性判定交给二阶重排
            scored = _filter_by_score(raw, min(min_score, _RECALL_SCORE_FLOOR))
            # 一阶关键词去噪 + 二阶 cross-encoder 精排（不可用时自动回退一阶）
            ranked = _rerank_two_stage(_q, scored)
            if not ranked and not _literal:
                # 向量与字面两路皆无命中（不是权限问题）
                if mode == "strict":
                    yield {"type": "answer", "text": "未在现有资料中找到相关内容。可确认资料已入库"
                                                     "（配置中心 → AI 中心 → 索引新书）。",
                           "mode": mode, "sources": []}
                    return
                yield from _stream_mixed_free(question, history, mode)
                return
            # 权限过滤：剔除当前用户无权查看的图书命中（与 global_chat 同一套判定）
            visible = _filter_by_permission(ranked, user) if ranked else []
            # 字面召回「加分」：与向量命中合并去重后按综合分排序，不再独占接管
            hits = _merge_literal_hits(visible, _literal[0] if _literal else [])
            if not hits:
                yield {"type": "answer", "text": "你没有权限查看此书内容。如需访问，请联系管理员开通相应图书的查看权限。",
                       "mode": mode, "sources": []}
                return
            if hits:
                #原本硬截 600 字符，对单本书（chunk 数量少）容易丢关键
                # 内容（如《诊所看病单》chunk 全文 804 字符含 CATATAN 备注，硬截会切掉后半段）。
                # 改为：命中数 ≤ 2 时不截断，让 LLM 看到完整资料；命中数 > 2 时仍按 600/条限
                # 制，避免 prompt 过长导致生成失败。
                ctx = _build_ctx(hits)
                #注入「章节结构」块（同 global_chat，置于资料之后、问题之前）
                _ol = _book_outline_block(hits)
                if _ol:
                    ctx = ctx + "\n\n" + _ol
    except Exception as e:
        print(f"⚠️ 检索失败: {e}")
        if mode == "strict":
            yield {"type": "answer", "text": "检索资料时出错，暂无法基于资料回答。", "mode": mode, "sources": []}
            return
        yield from _stream_mixed_free(question, history, mode)
        return

    #分类/目录类问题确定性作答（弱模型易把"岗位分类"答成"职责维度"，
    # 故直接依据文档大纲逐条列出并秒回，避免模型幻觉）。先下发来源，再定稿答案。
    # 具体岗位问题已被上方内容召回拦截（_focus_title 已置），不再走分类大纲。
    if _is_classification_question(_q) and not _focus_title:
        _det = _deterministic_outline_answer(hits, _q)
        if _det:
            yield {"type": "sources", "sources": _pack_sources(hits)}
            yield {"type": "done", "answer": _det, "mode": mode, "sources": _pack_sources(hits)}
            return

    #章节定位精准召回（同 global_chat，消除张冠李戴式答错）
    # _focus_title 已在上方定义（角色内容召回优先），此处不再重置。
    if _is_section_lookup_question(_q) and not _is_classification_question(_q) and not _focus_title:
        _loc = _locate_section_hits(hits, _q, user)
        if _loc:
            hits, ctx, _focus_title = _loc

    _focus_note = ""
    if _focus_title:
        _focus_note = (
            f"\n【重要】以下问题询问的是「{_focus_title}」章节的内容，下方资料即该章节原文；"
            f"请【仅依据该章节】逐条罗列，不要混入其他章节或其他岗位的内容。\n\n"
        )

    #低置信拒答（同 global_chat）
    _weak = (not _focus_title) and _is_weak_support(hits, _q)
    if _weak and mode == "strict":
        yield {"type": "answer",
               "text": "未在资料中找到足够明确的相关内容。\n\n"
                       "（检索到的片段与问题相关性较低，不足以作为回答依据，故不作推断。）\n"
                       "可换个更具体的问法，或确认资料已入库（配置中心 → AI 中心 → 索引新书）。",
               "mode": mode, "sources": _pack_sources(hits)}
        return
    _weak_note = _weak_support_notice() if _weak else ""

    # ---------- 生成（流式） ----------
    # 先下发来源，让前端即时展示「参考来源」，不必等生成结束
    yield {"type": "sources", "sources": _pack_sources(hits)}

    if mode == "strict":
        prompt = (
            "请仅根据以下资料回答用户问题，不要编造资料之外的内容；"
            "若资料不足以回答，请明确说明。\n"
            #明确要求按资料原样罗列，避免表单类资料被 LLM 漏字段
            "请按资料原样逐项/逐字段罗列（包含双语字段对照），不要合并、不要省略字段名。\n"
            #strict 模式下，资料代码常因 PDF/DOCX 提取丢失缩进/代码块标记，
            # 导致回答平铺。允许模型仅恢复其标准语法缩进与 ``` 围栏（还原结构、不改内容），
            # 其余资料仍须原样——只影响含代码的回答，不波及非代码回答。
            "若资料中的代码因提取丢失缩进或代码块标记，可恢复为该语言标准语法的缩进并补充 ``` 语言 围栏；"
            "仅恢复结构与格式，不得增删或改写任何代码逻辑与含义；其余资料仍须原样。\n"
            #资料已带「章节标题」前缀，要求模型按目录层级组织，
            # 避免把"岗位分类"误答成"职责维度"
            "资料编号后标注的「章节标题」即该段所属章节，请依此层级组织答案；"
            "若下方提供「章节结构（目录大纲）」块，回答「分类 / 目录 / 有哪几个岗位 / 分几部分」类问题"
            "【必须】逐条列出该块中的章节标题作为答案框架，禁止从资料段落自行归纳为其他维度"
            "（如把岗位分类答成「安全管理 / 设备维护」）。\n\n"
            f"{_weak_note}{_focus_note}资料：\n{ctx}\n\n问题：{question}\n\n回答："
        )
        max_tokens, temperature = 600, 0.2
    else:
        # mixed：资料 + 通用知识补充，逐段标注来源
        prompt = (
            "请结合下方资料与你的通用知识回答用户问题，并严格按以下规则标注来源：\n"
            "1. 凡是直接依据下方资料给出的内容，在句末注明「📚 资料 [n]」，n 对应资料编号；\n"
            "2. 仅当你额外补充了资料中没有的常识或推断时，在该处注明「💡 补充」；"
            "纯粹依据资料的句子只标 📚，不要加 💡；\n"
            "3. 若资料与你的补充不一致，请明确说明差异；\n"
            "4. 不要编造资料中不存在的细节或来源；\n"
            #按资料原样逐项/逐字段罗列，表单类保留双语对照
            "5. 请按资料原样逐项/逐字段罗列（表单类资料含双语字段对照），不要合并、不要省略字段名。\n"
            "若资料中的代码因提取丢失缩进或代码块标记，可恢复为该语言标准语法的缩进并补充 ``` 语言 围栏；仅恢复结构与格式，不得增删或改写任何代码逻辑与含义；其余资料仍须原样。\n"
            #分类/目录类问题必须依据「章节结构（目录大纲）」块
            "6. 资料编号后标注的「章节标题」即该段所属章节；若下方提供「章节结构（目录大纲）」块，"
            "回答「分类 / 目录 / 有哪几个岗位 / 分几部分」类问题【必须】逐条列出该块中的章节标题作为答案框架，"
            "禁止从资料段落自行归纳为其他维度（如把岗位分类答成「安全管理 / 设备维护」）。\n"
            #同书已合并为一条资料，禁止模型逐条复述同一内容、堆叠编号
            "7. 同一条事实无论由几条资料支撑，都只陈述一遍：归纳合并后作答，句末只标最相关的 1 个编号；\n"
            "严禁把同一段内容重复输出多遍，严禁在一句末尾堆叠多个「📚 资料 [n]」。\n\n"
            f"{_weak_note}{_focus_note}资料：\n{ctx}\n\n问题：{question}\n\n回答："
        )
        max_tokens, temperature = 700, (0.25 if _weak else 0.4)

    _q = get_infer_queue_info()
    if _q["busy"]:
        yield {"type": "status", "text": f"⏳ 模型正忙，正在排队（前面还有 {_q['waiters']} 个请求），预计需要稍等…"}
    yield {"type": "status", "text": "✍️ 正在生成…"}
    gen = llm_generate(prompt, max_tokens=max_tokens, temperature=temperature, history=history, stream=True)
    full = []
    try:
        for delta in gen:
            if delta:
                full.append(delta)
                yield {"type": "delta", "text": delta}
    except Exception as e:
        print(f"⚠️ 全局问答流式生成异常: {e}")
        if not full:
            yield {"type": "done", "answer": MODEL_NO_RESPONSE_MSG,
                   "mode": mode, "sources": _pack_sources(hits)}
            return
    answer = "".join(full).strip()
    if not answer:
        answer = MODEL_NO_RESPONSE_MSG
    sources = _pack_sources(hits)

    #引用校验异步化。校验本身要再跑一次 LLM（本机实测 ~12s），
    # 原先它卡在 done 之前，是「答案已生成但用户还在等」的主要来源。
    # 现在先下发 done 让答案立刻可见，校验结果随后以 grounding 事件单独下发。
    try:
        from .config import is_citation_check_enabled, is_grounding_async
        _ck_on, _async_on = is_citation_check_enabled(), is_grounding_async()
    except Exception:
        _ck_on, _async_on = True, False   # 取不到配置时回落旧的同步行为（更保守）

    if not _ck_on or not _async_on:
        # 同步（旧行为）：校验完成后再收尾，strict 模式不合规直接换成保守答案
        ok, note = _verify_grounding(answer, sources, question, mode)
        if not ok and mode == "strict":
            answer = ("检索到的资料不足以支撑一个确切的回答，为避免误导不作推断。\n\n"
                      "（引用校验：答案部分内容在所提供的资料中未能找到依据。）")
        yield {"type": "done", "answer": answer, "mode": mode,
               "sources": sources, "grounding": ok,
               "grounding_note": (note if not ok else "")}
        return

    # 异步：答案先出，校验待定
    yield {"type": "done", "answer": answer, "mode": mode, "sources": sources,
           "grounding": None, "grounding_pending": True}
    ok, note = _verify_grounding(answer, sources, question, mode)
    ev = {"type": "grounding", "grounding": ok,
          "grounding_note": (note if not ok else "")}
    if not ok and mode == "strict":
        # strict 模式依旧执行保守替换，只是下发时机延后 —— 前端据此覆盖展示
        ev["answer"] = ("检索到的资料不足以支撑一个确切的回答，为避免误导不作推断。\n\n"
                        "（引用校验：答案部分内容在所提供的资料中未能找到依据。）")
    yield ev


def _stream_mixed_free(question, history, mode):
    """mixed 模式下未检索到可依据资料时，用通用知识回答并流式下发（标注来源缺失）。"""
    prompt = (
        "资料库中未收录与问题直接相关的内容，因此下面的回答完全来自你的通用知识，"
        "不是来自用户的资料库。请在回答开头先写「💡 补充（资料库未收录）：」，"
        "然后基于你的通用知识作答；若你也不确定，请如实说明。\n\n"
        f"问题：{question}\n\n回答："
    )
    _q = get_infer_queue_info()
    if _q["busy"]:
        yield {"type": "status", "text": f"⏳ 模型正忙，正在排队（前面还有 {_q['waiters']} 个请求），预计需要稍等…"}
    yield {"type": "status", "text": "✍️ 正在生成…"}
    gen = llm_generate(prompt, max_tokens=600, temperature=0.5, history=history, stream=True)
    full = []
    try:
        for delta in gen:
            if delta:
                full.append(delta)
                yield {"type": "delta", "text": delta}
    except Exception as e:
        print(f"⚠️ mixed_free 流式生成异常: {e}")
    ans = "".join(full).strip()
    if not ans:
        ans = MODEL_NO_RESPONSE_MSG
    # 兜底：7B 模型偶尔不按指令标注，代码层强制补上来源缺失标记，保证透明度
    if "💡" not in ans and "资料库未收录" not in ans:
        ans = "💡 补充（资料库未收录）：" + ans
    yield {"type": "done", "answer": ans, "mode": mode, "sources": []}


def _mixed_free(question, history, mode):
    """mixed 模式下未检索到可依据资料时，用通用知识回答并标注来源缺失。"""
    prompt = (
        "资料库中未收录与问题直接相关的内容，因此下面的回答完全来自你的通用知识，"
        "不是来自用户的资料库。请在回答开头先写「💡 补充（资料库未收录）：」，"
        "然后基于你的通用知识作答；若你也不确定，请如实说明。\n\n"
        f"问题：{question}\n\n回答："
    )
    answer = llm_generate(prompt, max_tokens=600, temperature=0.5, history=history)
    if not answer:
        return {"answer": MODEL_NO_RESPONSE_MSG, "mode": mode, "sources": []}
    ans = answer.strip()
    # 兜底：7B 模型偶尔不按指令标注，代码层强制补上来源缺失标记，保证透明度
    if "💡" not in ans and "资料库未收录" not in ans:
        ans = "💡 补充（资料库未收录）：" + ans
    return {"answer": ans, "mode": mode, "sources": []}


# ---------------------------------------------------------------------------
# 混合检索重排：抑制「字面沾边、实则无关」的召回噪声
# ---------------------------------------------------------------------------
_STOP_CJK = set(
    "的了在是和与及或对于关于如何什么哪些哪那为何怎么怎样是否可可以有没有无"
    "一个这那我们你们他们它们她他它该被把将被与跟同对从到给为作为等且并很更"
    "最也又还都就才仅只其其中以上以下之内之间以及进行通过由于因为所以但然而"
    "之其者如若一个每种各个这些那些此该当应于把被给让使令需须应会能将要"
)

def _extract_terms(question):
    """从问题中抽取用于「关键词召回」的判别词。

    - 连续字母/数字串（文档编号、英文词）长度 >=3 直接作为词（小写）。
    - 中文：去标点后取 2~3 字连续片段，剔除含停用字的片段，保留有区分度的词。
    返回词集合；为空表示问题无有效判别词（退化为纯向量排序）。
    """
    terms = set()
    for m in re.findall(r'[A-Za-z0-9]{3,}', question or ''):
        terms.add(m.lower())
    cjk = re.sub(r'[^\u4e00-\u9fff]', '', question or '')
    for n in (3, 2):
        for i in range(len(cjk) - n + 1):
            t = cjk[i:i + n]
            if any(ch in _STOP_CJK for ch in t):
                continue
            terms.add(t)
    return terms


def _lexical_score(terms, haystack):
    """问题判别词在 (书名+正文) 中的命中比例，0~1。"""
    if not terms:
        return 0.0
    low = (haystack or '').lower()
    if not low:
        return 0.0
    cnt = sum(1 for t in terms if t.lower() in low)
    return cnt / len(terms)


# ---------------------------------------------------------------------------
# F2/F5 分层加权召回 + IDF 降权（用户诉求：文件名 > 标题目录 > 小章节 > 正文）
# ---------------------------------------------------------------------------
# 权重（严格遵循 书名 > 目录章节 > 小章节 > 正文；向量作语义基线）
_W_BOOK = 0.30   # 书名精确含 query 核心词
_W_CAT = 0.22    # 目录章节（section_path 的 L1/L2）含 query 词
_W_SUB = 0.14    # 小章节（section_path 的 L3）含 query 词
_W_BODY = 0.10   # 正文 IDF 加权命中
_W_VEC = 0.24    # 向量分（归一化）

# 向量分归一化底分（与 _filter_by_score 默认 min_score 对齐）
_VEC_FLOOR = 0.40

#召回阶段的粗筛下限。min_score（配置档位，general 默认 0.40）语义上是
# 「最终是否相关」的判定线，若直接用在召回阶段，会把语义相关但字面不沾边的命中提前
# 卡死 —— 实测目的段在索引中的子块余弦仅 0.3319，低于 0.40 被滤掉，而同一段父块
# 编码后高达 0.5067（高度相关）。改为：召回阶段只按此下限滤掉明显噪声（宽进），
# 真正的判定交给二阶 cross-encoder 精排（严出），符合检索-重排两阶段的分工。
_RECALL_SCORE_FLOOR = 0.30


def _body_idf_score(terms, body_text, idf):
    """正文命中比例按 IDF 加权（F5）：通用词（请假/考核/境内）低 IDF → 贡献小，
    专名/编号（EM-2026-010）高 IDF → 贡献大，拉开同主题制度书分差。"""
    if not terms or not idf:
        return 0.0
    low = (body_text or "").lower()
    num = 0.0
    den = 0.0
    for t in terms:
        w = idf.get(t.lower(), 0.0)
        den += w
        if t.lower() in low:
            num += w
    return (num / den) if den > 0 else 0.0


# 书名 IDF 加权分的「整书强保」阈值（见 _book_idf_score 说明）
_BOOK_STRONG_MIN = 0.30


def _book_idf_score(terms, book_name, idf):
    """书名命中按 IDF 加权：只有区分性词命中书名，才算强相关。

    原实现是布尔 OR —— 任一 term 命中书名即判 1.0，导致通用词污染：问「电气运维部
    晋升调薪管理制度目的是什么」时，「电气 / 运维 / 部 / 管理 / 制度」这批在库内
    高频出现的低 IDF 词，使《岗位职责》《请假通知》《受限空间》《安全奖惩制度》等
    6 本书【全部】被判为书名强命中并整书保留，噪声被一起喂给模型。

    改为 IDF 加权后，高频通用词贡献趋近 0，只有「晋升 / 调薪」这类库内罕见的高 IDF
    词命中书名才能拉高分数。实测同题各书得分：

        《电气运维部晋升调薪管理制度》 0.8640  ← 唯一正确的书
        《运维部安全生产会议管理制度》 0.2189
        《电气运维部岗位职责》         0.1413
        《关于…境内请假要求的通知》     0.1413
        《运维部受限空间作业…》         0.0576
        《员工安全奖惩制度》           0.0252

    与正确书分差极大（0.864 vs 0.219），阈值 _BOOK_STRONG_MIN=0.30 可干净切分，
    对不同的提问也留有足够安全边际。
    """
    if not terms or not book_name or not idf:
        return 0.0
    low = (book_name or "").lower()
    num = 0.0
    den = 0.0
    for t in terms:
        w = idf.get(t.lower(), 0.0)
        den += w
        if t.lower() in low:
            num += w
    return (num / den) if den > 0 else 0.0


# IDF 缓存：键为 document_chunks 行数指纹，避免每请求全表扫
_IDF_CACHE = {"fp": None, "idf": None}


def _get_idf():
    """统计所有 document_chunks 的 term→df，返回 {term: idf}（轻量缓存）。

    tokenize 与 _extract_terms 一致：英文/数字串(>=3) + 中文 2~3 字片段。
    """
    try:
        import math
        from core.db_base import get_db
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM document_chunks")
        total = cur.fetchone()[0] or 0
        if _IDF_CACHE["fp"] == total and _IDF_CACHE["idf"] is not None:
            conn.close()
            return _IDF_CACHE["idf"]
        cur.execute("SELECT chunk_text FROM document_chunks")
        df = {}
        for (txt,) in cur.fetchall():
            toks = set()
            for m in re.findall(r'[A-Za-z0-9]{3,}', txt or ''):
                toks.add(m.lower())
            cjk = re.sub(r'[^\u4e00-\u9fff]', '', txt or '')
            for n in (3, 2):
                for i in range(len(cjk) - n + 1):
                    toks.add(cjk[i:i + n])
            for t in toks:
                df[t] = df.get(t, 0) + 1
        conn.close()
        idf = {t: math.log((total + 1) / (d + 1)) for t, d in df.items()}
        _IDF_CACHE["fp"] = total
        _IDF_CACHE["idf"] = idf
        return idf
    except Exception:
        return {}


def _rerank_by_lexical(question, scored, strong=0.55, top_n=5):
    """向量召回后做「分层加权 + 关键词判别 + 整书保留收紧」重排（F2+F3+F5）。

    分层加权（用户诉求：文件名 > 标题目录 > 小章节 > 正文）：
        layered = 0.30*书名命中 + 0.22*目录章节命中 + 0.14*小章节命中
                + 0.10*正文IDF命中 + 0.24*向量(归一)
    权重顺序严格 书名 > 章 > 节 > 正文，使「境内请假」先命中被名《境内请假要求的
    通知》→ 再命中其「考核」章节，远远压过只是正文碰巧出现「请假」的晋升/会议书。

    F3 收紧整书保留：仅「书名精确命中」才整书强保；其余命中（目录/小章节/正文）
    只保留该命中 chunk 自身，不再把同主题 4 本书全本连带留下导致分不出谁对。
    """
    if not scored:
        return []
    terms = _extract_terms(question)
    idf = _get_idf()
    name_cache = {}
    enriched = []
    for h in scored:
        bid = h.get("book_id")
        if bid not in name_cache:
            try:
                from core.db_base import get_book_by_id
                name_cache[bid] = (get_book_by_id(bid) or {}).get("name") or ""
            except Exception:
                name_cache[bid] = ""
        bname = name_cache[bid]
        content = h.get("content") or ""
        sp = h.get("section_path") or ""
        sp_parts = [p.strip() for p in sp.split(">") if p.strip()]
        cat_part = " > ".join(sp_parts[:2])       # 目录章节（L1/L2）
        sub_part = sp_parts[2] if len(sp_parts) >= 3 else ""   # 小章节（L3）

        # 分层命中
        #书名命中由「布尔 OR」改为 IDF 加权连续分，避免「电气 / 运维 /
        # 制度」这类通用词让无关书整本混入（详见 _book_idf_score 说明）。
        # book_hit 保留布尔语义，仅供 any_hit 判断是否「有字面命中」。
        book_idf = _book_idf_score(terms, bname, idf)
        book_hit = 1.0 if book_idf > 0 else 0.0
        cat_hit = 1.0 if (terms and any(t.lower() in cat_part.lower() for t in terms)) else 0.0
        sub_hit = 1.0 if (terms and any(t.lower() in sub_part.lower() for t in terms)) else 0.0
        body_idf = _body_idf_score(terms, content, idf)
        vec = float(h.get("score") or 0.0)
        vec_norm = max(0.0, min(1.0, (vec - _VEC_FLOOR) / (1.0 - _VEC_FLOOR))) if vec > 0 else 0.0

        # 书名档位改用连续分 book_idf，使「晋升 / 调薪」这类高 IDF 词真正拉开差距
        layered = (_W_BOOK * book_idf + _W_CAT * cat_hit + _W_SUB * sub_hit
                   + _W_BODY * body_idf + _W_VEC * vec_norm)

        any_hit = (book_hit + cat_hit + sub_hit + (1.0 if body_idf > 0 else 0.0)) > 0
        h = dict(h)
        h["book_name"] = bname          # 注入，供下游 reranker / _pack_sources 使用
        h["_lex"] = 1.0 if any_hit else 0.0
        h["_book_hit"] = book_hit
        h["_book_idf"] = book_idf
        h["_cat_hit"] = cat_hit
        h["_sub_hit"] = sub_hit
        h["_body_idf"] = body_idf
        h["_layered"] = layered
        h["_hybrid"] = layered          # 兼容下方排序键
        enriched.append(h)

    has_lex = any(h["_lex"] > 0 for h in enriched)
    if has_lex:
        # F3：仅书名强命中 → 整书强保；否则仅保留各自命中的 chunk（不连带整本）
        #强保判定由「书名布尔 OR 命中」改为「书名 IDF 加权分 >= 阈值」。
        # 布尔判定下，问「电气运维部晋升调薪管理制度目的」会让 6 本书全部强保（含书名
        # 仅含「制度」二字的《员工安全奖惩制度》），噪声被整本喂给模型。改用 IDF 后
        # 实测只剩正确的 1 本（0.8640），其余 5 本（0.2189 / 0.1413 / 0.1413 /
        # 0.0576 / 0.0252）全部剔除。
        strong_books = {h.get("book_id") for h in enriched
                        if float(h.get("_book_idf") or 0.0) >= _BOOK_STRONG_MIN}
        if strong_books:
            kept = [h for h in enriched if h.get("book_id") in strong_books]
        else:
            kept = [h for h in enriched
                    if h["_lex"] > 0 or float(h.get("score") or 0.0) >= strong]
        kept.sort(key=lambda x: (x["_lex"] > 0, x["_layered"]), reverse=True)
        return kept[:top_n]
    # 无关键词命中：退回纯向量排序
    enriched.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return enriched[:top_n]


# ---------------------------------------------------------------------------
# 二阶重排：一阶关键词判别去噪 + 二阶 cross-encoder 精排
# ---------------------------------------------------------------------------
# 仅当重排模型可用时才扩大向量召回，为二阶提供足够候选；不可用时严格维持原有
# 召回规模，保证降级路径与改造前行为完全一致。
_VECTOR_RECALL_K = 20   # 重排可用时的向量召回条数
_LEGACY_RECALL_K = 5    # 重排不可用时的原召回条数

#二阶 cross-encoder 精排后的「严出」门槛。
# 召回阶段已放宽（粗筛下限降到 _RECALL_SCORE_FLOOR、每书名额 3 → 12），噪声必须在
# 这一层挡掉，否则放宽等于放水。cross-encoder 输出的是相关性 logit，0 附近是天然
# 分界 —— 实测同题：相关命中 ce≈6.4~7.6，无关命中 ce≈-1.8~-3.0，安全边际充足。
_CE_MIN = 0.0


def vector_recall_k():
    """本次检索应召回多少条候选（重排可用则放宽，否则维持原值）。"""
    try:
        if reranker.is_available():
            return _VECTOR_RECALL_K
    except Exception:
        pass
    return _LEGACY_RECALL_K


def _rerank_two_stage(question, scored, top_n=5):
    """一阶去噪 + 二阶精排。

    一阶（_rerank_by_lexical）剔除「字面沾边、实则无关」的噪声并做整书保留，
    同时放宽 top_n 给二阶留足候选；二阶（cross-encoder）在剩余的相关集合内按
    (问题, 段落) 成对打分精排，捕捉字面不重叠但语义高度相关的命中 —— 这正是纯
    向量召回「语义最近却答非所问」的短板。

    二阶不可用或异常时直接回退一阶截断结果，行为与改造前一致。
    """
    if not scored:
        return []
    stage1 = _rerank_by_lexical(question, scored, top_n=max(top_n, _VECTOR_RECALL_K))
    if not stage1:
        return []
    try:
        from .config import is_rerank_enabled
        if not is_rerank_enabled():
            return stage1[:top_n]
    except Exception:
        pass
    try:
        if not reranker.is_available():
            return stage1[:top_n]
        cands = stage1[:reranker.get_max_candidates()]
        passages = ["{}\n{}\n{}".format(h.get("book_name") or "", h.get("section_path") or "",
                                         h.get("content") or "")
                    for h in cands]
        scores = reranker.score_pairs(question, passages)
        if not scores or len(scores) != len(cands):
            return stage1[:top_n]
        for h, s in zip(cands, scores):
            h["_ce"] = float(s)
        ranked = sorted(cands, key=lambda x: x["_ce"], reverse=True)
        # 严出：挡掉被放宽的召回放进来的噪声（详见 _CE_MIN 说明）。
        # 全部低于门槛时返回空 —— 上层会据此走「未找到」/通用补充，而不是硬塞噪声。
        ranked = [h for h in ranked if float(h.get("_ce") or 0.0) >= _CE_MIN]
        return ranked[:top_n]
    except Exception as e:
        print("⚠️ 二阶重排异常，回退一阶结果: %s" % e)
        return stage1[:top_n]


def _filter_by_score(hits, min_score):
    """过滤掉相关性过低的命中。

    宁可回答「资料中没有」，也不要把无关片段丢给模型 —— 模型会据此编出
    看似合理的错误答案（实测：问某书核心观点时，命中的其实是元数据与
    另一本书的 AdaBoost 内容，模型却编出了一段很像样的"核心观点"）。
    """
    out = []
    try:
        threshold = float(min_score or 0.0)
    except Exception:
        threshold = 0.0
    for h in hits or []:
        try:
            sc = float(h.get("score") or 0.0)
        except Exception:
            continue
        if sc >= threshold:
            out.append(h)
    return out


def _exp_ns_visible(ns, user, cache=None):
    """判断经验帖命名空间（exp_<id>）对当前用户是否可见。

    经验帖不在 books 表里，走不了 user_can_view_book 那套图书/分类权限，
    因此直接查 experience_posts 的 is_public / user_id。
    判定规则与 experience_hub.routes.post_visible_to 严格一致
    （私有帖仅作者本人与管理员可见），但没有 import 那个函数 ——
    experience_hub 与 ai_center 相互引用，直接导入容易形成双向依赖。

    任何异常都 fail-closed（按不可见处理）：宁可少答，不可泄漏私有内容。
    """
    if cache is not None and ns in cache:
        return cache[ns]
    visible = False
    try:
        pid = int(str(ns).split("_", 1)[1])
    except Exception:
        pid = None
    if pid is not None:
        try:
            from core.db_base import db_query_one
            row = db_query_one(
                "SELECT is_public, user_id FROM experience_posts WHERE id = ?", (pid,)
            )
        except Exception:
            row = None
        if row:
            v = row.get("is_public")
            is_private = v is not None and int(v) == 0
            if not is_private:
                visible = True
            elif user and user.get("role") in ("admin", "superadmin"):
                # 管理员可见私有帖，与 experience_hub.post_visible_to 保持一致
                visible = True
            elif user:
                visible = str(user.get("id")) == str(row.get("user_id"))
    if cache is not None:
        cache[ns] = visible
    return visible


def _filter_by_permission(hits, user):
    """按图书/分类资源权限，剔除当前用户无权查看的图书命中。

    与图书级问答 routes.py 里的 user_can_view_book 校验保持同一套判定逻辑，
    确保「全局问答」与「单书问答」的权限边界一致。公开书（无权限规则）直接放行；
    login/selected/admin 等级的受限书，无权限则整本剔除（连来源都不回传，避免泄漏）。
    user 为 None（理论上网关 @login_required 已拦截）时，受限书一律不可见。

    P0-6：图书已删除（books 表里查不到）时 fail-closed，直接丢弃该命中。
    原先 get_book_by_id 返回 None → {} → user_can_view_book 对空书 `return True` 放行，
    导致「已删书但分块尚未 purge」的窗口期内，其正文可被问答读出。
    exp_% 命名空间是经验帖（不在 books 表内），改由 _exp_ns_visible 按其自身的
    公开/私有属性判定，**不再无条件放行** —— 经验帖支持私有后，
    继续放行意味着任何人都能在问答里把别人的私有帖当答案捞出来。

    注：这里刻意没有改动 core/db_base.user_can_view_book 的全局语义 —— 它是
    图书模块共用的判定函数，一刀切改 fail-closed 会连带影响其它调用方，
    因此把「书不存在」这一 AI 问答特有的场景收在本函数内处理。
    """
    if not hits:
        return hits
    try:
        from core.db_base import get_book_by_id, user_can_view_book
    except Exception:
        # 权限模块不可用：宁可少答，不可泄漏受限书内容
        return []
    book_cache = {}
    exp_cache = {}
    out = []
    for h in hits or []:
        bid = h.get("book_id")
        if bid is None:
            continue
        # 经验帖：不在 books 表里，按其自身的公开/私有判定
        if str(bid).startswith("exp_"):
            if _exp_ns_visible(bid, user, exp_cache):
                out.append(h)
            continue
        if bid not in book_cache:
            try:
                book_cache[bid] = get_book_by_id(bid) or {}
            except Exception:
                book_cache[bid] = {}
        book = book_cache[bid]
        # P0-6：查不到图书行 = 书已被删除 → 直接丢弃，不再依赖 user_can_view_book 的放行
        if not book:
            continue
        if user_can_view_book(book, user):
            out.append(h)
    return out


def _strip_table_header(text):
    """去掉 P1（表格表头上下文注入）写进 chunk 正文的「表头：…」展示前缀。

    该前缀是为向量检索 / LLM 上下文补充的列名信息，不应展示给用户——
    来源卡片出现「表头」字样是 引入的回归（91 个 chunk 入库正文被加了此前缀）。
    仅影响展示，不改存储、不改向量、不影响检索与 LLM 上下文。
    """
    if not text:
        return text
    lines = text.split("\n")
    if lines and lines[0].startswith("表头："):
        return "\n".join(lines[1:]).strip()
    return text


def _pack_sources(hits):
    """把检索命中打包成可下发的来源结构。

    带书名与定位信息（页码 / 条款号 / 章节标题），便于前端展示成
    「《书名》P12」或「《书名》5.2 车间电工岗位职责」。
    page / section / heading 都可能取不到，前端需容错。

    - 每条命中补 heading（章节标题，纯正则抽取，不依赖重建索引）；
    - 同一本书的多个 chunk 命中合并成一条，避免「同一个文件反复写、占地方」。
    """
    raw = []
    name_cache = {}
    for h in hits or []:
        try:
            txt = (h.get("content") or "").strip()
            if not txt:
                continue
            txt = _strip_table_header(txt)  # 去掉 P1 注入的「表头：…」展示前缀
            if not txt:
                continue
            bid = h.get("book_id")
            if bid not in name_cache:
                try:
                    from core.db_base import get_book_by_id
                    name_cache[bid] = (get_book_by_id(bid) or {}).get("name") or ""
                except Exception:
                    name_cache[bid] = ""
            heading = ""
            try:
                heading = extract_heading(txt)
            except Exception:
                pass
            # 章节面包屑兜底：无 section_path 时回退书名，保证来源卡片始终有上下文
            # （纯表格/OCR 或无标题开头的正文块自然无章节路径，但至少能定位到《书名》）。
            _sp = (h.get("section_path") or "").strip()
            if not _sp:
                _sp = name_cache[bid] or ""
            raw.append({
                "book_id": bid,
                "book_name": name_cache[bid],
                #页码抽取不可靠（实测「目的」段实为 P1，却标注 4/5/9/10），
                # 按用户要求不再展示页码标注，仅保留准确的章节标题（heading/section_path）。
                "section": h.get("section") or "",
                "section_path": _sp,
                "heading": heading,
                "score": round(float(h.get("score", 0.0)), 4),
                "text": txt[:200],
                # 完整原文（供前端来源卡片「展开看全文」使用，不在预览里截断）
                "full_text": txt,
            })
        except Exception:
            continue
    return _merge_sources_by_book(raw)


def _merge_sources_by_book(items):
    """同一本书的多个 chunk 命中合并成一条来源。

    合并后字段：
      - pages: 去重后的页码列表（升序）
      - sections: 去重后的条款号列表
      - headings: 去重后的章节标题列表（按出现顺序）
      - score: 该书中最高相似度
      - text: 由 headings 拼接成的预览（最多取前 3 个标题），无标题时回退首段
    前端据此渲染成「《书名》 P9, P33, P34：标题1 / 标题2 …」单行。
    """
    if not items:
        return []
    groups = {}
    order = []
    for it in items:
        bid = it.get("book_id")
        if bid not in groups:
            groups[bid] = {
                "book_id": bid,
                "book_name": it.get("book_name", ""),
                "pages": [],
                "sections": [],
                "section_paths": [],
                "headings": [],
                "score": 0.0,
                "text": "",
                # 展开卡片用的完整原文：默认取该书相似度最高的 chunk 全文
                "full_text": "",
            }
            order.append(bid)
        g = groups[bid]
        it_score = it.get("score", 0.0)
        # 命中分刷新为新高时，同步把该 chunk 的完整原文留作展开内容（首个最高分者胜出）
        ft = (it.get("full_text") or "").strip()
        if ft and it_score >= g["score"]:
            g["full_text"] = ft[:4000]
        g["score"] = max(g["score"], it_score)
        if it.get("page") is not None and it["page"] not in g["pages"]:
            g["pages"].append(it["page"])
        sec = it.get("section")
        if sec and sec not in g["sections"]:
            g["sections"].append(sec)
        sp = it.get("section_path")
        if sp and sp not in g["section_paths"]:
            g["section_paths"].append(sp)
        hd = (it.get("heading") or "").strip()
        if hd and hd not in g["headings"]:
            g["headings"].append(hd)
        if not g["text"] and (it.get("text") or "").strip():
            g["text"] = it["text"].strip()
    merged = []
    for bid in order:
        g = groups[bid]
        g["pages"].sort()
        g["score"] = round(g["score"], 4)
        # 预览：优先用章节标题拼接，无标题时回退原文首段
        if g["headings"]:
            g["text"] = " / ".join(g["headings"][:3])
        merged.append(g)
    return merged


def _build_ctx(hits, max_chars=None):
    """把检索命中拼成 LLM 上下文：每条资料带「章节标题」前缀，便于模型按目录结构组织答案。

    截断策略（与历史一致）：命中数 ≤ 2 时不截断（单本书/单 chunk 场景，避免丢 CATATAN 备注等）；
    命中数 > 2 时每条约 600 字符，避免 prompt 过长导致生成失败。

max_chars 未指定时，按当前后端上下文容量自动限流
    （见 get_prompt_budget）。云后端预算充足 → 不触发裁剪，行为与改动前一致；
    本地 llama_cpp（n_ctx=4096）会明显收紧，否则 prompt 溢出上下文、
    尾部的「问题：…」被切断，表现为答非所问或空响应。

    Args:
        hits: 命中列表
        max_chars: 本段资料的字符上限；None = 按后端预算自动取值
    """
    if not hits:
        return ""
    cap = max_chars
    if cap is None:
        try:
            budget = get_prompt_budget()
            cap = int(budget * _CTX_BUDGET_RATIO) if budget else None
        except Exception:
            cap = None
    #同一本书的多条命中合并为一条「资料」，编号与前端来源卡一一对应。
    # 此前同书多 chunk 各占一个号，模型逐条复述同一内容并在句末堆叠 [1][2][3]；
    # 合并后 [n] 即第 n 本书，正文标注与来源卡对得上。组内逐行去重防重叠段重复。
    groups = []
    _gi = {}
    for h in hits:
        key = h.get("book_id") or h.get("book_name") or ("#c" + str(id(h)))
        if key not in _gi:
            _gi[key] = len(groups)
            groups.append([h])
        else:
            groups[_gi[key]].append(h)
    per = 8000 if len(groups) <= 2 else 600
    if cap:
        # 命中书过多时先按预算裁掉排名靠后的（groups 已按相关度排序）
        max_g = max(1, cap // _MIN_CHARS_PER_HIT)
        if len(groups) > max_g:
            groups = groups[:max_g]
        # 再按组均分剩余预算
        per = min(per, max(_MIN_CHARS_PER_HIT, cap // max(1, len(groups))))
    chunks = []
    for gi, ghits in enumerate(groups, 1):
        bname = next((h.get("book_name") or "" for h in ghits if h.get("book_name")), "")
        seen = set()
        parts = []
        for h in ghits:
            for raw_line in (h.get("content") or "").split("\n"):
                ln = raw_line.strip()
                if not ln or ln in seen:
                    continue
                seen.add(ln)
                parts.append(raw_line)
        body = "\n".join(parts).strip()
        if not body:
            continue
        if bname:
            prefix = f"[资料 {gi}｜{bname}] "
        else:
            hd = ""
            try:
                hd = extract_heading(body)
            except Exception:
                pass
            prefix = f"[资料 {gi}｜{hd}] " if hd else f"[资料 {gi}] "
        chunks.append(prefix + body[:per])
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# 文档章节大纲（目录结构）
# ---------------------------------------------------------------------------
# 向量检索对「分类/目录/有哪几个岗位」类问题是结构性失效的：这类问题要的是
# 文档的章节层级，而向量只会召回语义最近的段落（往往是封面/页眉页）。
# 因此离线扫描该书所有分块的章节标题，去重后得到「大纲」，注入上下文，
# 让模型按目录结构组织答案。纯正则抽取，不依赖重建索引。
_OUTLINE_CACHE = {}


def invalidate_book_caches(book_id=None):
    """索引重建/内容变更后清缓存，避免陈旧大纲/IDF 被复用。

    - book_id 给定：只清该书大纲（_OUTLINE_CACHE）。
    - IDF 依赖全库分块分布，任何一次重建都可能改变它，故整体置失效，
      下次提问时按当前指纹惰性重算（见 _get_idf）。
    """
    global _OUTLINE_CACHE, _IDF_CACHE
    if book_id is not None:
        _OUTLINE_CACHE.pop(book_id, None)
    _IDF_CACHE["fp"] = None
    _IDF_CACHE["idf"] = None


def _build_book_outline(book_id):
    """扫描该书所有分块，抽取章节标题并去重，得到文档章节大纲。结果按首现顺序。"""
    if book_id in _OUTLINE_CACHE:
        return _OUTLINE_CACHE[book_id]
    outline = []
    conn = None
    try:
        # 统一走 core.db_base 连接层（WAL/busy_timeout/请求级 teardown 兜底）
        from core.db_base import get_db as _db_connect
        conn = _db_connect()
        cur = conn.cursor()
        cur.execute("SELECT chunk_text FROM document_chunks WHERE book_id=?", (book_id,))
        rows = cur.fetchall()
        cur.execute("SELECT name FROM books WHERE id=?", (book_id,))
        _row = cur.fetchone()
        book_name = (_row[0] or "").strip() if _row else ""
        seen = set()
        # 文档级泛标题无信息量，通用规则排除（不按书名硬编码）：
        # 标题与书名相同、或为书名子串（如《XX岗位职责》中抽出的「岗位职责」）
        # 即文档级标题复现，无导航价值；len>=3 防误杀短章节名。
        for (text,) in rows:
            h = extract_heading(text or "")
            if not h or h in seen:
                continue
            if book_name and (h == book_name or (len(h) >= 3 and h in book_name)):
                continue
            seen.add(h)
            outline.append(h)
    except Exception:
        # ⚠️ 不能把失败结果写进缓存：一次 DB 抖动（锁冲突等）就会让 outline 保持 []
        # 并成为"负面缓存"，此后该书的大纲块、分类作答、章节定位全部失效且永不重试，
        # 直到重建成功触发 invalidate_book_caches 才恢复。失败直接返回、下回重算。
        return outline
    finally:
        # 本函数常在后台线程（无 app context）里被调用，db_base 的请求级
        # teardown 兜底只对有上下文的连接生效，异常路径必须自己 close。
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _OUTLINE_CACHE[book_id] = outline
    return outline


def _book_outline_block(hits):
    """为命中书籍生成『章节结构』块（含使用说明），供模型回答分类/目录类问题。"""
    books = {}
    for h in hits or []:
        books.setdefault(h.get("book_id"), h.get("book_name", ""))
    blocks = []
    for bid, name in books.items():
        ol = _build_book_outline(bid)
        if ol:
            title = ("《" + name + "》") if name else "资料"
            lines = "\n".join(f"{i+1}. {t}" for i, t in enumerate(ol))
            blocks.append(
                f"【{title}章节结构（目录大纲）】回答「分类/目录/有哪几个岗位/分几部分」类问题时，"
                f"请优先依据以下章节标题逐条列出框架：\n{lines}"
            )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 分类 / 目录类问题：确定性大纲作答
# ---------------------------------------------------------------------------
# 弱模型（7B）即便在 prompt 中加「【必须】」也常忽略「章节结构」块，把"岗位分类"
# 答成"职责维度"。对此类问题改为直接依据文档大纲（目录）确定性作答，不依赖模型生成，
# 既保证准确，又秒回（无需等模型推理）。
_CLASSIFY_STRUCT_WORDS = ("分类", "岗位", "目录", "章节", "类别", "类型", "部分",
                          "分成", "包含哪些", "有哪几个", "有几个")
_CLASSIFY_ASK_WORDS = ("有哪", "几个", "哪些", "几类", "几部分", "怎么", "如何", "是否",
                       "吗", "？", "?", "列出", "所有", "全部", "一共")
# 大纲条目过滤：仅保留像「章节/岗位标题」的条目，剔除正文片段假标题
_OUTLINE_KEEP_KW = ("职责", "岗位", "制度", "规定", "办法", "规程", "规范", "细则", "方案",
                    "管理", "流程", "标准", "要求", "体系", "条例", "机制")
_OUTLINE_DROP_HEAD = ("确保", "拟定", "负责", "组织", "开展", "落实", "协助", "参与", "配合",
                      "建立", "制定", "完善", "监督", "检查", "执行", "完成", "做好", "加强",
                      "推进", "统筹", "协调", "根据", "按照", "通过", "对", "为", "在", "是",
                      "由", "将", "把", "使", "让", "需", "应", "要")
# 已知的「2 字章节/岗位前缀」，用于避免把合法标题（如"副班长级""车间电工"）误当成碎片
_OUTLINE_KNOWN_PREFIX = ("副班", "车间", "部门", "技术", "点检", "印尼", "电气", "班组", "岗位",
                         "资料", "经理", "主任", "文员", "主管", "科长", "班长", "员工", "专工",
                         "运维", "公司", "厂区", "安环", "调度", "检修", "运行", "试验", "质量",
                         "安全", "人事", "财务", "行政", "采购", "仓储", "物流", "后勤", "综合",
                         "生产", "设备")


# ---------------------------------------------------------------------------
#具体岗位「X 职责/负责什么」类问题 —— 通用内容召回
# ---------------------------------------------------------------------------
# 根因：向量召回对「部门经理岗位职责」类问题会命中封面/页眉，且「部门经理」与书中
# 「电气运维部经理」词面不完全一致，导致要么被分类路由当成「列全部分类」返回大纲，
# 要么向量分过低被滤掉，最终答非所问或编造。
# 通用修复（对所有书生效，查询时现算，不需重建索引）：
#   1) 抽取问题中的具体岗位实体（车间电工 / 部门经理 / 车间主任 …）；
#   2) 对实体做「去组织前缀」归一（部门经理→经理、车间电工→电工），兼容书中别名；
#   3) 直接对 document_chunks 的 section_path/正文做子串召回，命中即视为该岗位章节原文；
#   4) 经图书权限过滤后，作为命中直接喂给生成，绕过脆弱的向量召回与分类路由。
_ORG_PREFIXES = _OUTLINE_KNOWN_PREFIX  # 组织/部门前缀，匹配时剥离以兼容别名

# 去组织前缀后若只剩这类过于通用的词，当作匹配核心会命中几乎所有文档，
# 反而稀释真正相关的岗位（如「部门经理」剥成「经理」→ 满库命中）。仅保留有明确区分度的核心。
_ROLE_STOPWORDS = {
    "经理", "员工", "人员", "部门", "公司", "岗位", "组长", "主管",
    "主任", "负责人", "成员", "领导", "同事", "干部", "职工",
}

_ROLE_REJECT = ("有哪些", "哪些", "几类", "几个", "怎么", "如何", "是否", "吗",
                "有哪", "一共", "所有", "全部", "分别", "什么", "本", "该",
                "各", "此", "哪", "这", "那")


#Excel 表块召回的极简 token 抽取。无 jieba、无字段词锚点、无 LLM 分类。
# 仅识别「与 chunk 内容可能匹配的固定形态」：
#   1) 引号包裹的中文专名（「郭旭晨」）→ 优先于其他模式；
#   2) 独立 ≥6 位数字串（工号 / 设备编号）；排除 4 位年份；
#   3) 2-6 字中文短语（去通用停用词）；
#   4) 3-20 字英文/数字开头串（型号 / 产品编号）。
# 返回去重列表（最多 8 个），抽不到返回 []。
_EXCEL_TOKEN_STOP = frozenset((
    "的", "了", "是", "在", "和", "与", "或", "及", "把", "被", "给", "为",
    "有", "没", "无", "不", "也", "都", "再", "又", "才", "已", "将",
    "这个", "那个", "这里", "那里", "什么", "谁", "哪", "怎么", "如何",
    "是否", "为何", "多少", "几个", "几", "哪些", "哪个", "哪儿",
    "请", "请问", "麻烦", "一下",
    "今天", "昨天", "明天", "现在", "目前", "之前", "之后", "刚才",
))


def _excel_tokens(q):
    """从 query 抽出可在 Excel 表块里子串匹配的 token 列表。"""
    q = (q or "").strip()
    if not q:
        return []
    cands = []
    # 1) 引号专名
    for m in re.finditer(r"[「\"'“]([\u4e00-\u9fa5]{2,6})[」\"'”]", q):
        cands.append(m.group(1))
    # 2) 独立长数字串（≥6 位；4 位年份如 2026 不抽，避免扫全库）
    for m in re.finditer(r"(?<!\d)(\d{6,20})(?!\d)", q):
        cands.append(m.group(1))
    # 3) 2-6 字中文短语（去停用），并对长 token 加 2 字滑窗覆盖（避免「郭旭晨职务和」
    #    这种连字段词都能抽到的情形丢失短匹配——「郭旭」「旭晨」仍可命中 chunk）
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,6}", q):
        tok = m.group(0)
        if tok not in _EXCEL_TOKEN_STOP:
            cands.append(tok)
            if len(tok) >= 3:
                # 2 字滑窗：把长 token 切成 2 字一段，全收，最大化命中可能
                for i in range(len(tok) - 1):
                    sub = tok[i:i + 2]
                    if sub not in _EXCEL_TOKEN_STOP and len(sub) >= 2:
                        cands.append(sub)
    # 4) 英文 / 编号
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_-]{2,19}", q):
        cands.append(m.group(0))
    out = []
    seen = set()
    for c in cands:
        c = c.strip()
        if len(c) < 2 or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out[:8]


def _excel_table_recall(question, user):
    """Excel 表块专属召回：仅匹配 Excel 表格 chunk（带【工作表：…】/【表格：N】
    标记，或以 P1 注入的「表头：」开头的表数据块）。

    单层逻辑：
      1) 从 query 抽 token（_excel_tokens，无字段词锚点、无 jieba、无分类）；
      2) SQL 只查 Excel 表块 chunk（与一般向量召回隔离，专门路径）；
      3) chunk 正文任一 token 命中即召回；
      4) 经图书权限过滤后按命中 token 数排序，取 top 6。

    命中打高置信分（基础 0.6 ≥ _CONF_STRONG），绕开向量召回对稀有专名天然弱、坠入
    0.40 阈值被滤的短板。命中后直接进 _build_ctx，不再跑向量召回与弱支撑拒答。

    兼容旧数据（仅 P1 注入「表头：」前缀、没有【工作表】标记的 chunk）—— SQL 同时
    匹配这两种形态，新旧数据都不漏。

    返回 (hits, tokens) 或 None。token 抽不到/全部未命中时返回 None。
    """
    tokens = _excel_tokens(question)
    if not tokens:
        return None
    try:
        # 统一走 core.db_base 连接层（WAL/busy_timeout/请求级 teardown 兜底）
        from core.db_base import get_db as _db_connect
        conn = _db_connect()
        cur = conn.cursor()
        # Excel 表块：含【工作表】/【表格】标记，或以 P1 注入的「表头：」开头（兼容旧数据）；
        # 同时保留 books 守卫 / exp_ 命名空间守卫。
        cur.execute(
            "SELECT book_id, chunk_text, section_path, page FROM document_chunks "
            "WHERE (chunk_text LIKE '%【工作表%' "
            "       OR chunk_text LIKE '%【表格%' "
            "       OR chunk_text LIKE '表头：%' "
            "       OR chunk_text LIKE '%\\n表头：%') "
            "  AND (EXISTS(SELECT 1 FROM books b WHERE b.id = document_chunks.book_id) "
            "       OR book_id LIKE 'exp_%')"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None
    # 泛词/高频列名词不得作为拦截依据。仅按数据驱动的动态 DF 判定无区分度 token：
    #   token 在超过 max(3, 30%) 的 Excel 表块 chunk 中出现（如花名册每行都有的
    #   「职务」「工号」），视为高频列名，单独命中不构成「这是一次 Excel 点查」。
    # 这类 token 全部滤除后无可匹配 token 时不再拦截，放行向量召回——Excel 点查
    #   语义不变（专名/数字/≥3 字内容词仍可拦截，如「郭旭晨」滑窗「郭旭」「旭晨」）。
    total = max(1, len(rows))
    generic = set()
    for t in set(tokens):
        df = sum(1 for _, _text, _sp, _pg in rows if t and t in (_text or ""))
        if df >= max(3, total * 0.3):
            generic.add(t)
    eff = [t for t in tokens if t not in generic]
    if not eff:
        return None
    cands = []
    for bid, text, sp, page in rows:
        content = text or ""
        if not content:
            continue
        hit_here = [t for t in eff if t and t in content]
        if not hit_here:
            continue
        score = 0.6 + 0.05 * len(hit_here)  # 字面命中视为高置信（不被弱支撑拒答误杀）
        cands.append({
            "book_id": bid,
            "book_name": "",
            "content": content,
            "chunk_text": content,
            "page": page,
            "section": sp,
            "section_path": sp,
            "_raw": score,
            "score": round(min(score, 1.0), 4),
        })
    if not cands:
        return None
    cands = _filter_by_permission(cands, user)
    if not cands:
        return None
    cands.sort(key=lambda h: h.get("_raw", 0.0), reverse=True)
    for h in cands:
        h["score"] = round(min(h.get("_raw", 0.0), 1.0), 4)
        h.pop("_raw", None)
    return cands[:6], eff


# 字面召回（Excel）与向量召回融合时的加分幅度：同一 chunk 被两路同时命中，
# 说明既有语义相似度又有字面确证，给一次性加分（通用规则，无词表/文件名硬编码）。
_LITERAL_CONFIRM_BONUS = 0.15


def _merge_literal_hits(vector_hits, literal_hits):
    """字面召回与向量召回融合排序 —— 字面命中不再「接管」跳过向量管线，
    而是与向量命中合并去重后按综合分排序。

    规则（对所有文件通用，无任何硬编码）：
      · 同一 chunk（book_id + 正文前 120 字）两路都命中 → 取较高分并加分（双重确证）
      · 仅字面命中 → 保留字面置信分进入候选集（保证稀有专名不被向量阈值滤掉）
      · 仅向量命中 → 分数不变

    意义：即使字面召回误命中无关表块（如 query 的某片段恰好出现在某表块），
    PDF 等正文资料仍留在候选集并参与排序，不会像「接管」那样被整体跳过。
    """
    vec = list(vector_hits or [])
    lit = list(literal_hits or [])
    if not lit:
        return vec
    if not vec:
        return lit

    def _key(h):
        return (
            h.get("book_id") or "",
            (h.get("chunk_text") or h.get("content") or "")[:120],
        )

    merged = {}
    order = []
    for h in vec:
        k = _key(h)
        if k not in merged:
            merged[k] = dict(h)
            order.append(k)
    for h in lit:
        k = _key(h)
        if k in merged:
            base = max(float(merged[k].get("score") or 0.0),
                       float(h.get("score") or 0.0))
            merged[k]["score"] = round(min(base + _LITERAL_CONFIRM_BONUS, 1.0), 4)
        else:
            merged[k] = dict(h)
            order.append(k)
    out = [merged[k] for k in order]
    out.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
    return out


# 「姓名 + 内部属性（工号/职务/部门/手机/身份证/岗位/职级/邮箱/地址/学历/编号/薪资/工资）」
# 类点查问法关键词。命中其一即视为「人员属性查询」，走 _recall_person_attr 精准子串定位。
_PERSON_ATTR_KEYWORDS = (
    "工号", "职务", "部门", "手机", "身份证", "岗位", "职级", "邮箱",
    "地址", "学历", "编号", "卡号", "薪资", "工资", "级别", "姓名",
)


def _recall_person_attr(question, user):
    """人员属性精准召回（P2 行级原子切片配套的检索路径）：仅处理「姓名 + 内部属性」类点查。

    与 _excel_table_recall 的区别：后者按通用 token 命中、返回 top6 整块（仍可能因
    属性列在每行列出现而混入无关行）；本函数按「原子行内 `姓名：<人名>` 精确子串」定位，
    直接返回含该人那一行的原子 chunk，杜绝稀释与误召回。

    人名候选：从 query 枚举所有 2-4 字中文片段（排除属性词），按长度降序尝试
    `姓名：<候选>` 精确子串定位——避免 `_excel_tokens` 只产整串+2 字滑窗、抽不到
    「孙天伟」这类 3 字人名的问题。若人名未中但有 >=6 位数字串（工号/身份证），
    回退 `：<数字>`（匹配 `工号：…`/`身份证号：…`）。

    命中打高置信分（人名 0.9 / 数字 0.85），绕开向量召回对稀有专名天然弱、坠入阈值被滤
    的短板；命中后经图书权限过滤，防越权读受限花名册。返回 (hits, name) 或 None。
    """
    q = (question or "").strip()
    if not any(k in q for k in _PERSON_ATTR_KEYWORDS):
        return None
    # 人名候选：2-4 字中文片段（非属性词），去重且长候选优先
    name_cands = []
    seen = set()
    for seg in re.findall(r"[\u4e00-\u9fa5]{2,4}", q):
        for L in (4, 3, 2):
            for s in range(0, len(seg) - L + 1):
                c = seg[s:s + L]
                if c not in _PERSON_ATTR_KEYWORDS and c not in seen:
                    seen.add(c)
                    name_cands.append(c)
    name_cands.sort(key=len, reverse=True)
    # 长数字串回退（工号 / 身份证号点查）
    digits = [t for t in _excel_tokens(q) if re.fullmatch(r"\d{6,20}", t)]
    if not name_cands and not digits:
        return None
    # 构建 SQL 参数：人名 LIKE 在前，数字 LIKE 在后（便于区分命中类型）
    patterns = ["%姓名：" + c + "%" for c in name_cands]
    for d in digits:
        patterns.append("%：" + d + "%")
    try:
        # 统一走 core.db_base 连接层（WAL/busy_timeout/请求级 teardown 兜底）
        from core.db_base import get_db as _db_connect
        conn = _db_connect()
        cur = conn.cursor()
        # 图书守卫 / exp_ 命名空间守卫（与 _excel_table_recall / _recall_role_content 一致）
        sql = (
            "SELECT book_id, chunk_text, section_path, page FROM document_chunks "
            "WHERE ("
            + " OR ".join("chunk_text LIKE ?" for _ in patterns)
            + ") AND (EXISTS(SELECT 1 FROM books b WHERE b.id = document_chunks.book_id) "
            "       OR book_id LIKE 'exp_%')"
        )
        rows = cur.execute(sql, patterns).fetchall()
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    cands = []
    for bid, text, sp, page in rows:
        content = text or ""
        if not content:
            continue
        # 取最长命中的人名（name_cands 已按长度降序）
        matched_name = next((c for c in name_cands if ("姓名：" + c) in content), None)
        if matched_name:
            score, hit_name = 0.9, matched_name
        elif digits and any(("：" + d) in content for d in digits):
            score, hit_name = 0.85, None
        else:
            continue  # 理论不触发（SQL 已要求命中其一）
        cands.append({
            "book_id": bid,
            "book_name": "",
            "content": content,
            "chunk_text": content,
            "page": page,
            "section": sp,
            "section_path": sp,
            "_raw": score,
            "_hit_name": hit_name,
            "score": round(min(score, 1.0), 4),
        })
    cands = _filter_by_permission(cands, user)
    if not cands:
        return None
    cands.sort(key=lambda h: h.get("_raw", 0.0), reverse=True)
    best_name = next((h.get("_hit_name") for h in cands if h.get("_hit_name")), None)
    for h in cands:
        h.pop("_raw", None)
        h.pop("_hit_name", None)
    return cands, best_name


def _extract_role_entity(q):
    """从问题中抽取具体岗位/角色实体；抽不到返回 None（非具体岗位问题）。"""
    q = (q or "").strip()
    if not q:
        return None
    # 优先：X岗位职责 / X职责 / X责任
    m = re.search(r"([\u4e00-\u9fa5]{2,10})(?:的)?(?:岗位)?(?:职责|责任)", q)
    if not m:
        # 次选：X负责什么 / 干什么 / 做什么 / 工作内容 …
        m = re.search(r"([\u4e00-\u9fa5]{2,10})(?:的)?(?:岗位)?(?:负责什么|干什么|做什么|工作内容|工作范围|主要工作|岗位要求|任职要求)", q)
    if not m:
        return None
    ent = m.group(1).strip()
    if len(ent) < 2:
        return None
    if any(r in ent for r in _ROLE_REJECT):
        return None
    return ent


def _strip_org_prefix(word):
    """迭代剥离组织/部门前缀（电气运维部经理 → 经理）。

    兼容「电气运维部」+「部门」拼接导致的首字粘连（部部），剥前缀后若残留首字
    与次字相同（如「部部门经理」→「部门经理」），再剥一个首字。
    """
    w = word or ""
    changed = True
    while changed and len(w) > 1:
        changed = False
        for p in _ORG_PREFIXES:
            if w.startswith(p) and len(w) > len(p):
                w = w[len(p):]
                changed = True
                break
    # 去首字粘连：如「部部门经理」→「部门经理」
    if len(w) > 2 and w[0] == w[1]:
        w = w[1:]
    return w


def _role_match_cores(entity):
    """返回用于子串匹配的核心词（原词 + 去组织前缀后的核心）。

去前缀后的核心若是 _ROLE_STOPWORDS 中的通用词（经理/员工/…），
    不作为匹配核心，避免子串匹配命中几乎所有文档、稀释真正相关的岗位。
    """
    cores = [entity]
    core = _strip_org_prefix(entity)
    if core and core != entity and core not in _ROLE_STOPWORDS:
        cores.append(core)
    return cores


def _recall_role_content(question, user):
    """具体岗位问题的通用内容召回：返回 (命中列表, 实体) 或 None。

    对所有书生效；直接读 document_chunks 子串匹配，不依赖向量召回，不重建索引；
    命中经图书权限过滤（与全局问答同一套判定），防越权读受限书。
    """
    entity = _extract_role_entity(question)
    if not entity:
        return None
    cores = _role_match_cores(entity)  # 例：["部门经理", "经理"]
    try:
        # 统一走 core.db_base 连接层（WAL/busy_timeout/请求级 teardown 兜底）
        from core.db_base import get_db as _db_connect
        conn = _db_connect()
        cur = conn.cursor()
        # P0-6：图书删除后其分块要等下次重建才被 purge，这段时间里这些残留分块
        # 会被本函数全表扫到，再经「XX职责」类问题被读出去。这里只扫
        # 「books 表仍存在」或经验帖命名空间（exp_%，本就不在 books 表内）的分块，
        # 从源头堵住已删书正文泄漏。
        cur.execute(
            "SELECT book_id, chunk_text, section_path, page FROM document_chunks "
            "WHERE EXISTS(SELECT 1 FROM books b WHERE b.id = document_chunks.book_id) "
            "   OR book_id LIKE 'exp_%'"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None
    cands = []
    for bid, text, sp, page in rows:
        sp = sp or ""
        content = text or ""
        if not content:
            continue
        title_hit = any(c and c in sp for c in cores)
        content_hit = any(c and c in content for c in cores)
        if not (title_hit or content_hit):
            continue
        score = 0.0
        if title_hit:
            score += 0.6
        if content_hit:
            score += 0.3
        if entity in (sp + content):
            score += 0.2
        cands.append({
            "book_id": bid,
            "book_name": "",
            "content": content,
            "chunk_text": content,
            "page": page,
            "section": sp,
            "section_path": sp,
            # 内部排序用原始分（可能 >1，纯启发式）；展示分在下方归一化到 [0,1]，
            # 避免 cosine 风格的来源卡片出现 1.1 这类误导性数值。
            "_raw": score,
            "score": round(score, 4),
        })
    if not cands:
        return None
    # 图书权限过滤（防越权）：剔除当前用户无权查看的图书
    cands = _filter_by_permission(cands, user)
    if not cands:
        return None
    # 按原始分排序，再归一化展示分到 [0,1]
    cands.sort(key=lambda h: h.get("_raw", 0.0), reverse=True)
    for h in cands:
        h["score"] = round(min(h.get("_raw", 0.0), 1.0), 4)
        h.pop("_raw", None)
    return cands[:6], entity


def _clean_outline(outline):
    """剔除大纲中的正文片段假标题，仅保留像章节/岗位标题的条目，保持首现顺序。"""
    out, seen = [], set()
    for t in outline or []:
        if not t or t in seen:
            continue
        # 1) 先剔除明显正文片段（以动词/叙述词开头，如"确保…"/"拟定…"）
        if t[:2] in _OUTLINE_DROP_HEAD:
            continue
        # 2) 标题抽取偶尔会从词中部截断（如"岗位安全管理制度"→"位安全管理制度"），
        #    若首 2 字不是已知合法前缀，仅当「去掉首字后新首 2 字确为合法前缀」时才剥掉
        #    该 stray 首字 —— 避免把合法标题（如"全厂岗位安全管理制度"）误截成乱码。
        if (len(t) > 4 and t[:2] not in _OUTLINE_KNOWN_PREFIX
                and 4 <= len(t) - 1 <= 22 and t[1:3] in _OUTLINE_KNOWN_PREFIX
                and any(k in t[1:] for k in _OUTLINE_KEEP_KW)):
            t = t[1:]
        if not t or t in seen:
            continue
        # 3) 长度与章节关键词过滤
        if len(t) < 4 or len(t) > 24:
            continue
        if not any(k in t for k in _OUTLINE_KEEP_KW):
            continue
        seen.add(t)
        out.append(t)
    return out


def _is_classification_question(q):
    """判断是否为「分类/目录/有哪几个岗位/分几部分」类问题（应依据大纲确定性作答）。

若问题点名了某个具体岗位/角色（"车间电工岗位职责有哪些"），
    则它不是「列全部分类」问题，应走内容召回而非返回整本大纲。
    """
    q = (q or "").strip()
    if not q:
        return False
    if _extract_role_entity(q):
        return False
    if not any(w in q for w in _CLASSIFY_STRUCT_WORDS):
        return False
    if not (q.endswith(("？", "?")) or any(w in q for w in _CLASSIFY_ASK_WORDS)):
        return False
    return True


def _deterministic_outline_answer(hits, question):
    """对分类/目录类问题，依据命中书籍的章节大纲确定性作答。返回答案字符串或 None。"""
    if not hits:
        return None
    books = {}
    for h in hits:
        books.setdefault(h.get("book_id"), h.get("book_name", ""))
    # 优先取大纲条目最多的一本书
    best = None
    for bid, name in books.items():
        ol = _clean_outline(_build_book_outline(bid))
        if ol and (best is None or len(ol) > len(best[2])):
            best = (bid, name, ol)
    if not best or len(best[2]) < 3:
        return None
    bid, name, ol = best
    title = ("《" + name + "》") if name else "该资料"
    items = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(ol))
    return (
        f"根据{title}的章节结构（目录），{question.strip().rstrip('?？')}如下：\n"
        f"{items}\n\n"
        f"（以上分类依据该资料目录结构整理，共 {len(ol)} 类；各岗位/章节的具体职责详见对应章节。）"
    )


# ---------------------------------------------------------------------------
# 低置信拒答：抑制「弱相关资料 + 通用知识」硬编造
# ---------------------------------------------------------------------------
# 场景：命中里没有任何一条达到强相关（向量分 < 0.55）且问题关键词也没命中任何一条时，
# 说明检索到的是「字面沾边、实则无关」的片段。若照常生成，模型会把这些片段当依据
# 编出看似有据的答案 —— 与「分类答成职责维度」并列为「答错」的两大来源。
_CONF_STRONG = 0.55  # 与 _rerank_by_lexical 的 strong 阈值保持一致


def _is_weak_support(hits, question):
    """判断是否「弱支撑」：有命中，但没有任何一条算强相关。

    强相关 = 向量分 >= _CONF_STRONG，或问题关键词命中该命中的书名/正文。
    hits 为空返回 False（空命中由上层「未找到」分支独立处理）。
    """
    if not hits:
        return False
    try:
        terms = _extract_terms(question or "")
    except Exception:
        terms = set()
    for h in hits:
        try:
            #二阶 cross-encoder 已介入并判定该命中相关（_ce 存在即过 _CE_MIN 门槛），
            # 以 CE 分为准，不再被向量分（召回已放宽到 0.30）误判为弱相关而拒答。
            # 仅在 CE 可用且本命中带 _ce 时生效；CE 不可用时退回向量分判定（原逻辑）。
            if h.get("_ce") is not None:
                return False
            if float(h.get("score") or 0.0) >= _CONF_STRONG:
                return False
        except (TypeError, ValueError):
            pass
        if terms:
            body = "{} {}".format(h.get("book_name") or "", h.get("content") or "")
            try:
                # 必须用「命中比例」而非「任一命中」：问题切出的 2~3 字判别词数量多
                # （如"公司/年会/旅游/组织/怎么"），文档里碰巧含一个常用词就命中，
                # 实测会让 weak 判定恒为 False、拒答机制形同虚设。故要求过半命中。
                if _lexical_score(terms, body) >= 0.5:
                    return False
            except Exception:
                pass
    return True


def _weak_support_notice():
    """弱支撑时插入 prompt 的警示前缀，让模型不要把弱相关片段当确定依据。"""
    return (
        "⚠️ 注意：下方资料与问题的相关性较低，只是「可能相关」的片段，不足以作为确定依据。"
        "如据此无法得出确定结论，请直接说明资料未覆盖，不要从资料中强行推断。\n\n"
    )


# ---------------------------------------------------------------------------
# 章节定位精准召回：问「某岗位/某章节的职责或内容」时绕过向量召回
# ---------------------------------------------------------------------------
# 与分类问题同理：向量召回对「具体章节内容」结构性失效 —— 问「车间电工的职责」，
# 召回的却是封面/页眉页（含文档大标题"电气运维部岗位职责"），目标章节根本进不了
# top 榜。改为直接用章节名在 document_chunks 里定位该章节分块，确保模型看到的就是
# 正确章节，从源头消除「张冠李戴」式的答错。
_SECTION_ASK_WORDS = ("职责", "负责什么", "干什么", "做什么", "工作内容", "工作范围",
                      "岗位要求", "任职要求", "主要工作", "是做什么", "流程",
                      "步骤", "如何开展", "怎样进行", "内容是", "讲什么")
# 章节标题的通用后缀，匹配前先剥离，得到可与问题比对的核心词
_SECTION_TAIL = ("岗位职责", "管理制度", "管理规定", "管理办法", "操作规程", "管理规范",
                 "实施细则", "工作方案", "职责", "岗位", "制度", "规定", "办法",
                 "规程", "规范", "细则", "方案", "流程", "标准", "要求")


def _section_core(title):
    """剥离章节标题的通用后缀得到核心词（"车间电工岗位职责" → "车间电工"）。"""
    t = (title or "").strip()
    for suf in sorted(_SECTION_TAIL, key=len, reverse=True):
        if t.endswith(suf) and len(t) - len(suf) >= 2:
            return t[: len(t) - len(suf)].strip()
    return t


def _match_section_target(question, outline):
    """在章节大纲中找与问题最匹配的目标章节，返回 (章节标题, 核心词) 或 None。

    取最长匹配，避免"电工"抢在"车间电工"前面命中（后者更精确）。
引入角色实体核心词（去组织前缀）做双向子串比对，
    兼容「部门经理」与书中「电气运维部经理」这类词面不一致。
    """
    q = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", question or "")
    if not q or not outline:
        return None
    ent = _extract_role_entity(question)
    ent_cores = _role_match_cores(ent) if ent else []

    def _pick(min_len, trim_tail):
        """在核心词中挑选最长命中项。trim_tail=True 时核心词去掉末字后比对。"""
        best = None
        for t in outline:
            core = _section_core(t)
            if trim_tail:
                if len(core) < 3:
                    continue
                core = core[:-1]
            if len(core) < min_len:
                continue
            # 归一比对（C）：去组织前缀 + 角色实体核心词双向子串
            core_norm = _strip_org_prefix(core)
            q_norm = _strip_org_prefix(q)
            matched = (core in q or core_norm in q or core in q_norm or core_norm in q_norm
                       or any(ec in core or core in ec for ec in ent_cores)
                       or any(ec in q or q in ec for ec in ent_cores))
            if not matched:
                continue
            if best is None or len(core) > len(best[1]):
                best = (t, core)
        return best

    # 1) 核心词完整出现在问题中且 >=3 字（"车间电工" in "车间电工的职责"），取最长
    # 2) 退化匹配：核心词去末字（"点检员工" → "点检员"），处理口语省略
    # 3) 兜底：放宽到 2 字（"部门""安全"等泛词），仅在前面都无命中时才启用，
    #    避免泛词抢在精确条目前面命中
    return _pick(3, False) or _pick(3, True) or _pick(2, False)


def _is_section_lookup_question(q):
    """是否为「问某个具体章节/岗位内容」的问题。"""
    q = (q or "").strip()
    return bool(q) and any(w in q for w in _SECTION_ASK_WORDS)


def _db_path():
    """定位 book_manager.db（与 _build_book_outline 同一套 4 层 dirname 算法）。"""
    import os
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, "book_manager.db")


def _focus_hits_by_section(book_id, book_name, section, user):
    """直接取该书属于指定章节的分块作为命中（绕过向量召回）。

    section 来自 _clean_outline 清洗后的大纲，而 chunk 的 heading 是原始抽取值，
    故同时比较「原值相等」与「清洗后相等」，并兼容互相包含。
    返回命中列表（已过权限过滤），失败返回 []。
    """
    try:
        # 统一走 core.db_base 连接层（WAL/busy_timeout/请求级 teardown 兜底）
        from core.db_base import get_db as _db_connect
        conn = _db_connect()
        cur = conn.cursor()
        # P0-6：与 _recall_role_content 同款守卫 —— 已删除图书的分块不得被直接取出。
        # exp_% 是经验帖命名空间（不在 books 表内），按原逻辑放行。
        cur.execute(
            "SELECT chunk_text, page FROM document_chunks "
            "WHERE book_id=? "
            "  AND (EXISTS(SELECT 1 FROM books b WHERE b.id = document_chunks.book_id) "
            "       OR book_id LIKE 'exp_%') "
            "ORDER BY chunk_index",
            (book_id,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return []
    hits = []
    for text, page in rows:
        try:
            hd = extract_heading(text or "")
        except Exception:
            continue
        if not hd:
            continue
        cleaned = _clean_outline([hd])
        # 注意：只接受「section 是 hd 的子串」（hd 更具体，如带编号的 "1. 车间电工岗位职责"），
        # 绝不能反向用「hd 是 section 的子串」—— 否则泛标题「岗位职责」会被
        # 「车间电工岗位职责」包含而误命中，把别的岗位章节混进来（反而加剧答错）。
        if not (hd == section
                or (cleaned and cleaned[0] == section)
                or (len(section) >= 4 and section in hd)):
            continue
        hits.append({
            "book_id": book_id,
            "book_name": book_name,
            "content": text or "",
            "chunk_text": text or "",
            "page": page,
            "section": section,
            "score": 1.0,  # 章节名精确匹配，视为最强相关
        })
    if not hits:
        return []
    # 安全：章节定位绕过向量召回，同样必须过权限过滤，防止越权读到受限书正文
    try:
        hits = _filter_by_permission(hits, user)
    except Exception:
        return []
    return hits


def _locate_section_hits(hits, question, user):
    """若问题是在问某个具体章节/岗位的内容，则绕过向量召回，直接取该章节分块。

    返回 (新 hits, 新 ctx, 章节标题)；无法定位或定位后仍为空则返回 None（回退原命中）。
    """
    if not hits:
        return None
    books = {}
    for h in hits:
        books.setdefault(h.get("book_id"), h.get("book_name", ""))
    target = None
    for bid, name in books.items():
        ol = _clean_outline(_build_book_outline(bid))
        m = _match_section_target(question, ol)
        if m and (target is None or len(m[1]) > len(target[3])):
            target = (bid, name, m[0], m[1])
    if not target:
        return None
    bid, name, section = target[0], target[1], target[2]
    sec_hits = _focus_hits_by_section(bid, name, section, user)
    if not sec_hits:
        return None
    return sec_hits, _build_ctx(sec_hits), section


def ask_question_about_book(book_id, question, top_k: int = 5):
    """针对某本图书的内容问答（保守模式，严格依据本书已索引内容）。

    拒绝机制：按该书档位的相关性阈值过滤命中。低于阈值的命中视为「资料中没有」，
    直接返回未找到提示，绝不把无关片段丢给模型去编造（精确档阈值更严，0.45）。
    """
    #涉密锁定同样拦单书问答（与全局问答一致）。
    try:
        from .config import is_security_locked
        if is_security_locked():
            return {"answer": "当前已开启涉密锁定，AI 问答已禁用，无法读取图书正文。",
                    "sources": [], "security_locked": True}
    except Exception:
        pass
    try:
        from . import vector_store, embedder
        from .config import get_book_qa_mode, get_min_score
        from core.db_base import get_book_by_id

        qv = _encode_query(question)
        if qv is None:
            return {"answer": "向量模型未就绪，无法检索本书内容。", "sources": []}

        # 取本书档位，确定相关性阈值（精确档最严，公共档最松）
        book = get_book_by_id(book_id) or {}
        qa_mode = get_book_qa_mode(book)
        min_score = get_min_score(qa_mode)

        #复用全局问答检索链路，保证图书详情页问答与首页能力一致
        # （此前单书问答缺二阶精排/角色直取/分类·章节路由，更容易答错）。
        # 单书问答不跨书，故不传 user 做跨书权限过滤（沿用原路由的登录 + 模块守卫）。
        hits = []
        _focus_title = ""
        # 1) 具体岗位「X职责」类问题：优先于向量召回直接子串命中章节原文
        _role = _recall_role_content(question, None)
        if _role:
            _role_hits = [h for h in _role[0] if h.get("book_id") == book_id]
            if _role_hits:
                hits, _focus_title = _role_hits, _role[1]
        # 2) 向量召回 + 二阶 cross-encoder 精排（粗筛宽进、精排严出）
        if not hits:
            raw = vector_store.query(book_id, qv, top_k=top_k, use_parent=False) or []
            if not raw:
                return {"answer": "本书尚未建立索引，请先在配置中心 → AI 中心 → 重建索引。", "sources": []}
            scored = _filter_by_score(raw, min(min_score, _RECALL_SCORE_FLOOR))
            ranked = _rerank_two_stage(question, scored)
            if not ranked:
                return {
                    "answer": "本书已索引的资料中未找到与问题相关的内容。可换个问法，"
                              "或确认索引是否覆盖了相关章节（配置中心 → AI 中心 → 重建索引）。",
                    "sources": [],
                }
            hits = ranked
        # 3) 分类/目录类问题：确定性依据文档大纲作答（避免模型幻觉）
        if _is_classification_question(question) and not _focus_title:
            _det = _deterministic_outline_answer(hits, question)
            if _det:
                return {"answer": _det, "mode": qa_mode, "sources": _pack_sources(hits)}
        # 4) 章节定位精准召回：问「某章节职责/内容」时直接取该章节分块
        if _is_section_lookup_question(question) and not _is_classification_question(question) and not _focus_title:
            _loc = _locate_section_hits(hits, question, None)
            if _loc:
                hits, ctx, _focus_title = _loc
        # 5) 弱支撑提示：命中相关性低时引导模型不要强行推断
        _focus_note = ""
        if _focus_title:
            _focus_note = (
                f"\n【重要】以下问题询问的是「{_focus_title}」章节的内容，下方资料即该章节原文；"
                f"请【仅依据该章节】逐条罗列，不要混入其他章节或其他岗位的内容。\n\n"
            )
        _weak = (not _focus_title) and _is_weak_support(hits, question)
        _weak_note = _weak_support_notice() if _weak else ""

        #单本书命中 ≤ 2 时不再硬截（避免漏掉 CATATAN 备注等关键字段）。
        ctx = _build_ctx(hits)
        _ol = _book_outline_block(hits)
        if _ol:
            ctx = ctx + "\n\n" + _ol
        prompt = (
            "请仅根据以下本书资料回答，不要编造资料之外的内容。\n"
            "请按资料原样逐项/逐字段罗列（包含双语字段对照），不要合并、不要省略字段名。\n"
            #strict 模式下，资料代码常因 PDF/DOCX 提取丢失缩进/代码块标记，
            # 导致回答平铺。允许模型仅恢复其标准语法缩进与 ``` 围栏（还原结构、不改内容），
            # 其余资料仍须原样——只影响含代码的回答，不波及非代码回答。
            "若资料中的代码因提取丢失缩进或代码块标记，可恢复为该语言标准语法的缩进并补充 ``` 语言 围栏；"
            "仅恢复结构与格式，不得增删或改写任何代码逻辑与含义；其余资料仍须原样。\n"
            "资料编号后标注的「章节标题」即该段所属章节，请依此层级组织答案；"
            "若下方提供「章节结构（目录大纲）」块，回答「分类 / 目录 / 有哪几个岗位 / 分几部分」类问题"
            "【必须】逐条列出该块中的章节标题作为答案框架，禁止从资料段落自行归纳为其他维度"
            "（如把岗位分类答成「安全管理 / 设备维护」）。\n\n"
            + _focus_note + _weak_note +
            f"资料：\n{ctx}\n\n问题：{question}\n\n回答："
        )
        answer = llm_generate(prompt, max_tokens=600, temperature=0.2)
        if not answer:
            return {"answer": MODEL_NO_RESPONSE_MSG, "sources": _pack_sources(hits)}
        #引用校验。单书问答的「严格档」= precise；precise 下不合规答案退回保守回答，
        # general/public 仅附校验说明不降级。
        ok, note = _verify_grounding(answer, _pack_sources(hits), question, qa_mode)
        if not ok and qa_mode == "precise":
            return {
                "answer": "本书已索引的资料不足以支撑一个确切的回答，为避免误导不作推断。\n\n"
                          "（引用校验：答案部分内容在所提供的资料中未能找到依据。）",
                "sources": _pack_sources(hits),
                "mode": qa_mode,
                "grounding": False,
                "grounding_note": note,
            }
        return {
            "answer": answer.strip(),
            "sources": _pack_sources(hits),
            "mode": qa_mode,
            "grounding": ok,
            "grounding_note": (note if not ok else ""),
        }
    except Exception as e:
        return {"answer": f"问答失败：{e}", "sources": []}
