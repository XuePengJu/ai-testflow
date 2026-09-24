"""模型包：在此 import 各模型模块，确保注册到 Base.metadata。

init_db()（app/core/db.py）create_all 前也会直接 import，这里兜底保证
任何「先 import app.models」的调用路径都能发现全部模型（M1：TestTarget）。
"""
from app.models.automation import TestTarget  # noqa: F401  M1 全链路：被测系统
from app.models.automation import ExecutionRun  # noqa: F401  M2 执行引擎：自动化执行记录
