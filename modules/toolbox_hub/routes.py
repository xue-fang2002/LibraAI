from flask import render_template
from core.exceptions import module_exception_guard
from . import bp
from .zones import get_zones


@bp.route("/")
@module_exception_guard("toolbox_hub")
def index():
    """工具箱首页：功能区卡片由配置驱动（见 zones.py）。

    卡片内容取自 modules.yaml 的 modules.toolbox_hub.zones，
    未配置时回落到 zones.DEFAULT_ZONES —— 新增功能区只改配置，不必动模板。
    """
    zones = get_zones()
    return render_template(
        "toolbox_hub/index.html",
        current_module_id="toolbox_hub",
        zones=zones,
        zone_count=len(zones),
    )


@bp.route("/document")
@module_exception_guard("toolbox_hub")
def document():
    """文档处理区：PDF 工具 + Office 三件套 两个子页签。"""
    return render_template("toolbox_hub/document.html", current_module_id="toolbox_hub")
