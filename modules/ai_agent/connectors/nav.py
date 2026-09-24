"""网址导航连接器（link 型）。

从 nav_items 表动态生成「打开某个系统」的能力，AI 只负责挑出该去哪个站。

当前是纯跳转：nav_items 只有 url 字段。等深链方案落地（给表加
url_template / params 两列）后，这里可以升级为「AI 抽参数 → 拼出直达链接」，
即不用操作页面也能完成大量带条件的查询/导出需求。
"""
from .base import Connector, Capability, EXEC_LINK

# 单次最多暴露多少个导航项给 AI：清单会进 LLM prompt，多了既费 token 又降准确率
MAX_ITEMS = 40


class NavConnector(Connector):
    module_id = "nav_hub"
    display_name = "网站导航"

    def capabilities(self):
        rows = self._load_items()
        caps = []
        for r in rows[:MAX_ITEMS]:
            name = (r.get("name") or "").strip()
            url = (r.get("url") or "").strip()
            if not name or not url:
                continue
            desc = (r.get("description") or "").strip()
            tags = (r.get("tags") or "").strip()
            caps.append(Capability(
                cid=f"nav.{r.get('id')}",
                label=f"打开「{name}」",
                desc=desc or f"跳转到已收录的网站：{name}" + (f"（{tags}）" if tags else ""),
                module=self.module_id,
                execution=EXEC_LINK,
                target=url,
                keywords=self._keywords(name, desc, tags),
            ))
        return caps

    def _load_items(self):
        try:
            from core.db_base import db_query
            return db_query(
                "SELECT id, name, url, description, tags FROM nav_items "
                "ORDER BY is_hot DESC, sort_order ASC, id ASC"
            )
        except Exception:
            # 表还没建（nav_hub 首次访问才建表）或查询失败：静默降级为无能力
            return []

    @staticmethod
    def _keywords(name, desc, tags):
        out = [name]
        for src in (desc, tags):
            for piece in (src or "").replace("，", ",").split(","):
                piece = piece.strip()
                if piece and len(piece) <= 12:
                    out.append(piece)
        return out[:8]
