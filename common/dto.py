"""通用 DTO / 数据传输对象。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PaginationDTO:
    items: List[Any] = field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 20
    total_pages: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": self.items,
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "total_pages": self.total_pages,
        }


@dataclass
class ApiResult:
    code: int = 200
    msg: str = "success"
    data: Optional[Any] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {"code": self.code, "msg": self.msg, "data": self.data}
        if self.extras:
            payload.update(self.extras)
        return payload
