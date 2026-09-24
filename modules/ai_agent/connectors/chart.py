"""AI 图表连接器。

这是目前**唯一支持原地执行（inplace）**的能力：
`/chart/ai/chat`（自然语言 → spec）+ `/chart/ai/render`（spec → PNG）
两条链路全在后端完成，不依赖任何页面状态，所以可以直接在任务中心里出图。

后续要做原地执行的其它能力（比如借助工具箱工作区跑 dispatch），
照这里的写法加一个 execution=EXEC_INPLACE 的连接器即可。
"""
from .base import Connector, Capability, ParamSpec, EXEC_INPLACE


class ChartConnector(Connector):
    module_id = "chart"
    display_name = "AI 图表"

    def capabilities(self):
        return [
            Capability(
                cid="chart.ai_render",
                label="AI 生成图表",
                desc="根据一句自然语言描述（含数据）画出柱状图/折线图/饼图/散点图，直接出图片",
                module=self.module_id,
                execution=EXEC_INPLACE,
                target="/chart/ai/render",
                keywords=["图表", "画图", "柱状图", "折线图", "饼图", "统计", "趋势"],
                params=[
                    ParamSpec("text", "图表描述（需含具体数据）", required=True),
                    ParamSpec("type", "图表类型", type="enum",
                              options=["bar", "line", "pie", "scatter"], default="bar"),
                ],
            ),
        ]
