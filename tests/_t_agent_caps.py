"""P3 验证：ToolboxConnector 应从工具箱 registry 自动生成能力，无需硬编码。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from modules.ai_agent.connectors.toolbox import ToolboxConnector, _INPLACE_FILE_TOOLS
from modules.ai_agent.connectors.base import EXEC_INPLACE, EXEC_JUMP
from modules.toolbox.tools import registry as tregs

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


print("== registry 工具数 ==")
all_tools = tregs.all_tools()
print(f"  registry 扫描到 {len(all_tools)} 个工具")
check(len(all_tools) == 31, f"registry 应有 31 个工具（实际 {len(all_tools)}）")

print("== 连接器能力生成 ==")
conn = ToolboxConnector()
caps = conn.capabilities()
cids = [c.cid for c in caps]
print(f"  生成 {len(caps)} 个能力")
check(len(caps) == len(all_tools), f"能力数应等于工具数（{len(all_tools)}）")
check(cids == sorted(cids), "能力应按 cid 排序（registry 已排序）")

# 每个 cid 形如 toolbox.<TOOL_ID>
check(all(c.cid.startswith("toolbox.") for c in caps), "所有 cid 以 toolbox. 开头")

print("== 执行方式默认值 ==")
by_id = {c.cid: c for c in caps}

# 无文件工具 → 默认 inplace
check(by_id["toolbox.text_json_fmt"].execution == EXEC_INPLACE, "text_json_fmt 默认 inplace")
check(by_id["toolbox.text_json_fmt"].needs_files is False, "text_json_fmt 无文件")
check(by_id["toolbox.text_json_fmt"].handler == "text_json_fmt", "text_json_fmt handler = TOOL_ID")
check(by_id["toolbox.recognition_qrcode_gen"].execution == EXEC_INPLACE, "qrcode_gen 默认 inplace")

# 白名单内「有文件」工具 → inplace（保留直接执行体验）
check(by_id["toolbox.image_compress"].execution == EXEC_INPLACE, "image_compress 白名单→inplace")
check(by_id["toolbox.image_compress"].needs_files is True, "image_compress 需文件")
check(by_id["toolbox.document_pdf_merge"].execution == EXEC_INPLACE, "pdf_merge 白名单→inplace")

# 白名单内的「有文件」工具 → inplace（image_convert 在白名单里）
check(by_id["toolbox.image_convert"].execution == EXEC_INPLACE, "image_convert 白名单→inplace")
# 不在白名单的「有文件」工具 → jump（避免长任务阻塞）
check(by_id["toolbox.document_word_tools"].execution == EXEC_JUMP, "word_tools 默认 jump（需 Office）")
check(by_id["toolbox.document_ppt_tools"].execution == EXEC_JUMP, "ppt_tools 默认 jump（需 Office）")
check(by_id["toolbox.image_remove_bg"].execution == EXEC_JUMP, "remove_bg 默认 jump（下模型）")

# jump 工具不应带 handler
check(by_id["toolbox.document_word_tools"].handler == "", "jump 工具 handler 为空")

print("== 参数转换 ==")
jp = by_id["toolbox.text_json_fmt"]
check(len(jp.params) == 2, "json_fmt 应有 2 个参数")
mode = next((p for p in jp.params if p.name == "mode"), None)
check(mode is not None and mode.type == "enum" and "compress" in mode.options, "mode 为 enum 且含 compress 选项")

print("== 关键词派生 ==")
kw = by_id["toolbox.image_compress"].keywords
print(f"  image_compress 关键词: {kw}")
check(any("压缩" in k for k in kw), "关键词含『压缩』（来自 LABEL 派生）")
check("compress" in kw, "关键词含英文碎片 compress")
check(any(k == "图片" for k in kw), "关键词含分类中文『图片』")

print("== desc 自动生成 ==")
check(by_id["toolbox.image_compress"].desc == "批量压缩（图片工具）", "desc 自动生成带分类后缀")

print("== 全量能力经 registry 可达 ==")
from modules.ai_agent import registry as aregs
all_caps = aregs.all_capabilities()
tb = [c for c in all_caps if c.module == "toolbox"]
check(len(tb) == len(caps), "registry.all_capabilities() 包含自动生成的工具箱能力")

print()
if failures:
    print(f"❌ 失败 {len(failures)} 项")
    sys.exit(1)
else:
    print(f"✅ 全部通过：{len(caps)} 个工具箱能力自动发现成功")
