"""办公自动化连接器。

11 个能力，参数与 `static/office_tools/ai_office.js` 的 OT_AI_CAPS 一一对应
（这里把它们收拢到后端，任何界面都能取用，不再只有办公页面自己知道）。

执行方式统一为 async：办公自动化多为慢任务（尤其转 PDF / 单元格替换依赖
Office COM，可能要几十秒），且输入文件来自工具箱工作区（用户已在计划卡里选好），
因此走「后台线程执行 + 进度轮询」，不再跳转到办公页由人工上传。
后端处理器见 modules/ai_agent/office_runner.py（复用 office_tools.service）。
"""
from .base import Connector, Capability, ParamSpec, EXEC_ASYNC


class OfficeConnector(Connector):
    module_id = "office_tools"
    display_name = "办公自动化"

    def _target(self, tab: str, subtab: str = "") -> str:
        t = f"/office_tools?tab={tab}"
        if subtab:
            t += f"&subtab={subtab}"
        return t

    def capabilities(self):
        return [
            Capability(
                cid="office.mod1_folders",
                label="批量建文件夹",
                desc="按 Excel 某一列的名称批量创建文件夹，结果打包下载",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod1", "m1-folders"),
                handler="office.mod1_folders",
                keywords=["文件夹", "建目录", "批量创建"],
                params=[ParamSpec("column_header", "名称所在列标题", required=True)],
            ),
            Capability(
                cid="office.mod1_files",
                label="批量建文件",
                desc="按 Excel 某一列批量创建 excel/txt/word 文件",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod1", "m1-files"),
                handler="office.mod1_files",
                keywords=["建文件", "批量创建", "excel", "word"],
                params=[
                    ParamSpec("column_header", "名称所在列标题", required=True),
                    ParamSpec("file_type", "文件类型", type="enum",
                              options=["excel", "txt", "word"], default="excel"),
                ],
            ),
            Capability(
                cid="office.mod1_sheets",
                label="批量建分表",
                desc="给多个 Excel 批量添加分表，可选用模板填充",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod1", "m1-sheets"),
                handler="office.mod1_sheets",
                keywords=["分表", "sheet", "新建工作表"],
                params=[
                    ParamSpec("column_header", "分表名称列标题", required=True),
                    ParamSpec("fill", "用模板填充", type="bool", default=False),
                ],
            ),
            Capability(
                cid="office.mod2_extract",
                label="提取文件清单",
                desc="提取压缩包内文件/文件夹清单，可导出成表格",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod2"),
                handler="office.mod2_extract",
                keywords=["清单", "提取", "目录", "列表"],
                params=[
                    ParamSpec("mode", "提取对象", type="enum",
                              options=["all", "files", "filter"], default="all"),
                    ParamSpec("suffix", "按后缀筛选"),
                ],
            ),
            Capability(
                cid="office.mod3_simple",
                label="简单替换改名",
                desc="把文件名里的某段文字批量替换成另一段",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod3", "m3-simple"),
                handler="office.mod3_simple",
                keywords=["改名", "替换", "批量重命名"],
                params=[
                    ParamSpec("old_text", "查找文字", required=True),
                    ParamSpec("new_text", "替换为", required=True),
                ],
            ),
            Capability(
                cid="office.mod3_excel",
                label="Excel 对照改名",
                desc="按 Excel 对照表（原名列/新名列）批量重命名",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod3", "m3-excel"),
                handler="office.mod3_excel",
                keywords=["改名", "对照表", "excel", "映射"],
                params=[
                    ParamSpec("old_col", "原文件名列标题", required=True),
                    ParamSpec("new_col", "新文件名列标题", required=True),
                ],
            ),
            Capability(
                cid="office.mod4_cell",
                label="单元格替换",
                desc="批量替换多个 Excel 指定单元格的内容（需服务器装 Office）",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod4", "m4-cell"),
                handler="office.mod4_cell",
                keywords=["单元格", "替换", "excel"],
                params=[
                    ParamSpec("cell_addr", "单元格地址", required=True),
                    ParamSpec("old_text", "查找文字"),
                    ParamSpec("new_text", "替换为"),
                ],
            ),
            Capability(
                cid="office.mod4_pdf",
                label="Excel 转 PDF",
                desc="批量把 Excel 转成 PDF（需服务器装 Office）",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod4", "m4-pdf"),
                handler="office.mod4_pdf",
                keywords=["pdf", "转换", "excel转"],
                params=[],
            ),
            Capability(
                cid="office.mod5_movecopy",
                label="移动/复制文件",
                desc="按 Excel 映射表批量移动或复制文件",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod5", "m5-movecopy"),
                handler="office.mod5_movecopy",
                keywords=["移动", "复制", "搬运", "整理"],
                params=[
                    ParamSpec("action", "动作", type="enum",
                              options=["copy", "move"], default="copy"),
                ],
            ),
            Capability(
                cid="office.mod5_paths",
                label="提取文件路径",
                desc="提取压缩包内所有文件的路径清单（绝对/相对）",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod5", "m5-paths"),
                handler="office.mod5_paths",
                keywords=["路径", "清单", "提取"],
                params=[
                    ParamSpec("path_type", "路径类型", type="enum",
                              options=["absolute", "relative"], default="relative"),
                ],
            ),
            Capability(
                cid="office.mod4_delete_sheets",
                label="删除分表",
                desc="批量删除 Excel 里的指定分表（不可逆，必须由用户确认）",
                module=self.module_id, execution=EXEC_ASYNC, needs_files=True,
                target=self._target("mod4", "m4-delete"),
                handler="office.mod4_delete_sheets",
                keywords=["删除", "分表", "移除"],
                params=[ParamSpec("sheet_names", "待删除分表名（逗号分隔）", required=True)],
            ),
        ]
