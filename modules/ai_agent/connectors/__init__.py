"""连接器包：导入即注册全部连接器。

新增一个业务模块的连接器：
  1. 在 connectors/ 下新建 xxx.py，继承 Connector，实现 capabilities()
  2. 在本文件里 import 并加入 _CONNECTORS 列表
除此之外不需要改动任何地方。
"""
from .toolbox import ToolboxConnector
from .office import OfficeConnector
from .chart import ChartConnector
from .nav import NavConnector
from .workflow import WorkflowConnector

_CONNECTORS = [
    ToolboxConnector,
    OfficeConnector,
    ChartConnector,
    WorkflowConnector,
    NavConnector,
]

__all__ = ["ToolboxConnector", "OfficeConnector", "ChartConnector", "NavConnector",
           "WorkflowConnector", "_CONNECTORS"]
