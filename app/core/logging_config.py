"""运维日志配置（M10）：root logger 统一落盘 logs/app.log（按大小滚动）。

全项目 8 个 service 模块已有 `logger = logging.getLogger(...)` 埋点，但根 logger
无 handler → INFO 级业务日志全部被静默丢弃。本模块在应用启动时一次性接好：

- root logger：INFO（可用 LOG_LEVEL 环境变量覆盖）+ RotatingFileHandler
  写 `logs/app.log`（10MB 滚动，保留 14 份历史，UTF-8）；
- uvicorn 接管：把 uvicorn / uvicorn.error / uvicorn.access 的 propagate 置 True，
  其日志沿层级汇入 root，由同一个文件 handler 落盘（uvicorn 自带的 stderr
  handler 保留，负责控制台输出——两边各写一份，不存在重复写文件）；
- 幂等：root 上已挂同一个日志文件 handler 时直接返回，重复调用无副作用。
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import LOG_LEVEL

# 日志格式与时间格式（团队约定，与单测断言保持一致）
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# uvicorn 三个 logger：沿层级 propagate 进 root，统一由文件 handler 落盘
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# 单文件上限 10MB，保留 14 份历史（约两周滚动窗口）
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 14


def _make_file_handler(log_dir: Path) -> RotatingFileHandler:
    """创建指向 log_dir/app.log 的滚动文件 handler（目录不存在则创建）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    return handler


def _existing_file_handler(root: logging.Logger, log_dir: Path) -> RotatingFileHandler | None:
    """检查 root 上是否已挂同一个 app.log 的文件 handler（幂等判定用）。"""
    target = str((log_dir / "app.log").resolve())
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and h.baseFilename == target:
            return h
    return None


def setup_logging(log_dir: str | Path = "logs", level: str | None = None) -> None:
    """初始化应用日志（幂等，可在进程生命周期内重复调用）。

    Args:
        log_dir: 日志目录，默认项目根下 `logs/`（单测传临时目录隔离）。
        level:   日志级别名（DEBUG/INFO/...），缺省读 LOG_LEVEL 环境变量，
                 再缺省 INFO。非法值回落 INFO。
    """
    level_name = (level or LOG_LEVEL or "INFO").strip().upper()
    numeric_level = getattr(logging, level_name, logging.INFO)

    log_path = Path(log_dir)
    root = logging.getLogger()  # root logger（不带名字参数）

    # 幂等：同一个日志文件已挂 handler → 只同步级别，不再追加（避免测试/热重载双写）
    if _existing_file_handler(root, log_path):
        root.setLevel(numeric_level)
        return

    root.setLevel(numeric_level)
    root.addHandler(_make_file_handler(log_path))

    # uvicorn 接管：清掉 uvicorn.run 默认 dictConfig 挂上的 handler，
    # 全部沿层级 propagate 进 root，统一由本模块的格式落盘（避免双份格式混杂）
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def get_log_level() -> str:
    """返回当前生效的日志级别名（诊断/health 接口展示用）。"""
    return logging.getLevelName(logging.getLogger().getEffectiveLevel())
