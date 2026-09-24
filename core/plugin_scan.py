"""
插件扫描器 PluginScanner

职责：
1) 遍历 modules/ 下所有子文件夹，读取每个模块的 __init__.py 中的 module_info
2) 根据 modules.yaml 过滤（启用/禁用、排序）
3) 为 Flask app 自动注册所有模块的 Blueprint（app.py 只需调用 register_all_modules）
4) 生成侧边栏菜单数据，供 layout_base.html 渲染

模块元数据 module_info 规范（每个模块 __init__.py 中定义）：
    module_info = {
        "module_id": "book_lib",           # 唯一ID，与 modules.yaml 对应
        "display_name": "图书馆",           # 侧边栏显示名称
        "icon": "📚",                       # 侧边栏图标
        "route_prefix": "/book",            # Blueprint 路由前缀（根则写 "/"）
        "url_default_endpoint": "index",    # 默认点击导航跳转到的 endpoint（不含蓝图名前缀）
        "permission_required": None,        # 访问该模块需要的权限，None=所有人可见
        "blueprint": <Flask Blueprint 对象>,
        # 可选
        "description": "...",
        "version": "1.0.0",
    }
"""

import importlib
import os
import pkgutil
import sys
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, url_for, g

from .config_loader import (
    is_module_enabled,
    get_module_sort_order,
    get_module_public,
    load_modules_config,
)
from .db_base import has_permission

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULES_DIR = os.path.join(_BASE_DIR, "modules")

# 全局缓存
_scanned_modules: Dict[str, Dict[str, Any]] = {}
_scan_done = False


class PluginScanner:
    def __init__(self, modules_dir: str = _MODULES_DIR):
        self.modules_dir = modules_dir
        self._modules: Dict[str, Dict[str, Any]] = {}

    # --------------------------------------------------------
    # 扫描 & 元数据加载
    # --------------------------------------------------------
    def scan(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        扫描 modules/<dir>/__init__.py，读取 module_info。
        结果存入全局缓存（force=True 可强制重新扫描）。
        """
        global _scanned_modules, _scan_done
        if _scan_done and not force:
            return dict(_scanned_modules)

        if not os.path.isdir(self.modules_dir):
            print(f"[plugin_scan] modules 目录不存在: {self.modules_dir}")
            return {}

        # 确保 modules 目录在 sys.path 中可被 import
        if _BASE_DIR not in sys.path:
            sys.path.insert(0, _BASE_DIR)

        result: Dict[str, Dict[str, Any]] = {}

        for entry in sorted(os.listdir(self.modules_dir)):
            full = os.path.join(self.modules_dir, entry)
            if not os.path.isdir(full):
                continue
            init_py = os.path.join(full, "__init__.py")
            if not os.path.exists(init_py):
                continue

            module_pkg_path = f"modules.{entry}"
            try:
                if module_pkg_path in sys.modules:
                    mod = sys.modules[module_pkg_path]
                    _info = getattr(mod, "module_info", None)
                    _has_bp = isinstance(_info, dict) and _info.get("blueprint") is not None
                    # ⚠️ 带 Blueprint 的模块若已被完整导入（routes 子模块已执行），
                    # 这里绝不能再 reload：importlib.reload 只重新执行 __init__.py，
                    # 会新建一个空 Blueprint，而 routes 等子模块因已在 sys.modules
                    # 中不会重新执行 —— 结果新 Blueprint 上一条路由都没有，整个模块
                    # 变成 404（曾由 ai_agent 连接器顶层 import modules.toolbox 触发）。
                    # 仅「无 Blueprint 的纯元数据模块」保留 reload 以刷新元数据。
                    if not _has_bp:
                        mod = importlib.reload(mod)
                else:
                    mod = importlib.import_module(module_pkg_path)
            except Exception as e:
                print(f"[plugin_scan] ⚠️ 加载模块[{entry}]失败(import异常): {e}")
                continue

            info = getattr(mod, "module_info", None)
            if not isinstance(info, dict):
                print(f"[plugin_scan] ⚠️ 跳过模块[{entry}]: __init__.py 未定义 module_info 字典")
                continue

            mid = info.get("module_id") or entry
            bp = info.get("blueprint")
            # module_id 校验
            if mid != entry:
                print(f"[plugin_scan] ⚠️ 模块[{entry}] module_id={mid} 与文件夹名不一致，将以文件夹名为准")
                info["module_id"] = entry
                mid = entry

            info.setdefault("display_name", mid)
            info.setdefault("icon", "📄")
            info.setdefault("route_prefix", f"/{mid}")
            info.setdefault("url_default_endpoint", "index")

            # 模块自报元数据（供 config_center 聚合、权限分组、AI 分组使用）
            # 这些字段由各模块 __init__.py 声明，plugin_scan 只负责收集
            info["config_entries"] = getattr(mod, "config_entries", []) or []
            info["PERMISSIONS"] = getattr(mod, "PERMISSIONS", []) or []
            info["ai_features"] = getattr(mod, "ai_features", []) or []

            # 若未定义 blueprint 也允许（纯元数据模块），但注册时会跳过
            if bp is None:
                print(f"[plugin_scan] 模块[{mid}]未提供 blueprint，仅注册为导航项")

            result[mid] = info

        _scanned_modules = result
        _scan_done = True
        return dict(result)

    # --------------------------------------------------------
    # 应用 modules.yaml 的过滤 + 排序
    # --------------------------------------------------------
    def get_enabled_modules(self) -> List[Dict[str, Any]]:
        """返回已启用的模块列表，按 sort_order 从小到大排序。"""
        all_mods = self.scan()
        enabled_list: List[Dict[str, Any]] = []
        for mid, info in all_mods.items():
            if not is_module_enabled(mid):
                continue
            order = get_module_sort_order(mid)
            info = dict(info)
            info["_sort_order"] = order
            enabled_list.append(info)
        enabled_list.sort(key=lambda m: (m.get("_sort_order", 999), m.get("module_id", "")))
        return enabled_list

    # --------------------------------------------------------
    # 生成侧边栏菜单项（带权限过滤 + URL拼接）
    # --------------------------------------------------------
    # 默认对所有人（含匿名）可见的模块。其他模块仅管理员可见（可手动访问其 URL）。
    #
    # ⚠️ 这里是**兜底默认值**：优先读 modules.yaml 里每个模块的 public 字段
    # （见 _is_public_module）。新模块只要在 modules.yaml 写 `public: true` 即可对
    # 普通用户可见，不必再改本文件；只有 yaml 完全没写 public 时才回落到本集合。
    DEFAULT_PUBLIC_MODULES = {"homepage", "book_lib", "chart", "experience_hub","office_tools","toolbox","nav_hub",
                              "toolbox_hub", "feedback", "ai_agent"}

    # 非导航模块：虽在 modules.yaml 启用，但不作为侧边栏导航项出现
    # （例如 ai_center 是全局 AI 服务开关，其配置页由 config_center 聚合，不应单独占侧边栏）
    NON_NAV_MODULES = {"ai_center"}

    # 已合并进「工具箱 Hub」的模块：侧边栏不再单独出现，由 toolbox_hub 统一收口。
    # 注意：这里只影响侧边栏显示，不注销蓝图 —— 这些模块的路由（/chart、/office_tools、/toolbox）
    # 仍保持原样可访问，Hub 页面正是通过深链把用户导向它们（hub 收口：只归组换壳，不动业务代码）。
    HUB_MERGED_MODULES = {"chart", "office_tools", "toolbox"}

    # 上述被收口模块的「归属导航项」：它们的页面在侧栏没有自己的入口，
    # 若不归一化，current_module_id 匹配不到任何 nav-item，侧栏将完全不高亮
    # （黑夜模式下表现为「选中灯光整个消失」）。这里统一挂到其所属的 Hub 导航项上。
    # 仅影响侧栏高亮标识，不改变这些模块自身的路由与业务逻辑。
    # 以后再有模块并入工具箱 Hub，只需在此登记归属即可。
    HUB_PARENT_MODULE = {
        "chart": "toolbox_hub",
        "office_tools": "toolbox_hub",
        "toolbox": "toolbox_hub",
    }

    def _is_public_module(self, module_id: str) -> bool:
        """普通用户能否在侧边栏看到该模块。

        配置驱动：优先读 modules.yaml 的 `modules.<id>.public`；
        未配置（None）时回落到 DEFAULT_PUBLIC_MODULES，保证老部署行为不变。
        """
        val = get_module_public(module_id)
        if val is None:
            return module_id in self.DEFAULT_PUBLIC_MODULES
        return bool(val)

    def build_menu_items(self) -> List[Dict[str, Any]]:
        """
        生成给 Jinja2 模板使用的侧边栏菜单数组：
        [
          {"module_id": "homepage", "display_name": "AI问答", "icon": "💬", "url": "/"},
          ...
        ]

        侧边栏可见性策略：
        - 匿名用户 / 普通用户：仅显示 DEFAULT_PUBLIC_MODULES 中定义的模块（AI问答 + 图书馆）
        - 管理员（role=admin/superadmin）：显示全部已启用模块
        - 显式配置 permission_required 的模块，仍按权限校验（路由层也会再拦截）
        """
        menu: List[Dict[str, Any]] = []
        user = g.get("current_user") if hasattr(g, "current_user") else None
        is_admin = False
        if user:
            role = (user.get("role") or "").lower()
            is_admin = role in ("admin", "superadmin")
        enabled = self.get_enabled_modules()

        for info in enabled:
            mid = info["module_id"]
            # 0) 非导航模块（如 ai_center 服务开关）不进侧边栏
            if mid in self.NON_NAV_MODULES:
                continue
            # 0.1) 已合并进工具箱 Hub 的模块：侧边栏只保留「工具箱」一项（路由仍可正常访问）
            if mid in self.HUB_MERGED_MODULES:
                continue
            # 1) 非管理员：按 public 配置过滤（yaml 优先，未配置才回落默认集合）
            if not is_admin and not self._is_public_module(mid):
                continue
            # 2) 模块显式声明了权限要求：没有该权限则不可见
            perm = info.get("permission_required")
            if perm:
                if not hasattr_permission_wrapper(user, perm):
                    continue
            display = info.get("display_name", mid)
            icon = info.get("icon", "📄")
            prefix = info.get("route_prefix", f"/{mid}")
            endpoint_name = info.get("url_default_endpoint", "index")
            # 构造 URL：优先用 url_for（蓝图端点名是 <blueprint_name>.<endpoint>）
            try:
                bp = info.get("blueprint")
                if bp is not None:
                    bp_name = getattr(bp, "name", mid)
                    url = url_for(f"{bp_name}.{endpoint_name}")
                else:
                    url = prefix or "/"
            except Exception:
                url = prefix.rstrip("/") or "/"
                if endpoint_name and endpoint_name != "index":
                    url += f"/{endpoint_name}"
            menu.append({
                "module_id": mid,
                "display_name": display,
                "icon": icon,
                "url": url,
            })
        return menu

    # --------------------------------------------------------
    # 获取所有模块的 URL 映射（给全局上下文注入用）
    # --------------------------------------------------------
    def build_module_urls(self) -> Dict[str, str]:
        urls: Dict[str, str] = {}
        enabled = self.get_enabled_modules()
        for info in enabled:
            mid = info["module_id"]
            try:
                bp = info.get("blueprint")
                endpoint_name = info.get("url_default_endpoint", "index")
                if bp is not None:
                    bp_name = getattr(bp, "name", mid)
                    urls[mid] = url_for(f"{bp_name}.{endpoint_name}")
                else:
                    urls[mid] = info.get("route_prefix", f"/{mid}")
            except Exception:
                urls[mid] = info.get("route_prefix", f"/{mid}")
        return urls

    # --------------------------------------------------------
    # 给 Flask app 自动注册所有 Blueprint
    # --------------------------------------------------------
    def register_blueprints(self, app: Flask) -> List[str]:
        """
        自动扫描并注册所有启用模块的 Blueprint。
        app.py 只需调用一次。
        返回：已注册的模块ID列表。
        """
        registered: List[str] = []
        enabled = self.get_enabled_modules()
        for info in enabled:
            bp = info.get("blueprint")
            if bp is None:
                continue
            mid = info["module_id"]
            prefix = info.get("route_prefix") or f"/{mid}"
            # 规范化前缀：根路由用 "/" 或 ""
            if prefix in ("/", "", None):
                prefix_url = None
            else:
                prefix_url = prefix.rstrip("/")
                if not prefix_url.startswith("/"):
                    prefix_url = "/" + prefix_url
            try:
                app.register_blueprint(bp, url_prefix=prefix_url)
                registered.append(mid)
                print(f"✅ 已注册模块 [{mid}] -> {prefix_url or '/'}（{getattr(bp, 'name', mid)}）")
            except Exception as e:
                print(f"[plugin_scan] ❌ 注册模块[{mid}]失败: {e}")
        return registered


def hasattr_permission_wrapper(user, perm: str) -> bool:
    try:
        return has_permission(user, perm)
    except Exception:
        return False


# ============================================================
# 便捷全局函数（便于调用方使用，无需每次实例化）
# ============================================================
_global_scanner: Optional[PluginScanner] = None


def _get_scanner() -> PluginScanner:
    global _global_scanner
    if _global_scanner is None:
        _global_scanner = PluginScanner()
    return _global_scanner


def get_all_module_info(force: bool = False) -> Dict[str, Dict[str, Any]]:
    return _get_scanner().scan(force=force)


def get_menu_items() -> List[Dict[str, Any]]:
    return _get_scanner().build_menu_items()


def get_module_urls() -> Dict[str, str]:
    return _get_scanner().build_module_urls()


def register_all_modules(app: Flask) -> List[str]:
    return _get_scanner().register_blueprints(app)


# ============================================================
# 模块自报元数据聚合（供 config_center 使用）
# ============================================================
def get_config_entries_grouped() -> List[Dict[str, Any]]:
    """
    返回各模块自报的配置项，按模块分组：
    [{"module_id","display_name","icon","entries":[{"name","desc","url"}]}, ...]
    仅包含声明了 config_entries 且非空的模块。
    """
    out: List[Dict[str, Any]] = []
    for mid, info in get_all_module_info().items():
        entries = info.get("config_entries") or []
        if not entries:
            continue
        out.append({
            "module_id": mid,
            "display_name": info.get("display_name", mid),
            "icon": info.get("icon", "📄"),
            "entries": entries,
        })
    return out


def get_all_permissions() -> List[Tuple[str, str]]:
    """聚合所有模块自报的 PERMISSIONS，去重（按 key）。"""
    seen = set()
    result: List[Tuple[str, str]] = []
    for info in get_all_module_info().values():
        for p in (info.get("PERMISSIONS") or []):
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            key = p[0]
            if key in seen:
                continue
            seen.add(key)
            result.append((key, p[1]))
    return result


def get_permissions_grouped() -> List[Dict[str, Any]]:
    """按模块分组返回权限点，供用户管理弹窗分组渲染。"""
    out: List[Dict[str, Any]] = []
    for mid, info in get_all_module_info().items():
        perms = info.get("PERMISSIONS") or []
        if not perms:
            continue
        out.append({
            "module_id": mid,
            "display_name": info.get("display_name", mid),
            "icon": info.get("icon", "📄"),
            "perms": [{"key": p[0], "name": p[1]} for p in perms if isinstance(p, (list, tuple)) and len(p) >= 2],
        })
    return out


def get_all_ai_features() -> List[Dict[str, Any]]:
    """聚合所有模块自报的 ai_features：[{"module_id","display_name","features":[...]}]。"""
    out: List[Dict[str, Any]] = []
    for mid, info in get_all_module_info().items():
        feats = info.get("ai_features") or []
        if not feats:
            continue
        out.append({
            "module_id": mid,
            "display_name": info.get("display_name", mid),
            "icon": info.get("icon", "📄"),
            "features": feats,
        })
    return out

