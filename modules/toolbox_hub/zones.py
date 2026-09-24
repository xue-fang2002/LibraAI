"""工具箱 Hub 功能区配置（P2：卡片配置化）。

目标：新增一个功能区**只改配置**，不动 index.html / routes.py。

用法（modules.yaml）：
    modules:
      toolbox_hub:
        enabled: true
        sort_order: 4
        zones:
          - icon: "📊"
            title: "图表工具"
            desc: "Excel / CSV 上传即可出图…"
            badges: [{text: "5 大类", kind: "ok"}, {text: "约百种变体"}]
            url: "endpoint:chart.index?hub=1"

字段说明：
  icon / title / desc   展示文案
  badges                角标列表，每项 {"text": "...", "kind": "ok"|"todo"}，kind 省略为普通
  url                   "endpoint:xxx.yyy?query" → 经 url_for 解析（推荐，路由改名也不坏链）
                        其余按原样使用（如 "/toolbox?hub=1#/image"、"https://..."）

未配置 zones（或配置不是非空列表）时回落到 DEFAULT_ZONES，保证老部署行为不变。
"""
from typing import Any, Dict, List

from flask import url_for

from core.config_loader import get_config


DEFAULT_ZONES: List[Dict[str, Any]] = [
    {
        "icon": "📊",
        "title": "图表工具",
        "desc": "Excel / CSV 上传即可出图，柱形、折线、饼环、关系分布、统计科研五大类，含 3D 图表。",
        "badges": [{"text": "5 大类", "kind": "ok"}, {"text": "约百种变体"}],
        "url": "endpoint:chart.index?hub=1",
    },
    {
        "icon": "📄",
        "title": "文档处理",
        "desc": "PDF 与 Office 合并为一区：PDF 合并 / 拆分 / 提取，Excel 分表 / 替换 / 转 PDF / 对照改名。",
        "badges": [{"text": "PDF 可用", "kind": "ok"}, {"text": "Excel 可用", "kind": "ok"},
                   {"text": "部分待实现", "kind": "todo"}],
        "url": "endpoint:toolbox_hub.document",
    },
    {
        "icon": "🛠️",
        "title": "办公自动化",
        "desc": "批量建文件夹 / 建文件 / 建分表，批量改名，移动复制，路径提取与清单导出。",
        "badges": [{"text": "批量处理", "kind": "ok"}, {"text": "6 个模块"}],
        "url": "endpoint:office_tools.index?hub=1",
    },
    {
        "icon": "🖼️",
        "title": "图片处理",
        "desc": "批量压缩、格式转换、缩略图、裁剪旋转、滤镜、水印、拼接、EXIF、AI 抠图；尺寸修改 / 版式装饰 / GIF 拆分合成。",
        "badges": [{"text": "12 项", "kind": "ok"}, {"text": "抠图需 rembg", "kind": "todo"}],
        "url": "/toolbox?hub=1#/image",
    },
    {
        "icon": "🔤",
        "title": "文本工具",
        "desc": "JSON 格式化、编码转换、正则测试、时间戳、文本对比、密码生成、UUID。",
        "badges": [{"text": "全部可用", "kind": "ok"}, {"text": "纯本地处理"}],
        "url": "/toolbox?hub=1#/text",
    },
    {
        "icon": "🔎",
        "title": "识别工具",
        "desc": "二维码生成与识别；图片 / 截图 / 扫描 PDF 文字识别（OCR）。",
        "badges": [{"text": "二维码可用", "kind": "ok"}, {"text": "OCR 需引擎", "kind": "todo"}],
        "url": "/toolbox?hub=1#/recognition",
    },
    {
        "icon": "🧮",
        "title": "计算换算",
        "desc": "单位换算、进制转换、房贷、投资收益、五险一金、人民币大写、BMI、日期计算。",
        "badges": [{"text": "全部可用", "kind": "ok"}, {"text": "纯本地计算"}],
        "url": "/toolbox?hub=1#/calc",
    },
    {
        "icon": "📦",
        "title": "压缩打包",
        "desc": "多文件打包 zip / tar / tar.gz，以及 zip / tar / tar.gz 解压。",
        "badges": [{"text": "全部可用", "kind": "ok"}, {"text": "rar/7z 不支持"}],
        "url": "/toolbox?hub=1#/archive",
    },
    {
        "icon": "⚙️",
        "title": "系统工具",
        "desc": "批量重命名、文件哈希校验、屏幕取色器、剪贴板历史。",
        "badges": [{"text": "全部可用", "kind": "ok"}, {"text": "4 项"}],
        "url": "/toolbox?hub=1#/system",
    },
]

_ENDPOINT_PREFIX = "endpoint:"


def _resolve_url(raw: str) -> str:
    """把 "endpoint:chart.index?hub=1" 解析成真实 URL；其它原样返回。

    用 endpoint 而不是硬编码路径，好处是模块改路由前缀时链接不会坏。
    """
    raw = (raw or "").strip()
    if not raw.startswith(_ENDPOINT_PREFIX):
        return raw
    rest = raw[len(_ENDPOINT_PREFIX):].strip()
    endpoint, _, query = rest.partition("?")
    try:
        url = url_for(endpoint)
    except Exception:
        # endpoint 不存在时不让整页挂掉：退化成 /<endpoint 第一段>
        return "/" + endpoint.split(".")[0]
    return f"{url}?{query}" if query else url


def _normalize(zones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for z in zones or []:
        if not isinstance(z, dict) or not z.get("title"):
            continue
        badges = []
        for b in (z.get("badges") or []):
            if isinstance(b, dict) and b.get("text"):
                badges.append({"text": str(b["text"]), "kind": b.get("kind") or ""})
            elif isinstance(b, str):
                badges.append({"text": b, "kind": ""})
        out.append({
            "icon": z.get("icon") or "📄",
            "title": str(z["title"]),
            "desc": z.get("desc") or "",
            "badges": badges,
            "url": _resolve_url(z.get("url") or ""),
        })
    return out


def get_zones() -> List[Dict[str, Any]]:
    """取功能区列表：yaml 优先，未配置则回落到 DEFAULT_ZONES。"""
    cfg = get_config("modules.toolbox_hub.zones", None)
    if isinstance(cfg, list) and cfg:
        return _normalize(cfg)
    return _normalize(DEFAULT_ZONES)
