"""M0 质量部分运行：按测试类型（api / unit）生成 pytest node-id 清单文件。

用法：
    .venv/bin/python scripts/quality/test_scope.py --kind api --out /tmp/api-list.txt
    .venv/bin/python scripts/quality/test_scope.py --kind unit --out /tmp/unit-list.txt

pytest 侧用 @文件语法消费：pytest @/tmp/api-list.txt
类型判定复用 aggregate_quality 的 AST 扫描（与看板展示口径一致）。
类内用例带类名前缀：tests/test_x.py::TestClass::test_y（缺前缀 pytest 会报 not found）。
"""
import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate_quality import build_scope_nodeids  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按类型生成 pytest node-id 清单")
    parser.add_argument("--kind", choices=["api", "unit"], required=True)
    parser.add_argument("--out", type=Path, required=True, help="清单输出文件路径")
    args = parser.parse_args()

    tests_dir = _PROJECT_ROOT / "tests"
    nodeids = build_scope_nodeids(tests_dir, args.kind)
    if not nodeids:
        print(f"警告：未找到任何 {args.kind} 类型用例", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(nodeids) + ("\n" if nodeids else ""), encoding="utf-8")
    print(f"{args.kind} 清单 {len(nodeids)} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
