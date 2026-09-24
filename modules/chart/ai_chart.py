"""
AI 图表引擎（独立通道）：自然语言 -> 结构化 spec -> Matplotlib 服务端出 PNG。

设计边界：
- 完全不耦合现有 ECharts 手动工具（chart.js / echarts.min.js），是独立的出图通道。
- 主题配色取自 layout_base.css 的 5 套明亮主题（--theme-primary 等），并支持暗色。
- 中文字体走系统已注册的 CJK 字体，避免乱码。
"""
from __future__ import annotations

import io
import re
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")  # 服务端无显示，必须用 Agg 后端
import matplotlib.pyplot as plt

# ---------- 中文字体（系统已注册：Microsoft YaHei / SimHei / Noto Sans SC ...） ----------
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans SC", "PingFang SC",
    "Heiti TC", "WenQuanYi Micro Hei",
]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.family"] = "sans-serif"

# ---------- 主题配色（与 static/css/layout_base.css 的 --theme-* 对齐） ----------
THEME_COLORS = {
    "quantum": {"primary": "#00f0ff", "secondary": "#00d4ff", "accent": "#b967ff"},
    "aurora":  {"primary": "#c084fc", "secondary": "#a855f7", "accent": "#e879f9"},
    "plasma":  {"primary": "#34d399", "secondary": "#10b981", "accent": "#6ee7b7"},
    "magma":   {"primary": "#fb923c", "secondary": "#f97316", "accent": "#fdba74"},
    # silver 主色近白，出图改用 slate 蓝灰梯段，保证可分辨
    "silver":  {"primary": "#64748b", "secondary": "#475569", "accent": "#0ea5e9"},
}

# 多系列分类调色板（按主题，颜色彼此可区分）
THEME_PALETTE = {
    "quantum": ["#00f0ff", "#00d4ff", "#b967ff", "#2f6df6", "#22d3ee", "#818cf8", "#f472b6", "#34d399"],
    "aurora":  ["#c084fc", "#a855f7", "#e879f9", "#8b5cf6", "#d946ef", "#6366f1", "#f472b6", "#38bdf8"],
    "plasma":  ["#34d399", "#10b981", "#6ee7b7", "#14b8a6", "#22c55e", "#84cc16", "#facc15", "#38bdf8"],
    "magma":   ["#fb923c", "#f97316", "#fdba74", "#f59e0b", "#ef4444", "#ec4899", "#f43f5e", "#fbbf24"],
    "silver":  ["#64748b", "#475569", "#0ea5e9", "#6366f1", "#8b5cf6", "#ec4899", "#f97316", "#10b981"],
}

DARK_BG = "#1b2230"
LIGHT_BG = "#ffffff"

_VALID_TYPES = {"bar", "line", "pie", "scatter"}


def _resolve_theme(theme):
    theme = (theme or "quantum")
    if not isinstance(theme, str):
        theme = "quantum"
    theme = theme.lower()
    return theme if theme in THEME_COLORS else "quantum"


def _parse_spec(raw):
    """把 LLM 文本/对象解析成 dict spec；容错抽取 JSON（去 ``` 围栏 / 取首个 {...}）。"""
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    text = str(raw)
    text = re.sub(r"```(?:json)?", "", text, flags=re.I)
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_chart_spec(raw):
    """对外的 spec 解析入口（chat 路由调用）。"""
    return _parse_spec(raw)


def _coerce_numbers(values):
    out = []
    for v in (values or []):
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _extend_palette(palette, n):
    if n <= len(palette):
        return palette[:n]
    return [palette[i % len(palette)] for i in range(n)]


def render_spec(spec, theme="quantum", dark=False):
    """渲染 spec 为 PNG 字节流。失败抛异常，由路由层兜底。"""
    spec = _parse_spec(spec) if not isinstance(spec, dict) else spec
    if not isinstance(spec, dict):
        raise ValueError("无效的图表 spec")

    theme = _resolve_theme(theme)
    palette = THEME_PALETTE[theme]
    cinfo = THEME_COLORS[theme]
    ctype = str(spec.get("type") or "bar").lower()
    if ctype not in _VALID_TYPES:
        ctype = "bar"
    title = str(spec.get("title") or "")

    bg = DARK_BG if dark else LIGHT_BG
    fg = "#e6edf6" if dark else "#1f2937"
    sub = "#9fb0c3" if dark else "#6b7280"
    # Matplotlib 不接受 rgba() 字符串，网格/边框用 (r,g,b,a) 元组
    grid_c = (1.0, 1.0, 1.0, 0.10) if dark else (0.06, 0.09, 0.16, 0.10)

    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    for spine in ax.spines.values():
        spine.set_color(grid_c)
    ax.tick_params(colors=fg, labelsize=10)
    ax.title.set_color(fg)

    if ctype == "scatter":
        data = spec.get("data") or []
        xs = _coerce_numbers([p[0] for p in data if isinstance(p, (list, tuple)) and len(p) >= 2])
        ys = _coerce_numbers([p[1] for p in data if isinstance(p, (list, tuple)) and len(p) >= 2])
        ax.scatter(xs, ys, s=64, color=cinfo["primary"], edgecolor=cinfo["accent"],
                   linewidth=1.2, alpha=0.9, zorder=3)
        ax.set_xlabel(str(spec.get("x_label") or ""), color=fg)
        ax.set_ylabel(str(spec.get("y_label") or ""), color=fg)
        ax.grid(color=grid_c, linewidth=0.8, zorder=0)
    else:
        categories = [str(c) for c in (spec.get("categories") or [])]
        series = spec.get("series") or []
        if not series:
            series = [{"name": str(spec.get("title") or "数值"),
                       "data": _coerce_numbers(spec.get("values") or [])}]
        if not categories and series and series[0].get("data"):
            categories = [str(i + 1) for i in range(len(series[0]["data"]))]
        if ctype == "pie":
            vals = _coerce_numbers((series[0].get("data") or []))
            cols = _extend_palette(palette, len(vals))
            wedges, _, _ = ax.pie(
                vals, labels=categories, colors=cols, autopct="%1.1f%%",
                textprops={"color": fg, "fontsize": 10}, pctdistance=0.78,
            )
            for w in wedges:
                w.set_edgecolor(bg)
            ax.axis("equal")
            if title:
                ax.set_title(title, color=fg, fontsize=14, fontweight="bold")
            return _finish(fig, bg)

        n = len(series)
        if ctype == "bar" and n > 1:
            x = np.arange(len(categories))
            width = 0.8 / max(n, 1)
            for i, s in enumerate(series):
                ax.bar(x + (i - (n - 1) / 2) * width, _coerce_numbers(s.get("data")),
                       width, label=str(s.get("name") or f"系列{i+1}"),
                       color=palette[i % len(palette)], zorder=3)
            ax.set_xticks(x)
            ax.set_xticklabels(categories)
        elif ctype == "bar":
            ax.bar(categories, _coerce_numbers(series[0].get("data")), color=cinfo["primary"],
                   width=0.6, edgecolor=cinfo["accent"], linewidth=0.8, zorder=3)
        else:  # line
            for i, s in enumerate(series):
                ax.plot(categories, _coerce_numbers(s.get("data")), marker="o",
                        label=str(s.get("name") or f"系列{i+1}"),
                        color=palette[i % len(palette)], linewidth=2.2, zorder=3)
        ax.set_xlabel(str(spec.get("x_label") or ""), color=fg)
        ax.set_ylabel(str(spec.get("y_label") or ""), color=fg)
        ax.grid(axis="y", color=grid_c, linewidth=0.8, zorder=0)
        if n > 1 or ctype == "line":
            ax.legend(facecolor=bg, edgecolor=grid_c, labelcolor=fg, fontsize=9)

    if title and ctype != "pie":
        ax.set_title(title, color=fg, fontsize=14, fontweight="bold")

    return _finish(fig, bg)


def _finish(fig, bg):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_chart_prompt(text):
    """构造给 LLM 的图表结构化解析 prompt（只输出 JSON）。"""
    return (
        "你是一个图表生成助手。根据用户的中文描述，生成绘制一张图表所需的结构化 JSON。\n"
        "只输出 JSON 本身，不要任何解释文字、不要 markdown 围栏、不要代码块。\n\n"
        "JSON 格式：\n"
        "{\n"
        '  "type": "bar" | "line" | "pie" | "scatter",\n'
        '  "title": "图表标题（简洁中文）",\n'
        '  "x_label": "X 轴名称（可选，没有就填空字符串）",\n'
        '  "y_label": "Y 轴名称（可选，没有就填空字符串）",\n'
        '  "categories": ["类别1", "类别2", "..."],\n'
        '  "series": [ {"name": "系列名", "data": [数值, ...], "type": "bar"} ],\n'
        '  "values": [数值, ...],\n'
        '  "data": [[x, y], ...]\n'
        "}\n\n"
        "规则：\n"
        "1. 从描述中提取真实数字，不要编造数值。\n"
        "2. 多组数据用多个 series（例如多个年份/多个部门对比）。\n"
        "3. 柱形/折线/饼图必须给出 categories；饼图用单个 series 或 values。\n"
        "4. 散点图用 data（[[x,y],...]），type 设为 scatter。\n"
        "5. type 缺省用 bar。\n\n"
        f"用户描述：{text}\n\n"
        "请只输出 JSON："
    )
