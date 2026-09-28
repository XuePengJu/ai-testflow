"""M10 日志落盘单测：setup_logging() 的落盘、格式与幂等行为。

覆盖点：
1. 调用 setup_logging(tmp) 后 logs/app.log 存在，且写入一条测试日志可见；
2. 日志行符合约定格式（asctime | LEVEL | name | message）；
3. 重复调用幂等（root 上不重复挂 handler，文件不双写）；
4. 级别过滤：ERROR 级别的 LOG_LEVEL 下 INFO 日志不落盘；
5. uvicorn 接管：propagate 置 True，uvicorn 日志沿层级汇入 root 文件。
"""
import logging
from logging.handlers import RotatingFileHandler

import pytest

from app.core.logging_config import setup_logging


@pytest.fixture()
def log_dir(tmp_path):
    """每个用例独立的日志目录（参数化 handler 路径，避免污染真实 logs/）。"""
    return tmp_path / "logs"


def _file_handler_of(log_dir):
    """从 root logger 上找到指向 log_dir/app.log 的文件 handler。"""
    target = str((log_dir / "app.log").resolve())
    for h in logging.getLogger().handlers:
        if isinstance(h, RotatingFileHandler) and h.baseFilename == target:
            return h
    return None


def test_setup_logging_creates_file_and_writes(log_dir):
    """调用后 app.log 存在，测试日志可见，格式符合约定。"""
    setup_logging(log_dir=log_dir)

    assert (log_dir / "app.log").exists(), "setup_logging 应创建 logs/app.log"

    logging.getLogger("test.m10").info("hello-m10-log")

    # RotatingFileHandler 默认有缓冲，flush 后再读
    _file_handler_of(log_dir).flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "hello-m10-log" in content, "测试日志应写入 app.log"
    # 格式：asctime | LEVEL(左对齐7位) | logger名 | message
    assert " | INFO    | test.m10 | hello-m10-log" in content


def test_setup_logging_idempotent(log_dir):
    """重复调用不重复挂 handler，日志不双写。"""
    setup_logging(log_dir=log_dir)
    setup_logging(log_dir=log_dir)
    setup_logging(log_dir=log_dir)

    handlers = _file_handlers_on_root(log_dir)
    assert len(handlers) == 1, f"同一日志文件只应有 1 个 handler，实际 {len(handlers)}"

    logging.getLogger("test.m10").info("idempotent-check")
    handlers[0].flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert content.count("idempotent-check") == 1, "同一条日志不应写入两次"


def _file_handlers_on_root(log_dir):
    target = str((log_dir / "app.log").resolve())
    return [h for h in logging.getLogger().handlers
            if isinstance(h, RotatingFileHandler) and h.baseFilename == target]


def test_setup_logging_respects_level(log_dir):
    """LOG_LEVEL=ERROR 时 INFO 日志不落盘（级别经参数显式传入）。"""
    setup_logging(log_dir=log_dir, level="ERROR")

    logging.getLogger("test.m10").info("should-not-appear")
    logging.getLogger("test.m10").error("should-appear")

    h = _file_handler_of(log_dir)
    h.flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "should-not-appear" not in content
    assert "should-appear" in content


def test_setup_logging_takes_over_uvicorn(log_dir):
    """uvicorn 系 logger propagate=True，日志沿层级汇入 root 的文件 handler。"""
    setup_logging(log_dir=log_dir)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.propagate is True, f"{name} 应 propagate 进 root"

    logging.getLogger("uvicorn.error").info("uvicorn-merged-check")
    _file_handler_of(log_dir).flush()
    content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "uvicorn-merged-check" in content, "uvicorn 日志应落进同一个 app.log"


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """用例前后清理 root logger 上本测试挂的 handler，不污染其他测试。"""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler):
            h.close()
            root.removeHandler(h)
