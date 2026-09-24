"""
通用流程运行入口：由 handlers.py 以 subprocess 调起。

用法： python runner.py <flow_id> <params.json>

- params.json 位于任务临时目录 work_dir 内，故 temp_dir = 该文件所在目录；
- 框架把项目根目录加入 sys.path 后，按 flow_id 动态加载对应流程模块并调用其 run()。
"""

import os
import sys
import json
import importlib

# 把项目根目录加入 sys.path（runner.py 位于 modules/toolbox/automation/，上溯 4 级即根）
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main():
    if len(sys.argv) < 3:
        print("usage: python runner.py <flow_id> <params.json>", file=sys.stderr)
        sys.exit(2)

    flow_id = sys.argv[1]
    params_path = sys.argv[2]

    try:
        with open(params_path, "r", encoding="utf-8") as f:
            params = json.load(f)
    except Exception as e:
        print(f"读取参数失败: {e}", file=sys.stderr)
        sys.exit(3)

    # 用后即删：参数文件落盘即可能被扫描泄露，读完立刻清除
    try:
        os.remove(params_path)
    except Exception:
        pass

    # 敏感凭据经环境变量传递（不落盘），读入后立即从环境中抹除
    try:
        env_user = os.environ.pop("TB_FLOW_USER", None)
        env_pwd = os.environ.pop("TB_FLOW_PWD", None)
        if env_user and not params.get("user"):
            params["user"] = env_user
        if env_pwd and not params.get("pwd"):
            params["pwd"] = env_pwd
    except Exception:
        pass

    temp_dir = os.path.dirname(os.path.abspath(params_path))

    try:
        mod = importlib.import_module(f"modules.toolbox.automation.flows.{flow_id}")
    except Exception as e:
        print(f"加载流程失败: {flow_id}: {e}", file=sys.stderr)
        sys.exit(4)

    exit_code = 0
    try:
        mod.run(temp_dir, params)
    except Exception as e:  # noqa: BLE001
        print(f"流程执行异常: {e}", file=sys.stderr)
        exit_code = 5
    finally:
        # 框架级保底 ①：SSE 端点（workflow/routes.py）**只靠 done.flag 判断流程结束**。
        # 写 done.flag 的要求只写在 base.py 的注释契约里，流程模块极易漏掉
        # （例如 examples/example_flow.py 就没写），结果是流程早已跑完，前端却要
        # 空转到 1800 次轮询耗尽才报「进度拉取超时」。这里一律补写，
        # 流程自己已写过则不覆盖（保留其自定义内容）。
        try:
            _flag = os.path.join(temp_dir, "done.flag")
            if not os.path.exists(_flag):
                with open(_flag, "w", encoding="utf-8") as f:
                    f.write(str(exit_code))
        except Exception:
            pass
        # 框架级保底 ②：释放 launcher.py 建的单实例锁。锁此前只有具体流程
        # 自己清理，其余流程（含被强杀的）残留后，复用同一 temp_id 再点会永久
        # 返回 409「该任务已在运行中」。
        try:
            _lock = os.path.join(temp_dir, "run.lock")
            if os.path.exists(_lock):
                os.remove(_lock)
        except Exception:
            pass

    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
