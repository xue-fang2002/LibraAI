"""八字排盘与性格/岗位倾向分析（纯内存，不落库）。

设计原则：**分工明确，命理计算与「表达」彻底分离**。

- 本文件只做**确定性计算**：四柱、五行、十神、强弱打分、规则映射。
  全都是查表与算术，同一个八字任何时候算出来的结果必须完全一致。
- 语言组织交给 LLM（见 engine.py），它不许补算、不许改结论。

为什么不让 LLM 排盘：四柱依赖节气交接的精确时刻，差一天月柱全错，
模型会自信地算错，且自己无法察觉。这个坑在 ai_staff 的只读工具上已经踩过。

流派口径：本实现采用通行的**子平法·旺衰扶抑**口径，强弱打分是本工程的可复现近似，
不等于任何一位命理师的判断；不同流派（格局派、调候派、盲派）可能给出不同结论。
所有对外结论都强制带免责说明。

⚠️ 隐私：本模块所有输入（姓名/生日/时辰）只在内存中流转，
不写数据库、不写审计日志（tools.private_args 会拦住参数落盘）。
"""
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from lunar_python import Solar, EightChar  # type: ignore
except Exception:  # pragma: no cover - 缺库时由工具层返回友好提示
    Solar = None  # type: ignore
    EightChar = None  # type: ignore

# ============================================================ 基础常量
GAN_WU_XING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
               "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
ZHI_WU_XING = {"寅": "木", "卯": "木", "巳": "火", "午": "火", "申": "金",
               "酉": "金", "亥": "水", "子": "水", "辰": "土", "戌": "土",
               "丑": "土", "未": "土"}
YANG_GAN = {"甲", "丙", "戊", "庚", "壬"}
# 五行生克：key 生 value
SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

ZHI_ORDER = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 时辰名 -> 代表小时（用于把下拉选择转成具体小时）
SHICHEN_HOUR = {"子": 0, "丑": 2, "寅": 4, "卯": 6, "辰": 8, "巳": 10,
                "午": 12, "未": 14, "申": 16, "酉": 18, "戌": 20, "亥": 22}

# 十神归大类：正偏同气合并统计，降低 7B 解读时的组合爆炸
SHI_SHEN_GROUP = {
    "比肩": "比劫", "劫财": "比劫",
    "食神": "食伤", "伤官": "食伤",
    "正财": "财星", "偏财": "财星",
    "正官": "官杀", "七杀": "官杀",
    "正印": "印星", "偏印": "印星",
    "日主": "日主",
}

# ------------------------------------------------------------ 真太阳时校正
# 城市经度（近似到分即可，4 分钟/度，误差在分钟级，够用）
CITY_LNG = {
    "北京": 116.41, "上海": 121.47, "天津": 117.20, "重庆": 106.55,
    "广州": 113.26, "深圳": 114.06, "杭州": 120.15, "南京": 118.78,
    "武汉": 114.31, "成都": 104.07, "西安": 108.95, "沈阳": 123.43,
    "大连": 121.62, "长春": 125.32, "哈尔滨": 126.53, "郑州": 113.63,
    "济南": 117.00, "青岛": 120.38, "长沙": 112.94, "南昌": 115.89,
    "合肥": 117.28, "福州": 119.30, "厦门": 118.09, "昆明": 102.71,
    "贵阳": 106.63, "南宁": 108.32, "海口": 110.20, "太原": 112.55,
    "石家庄": 114.51, "兰州": 103.83, "银川": 106.27, "西宁": 101.78,
    "呼和浩特": 111.75, "乌鲁木齐": 87.62, "拉萨": 91.11, "苏州": 120.62,
    "无锡": 120.30, "宁波": 121.55, "温州": 120.70, "东莞": 113.75,
    "佛山": 113.11, "珠海": 113.55, "中国台北": 121.52,
    "中国香港": 114.17, "中国澳门": 113.55,
}

# ------------------------------------------------------------ 规则字典
# 十神大类 -> 性格倾向（用于生成标签，不用于下结论）
GROUP_TRAITS = {
    "比劫": ["独立性强，习惯自己拿主意", "竞争意识强，不服输", "重义气，靠得住", "有时固执，不易被说服"],
    "食伤": ["表达欲与创造力强", "脑子活，喜欢新东西", "审美在线，对细节敏感", "不喜束缚，不太买规则的账"],
    "财星": ["务实，看重结果与收益", "人际圆融，会经营关系", "资源整合能力强", "偏理性，取舍快"],
    "官杀": ["责任心强，说到做到", "守规则，重视秩序与流程", "抗压能力好", "容易把压力往自己身上揽"],
    "印星": ["学习能力强，坐得住", "思考深，注重逻辑与体系", "心软，愿意帮人", "偶尔回避冲突，拖延决断"],
}

# 十神大类 -> 适合的工作方向（多头衔接ugas给 LLM 组织的原始素材）
GROUP_JOBS = {
    "比劫": ["独立负责一块业务的岗位", "攻坚/拓荒类项目", "创业或内部创业角色", "竞技性强、结果即时可见的岗位"],
    "食伤": ["创意、设计、内容、品牌类岗位", "研发与技术攻关", "培训、演讲、客户方案讲解", "产品经理（偏创新型）"],
    "财星": ["销售、商务拓展、客户经营", "运营与生意盘相关的岗位", "供应链与资源统筹", "降本增效、经营分析类岗位"],
    "官杀": ["管理与团队负责人", "风控、合规、质量管理", "执行类项目管理（PMO）", "需要严格按流程推进的岗位"],
    "印星": ["研究与教研、技术沉淀类岗位", "规划、策略、文案撰写", "培训、咨询、顾问角色", "后台支持与体系建设"],
}

# 十神大类 -> 管理/协作上的注意点
GROUP_CAUTION = {
    "比劫": ["给空间比给指令有效，别用强管控", "分配目标时明确边界，避免与同事撞车"],
    "食伤": ["少用条文约束，多给方向和自由度", "需要被看见：公开认可比加薪更管用"],
    "财星": ["把激励与结果挂钩最有效", "不适合长期没有反馈的岗位"],
    "官杀": ["注意别把任务压到过载，他不擅长拒绝", "流程不清会让他焦虑，先把规则讲透"],
    "印星": ["重大决策要替他设截止时间", "多给成长资源（培训/书籍/师承）"],
}

# 五行 -> 行业倾向（辅助维度，权重低于十神）
ELEMENT_FIELDS = {
    "木": "教育出版、农林文创、成长型业务",
    "火": "互联网传媒、品牌营销、能源餐饮",
    "土": "地产工程、行政后勤、供应链与稳定性业务",
    "金": "金融法务、精密制造、数据与规则密集型岗位",
    "水": "物流贸易、信息流通、咨询顾问类岗位",
}

# 日主阴阳气质
POLARITY_NOTE = {
    "阳": "外放型：先行动再想，气场直接，适合带队和对外角色",
    "阴": "内敛型：先想再动，观察细腻，适合深耕和幕僚角色",
}

STRENGTH_NOTE = {
    "身强": "能量足、抗压好，能扛事也能扛责任；注意别什么都自己上，学会分权",
    "中和": "强弱均衡，适应面广，能上能下；注意别什么都做，缺少聚焦点",
    "身弱": "敏感细腻、共情强，靠脑子和协作取胜；注意别硬扛高强度消耗型岗位",
}

DISCLAIMER = "以上为基于传统命理的倾向性参考，用于自我认知与团队沟通，不作为招聘、定岗、晋升或薪酬的人事决策依据。"


# ============================================================ 输入处理
def tz_minutes(city: str, date: datetime) -> Tuple[int, str]:
    """真太阳时校正分钟数：经度差 + 均时差。城市未知返回 (0, 提示)。"""
    lng = CITY_LNG.get((city or "").strip())
    if not lng:
        return 0, "未提供出生地，按标准北京时间计算"
    offset = (lng - 120.0) * 4.0
    n = int(date.strftime("%j"))
    b = 2 * math.pi * (n - 81) / 364.0
    eq = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    total = offset + eq
    return int(round(total)), f"按{city}真太阳时校正 {int(round(total))} 分钟（经度差 {int(round(offset))} + 均时差 {int(round(eq))}）"


def hour_to_shichen(hour: float) -> str:
    """小时 -> 时辰名。23 点后归入子时（库按当日处理，见模块注释）。"""
    idx = int(((int(hour) + 1) % 24) / 2)
    return ZHI_ORDER[idx]


# ============================================================ 排盘
def _pillars(e: Any) -> List[Dict[str, Any]]:
    out = []
    for label, key in (("年柱", "Year"), ("月柱", "Month"), ("日柱", "Day"), ("时柱", "Time")):
        gan = getattr(e, f"get{key}Gan")()
        zhi = getattr(e, f"get{key}Zhi")()
        hide = getattr(e, f"get{key}HideGan")() or []
        hide_ss = getattr(e, f"get{key}ShiShenZhi")() or []
        out.append({
            "label": label,
            "gan": gan,
            "zhi": zhi,
            "ganzhi": f"{gan}{zhi}",
            "element": GAN_WU_XING.get(gan, ""),
            "zhi_element": ZHI_WU_XING.get(zhi, ""),
            "shi_shen": getattr(e, f"get{key}ShiShenGan")(),
            "hide_gan": hide,
            "hide_shi_shen": hide_ss,
            "changsheng": getattr(e, f"get{key}DiShi")(),
        })
    return out


def _element_counts(pillars: List[Dict[str, Any]]) -> Dict[str, int]:
    """本气计数：天干 + 地支本气（不含藏干），这是判断「五行缺什么」的常规口径。"""
    counts = {"金": 0, "木": 0, "水": 0, "火": 0, "土": 0}
    for p in pillars:
        for key in ("element", "zhi_element"):
            el = p.get(key, "")
            if el:
                counts[el] = counts.get(el, 0) + 1
    return counts


def _weighted_counts(pillars: List[Dict[str, Any]]) -> Dict[str, float]:
    """含藏干的加权五行分布，权重 0.4，用于看元素的实际影响力。"""
    counts = {"金": 0.0, "木": 0.0, "水": 0.0, "火": 0.0, "土": 0.0}
    for p in pillars:
        for key in ("element", "zhi_element"):
            el = p.get(key, "")
            if el:
                counts[el] += 1.0
        for g in p["hide_gan"]:
            el = GAN_WU_XING.get(g, "")
            if el:
                counts[el] += 0.4
    return {k: round(v, 1) for k, v in counts.items()}


def _shi_shen_stats(pillars: List[Dict[str, Any]]) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    for p in pillars:
        ss = p["shi_shen"]
        if ss and ss != "日主":
            stats[ss] = stats.get(ss, 0) + 1
        for h in p["hide_shi_shen"]:
            if h and h != "日主":
                stats[h] = stats.get(h, 0) + 1
    return stats


def _group_stats(stats: Dict[str, int]) -> Dict[str, int]:
    groups: Dict[str, int] = {}
    for ss, n in stats.items():
        g = SHI_SHEN_GROUP.get(ss)
        if not g or g == "日主":
            continue
        groups[g] = groups.get(g, 0) + n
    return groups


def _strength(pillars: List[Dict[str, Any]], day_master_el: str) -> Dict[str, Any]:
    """旺衰打分（工程近似）：月令 35 + 其余地支各 15 + 天干加減各 12，满分约 100。"""
    detail: List[str] = []
    score = 0.0
    month_zhi_el = pillars[1]["zhi_element"]
    if month_zhi_el == day_master_el or SHENG.get(month_zhi_el) == day_master_el:
        score += 35
        detail.append(f"月令{month_zhi_el}当令扶身 +35")
    elif KE.get(month_zhi_el) == day_master_el or SHENG.get(day_master_el) == month_zhi_el:
        detail.append(f"月令{month_zhi_el}失令泄耗 +0")
    else:
        detail.append(f"月令{month_zhi_el}耗身 +0")

    for p in (pillars[0], pillars[2], pillars[3]):
        if p["zhi_element"] == day_master_el:
            score += 15
            detail.append(f"{p['label']}{p['zhi']}通根 +15")

    for p in pillars:
        ss = p["shi_shen"]
        if ss in ("正印", "偏印"):
            score += 12
            detail.append(f"{p['label']}{ss}生身 +12")
        elif ss in ("比肩", "劫财"):
            score += 12
            detail.append(f"{p['label']}{ss}帮身 +12")
        elif ss in ("正财", "偏财", "正官", "七杀", "食神", "伤官"):
            score -= 8
            detail.append(f"{p['label']}{ss}泄耗 -8")

    score = max(0.0, min(100.0, score))
    if score >= 55:
        verdict = "身强"
    elif score <= 35:
        verdict = "身弱"
    else:
        verdict = "中和"
    return {"score": round(score, 1), "verdict": verdict, "detail": detail}


# ============================================================ 主入口
def analyze(year: int, month: int, day: int, hour: float,
            gender: str = "", city: str = "",
            hour_known: bool = True) -> Dict[str, Any]:
    """排盘并给出结构化解读素材。hour_known=False 表示时辰不确定，只算三柱。

    返回 dict，字段设计成 LLM 能直接组织成通顺中文，且每一项都可追溯到具体来源。
    """
    if Solar is None:
        return {"ok": False, "error": "缺少排盘库 lunar_python，请 pip install lunar_python"}

    try:
        base_dt = datetime(year, month, day, int(hour) % 24, int(round((hour % 1) * 60)))
    except Exception as e:
        return {"ok": False, "error": f"出生日期不合法：{e}"}

    from datetime import timedelta
    shift, tz_note = tz_minutes(city, base_dt)
    calc_dt = base_dt
    if shift:
        calc_dt = base_dt + timedelta(minutes=shift)
        # 真太阳时校正可能跨日/跨时辰，这会改变日柱或时柱 —— 属正确行为，但必须显式告知
        alert = ""
        if calc_dt.date() != base_dt.date():
            alert += f"校正后跨入 {calc_dt.strftime('%Y-%m-%d')}，日柱已按真太阳时重新推算；"
        src_sc, dst_sc = hour_to_shichen(base_dt.hour + base_dt.minute / 60), hour_to_shichen(
            calc_dt.hour + calc_dt.minute / 60)
        if src_sc != dst_sc:
            alert += f"时辰由{src_sc}时校正为{dst_sc}时；"
        if alert:
            alert += "如你知道的是当地实际日照时间，可在提问时说明按北京时间重算。"
    else:
        alert = ""

    try:
        solar = Solar.fromYmdHms(calc_dt.year, calc_dt.month, calc_dt.day,
                                 calc_dt.hour, calc_dt.minute, 0)
        lunar = solar.getLunar()
        eight = EightChar.fromLunar(lunar)
    except Exception as e:  # 日期越界等
        return {"ok": False, "error": f"日期无法排盘：{e}"}

    pillars = _pillars(eight)
    if not hour_known:
        pillars[3] = {"label": "时柱", "gan": "?", "zhi": "?", "ganzhi": "未知",
                      "element": "", "zhi_element": "", "shi_shen": "未知",
                      "hide_gan": [], "hide_shi_shen": [], "changsheng": ""}

    day_master = pillars[2]["gan"]
    dm_el = GAN_WU_XING.get(day_master, "")
    polarity = "阳" if day_master in YANG_GAN else "阴"

    counts = _element_counts(pillars)
    missing = [el for el, n in counts.items() if n == 0]
    dominant = sorted(counts.items(), key=lambda kv: -kv[1])[0][0]

    ss_stats = _shi_shen_stats(pillars)
    groups = _group_stats(ss_stats)
    ranked = sorted(groups.items(), key=lambda kv: -kv[1])
    top_groups = [g for g, _ in ranked[:2]]
    if not ranked:
        top_groups = []

    strength = _strength(pillars, dm_el) if hour_known else \
        {"score": 0.0, "verdict": "缺时辰无法判定", "detail": ["未提供出生时辰，强弱打分不可靠"]}

    traits: List[str] = []
    jobs: List[str] = []
    cautions: List[str] = []
    for g in top_groups:
        traits.extend(GROUP_TRAITS.get(g, [])[:3])
        jobs.extend(GROUP_JOBS.get(g, [])[:3])
        cautions.extend(GROUP_CAUTION.get(g, [])[:2])

    dayun: List[str] = []
    start_age = ""
    if gender in ("男", "女"):
        try:
            yun = eight.getYun(1 if gender == "男" else 2)
            dayun = [d.getGanZhi() for d in (yun.getDaYun() or [])[1:7]]
            start_age = f"{yun.getStartYear()}岁{yun.getStartMonth()}月起运"
        except Exception:
            dayun, start_age = [], ""

    return {
        "ok": True,
        "name_hint": "",
        "birth": {
            "solar": f"{year}-{month:02d}-{day:02d}",
            "hour": f"{int(hour) if hour_known else '?'}时",
            "shichen": hour_to_shichen(hour) if hour_known else "未知",
            "gender": gender or "未知",
            "city": city or "未知",
            "hour_known": hour_known,
            "true_solar_note": tz_note,
            "true_solar_alert": alert,
            "true_solar_time": calc_dt.strftime("%Y-%m-%d %H:%M") if shift else "",
        },
        "lunar": {
            "text": lunar.toString(),
            "jieqi": f"{lunar.getPrevJieQi().getName()} → {lunar.getNextJieQi().getName()}",
            "shengxiao": lunar.getYearShengXiao(),
            "nayin": [lunar.getYearNaYin(), lunar.getMonthNaYin(),
                      lunar.getDayNaYin(), lunar.getTimeNaYin()],
        },
        "chart": {
            "pillars": pillars,
            "si_zhu": " ".join(p["ganzhi"] for p in pillars),
            "day_master": {"gan": day_master, "element": dm_el, "polarity": polarity},
            "strength": strength,
        },
        "elements": {
            "counts": counts,
            "weighted": _weighted_counts(pillars),
            "missing": missing,
            "dominant": dominant,
            "field_hint": ELEMENT_FIELDS.get(dominant, ""),
        },
        "shi_shen": {
            "raw": ss_stats,
            "groups": groups,
            "top": top_groups,
        },
        "readout": {
            "polarity_note": POLARITY_NOTE.get(polarity, ""),
            "strength_note": STRENGTH_NOTE.get(strength.get("verdict", ""), ""),
            "traits": traits,
            "jobs": jobs,
            "cautions": cautions,
            "dayun": dayun,
            "dayun_start": start_age,
        },
        "disclaimer": DISCLAIMER,
    }


def to_text(report: Dict[str, Any]) -> str:
    """把结构化结果压成一段紧凑的事实文本，塞给 LLM 作为「已取证的数据」。

    关键：这段是**唯一可信来源**，提示词会要求模型不得在此基础上追加新的命理结论。
    """
    if not report.get("ok"):
        return f"排盘失败：{report.get('error')}"
    b = report["birth"]
    c = report["chart"]
    e = report["elements"]
    r = report["readout"]
    lines = [
        f"出生：公历 {b['solar']} {b['hour']}（{b['shichen']}时），性别 {b['gender']}，出生地 {b['city']}",
        f"真太阳时：{b['true_solar_note']}" + (f"，校正后 {b['true_solar_time']}" if b["true_solar_time"] else ""),
    ]
    if b.get("true_solar_alert"):
        lines.append(f"校正影响：{b['true_solar_alert']}")
    lines += [
        f"农历：{report['lunar']['text']}；生肖 {report['lunar']['shengxiao']}；"
        f"节气 {report['lunar']['jieqi']}；纳音 {' '.join(report['lunar']['nayin'])}",
        f"四柱：{c['si_zhu']}",
        f"日主：{c['day_master']['gan']}（{c['day_master']['element']}，{c['day_master']['polarity']}）",
        f"强弱：{c['strength'].get('verdict')}（{c['strength'].get('score')} 分）；依据：{'；'.join(c['strength'].get('detail', [])[:6])}",
        f"五行本气：{e['counts']}；含藏干加权：{e['weighted']}；缺失：{e['missing'] or '无'}"
        f"；最旺：{e['dominant']}（行业倾向：{e['field_hint']}）",
        f"十神统计：{report['shi_shen']['raw']}；归类：{report['shi_shen']['groups']}；"
        f"主导：{'/'.join(report['shi_shen']['top']) or '不显著'}",
        f"阴阳气质：{r['polarity_note']}",
        f"强弱含义：{r['strength_note']}",
        f"性格要点：{'；'.join(r['traits'])}",
        f"适合方向：{'；'.join(r['jobs'])}",
        f"相处提示：{'；'.join(r['cautions'])}",
    ]
    if r["dayun"]:
        lines.append(f"大运：{r['dayun_start']} → {' '.join(r['dayun'])}")
    if not b["hour_known"]:
        lines.append("注意：未提供出生时辰，时柱缺失，以上结论基于年月日三柱，准确度下降。")
    lines.append(f"免责：{report['disclaimer']}")
    return "\n".join(lines)
