"""运行阶段 C 的完整船壳编译回归并输出确定性综合报告。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from 高天荒野舰艇数据契约 import canonical_sha256, load_hull_blueprint
from 高天荒野舰艇无界面船壳编译器 import HULL_COMPILER_INTERFACE_ID


ROOT = Path(__file__).resolve().parent
TESTS = (
    ("contract_and_hull", "高天荒野舰艇规范数据与船壳编译器测试.py"),
    ("aerodynamic_cache", "高天荒野舰艇气动缓存测试.py"),
    ("hull_rcs_cache", "高天荒野舰艇RCS缓存测试.py"),
)
HULL_FIXTURES = (
    ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json",
    ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20双层分离上层船壳.v1.json",
)
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段C船壳编译接口.v1.json"


def run_test(script_name: str) -> dict[str, Any]:
    completed = subprocess.run(
        (sys.executable, "-X", "utf8", str(ROOT / script_name)),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{script_name} 失败（exit={completed.returncode}）\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    result = json.loads(completed.stdout)
    if result.get("status") != "PASS":
        raise RuntimeError(f"{script_name} 未返回 PASS")
    return result


def build_report() -> dict[str, Any]:
    test_results = {name: run_test(script) for name, script in TESTS}
    fixtures = []
    for path in HULL_FIXTURES:
        blueprint = load_hull_blueprint(path)
        fixtures.append(
            {
                "id": blueprint.id,
                "source_sha256": canonical_sha256(blueprint),
                "version": blueprint.version,
            }
        )
    return {
        "balance_status": "prototype_unbalanced",
        "compiler_interface": HULL_COMPILER_INTERFACE_ID,
        "fixture_level": "canonical_blueprint_fixture",
        "hull_fixtures": fixtures,
        "report": "gaotian.hull-compiler.stage-c-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
        "tests": test_results,
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 C 报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
