"""运行阶段 D 的模块与舾装回归并核对确定性保存报告。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from 高天荒野舰艇数据契约 import (
    canonical_sha256,
    load_module_prototype_catalog,
    load_outfit_plan,
)
from 高天荒野舰艇无界面舾装编译器 import (
    DERIVED_SHIP_SNAPSHOT_INTERFACE_ID,
    OUTFIT_COMPILER_INTERFACE_ID,
)


ROOT = Path(__file__).resolve().parent
TESTS = (
    ("module_contract", "高天荒野舰艇模块原型契约测试.py"),
    ("outfit_compiler", "高天荒野舰艇舾装编译器测试.py"),
    ("actuator_aggregation", "高天荒野舰艇执行器聚合与派生快照测试.py"),
)
MODULE_FIXTURE = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
OUTFIT_FIXTURE = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20最小舾装.v1.json"
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段D舾装编译接口.v1.json"


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
    module_fixture = load_module_prototype_catalog(MODULE_FIXTURE)
    outfit_fixture = load_outfit_plan(OUTFIT_FIXTURE)
    return {
        "balance_status": "prototype_unbalanced",
        "compiler_interface": OUTFIT_COMPILER_INTERFACE_ID,
        "derived_snapshot_interface": DERIVED_SHIP_SNAPSHOT_INTERFACE_ID,
        "fixture_level": "contract_fixture",
        "fixtures": {
            "module_catalog": {
                "id": module_fixture.id,
                "source_sha256": canonical_sha256(module_fixture),
                "version": module_fixture.version,
            },
            "outfit_plan": {
                "id": outfit_fixture.id,
                "source_sha256": canonical_sha256(outfit_fixture),
                "version": outfit_fixture.version,
            },
        },
        "report": "gaotian.outfit-compiler.stage-d-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
        "tests": {name: run_test(script) for name, script in TESTS},
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 D 报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
