"""AI 员工花名册（配置化）。

目标：**新增一个员工只改配置，不动模板 / 路由**。
首页 `/staff/` 是一面卡片墙，每张卡片一个员工；点击进 `/staff/<id>` 与该员工对话。

用法（config/modules.yaml）：
    modules:
      ai_staff:
        enabled: true
        staffs:
          - id: ops
            icon: "🔧"
            name: "运维助手"
            role: "运维 / 排障"
            desc: "读代码、查库、看日志…"
            badges: [{text: "可用", kind: "ok"}]
            tools: []            # 留空 = 可用全部工具；填名字则只放行这几个
            samples: ["侧栏宽度在哪定义？"]
            enabled: true

字段说明：
  id        员工唯一标识，决定对话页地址 /staff/<id>（只允许字母数字下划线短横）
  icon      头像：单个 emoji，卡片墙与对话页共用
  name/role/desc/badges   卡片展示文案（badges 每项 {"text","kind"}，kind: ok|todo）
  persona   注入系统提示词的角色设定，决定它怎么思考、怎么答
  tools     该员工可用的工具白名单，空列表表示全部放行
  samples   对话页空状态里的示例问题
  enabled   false 时卡片置灰不可点（占位 / 停用的员工）
  min_role  可用这个员工的最低身份：留空=所有登录用户；admin=仅管理员。
            模块本身对所有登录用户可见，但每个员工单独把关——开放给全体的
            「AI 客服」与仅限管理员的「运维助手」可以共存。
  url       可选；留空自动解析为 endpoint:ai_staff.chat?staff=<id>
            也支持 "endpoint:xxx.yyy?a=1" 与外部链接

未配置 staffs（或不是非空列表）时回落到 DEFAULT_STAFFS。
"""
import re
from typing import Any, Dict, List

from flask import url_for

from core.config_loader import get_config

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _opt(raw: Any) -> Dict[str, str]:
    """下拉选项归一化：支持 {"value","text"} 或纯字符串。"""
    if isinstance(raw, dict):
        return {"value": str(raw.get("value") or ""), "text": str(raw.get("text") or raw.get("value") or "")}
    s = str(raw)
    return {"value": s, "text": s}


def _cities() -> List[str]:
    """出生地下拉的建议列表（真太阳时校正用）。取不到排盘库就退化成自由输入。"""
    try:
        from . import bazi
        return sorted(bazi.CITY_LNG.keys())
    except Exception:
        return []


_HR_FORM: Dict[str, Any] = {
    "title": "出生信息",
    "hint": "只为这一次排盘使用，不写入数据库，不在日志里留痕",
    "submit": "排盘并分析",
    "auto_tool": "calc_bazi",
    "fields": [
        {"key": "name", "label": "姓名 / 昵称", "type": "text", "required": False,
         "placeholder": "选填，只是为了称呼得顺口"},
        {"key": "birth", "label": "出生日期", "type": "date", "required": True,
         "hint": "按公历"},
        {"key": "hour", "label": "出生时辰", "type": "select", "required": False,
         "hint": "不确定就选未知，会按年月日三柱推算并明确告知",
         "options": [
             {"value": "未知", "text": "未知"},
             {"value": "子", "text": "子时 23:00-01:00"},
             {"value": "丑", "text": "丑时 01:00-03:00"},
             {"value": "寅", "text": "寅时 03:00-05:00"},
             {"value": "卯", "text": "卯时 05:00-07:00"},
             {"value": "辰", "text": "辰时 07:00-09:00"},
             {"value": "巳", "text": "巳时 09:00-11:00"},
             {"value": "午", "text": "午时 11:00-13:00"},
             {"value": "未", "text": "未时 13:00-15:00"},
             {"value": "申", "text": "申时 15:00-17:00"},
             {"value": "酉", "text": "酉时 17:00-19:00"},
             {"value": "戌", "text": "戌时 19:00-21:00"},
             {"value": "亥", "text": "亥时 21:00-23:00"},
         ]},
        {"key": "gender", "label": "性别", "type": "select", "required": False,
         "hint": "影响大运排法", "options": ["未知", "男", "女"]},
        {"key": "city", "label": "出生地", "type": "text", "required": False,
         "suggest_from": "cities", "placeholder": "选填，如 北京 / 乌鲁木齐",
         "hint": "用于真太阳时校正，位置越偏西校正量越大"},
    ],
}

# 未来员工若没写 persona，用的兜底角色设定
DEFAULT_PERSONA = "你是本项目的 AI 员工，只能使用列出的只读工具取证，然后基于真实证据回答。"

DEFAULT_STAFFS: List[Dict[str, Any]] = [
    {
        "id": "ops",
        "icon": "🔧",
        "name": "运维助手",
        "role": "运维 / 排障",
        "desc": "读项目文件、检索代码、只读查库、分析运行日志；结论必须带证据（文件路径 + 行号，或真实查询结果）。",
        "badges": [{"text": "可用", "kind": "ok"}, {"text": "只读不动手"}, {"text": "6 个工具"}],
        "persona": (
            "你是本项目（一个 Flask 应用）的 AI 员工「运维助手」，负责排查与取证。\n"
            "你的活儿是：定位代码/配置写在哪、查库核对数据、翻运行日志找异常。\n"
            "回答要让运维能直接照着做：给出可点击的文件路径 + 行号，或真实的查询/日志结果。"
        ),
        "tools": [],  # 全部放行：list_dir / code_search / read_file / db_schema / db_query / log_search
        "samples": [
            "侧栏 200px 是在哪个 CSS 里定义的？",
            "最近 60 分钟有哪些 ERROR 日志？",
            "users 表里有几个用户？",
        ],
        "enabled": True,
        "min_role": "admin",
    },
    {
        "id": "hr",
        "icon": "💼",
        "name": "人力参谋",
        "role": "识人 / 岗位匹配",
        "desc": "按传统子平法排八字：给出性格画像、适合的工作方向与协作方式。"
                "填表即可，数据只在内存里算一次，不留底。",
        "badges": [{"text": "可用", "kind": "ok"}, {"text": "不落库"}, {"text": "仅供参考"}],
        "persona": (
            "你是团队里的 AI 员工「人力参谋」，用传统子平命理看一个人的性格底色与工作倾向。\n"
            "你的活儿是：把排好的盘讲成人话——他是什么气质、适合干什么活、跟他合作要注意什么。\n"
            "回答顺序固定为：① 命盘速览 ② 性格画像 ③ 适合的工作方向 ④ 协作建议。\n"
            "语气平和专业，讲人话，不故弄玄虚，不用感叹句堆砌。\n"
            "⚠️ 铁律：\n"
            "1. 所有数字、四柱、五行、十神、强弱一律以「观察」里给出的事实为准，"
            "不得自行推算、修改或补充任何命理结论；你是在解读，不是在算命。\n"
            "2. 不许把倾向说成定论：用「偏」「更像是」「通常」这类词，"
            "禁止出现「他一定能」「不适合做 X」「注定」等断言。\n"
            "3. 必须在结尾保留免责说明，说明这只是自我认知与团队协作的参考，"
            "不作为招聘、定岗、晋升、薪酬的人事决策依据。\n"
            "4. 用户问到两个人的搭配、或某个岗位适不适合某人时，可以先请他补齐对方的信息再算。\n"
        ),
        # 明确白名单：命理员工不需要也不允许碰代码、数据库和日志
        "tools": ["calc_bazi"],
        "form": _HR_FORM,
        "samples": [
            "这个人的性格特点是什么？",
            "适合放在什么类型的岗位上？",
            "跟他沟通要注意什么？",
        ],
        "enabled": True,
        "min_role": "admin",
    },
    {
        "id": "help",
        "icon": "💬",
        "name": "AI客服",
        "role": "网站使用 / 客服答疑",
        "desc": "回答本网站的使用问题：某个功能在哪、某个工具怎么用、这本书在哪个分类。"
                "查不到会直说查不到，不会瞎编入口。",
        "badges": [{"text": "可用", "kind": "ok"}, {"text": "全体可用"}, {"text": "只读"}],
        "persona": (
            "你是本网站的 AI 客服，负责回答「这个功能在哪、怎么用」这类问题。\n"
            "你的服务对象是不懂代码的普通同事，所以回答里不要出现源码文件名、数据库表名、 "
            "堆栈报错这些开发细节，只讲人话。\n"
            "\n"
            "⚠️ 铁律（比回答得漂亮更重要）：\n"
            "1. 一切结论必须来自工具返回的事实：地址来自 site_map，工具路径来自 tool_usage， "
            "图书分类来自 book_search。工具没返回的东西，你一个字都不许编。\n"
            "2. 工具查不到时，直接回答「我没找到这个功能」，并说明你查了哪些关键词。"
            "严禁根据印象猜测或拼接出一个看起来像网址的东西——编错的地址比不回答更糟。\n"
            "3. 回答顺序：先一句话给结论（去哪做），再给路径（如 工具箱 → 文档处理 → 文档格式转换），"
            "最后补一句怎么用。不要把查到的清单原样堆上去让用户自己挑。\n"
            "4. 用户问的要是某个你自己不太确定是否对得上的功能，就先说清楚你查到的是什么，"
            "让他自己判断，不要替他做肯定答复。\n"
            "5. 图书相关问题只能查到公共图书和对方自己的私有图书。查不到时，"
            "可以提示他这本书可能是别人的私有资料。\n"
            "6. 遇到代码排障、查日志、查表结构这类开发问题，或者算八字看性格，"
            "告诉对方去找对应员工（运维助手 / 人力参谋），你不要硬答。\n"
        ),
        # 白名单很关键：开放给全体员工的角色，绝不给他碰代码、日志和自由查库的权限。
        # 查资料只用受限的 book_search（自带行级过滤，看不到别人的私有书）。
        "tools": ["site_map", "tool_usage", "book_search"],
        "min_role": "",  # 留空 = 所有登录用户可用
        "samples": [
            "把 Excel 转成 PDF 要去哪个工具？",
            "在哪上传新图书？",
            "《Python 基础》这本书在哪个分类？",
            "怎么给 PDF 加水印？",
        ],
        "enabled": True,
    },
    {
        "id": "_more",
        "icon": "➕",
        "name": "更多员工",
        "role": "敬请期待",
        "desc": "AI 员工是可扩展的：在 modules.yaml 的 modules.ai_staff.staffs 里加一条配置，就会出现一张新卡片。",
        "badges": [{"text": "待接入", "kind": "todo"}],
        "persona": DEFAULT_PERSONA,
        "tools": [],
        "samples": [],
        "enabled": False,
    },
]

_ENDPOINT_PREFIX = "endpoint:"


def _resolve_url(raw: str, sid: str) -> str:
    """解析卡片链接：留空 → 本模块对话页；"endpoint:xxx?a=1" → url_for；其余原样。"""
    raw = (raw or "").strip()
    if not raw:
        try:
            return url_for("ai_staff.chat", staff_id=sid)
        except Exception:
            return f"/staff/{sid}"
    if raw.startswith(_ENDPOINT_PREFIX):
        rest = raw[len(_ENDPOINT_PREFIX):].strip()
        endpoint, _, query = rest.partition("?")
        try:
            url = url_for(endpoint)
        except Exception:
            return "/" + endpoint.split(".")[0]
        return f"{url}?{query}" if query else url
    return raw


def _normalize(staffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in staffs or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid or not _ID_RE.match(sid):
            continue
        badges = []
        for b in (s.get("badges") or []):
            if isinstance(b, dict) and b.get("text"):
                badges.append({"text": str(b["text"]), "kind": b.get("kind") or ""})
            elif isinstance(b, str):
                badges.append({"text": b, "kind": ""})
        samples = [str(x) for x in (s.get("samples") or []) if str(x).strip()][:6]
        tools = [str(x) for x in (s.get("tools") or []) if str(x).strip()]
        out.append({
            "id": sid,
            "icon": s.get("icon") or "💼",
            "name": str(s.get("name") or sid),
            "role": str(s.get("role") or ""),
            "desc": str(s.get("desc") or ""),
            "badges": badges,
            "persona": str(s.get("persona") or DEFAULT_PERSONA),
            "tools": tools,
            "samples": samples,
            "enabled": bool(s.get("enabled", True)),
            "min_role": str(s.get("min_role") or "").strip().lower(),
            "url": _resolve_url(str(s.get("url") or ""), sid),
            "form": _normalize_form(s.get("form")),
        })
    return out


def _normalize_form(raw: Any) -> Dict[str, Any]:
    """表单配置归一化。前端据此渲染输入面板；没有 form 的员工走纯聊天。

    支持 suggest_from: "cities" —— 出生地下拉建议从排盘库的经纬度表动态取，
    避免 cities 写死在 yaml 里和代码不同步。
    """
    if not isinstance(raw, dict):
        return {}
    fields = []
    for f in (raw.get("fields") or []):
        if not isinstance(f, dict) or not f.get("key"):
            continue
        item = {
            "key": str(f["key"]),
            "label": str(f.get("label") or f["key"]),
            "type": str(f.get("type") or "text"),
            "required": bool(f.get("required")),
            "placeholder": str(f.get("placeholder") or ""),
            "hint": str(f.get("hint") or ""),
            "value": str(f.get("value") or f.get("default") or ""),
        }
        opts = [_opt(o) for o in (f.get("options") or [])]
        if not opts and f.get("suggest_from") == "cities":
            opts = [_opt(c) for c in _cities()]
        item["options"] = opts
        fields.append(item)
    if not fields:
        return {}
    return {
        "title": str(raw.get("title") or "填写信息"),
        "hint": str(raw.get("hint") or ""),
        "submit": str(raw.get("submit") or "提交"),
        "auto_tool": str(raw.get("auto_tool") or ""),
        "fields": fields,
    }


def get_staffs() -> List[Dict[str, Any]]:
    """全部员工（含停用的，用于首页卡片墙）。"""
    cfg = get_config("modules.ai_staff.staffs", None)
    if isinstance(cfg, list) and cfg:
        return _normalize(cfg)
    return _normalize(DEFAULT_STAFFS)


def role_ok(user: Any, min_role: str) -> bool:
    """身份是否满足要求。min_role 为空 = 只要登录；admin = 仅管理员。"""
    mr = str(min_role or "").strip().lower()
    if not mr or mr in ("login", "user", "all", "public"):
        return bool(user)
    if mr in ("admin", "superadmin"):
        return isinstance(user, dict) and str(user.get("role") or "").lower() in ("admin", "superadmin")
    return bool(user)


def can_use(user: Any, staff: Dict[str, Any]) -> bool:
    """某用户能否用某个员工（员工级门禁，配合模块级 login 一起收敛风险）。"""
    if not staff or not staff.get("enabled"):
        return False
    return role_ok(user, staff.get("min_role") or "")


def visible_staffs(user: Any) -> List[Dict[str, Any]]:
    """卡片墙只展示当前用户有权使用的员工。

    不可见的员工直接不渲染，而不是置灰显示——后者等于把内部能力清单摆给无权的人看。
    停用的占位卡（enabled=false）因 can_use 返回 False 同样会被过滤掉；若将来要展示
    「敬请期待」占位，单独给占位卡把 enabled 置 True 并在模板里标记为不可点即可。
    """
    out: List[Dict[str, Any]] = []
    for s in get_staffs():
        if can_use(user, s):
            out.append(s)
        elif not s.get("enabled") and role_ok(user, "admin"):
            # 停用/占位卡只对管理员可见：让他知道还有哪些员工待接入，
            # 普通用户没必要看到一堆点不开的灰卡（也等于泄露内部能力清单）
            out.append(s)
    return out


def get_staff(sid: str) -> Dict[str, Any]:
    """按 id 取员工；不存在或已停用返回 {}（调用方据此判 404）。"""
    for s in get_staffs():
        if s["id"] == sid and s["enabled"]:
            return s
    return {}
