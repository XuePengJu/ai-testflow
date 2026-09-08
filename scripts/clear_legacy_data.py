"""清空旧任务测试数据（一次性，部署/重构时执行）。

删除范围：tasks / step_logs 表全量 + outputs / uploads 目录下的导出与上传文件。
保留：用户账号（users）、分类（categories）、.DS_Store 与目录结构。

老板明确：旧任务不做保留，直接删除（都是测试数据）。本地库与服务器库各自独立，需分别执行。
用法：cd 项目根目录 && python scripts/clear_legacy_data.py
"""
import shutil
import sqlite3
import sys
from pathlib import Path

# 让脚本在任意 cwd 下都能 import app 包（项目根目录加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import DB_PATH, OUTPUT_DIR, UPLOAD_DIR


def clear() -> None:
    # 1. 清空表数据
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tasks")
    n_tasks = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM step_logs")
    n_steps = cur.fetchone()[0]
    cur.execute("DELETE FROM step_logs")
    cur.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()
    print(f"已清空 tasks（{n_tasks} 条）/ step_logs（{n_steps} 条）")

    # 2. 清空导出与上传文件（保留 .DS_Store 与目录结构）
    for base in (OUTPUT_DIR, UPLOAD_DIR):
        if not base.exists():
            continue
        n = 0
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name != ".DS_Store":
                p.unlink()
                n += 1
        print(f"已清空 {base.name} 下 {n} 个文件")

    print("完成")


if __name__ == "__main__":
    clear()
