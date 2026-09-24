"""
工作自动化流程 —— 流程模块契约（Flow Contract）

任何“流程”都是 modules/toolbox/automation/flows/ 下的一个 Python 模块。
框架会在启动时自动扫描该目录，发现所有符合契约的模块并展示到工具箱。

一个合法的流程模块必须满足：

  1) 模块级常量（用于前端展示与表单生成）：
       FLOW_ID     : str  唯一标识，**必须与文件名一致**（如文件 invoice_upload.py
                          则 FLOW_ID = "invoice_upload"）：
                          get_flow(flow_id) 是按 flow_id 当模块名去 import flows.<flow_id> 的，
                          两者不一致会出现「前端看得到流程、点下去报 404 未找到流程」。
       FLOW_NAME   : str  展示名（如 "发票上传流程"）
       FLOW_ICON   : str  emoji 图标
       FLOW_DESC   : str  一句话说明
       FLOW_PARAMS : list 参数表单 schema（写法参考 tools-config.js 里其它工具的 params）
       FLOW_ACCEPT : str  上传文件类型（如 ".xlsx,.zip"），None 表示不需要上传
       FLOW_STREAM : bool 是否走 SSE 实时进度（长任务建议 True）

  2) 入口函数：
       def run(work_dir: str, params: dict):
           '''在 work_dir 内执行流程，把日志写 run.log、进度写 progress.json、
              结束写 done.flag。框架据此通过 SSE 回传前端。'''

  3) 流程文件放在 flows/ 目录下 —— 该目录已被 .gitignore 忽略，
     私有流程（含内部地址 / 账号逻辑）可安全存放于此而不进入公开仓库；
     而框架（registry / runner / 前端）全部开源。
     参考 examples/example_flow.py 复制改写即可新增流程。

本文件提供两个可选辅助函数，供流程模块写日志与进度。
"""

import os
import json


def write_log(work_dir, msg):
    path = os.path.join(work_dir, "run.log")
    line = msg if msg.endswith("\n") else msg + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def write_progress(work_dir, current, total, status, extra=None):
    data = {"current": current, "total": total, "status": status}
    if extra:
        data.update(extra)
    with open(os.path.join(work_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def finish(work_dir, status="done", extra=None):
    """标记流程结束（写 done.flag）—— 流程收尾时**必须**调用。

    SSE 端点只看 done.flag 判断流程是否结束，而 write_log/write_progress 都不写它。
    漏掉这一步的后果：流程早已跑完，前端进度条却一直转到 30 分钟轮询耗尽才报
    「进度拉取超时」。runner.py 现在有兜底补写，但流程仍应显式调用以便携带状态。
    """
    data = {"current": 100, "total": 100, "status": status}
    if extra:
        data.update(extra)
    with open(os.path.join(work_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open(os.path.join(work_dir, "done.flag"), "w", encoding="utf-8") as f:
        f.write(str(status))
