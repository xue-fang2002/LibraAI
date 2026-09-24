"""
示例流程（模板）—— 复制本文件到 flows/ 目录并改名，即可新增一个自动化流程。

这是开源框架的一部分（位于 examples/，会出现在公开仓库），用于演示契约写法。
它不执行任何真实操作，仅说明一个流程模块该怎么写。

注意：放到 flows/ 下才会被框架自动发现。flows/ 已被 .gitignore 忽略，
所以你公司自己的流程文件放在那里不会进入 GitHub。
"""

from modules.toolbox.automation.base import write_log, write_progress

# ---- 1) 流程元数据（前端据此渲染工具卡片与参数表单）----
# ⚠️ FLOW_ID 必须与文件名一致：get_flow() 按 flow_id 当模块名导入 flows.<flow_id>，
#    不一致会导致「页面看得到流程、点下去报 404」。复制本文件改名时记得同步改 FLOW_ID。
FLOW_ID = "example_flow"                  # 唯一标识，必须与文件名一致
FLOW_NAME = "示例流程"                     # 工具箱里显示的名字
FLOW_ICON = "🧩"                           # 图标
FLOW_DESC = "演示如何编写一个自动化流程（不执行真实操作）"
FLOW_PARAMS = [
    {"name": "user", "label": "登录账号", "type": "text", "value": ""},
    {"name": "pwd", "label": "登录密码", "type": "password", "value": ""},
    {"name": "wait_sec", "label": "步骤等待秒数", "type": "number", "min": 1, "max": 30, "value": 4, "unit": "s"},
]
FLOW_ACCEPT = None                        # 不需要上传文件；需要则写如 ".xlsx,.zip"
FLOW_STREAM = True                        # 长任务走 SSE 实时进度


# ---- 2) 入口函数：在 work_dir 内执行，日志/进度写入该目录 ----
def run(work_dir, params):
    write_log(work_dir, "🚀 示例流程启动")
    steps = ["步骤一：登录", "步骤二：处理业务", "步骤三：保存结果"]
    for i, s in enumerate(steps, 1):
        write_progress(work_dir, i, len(steps), "running")
        write_log(work_dir, f"  -> {s}")
        # 这里放你的自动化逻辑（Playwright / 接口调用 / 文件处理 …）
        # 例如：用 params.get("user") / params.get("pwd") 登录内部系统
    write_progress(work_dir, len(steps), len(steps), "done", {"success": len(steps)})
    write_log(work_dir, "🏁 示例流程结束")
