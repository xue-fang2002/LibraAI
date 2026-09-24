"""配置加载器：读取 config/*.yaml 并提供统一访问接口。"""

import os
import threading
from typing import Any, Dict, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_BASE_DIR, "config")

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 配置缓存5分钟


def _read_yaml(filepath: str) -> Dict[str, Any]:
    """安全读取 yaml 文件；缺失 yaml 库时尝试用 json 兼容或返回空。"""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if _YAML_AVAILABLE:
            data = yaml.safe_load(content)
            return data if isinstance(data, dict) else {}
        # 没有 PyYAML：如果恰好是 JSON 格式也能解析（兜底）
        import json
        return json.loads(content)
    except Exception as e:
        # 配置语法错误必须暴露真实原因，不能静默降级为空 dict ——
        # 否则所有模块被判未启用，首页/登录全 500，且报错与真实原因无关。
        # 仅当异常确为 YAML 解析错误时才向上抛出；其余（含 yaml 库不可用时的
        # JSON 兜底失败、IO 错误等）保持静默兜底，避免误伤。
        # 注意：yaml 库可能未安装（_YAML_AVAILABLE=False），此时不能直连引用
        # yaml.YAMLError，否则会 NameError；改为按需局部 import 后做 isinstance 判定。
        _is_yaml_err = False
        try:
            import yaml as _yaml_mod
            _is_yaml_err = isinstance(e, _yaml_mod.YAMLError)
        except Exception:
            _is_yaml_err = False
        if _is_yaml_err:
            raise
        print(f"[config_loader] 读取 {filepath} 失败: {e}")
        return {}


def load_app_config(force: bool = False) -> Dict[str, Any]:
    """加载全局 app.yaml 配置，带内存缓存。"""
    key = "app_config"
    ts_key = "app_config_ts"
    import time
    with _cache_lock:
        now = time.time()
        if not force and _cache.get(key) and (now - _cache.get(ts_key, 0)) < _CACHE_TTL:
            return _cache[key]
        cfg = _read_yaml(os.path.join(_CONFIG_DIR, "app.yaml"))
        # 容器化支持：环境变量 DB_PATH 可覆盖数据库文件路径（例如挂卷到 /app/data），
        # 不设置时行为与之前完全一致，完全按 app.yaml 走。
        _db_env = (os.environ.get("DB_PATH") or "").strip()
        if _db_env:
            try:
                if not isinstance(cfg.get("database"), dict):
                    cfg["database"] = {}
                cfg["database"]["path"] = _db_env
            except Exception:
                pass
        _cache[key] = cfg
        _cache[ts_key] = now
        return cfg


def load_modules_config(force: bool = False) -> Dict[str, Any]:
    """加载 modules.yaml 模块开关配置，带内存缓存。
    若 modules.yaml 不存在（如全新 clone 尚未生成），回退读取 modules.yaml.example 模板，
    使仓库克隆后即可直接启动。"""
    key = "modules_config"
    ts_key = "modules_config_ts"
    import time
    with _cache_lock:
        now = time.time()
        if not force and _cache.get(key) and (now - _cache.get(ts_key, 0)) < _CACHE_TTL:
            return _cache[key]
        cfg_path = os.path.join(_CONFIG_DIR, "modules.yaml")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(_CONFIG_DIR, "modules.yaml.example")
        cfg = _read_yaml(cfg_path)
        _cache[key] = cfg
        _cache[ts_key] = now
        return cfg


def get_config(path: str, default: Any = None) -> Any:
    """
    按点分路径读取配置，优先 modules.yaml，其次 app.yaml。
    例：get_config("modules.book_lib.enabled") -> True
        get_config("app.port", 5000) -> 5000
    """
    sources = [load_modules_config(), load_app_config()]
    for src in sources:
        node = src
        ok_flag = True
        for seg in path.split("."):
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                ok_flag = False
                break
        if ok_flag:
            return node
    return default


def get_ai_mode_from_config() -> str:
    """获取 AI 模式：modules.yaml > app.yaml > 默认 off"""
    mode = get_config("modules.ai_center.ai_mode")
    if mode:
        return str(mode).lower()
    mode = get_config("ai.default_mode", "off")
    return str(mode).lower()


def is_module_enabled(module_id: str) -> bool:
    """查询某个模块是否启用。"""
    return bool(get_config(f"modules.{module_id}.enabled", False))


def get_module_sort_order(module_id: str) -> int:
    """获取模块排序值，越小越靠前，默认999。"""
    val = get_config(f"modules.{module_id}.sort_order", 999)
    try:
        return int(val)
    except Exception:
        return 999


def get_module_public(module_id: str) -> Optional[bool]:
    """模块是否对普通用户（非管理员）可见 —— 配置驱动，加模块不必改代码。

    返回 None 表示该模块**未配置** public，调用方应回落到兼容用的默认集合
    （plugin_scan.DEFAULT_PUBLIC_MODULES），避免老部署在没改配置时行为突变。
    """
    val = get_config(f"modules.{module_id}.public", None)
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)
