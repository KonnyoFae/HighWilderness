"""运行阶段 E 运行时参数第二切片并核对确定性保存报告。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from 高天荒野舰艇数据契约 import (
    COMBAT_SYSTEM_MODULE_CONTRACT_ID,
    canonical_sha256,
    load_ship_instance_snapshot,
    load_sortie_configuration,
)
from 高天荒野舰艇运行时参数编译器 import (
    DAMAGE_RESPONSE_POLICY_ID,
    POWER_ALLOCATION_POLICY_ID,
    RUNTIME_SHIP_PARAMETERS_INTERFACE_ID,
)
from 高天荒野舰艇战术机动求解器 import (
    FIXED_STEP_POLICY_ID,
    PROTOTYPE_ENVIRONMENT_PROFILE_ID,
    TACTICAL_DYNAMICS_INTERFACE_ID,
)


ROOT = Path(__file__).resolve().parent
TESTS = (
    ("combat_system_contract", "高天荒野舰艇战斗系统契约测试.py"),
    ("sortie_configuration", "高天荒野舰艇出航配置编译器测试.py"),
    ("runtime_parameters", "高天荒野舰艇运行时参数编译器测试.py"),
    ("tactical_dynamics", "高天荒野舰艇战术机动求解器测试.py"),
)
SORTIE_FIXTURE = ROOT / "舰艇数据" / "出航配置夹具" / "标准155x20载货出航.v1.json"
INSTANCE_FIXTURE = ROOT / "舰艇数据" / "舰艇实例夹具" / "标准155x20完好实例.v1.json"
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段E运行时参数接口.v1.json"


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
    sortie = load_sortie_configuration(SORTIE_FIXTURE)
    instance = load_ship_instance_snapshot(INSTANCE_FIXTURE)
    return {
        "balance_status": "prototype_unbalanced",
        "compiler_interface": RUNTIME_SHIP_PARAMETERS_INTERFACE_ID,
        "combat_system_contract": COMBAT_SYSTEM_MODULE_CONTRACT_ID,
        "damage_response_policy": DAMAGE_RESPONSE_POLICY_ID,
        "deferred_capabilities": [
            "safe_power_mode_upgrade_entitlement",
        ],
        "environment_profile": PROTOTYPE_ENVIRONMENT_PROFILE_ID,
        "fixed_step_policy": FIXED_STEP_POLICY_ID,
        "fixture_level": "contract_fixture",
        "fixtures": {
            "ship_instance_snapshot": {
                "id": instance.id,
                "source_sha256": canonical_sha256(instance),
                "version": instance.version,
            },
            "sortie_configuration": {
                "id": sortie.id,
                "source_sha256": canonical_sha256(sortie),
                "version": sortie.version,
            },
        },
        "power_policy": POWER_ALLOCATION_POLICY_ID,
        "report": "gaotian.runtime-ship-parameters.stage-e-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
        "tactical_dynamics_interface": TACTICAL_DYNAMICS_INTERFACE_ID,
        "tests": {name: run_test(script) for name, script in TESTS},
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 E 运行时报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
