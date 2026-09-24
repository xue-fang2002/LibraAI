"""统一日志（#26）。

- 提供带文件滚动 + 控制台的 logger，任何模块用 get_logger(__name__) 即可。
- 错误信息统一带 traceback（logger.exception），替代散落的 print，便于排障。
- setup_logging() 在 app 启动时调用一次；get_logger 在导入时即可安全使用
  （未配置时回退到 root 的 lastResort handler，不会因缺 handler 而崩溃）。
"""

import logging
import logging.handlers
import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_CONFIGURED = False
_ROOT_NAME = "xianmufile"


def setup_logging(level=logging.INFO, log_file="app.log",
                  max_bytes=5 * 1024 * 1024, backup_count=3) -> logging.Logger:
    """配置 xianmufile 根 logger（文件滚动 + 控制台）。幂等，多次调用只生效一次。"""
    global _CONFIGURED
    logger = logging.getLogger(_ROOT_NAME)
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False  # 子 logger 仍会 propagate 到这里；这里只阻断继续向上

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 文件（按大小滚动，避免单文件无限增长）
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(_LOG_DIR, log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # 文件日志不可用不应阻断应用启动，仅保留控制台
        pass

    _CONFIGURED = True
    return logger


def get_logger(name=None) -> logging.Logger:
    """获取子 logger；未指定 name 时返回根 logger。"""
    if name is None:
        return logging.getLogger(_ROOT_NAME)
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
