"""能力注册表：所有连接器的注册与查询入口。

对外只暴露这几个函数，routes / planner 都从这里取能力：
  all_capabilities()            全部能力
  get_capability(cid)           按 id 取
  by_module(module_id)          某模块的能力
  summarize_modules()           给 LLM 粗分类用的模块摘要（很省 token）
  summarize_capabilities(caps)  给 LLM 精选候选用的能力摘要
"""
from typing import Any, Dict, List, Optional

from .connectors import _CONNECTORS
from .connectors.base import Capability, Connector

_INSTANCES: Optional[List[Connector]] = None


def _instances() -> List[Connector]:
    """惰性实例化连接器（nav 连接器要查库，不适合在 import 期就跑）。"""
    global _INSTANCES
    if _INSTANCES is None:
        _INSTANCES = [cls() for cls in _CONNECTORS]
    return _INSTANCES


def reset():
    """清空实例缓存（测试用，或导航项更新后强制刷新）。"""
    global _INSTANCES
    _INSTANCES = None


def all_capabilities() -> List[Capability]:
    """全部能力。单个连接器出错不影响其它连接器。"""
    caps: List[Capability] = []
    for conn in _instances():
        try:
            caps.extend(conn.capabilities())
        except Exception as e:
            print(f"⚠️ [ai_agent] 连接器 {conn.__class__.__name__} 加载失败: {e}")
    return caps


def get_capability(cid: str) -> Optional[Capability]:
    for cap in all_capabilities():
        if cap.cid == cid:
            return cap
    return None


def get_connector(module_id: str) -> Optional[Connector]:
    for conn in _instances():
        if conn.module_id == module_id:
            return conn
    return None


def by_module(module_id: str) -> List[Capability]:
    return [c for c in all_capabilities() if c.module == module_id]


def modules_summary() -> List[Dict[str, Any]]:
    """模块级摘要，给意图路由的第一级（粗分类）用。

    刻意只给「模块 + 一句话 + 关键词」，不放具体能力，
    这样 prompt 不会因为几十条能力而撑爆 n_ctx。
    """
    out = []
    for conn in _instances():
        caps = conn.capabilities()
        if not caps:
            continue
        kws: List[str] = []
        for c in caps:
            for k in c.keywords:
                if k not in kws:
                    kws.append(k)
        out.append({
            "module": conn.module_id,
            "name": conn.display_name,
            "count": len(caps),
            "keywords": kws[:12],
        })
    return out


def to_prompt_list(caps: List[Capability]) -> List[Dict[str, Any]]:
    """把能力列表转成给 LLM 的精简摘要（第二级精选时用）。"""
    return [c.to_prompt_dict() for c in caps]


def match_module_by_keywords(text: str) -> List[Any]:
    """按关键词给模块打分，返回 [(score, module_id), ...]（降序）。

    ⚠️ 这里必须遍历**全部能力**的关键词，不能用 modules_summary() 的 keywords：
    后者为了省 token 只取前 12 个，排在后面的能力（二维码、JSON、重命名…）
    会永远匹配不上，导致本来一句关键词就能定位的请求被迫去问 LLM。
    """
    t = (text or "").lower()
    scores: Dict[str, int] = {}
    for cap in all_capabilities():
        hit = 0
        for kw in cap.keywords:
            if kw and str(kw).lower() in t:
                hit += 1
        if hit:
            scores[cap.module] = scores.get(cap.module, 0) + hit
    return sorted(((v, k) for k, v in scores.items()), key=lambda x: -x[0])
