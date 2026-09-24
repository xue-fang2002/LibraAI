"""
工作自动化流程（RPA 长任务）框架。

设计目标：
- 框架本身完全开源、可提交到 GitHub；
- 私有流程放在 flows/ 子目录（已被 .gitignore 忽略），不进入公开仓库；
- 新增流程 = 在 flows/ 下新建一个符合「流程契约」的 Python 模块，
  框架自动发现并展示到工具箱，无需改动任何框架代码。

流程契约见 base.py。
"""

from .registry import get_flows, get_flow

__all__ = ["get_flows", "get_flow"]
