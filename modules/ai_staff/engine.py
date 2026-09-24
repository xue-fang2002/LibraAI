"""ReAct 编排器（本模块唯一调用 LLM 的地方）+ 内存任务状态机。

为什么用「文本协议」而不是 function calling：
  项目现有的 llm_generate(prompt, max_tokens, temperature, timeout, history, stream)
  没有 tools 参数，且后端是 7B 量级模型，工具调用格式遵从率不稳定。
  所以这里用最朴素的 ReAct 文本协议（思考 / 行动 / 答案），并做容错解析：
  - 找不到「行动:」但能找到「答案:」→ 直接结束
  - 工具名不在清单里 → 把可用工具名回灌，让它重试
  - JSON 解析失败 → 把报错原文回灌，让它重写
  - 超过轮次上限 → 强制收尾，要求它基于已有观察作答

⚠️ llm_generate 是同步阻塞 + 串行推理锁，所以任务必须跑在后台线程里，
   绝不能在请求线程中直接调用。
"""
import json
import os
import re
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, Dict, List, Optional

from . import guard, tools

MAX_ROUNDS = 6                 # 工具调用轮次上限
MAX_OBS_CHARS = 1600           # 回灌给模型的观察长度上限
TASK_TTL = 30 * 60             # 任务在内存中的存活时间

_RE_ANSWER = re.compile(r"答案\s*[:：](.*)", re.S)
_RE_ACTION_LOOSE = re.compile(r"行动\s*[:：]\s*([A-Za-z_][\w]*)")
_RE_THOUGHT = re.compile(r"思考\s*[:：](.*?)(?=行动\s*[:：]|答案\s*[:：]|$)", re.S)


SYSTEM_PROMPT = """你是「{name}」，是本项目（一个 Flask 应用）的 AI 员工。
{persona}

你只能使用下面列出的只读工具来取证，然后基于真实证据回答问题。

可用工具（调用时只能使用这些名字）：
{tools}

严格按下面的格式输出，每次只输出一个行动或最终答案，不要输出多余解释：
思考: 你这一步要查什么、为什么
行动: 工具名({{"参数名": "值"}})

收到「观察:」的结果是工具返回的真实数据。你可以继续下一轮：
思考: ...
行动: ...

证据足够后输出：
答案: 最终答复

项目地图（modules/ 下的模块目录名，即模块英文 id）：
{project_map}

铁律：
1. 绝不编造文件路径、行号、字段名或查询结果；没查到就直说没查到。
2. 答案里必须带上真实证据（文件路径 + 行号，或真实的查询/日志结果）。
3. 工具报错时读错误信息，换个参数重试；同一工具连续失败 2 次就换思路。
4. 最多 {rounds} 轮，必须在此之前给出答案。
5. 数据库只允许 SELECT；不要把数据库里的敏感内容（密码、密钥）写进答案。
6. 用户的问题是中文，但代码里的变量/函数/配置项/字段名都是英文：
   检索前先把中文意图翻译成可能的英文标识符（例：切片 → chunk/max_chars/split，
   侧栏 → sidebar，权限 → permission/access，日志 → log）。
7. 禁止用相同参数重复调用同一个工具；一次没命中就换英文词根、换工具，
   或先用 list_dir 看目录结构再定位。重复无效调用会被强制打断。
8. 答案里每处「文件:行号」都会被引擎回查；引用不存在的文件会被判为编造并打回重写。
9. 回答「在哪 / 去哪 / 某功能在哪个模块」时，模块名与入口路径只能**原样引用**
   观察里 site_map / tool_usage 返回的 name 与 path 字段，一字不许改。
   观察里没出现过的模块名、入口名一律不许写；源码目录名不是页面名称，禁止自己翻译。
   查不到就回答「我没找到这个功能」，并说明你查过哪些关键词——
   不知道就说不知道，编一个答案比不回答更糟。
"""


def _project_map(root: str) -> str:
    """列出 modules/ 下的模块目录名（模块英文 id）。

    7B 最常翻车的地方就是把中文功能名猜成错的英文（「AI 员工」→ ai_employee，
    真实是 ai_staff），然后一路搜不到。把真实目录清单摆出来，这一步就不用猜了。
    """
    try:
        base = os.path.join(root, "modules")
        names = sorted(
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d)) and not d.startswith("_")
        )
    except Exception:
        return ""
    return "、".join(names[:40]) if names else ""


def _tools_prompt(allowed: Optional[List[str]] = None) -> str:
    """工具清单提示词；allowed 非空时只列出该员工被放行的工具。"""
    specs = tools.TOOL_SPECS
    if allowed:
        specs = [t for t in specs if t["name"] in allowed]
    lines = []
    for t in specs:
        args = ", ".join(f'{k}（{v}）' for k, v in t["args"].items())
        lines.append(f"- {t['name']}：{t['desc']}；参数：{args}")
    return "\n".join(lines)


def _llm(prompt: str, max_tokens: int = 400) -> Optional[str]:
    """调用 LLM；不可用时返回 None。"""
    try:
        from modules.ai_center.internal.qa import llm_generate
        return llm_generate(prompt, max_tokens=max_tokens, temperature=0.1)
    except Exception as e:
        print(f"[ai_staff] LLM 不可用: {e}")
        return None


def _parse(raw: str) -> Dict[str, Any]:
    """从模型输出里解析出 答案 / 行动 / 思考。"""
    out: Dict[str, Any] = {"thought": "", "answer": "", "tool": "", "args": {}}
    if not raw:
        return out
    m = _RE_ANSWER.search(raw)
    if m:
        out["answer"] = m.group(1).strip()
        tm = _RE_THOUGHT.search(raw)
        if tm:
            out["thought"] = tm.group(1).strip()
        return out
    tm = _RE_THOUGHT.search(raw)
    if tm:
        out["thought"] = tm.group(1).strip()
    am = _RE_ACTION_LOOSE.search(raw)
    if am:
        out["tool"] = am.group(1)
        # JSON 单独取：从工具名之后第一个 { 到最后一个 }，
        # 这样模型把 JSON 写成多行、或前后带多余文字时也尽量能解析出来。
        s = raw.find("{", am.end())
        e = raw.rfind("}")
        if s == -1 or e <= s:
            out["args"] = {}
        else:
            try:
                out["args"] = json.loads(raw[s:e + 1])
            except Exception:
                out["args"] = None
        if not isinstance(out["args"], dict):
            out["args"] = None
    return out


def _clip(obj: Any, limit: int = MAX_OBS_CHARS) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "...(已截断)"


# 隐私工具集：这些工具的参数含个人信息（生日、时辰），审计与前端推送都不许带上
_PRIVATE_TOOLS = {t["name"] for t in tools.TOOL_SPECS if t.get("private_args")}


# ============================================================ 主流程
def run(question: str, root: str, ctx: Dict[str, Any], emit) -> None:
    """执行一次问答编排。emit(event_dict) 用于把过程推给前端。"""
    staff = (ctx or {}).get("staff") or {}
    allowed = list(staff.get("tools") or [])
    transcript = [
        SYSTEM_PROMPT.format(
            name=staff.get("name") or "AI 员工",
            persona=staff.get("persona") or "基于只读工具取证后作答。",
            tools=_tools_prompt(allowed),
            project_map=_project_map(root) or "（未取到）",
            rounds=MAX_ROUNDS,
        ),
        f"\n用户的问题：{question}\n",
    ]
    used: Dict[str, int] = {}
    sig_used: Dict[str, int] = {}
    seen_files: set = set()      # 本次取数真正见过的文件（第二层证据校验用）
    seen_lines: set = set()      # 本次取数真正见过的 (文件, 行号)
    seen_paths: set = set()      # 本次取数真正见过的导航路径（site_map/tool_usage）
    seen_modules: set = set()    # 本次取数真正见过的模块名（第三层导航校验用）
    any_hit = False              # 是否至少有一次真正命中（全没命中时要它老实说没找到）
    answer = ""

    # ---- 表单预取：用户填表时已经在后端算完，结果直接注入，不必让模型再调一次工具 ----
    # 这是「人力参谋」这类表单型员工的关键设计：7B 的第一步行动很难猜对，
    # 与其赌它调工具，不如先把算好的事实摆进观察里，它只需做解读。
    pre = (ctx or {}).get("pre")
    if pre and pre.get("text"):
        emit({"type": "tool", "round": 0, "name": pre.get("tool") or "", "args": {},
              "result": {"ok": True, "text": pre.get("text"), "report": pre.get("report")},
              "ok": True, "pre": True})
        transcript.append(
            "\n观察: " + str(pre["text"]) + "\n"
            "（以上是系统根据用户填写的信息直接算出的结果，真实且完整。"
            "禁止再次调用同一工具，禁止重新推算或修改其中的任何数字、四柱与结论；"
            "你的任务只是把它组织成通顺的中文。）\n")
        any_hit = True

    for rnd in range(1, MAX_ROUNDS + 1):
        prompt = "\n".join(transcript) + "\n"
        raw = _llm(prompt)
        if raw is None:
            emit({"type": "error", "msg": "AI 后端不可用或超时（请确认 ollama/本地模型已就绪）"})
            emit({"type": "done"})
            return
        parsed = _parse(raw)
        if parsed["thought"]:
            emit({"type": "step", "round": rnd, "text": parsed["thought"][:500]})

        if parsed["answer"]:
            answer = parsed["answer"]
            break

        if not parsed["tool"]:
            transcript.append(
                "\n（上一轮输出格式不对，必须包含「行动: 工具名({...})」或「答案: ...」）\n")
            emit({"type": "step", "round": rnd, "text": "输出格式不对，已要求重试"})
            continue

        name = parsed["tool"]
        args = parsed["args"]
        if args is None:
            transcript.append(
                f"\n行动: {name} 的参数 JSON 无法解析，请重新输出一行"
                f'「行动: {name}({{"参数": "值"}})」，参数必须是合法 JSON。\n')
            emit({"type": "step", "round": rnd, "text": f"{name} 参数解析失败，要求重写"})
            continue

        # 同一工具 + 同一参数连用：7B 最常见的死循环（中文词反复搜英文代码）。
        # 第二次就直接拦下来并强制换路，别让它把轮次预算烧光。
        sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        sig_used[sig] = sig_used.get(sig, 0) + 1
        if sig_used[sig] >= 2:
            transcript.append(
                f"\n（你刚刚用完全相同的参数调用过 {name}，结果也一样。"
                "禁止再用同样参数重试：请换英文词根、换工具，或先用 list_dir 看目录结构；"
                "实在找不到就直说没找到。）\n")
            emit({"type": "step", "round": rnd,
                  "text": f"检测到重复调用 {name}（相同参数），已要求换思路"})
            continue

        used[name] = used.get(name, 0) + 1
        if allowed and name not in allowed:
            result: Dict[str, Any] = {
                "ok": False,
                "error": f"该员工无权使用工具：{name}",
                "available": allowed,
            }
        else:
            result = tools.call_tool(name, root, ctx, args)
            # 含个人信息的工具：审计只记工具名，不落参数
            if name in _PRIVATE_TOOLS:
                guard.audit(ctx.get("user"), "tool", target=name,
                            detail=f"staff={staff.get('id', '')} args=[隐私参数已脱敏]")
            else:
                guard.audit(ctx.get("user"), "tool", target=name,
                            detail=f"staff={staff.get('id', '')} q={question[:80]} args={_clip(args, 400)}")
        tools.collect_refs(result, seen_files, seen_lines)
        tools.collect_nav_refs(result, seen_paths, seen_modules)
        if result.get("ok") and (result.get("hit_count") or result.get("row_count")
                                 or result.get("tables") or result.get("path")
                                 or result.get("report")):
            any_hit = True
        emit({
            "type": "tool",
            "round": rnd,
            "name": name,
            "args": {} if name in _PRIVATE_TOOLS else args,
            "result": result,
            "ok": bool(result.get("ok")),
        })
        transcript.append(f"\n行动: {name}({_clip(args, 300)})\n观察: {_clip(result)}\n")

        if used.get(name, 0) >= 3:
            transcript.append(f"\n（{name} 已调用多次，请换工具或直接给答案）\n")

    if not answer:
        # 轮次用尽：再给一次机会，明确要求只输出答案
        miss = ("本轮所有检索都没命中任何结果，不要编造。" if not any_hit else "")
        transcript.append(
            f"\n已达轮次上限。{miss}请只输出「答案: ...」，基于上面已有的观察作答；"
            "没查到就直说没查到，并列出你试过的关键词。\n")
        raw = _llm("\n".join(transcript), max_tokens=500)
        parsed = _parse(raw or "")
        answer = parsed.get("answer") or (raw or "").strip() or "没有在限定轮次内得出结论，请缩小问题范围再问一次。"

    answer = _check_evidence(answer, root, transcript, emit, seen_files, seen_lines,
                              any_hit, seen_paths, seen_modules)

    emit({"type": "final", "text": answer})
    emit({"type": "done"})


def _check_evidence(answer: str, root: str, transcript: List[str], emit,
                    seen_files: set, seen_lines: set, any_hit: bool = True,
                    seen_paths: Optional[set] = None,
                    seen_modules: Optional[set] = None) -> str:
    """答案出闸前的证据校验。

    第一层（硬）：引用的文件不存在 / 行号越界 → 一定是编造，打回重写一次。
    第二层（软）：文件确实存在，但这一行本次取数没真正命中过（张冠李戴）
                → 不删答案，但在前面挂警告，让用户知道这条没被证实。
    第三层（硬→软）：导航说法（「X 模块」「A → B → C」）在本次 site_map /
                tool_usage 观察里找不到原文 → 打回重写一次；仍不过关则挂警告。
    """
    bad = tools.verify_evidence(root, answer)
    if bad:
        emit({"type": "step", "text": f"证据校验：{len(bad)} 处引用对不上（{'；'.join(bad)}）"})
        miss = "本轮检索没有命中任何结果。" if not any_hit else ""
        transcript.append(
            "\n（证据校验未通过：你引用的 " + "；".join(bad) +
            " 并不存在或行号越界——这些不是你观察里出现过的内容。" + miss +
            "请重新输出「答案: ...」，只引用你真实见过的文件路径与行号；"
            "如果确实没查到，就直说没查到，并列出你试过的关键词，不要编。）\n")
        raw = _llm("\n".join(transcript) + "\n", max_tokens=500)
        parsed = _parse(raw or "")
        fixed = parsed.get("answer") or (raw or "").strip()
        bad2 = tools.verify_evidence(root, fixed) if fixed else bad
        if fixed and not bad2:
            emit({"type": "step", "text": "证据校验：修正后通过"})
            answer = fixed
        else:
            return (f"⚠️ 以下引用未能核实（可能不存在或行号越界）：{'；'.join(bad2 or bad)}"
                    f"\n\n{fixed or answer}")

    soft = tools.unverified_refs(answer, seen_files, seen_lines)
    if soft:
        emit({"type": "step", "text": f"证据校验：{len(soft)} 处引用本次未直接命中，已标注"})
        return (f"⚠️ 以下引用本次取数没有直接命中，请自行复核：{'；'.join(soft)}\n\n{answer}")

    # 第三层：导航说法（模块名 / 箭头路径）必须能在观察里找到原文
    nav = tools.unverified_nav(answer, seen_paths or set(), seen_modules or set())
    if nav:
        emit({"type": "step", "text": f"导航校验：{len(nav)} 处说法没有依据（{'；'.join(nav)}），打回重写"})
        transcript.append(
            "\n（导航校验未通过：你答案里的 " + "；".join(nav) +
            " 并不是你观察里出现过的内容。要求：\n"
            "1. 模块名和入口路径只能原样照抄 site_map / tool_usage 返回的 name 与 path 字段；\n"
            "2. 观察里确实没有 → 直接回答「我没找到这个功能」，并说明查过哪些关键词；\n"
            "3. 如果那只是描述操作步骤（不是导航路径），请改成不带箭头的说法。\n"
            "请重新输出「答案: ...」。不知道就说不知道，不要编。）\n")
        raw = _llm("\n".join(transcript) + "\n", max_tokens=500)
        parsed = _parse(raw or "")
        fixed = parsed.get("answer") or (raw or "").strip()
        nav2 = tools.unverified_nav(fixed, seen_paths or set(), seen_modules or set()) if fixed else nav
        if fixed and not nav2:
            emit({"type": "step", "text": "导航校验：修正后通过"})
            answer = fixed
        else:
            emit({"type": "step", "text": "导航校验：仍未通过，答案前挂警告"})
            answer = (f"⚠️ 以下说法未经核实，可能不存在：{'；'.join(nav2 or nav)}\n\n{fixed or answer}")
    return answer


# ============================================================ 任务状态机
class Task:
    def __init__(self, tid: str, question: str, user_id: str = "", staff_id: str = ""):
        self.tid = tid
        self.question = question
        self.user_id = str(user_id or "")      # 归属人：SSE 只允许本人或管理员拉取
        self.staff_id = str(staff_id or "")
        self.q: Queue = Queue()
        self.thread: Optional[threading.Thread] = None
        self.cancelled = False
        self.created_at = time.time()
        self.finished = False

    def owned_by(self, user) -> bool:
        """任务归属校验：防止凭 task_id 读取别人的取数过程与答案。"""
        if not isinstance(user, dict):
            return False
        if str(user.get("role") or "").lower() in ("admin", "superadmin"):
            return True
        uid = str(user.get("id") or "")
        return bool(uid) and uid == self.user_id

    def emit(self, event: Dict[str, Any]):
        if self.cancelled:
            return
        self.q.put(event)
        if event.get("type") == "done":
            self.finished = True


_TASKS: Dict[str, Task] = {}
_LOCK = threading.Lock()
_RUNNING = 0                     # 同时只允许 1 个任务（LLM 推理锁是串行的）
MAX_CONCURRENT = 1


def create_task(question: str, root: str, ctx: Dict[str, Any],
                pre: Optional[dict] = None) -> Optional[Task]:
    global _RUNNING
    with _LOCK:
        _reap()
        if _RUNNING >= MAX_CONCURRENT:
            return None
        _RUNNING += 1
        tid = uuid.uuid4().hex[:12]
        owner = (ctx or {}).get("user") or {}
        staff = (ctx or {}).get("staff") or {}
        task = Task(tid, question,
                    user_id=str(owner.get("id") or ""),
                    staff_id=str(staff.get("id") or ""))
        _TASKS[tid] = task

    # pre 只随本次任务流转，任务结束后随任务对象一起被回收（内存中没有第二份副本）
    run_ctx = dict(ctx or {})
    if pre:
        run_ctx["pre"] = pre

    def _worker():
        global _RUNNING
        try:
            run(question, root, run_ctx, task.emit)
        except Exception as e:
            task.emit({"type": "error", "msg": f"{type(e).__name__}: {e}"})
            task.emit({"type": "done"})
        finally:
            with _LOCK:
                _RUNNING -= 1

    t = threading.Thread(target=_worker, daemon=True, name=f"ai_staff_{tid}")
    task.thread = t
    t.start()
    return task


def get_task(tid: str) -> Optional[Task]:
    with _LOCK:
        return _TASKS.get(tid)


def cancel_task(tid: str) -> bool:
    task = get_task(tid)
    if not task:
        return False
    task.cancelled = True
    task.finished = True
    task.q.put({"type": "done"})
    return True


def _reap():
    """清理过期任务，避免内存无限增长（调用方需持有 _LOCK）。"""
    now = time.time()
    dead = [k for k, v in _TASKS.items() if now - v.created_at > TASK_TTL]
    for k in dead:
        _TASKS.pop(k, None)


def next_event(task: Task, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    try:
        return task.q.get(timeout=timeout)
    except Empty:
        return None


def busy() -> bool:
    with _LOCK:
        return _RUNNING >= MAX_CONCURRENT
