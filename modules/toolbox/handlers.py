import os
import json
import base64
import urllib.parse
import html
import re
import time
import uuid as uuid_mod
from datetime import datetime
import sys
import subprocess
from core.response import ok, fail

# 共享 helper 已收拢到 tools/_common.py（工具目录化重构，见 tools/README 约定）。
# 这里以**原名别名**导入：既有 handle_* 一行都不用改，重构可逐类推进。
from .tools._common import (                      # noqa: E402
    get_temp_dir,
    safe_temp_id,
    in_out as _in_out,
    input_files as _input_files,
    cjk_font as _cjk_font,
    ocr_with_tesseract as _ocr_with_tesseract,
    office_to_pdf as _office_to_pdf,
)
from .tools import registry as _tools_registry    # noqa: E402


def dispatch(category, tool, temp_id, params):
    """统一分发器"""
    key = f"{category}_{tool}"
    handler_map = {
        # 图片处理 —— 已迁入 tools/image/（registry 自动发现，见 dispatch ①）
        # 新增图片工具直接在 tools/image/ 下加 .py，不必再动本表。
        # 文档处理 —— 已迁入 tools/document/（registry 自动发现，见 dispatch ①）
        # 新增文档工具直接在 tools/document/ 下加 .py，不必再动本表。
        # 文本工具 —— 已迁入 tools/text/（registry 自动发现，见 dispatch ①）
        # 新增文本工具直接在 tools/text/ 下加 .py，不必再动本表。
        # text_markdown / system_color_picker / system_clipboard 为纯前端工具，
        # 不进分发表（config 里标 frontendOnly，前端也不会发起处理请求）
        # 识别工具 / 系统工具 —— 已迁入 tools/recognition/、tools/system/
        # （registry 自动发现，见 dispatch ①）。至此 handler_map 已清空：
        # 新增任何工具都只需在 tools/<分类>/ 下加 .py，不再需要动本文件。
    }
    # 工作自动化流程（RPA 长任务）为可插拔框架：按 category 统一分发，
    # 具体流程由 flows/ 目录下符合契约的模块提供，不在 handler_map 写死。
    if category == "automation":
        return handle_automation_run(tool, temp_id, params)
    # ① 先查新目录 tools/<分类>/<工具>.py（registry 自动发现，见 tools/registry.py）。
    #    迁移期 tools/ 逐步填充，命中即走新实现。
    mod = _tools_registry.get(key) or _tools_registry.get(str(tool or ""))
    if mod is not None:
        return mod.run(temp_id, params)

    # ② 回落到旧表（尚未迁移的工具仍走这里）—— 保证重构期间零行为变化，可随时暂停。
    handler = handler_map.get(key)
    if not handler:
        # 前端工具 id 本身已带分类前缀（如 image_compress），直接按 tool 再查一次
        handler = handler_map.get(tool)
    if not handler:
        return fail(f"未知工具: {category}/{tool}", code=400)
    return handler(temp_id, params)


def handle_not_implemented(temp_id, params):
    return fail("该功能尚未实现，敬请期待", code=501)


# ==================== 工作自动化流程（RPA 长任务，可插拔框架） ====================
def handle_automation_run(flow_id, temp_id, params):
    """通用自动化流程启动入口。

    - 经 registry 校验 flow_id 对应的流程模块是否存在（私有流程在 flows/ 下）；
    - 写参数 JSON -> 单实例锁 -> 后台 subprocess 调起通用 runner.py，立即返回；
    - 真实进度由通用 SSE 端点 /api/automation/<flow_id>/stream/<temp_id> 回传。

    公司专属逻辑（如 OA 地址/令牌）全部封装在 flows/<flow_id>.py 内，
    本文件不写任何公司特定代码，因此可安全提交到公开仓库。
    """
    if not flow_id:
        return fail("缺少流程标识 flow_id", code=400)

    temp_dir = get_temp_dir()
    if not temp_id:
        # 无文件流程（如「每日学习打卡」）：前端没有上传环节，temp_id 为空。
        # 框架早期按"自动化流程必带上传文件"（OA 结算）写死了拦截，这里改为
        # 自动分配一个干净工作目录——SSE 端点按同一约定读取三件套，零特殊分支。
        temp_id = "nofile_%s_%s" % (
            datetime.now().strftime("%Y%m%d%H%M%S"), uuid_mod.uuid4().hex[:6])
        work_dir = os.path.join(temp_dir, temp_id)
        os.makedirs(work_dir, exist_ok=True)
    else:
        # temp_id 来自请求，必须消毒后再拼路径：绝对路径会丢弃基目录、
        # "../.." 能跳出临时目录根（详见 tools/_common.safe_temp_id）。
        work_dir = os.path.join(temp_dir, safe_temp_id(temp_id))
        if not os.path.isdir(work_dir):
            return fail("临时目录不存在，请重新上传", code=400)

    # 启动核心已抽到 automation/launcher.py（ai_agent 异步执行器共用，零复制）：
    # 参数落盘、凭据剥离到环境变量、清进度文件、单实例锁、subprocess 调 runner.py
    from modules.toolbox.automation.launcher import launch_flow
    launch_ok, code, msg = launch_flow(flow_id, work_dir, params)
    if not launch_ok:
        return fail(msg, code=code)

    return ok(data={"temp_id": temp_id, "stream": True})




# ============================================================
#补齐：图片/PDF/文档/识别/系统 等原「未实现」功能
# 约定：与既有 handler 一致，输出到 temp_id/out，返回 ok(data={"results":[...]}) 或
#       ok(data={"result": "文本结果"})；缺依赖时返回 503 + 安装提示，不抛裸异常。
# ============================================================

# 注：_CJK_FONT_CANDIDATES / _cjk_font / _in_out / _input_files
# 已迁入 tools/_common.py，本文件顶部以原名别名导入，下方 handler 无需改动。

# 注：识别工具（qrcode_gen/scan、ocr_image/screenshot/pdf）与系统工具（rename）
# 已迁入 tools/recognition/、tools/system/，由 registry 自动发现。
