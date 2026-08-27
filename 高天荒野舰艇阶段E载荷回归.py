"""运行阶段 E 首切片回归并核对确定性保存报告。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from 高天荒野舰艇出航配置编译器 import SORTIE_COMPILER_INTERFACE_ID
from 高天荒野舰艇数据契约 import canonical_sha256, load_sortie_configuration
from 高天荒野舰艇运行时参数编译器 import RUNTIME_SHIP_PARAMETERS_INTERFACE_ID


ROOT = Path(__file__).resolve().parent
TEST_SCRIPT = "高天荒野舰艇出航配置编译器测试.py"
SORTIE_FIXTURE = ROOT / "舰艇数据" / "出航配置夹具" / "标准155x20载货出航.v1.json"
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段E出航载荷接口.v1.json"


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
    fixture = load_sortie_configuration(SORTIE_FIXTURE)
    return {
        "balance_status": "prototype_unbalanced",
        "compiler_interface": SORTIE_COMPILER_INTERFACE_ID,
        "deferred_capabilities": [
            "safe_power_mode_upgrade_entitlement",
        ],
        "downstream_runtime_interface": RUNTIME_SHIP_PARAMETERS_INTERFACE_ID,
        "fixture_level": fixture.fixture_level,
        "fixtures": {
            "sortie_configuration": {
                "id": fixture.id,
                "source_sha256": canonical_sha256(fixture),
                "version": fixture.version,
            }
        },
        "report": "gaotian.sortie-compiler.stage-e-load-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
        "tests": {"sortie_configuration": run_test(TEST_SCRIPT)},
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 E 报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
