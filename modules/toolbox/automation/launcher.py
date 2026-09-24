"""自动化流程通用启动器（纯 Python，无 Flask 依赖）。

从 handlers.handle_automation_run 中抽出的启动核心，供两处复用：
  1. toolbox 的 HTTP 入口（handle_automation_run）；
  2. ai_agent 的异步执行器（AI 任务中心把流程当 async 能力跑）。

职责：校验流程存在 → 写参数 JSON（凭据剥离到子进程环境变量，不落盘）→
清理上一轮进度文件 → 单实例锁 → 后台 subprocess 调起通用 runner.py。
真实进度由流程自身写 work_dir 下的 run.log / progress.json / done.flag 外抛。
"""
import os
import sys
import json
import subprocess

from modules.toolbox.automation.registry import get_flow

# 与 handlers 原实现完全一致的失败码：404=流程不存在 409=已在运行 500=环境问题

# flow 子进程需要的一组重型依赖；只有全部可用的解释器才拿来跑流程。
_FLOW_REQUIRED_MODS = ["playwright", "pandas", "ddddocr", "cv2", "openpyxl"]


def _resolve_flow_python():
    """挑一个装齐 flow 依赖的解释器来跑子进程。

    默认继承 app.py 的解释器(sys.executable)。一旦 app 被某个没装这些包的
    Python 拉起（例如 IDE 用 managed python 重启服务），子进程会直接报
    「No module named 'playwright'」并卡在「执行中」。这里优先探测 anaconda
    （实测依赖最齐全），再回落 sys.executable；都不行就退回 sys.executable
    保持原行为，不引入更坏的情况。
    """
    candidates = []
    # 经验路径：anaconda 自带全套依赖，优先
    _ana = r"D:/anaconda/python.exe"
    if os.path.exists(_ana):
        candidates.append(_ana)
    if sys.executable and sys.executable not in candidates:
        candidates.append(sys.executable)

    probe = (
        "import importlib.util as u;"
        "mods=" + repr(_FLOW_REQUIRED_MODS) + ";"
        "print('OK' if all(u.find_spec(m) for m in mods) else 'MISSING')"
    )
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        try:
            out = subprocess.run(
                [cand, "-c", probe],
                capture_output=True, text=True, timeout=25,
            )
            if out.returncode == 0 and "OK" in (out.stdout or ""):
                return cand
        except Exception:
            continue
    return sys.executable


def launch_flow(flow_id, work_dir, params):
    """在 work_dir 里启动一个自动化流程（立即返回，不等待完成）。

    返回 (ok: bool, code: int, msg: str)。ok=False 时 code/msg 可直接
    映射成 HTTP fail(code, msg) 或任务失败原因。
    """
    mod = get_flow(flow_id)
    if mod is None:
        return False, 404, f"未找到自动化流程: {flow_id}（该流程可能未在本机安装）"

    payload = dict(params or {})
    payload["flow_id"] = flow_id
    payload["work_dir"] = work_dir
    # 凭据明文不落盘：从 payload 剥离，改经子进程环境变量传递，
    # runner 读入后即从环境中抹除，params.json 只含非敏感参数。
    flow_user = payload.pop("user", None)
    flow_pwd = payload.pop("pwd", None)

    params_path = os.path.join(work_dir, "params.json")
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    # 清理上一轮产物
    for fn in ("run.log", "progress.json", "done.flag", "progress.txt"):
        p = os.path.join(work_dir, fn)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # 单实例锁，避免同一任务并发
    lock = os.path.join(work_dir, "run.lock")
    if os.path.exists(lock):
        return False, 409, "该任务已在运行中，请等待完成后再试"
    open(lock, "w").close()

    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
    if not os.path.exists(runner):
        try:
            os.remove(lock)
        except Exception:
            pass
        return False, 500, "自动化运行器缺失: " + runner

    # flow 子进程默认继承 app.py 的解释器(sys.executable)。一旦 app 被某个
    # 没装 playwright/pandas 等依赖的 Python 拉起（例如 IDE 用 managed python
    # 重启），子进程会直接报「No module named 'playwright'」并卡在「执行中」。
    # 这里优先探测 anaconda（实测依赖最齐全），再回落 sys.executable。
    flow_python = _resolve_flow_python()

    # 子进程输出重定向到 run.log：真实报错（流程 import 失败 / OA token 过期 /
    # 登录页 selector 变化等）原本被 DEVNULL 吞掉，导致「卡着且日志什么都没有」。
    # run.log 是流程自身也在写的同一个文件，worker 会逐行抽进 AI 任务的 progress.log。
    run_log_path = os.path.join(work_dir, "run.log")
    try:
        child_std = open(run_log_path, "a", encoding="utf-8", errors="replace")
    except Exception:
        child_std = subprocess.DEVNULL
    try:
        child_env = os.environ.copy()
        if flow_user:
            child_env["TB_FLOW_USER"] = str(flow_user)
        if flow_pwd:
            child_env["TB_FLOW_PWD"] = str(flow_pwd)
        child = subprocess.Popen(
            [flow_python, runner, flow_id, params_path],
            cwd=work_dir,
            stdout=child_std,
            stderr=child_std,
            env=child_env,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        # 把子进程 PID 写进锁文件，供「停止」接口精准杀进程树
        # （playwright 还会拉起 chromium 子进程，必须连根杀）。
        try:
            with open(lock, "w", encoding="utf-8") as lf:
                lf.write(str(child.pid))
        except Exception:
            pass
    except Exception as e:
        try:
            if child_std is not subprocess.DEVNULL:
                child_std.close()
        except Exception:
            pass
        try:
            os.remove(lock)
        except Exception:
            pass
        return False, 500, f"启动自动化失败: {e}"

    return True, 200, ""


def cancel_flow(work_dir):
    """停止 work_dir 下的自动化流程：杀掉子进程树（含 playwright 拉起的
    chromium 子进程）+ 清理锁与进度标记，并写 cancelled.flag 让 SSE 立即感知。

    返回 (ok: bool, msg: str)。
    """
    lock = os.path.join(work_dir, "run.lock")
    pid = None
    if os.path.exists(lock):
        try:
            with open(lock, "r", encoding="utf-8") as lf:
                pid = int((lf.read() or "0").strip() or "0") or None
        except Exception:
            pid = None

    if pid:
        try:
            if os.name == "nt":
                # /T 杀整棵进程树（含 chromium），/F 强制
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=15,
                )
            else:
                import signal
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            pass

    # 清理标记文件：SSE 循环据此得知任务已结束
    for fn in ("run.lock", "progress.json", "done.flag", "progress.txt"):
        p = os.path.join(work_dir, fn)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    # 写「已取消」标记，SSE 优先检测它（避免旧进程 finally 又写回 done.flag）
    try:
        with open(os.path.join(work_dir, "cancelled.flag"), "w", encoding="utf-8") as f:
            f.write("cancelled")
    except Exception:
        pass
    return True, "已发送停止指令"
