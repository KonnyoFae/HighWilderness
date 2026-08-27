"""运行阶段 G 端到端场景并核对确定性保存报告。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇阶段F三舰集成测试 import SHIP_PATHS
from 高天荒野舰艇阶段G端到端集成测试 import build_result
from 高天荒野舰艇数据契约 import (
    canonical_sha256,
    load_hull_blueprint,
    load_outfit_plan,
    load_ship_instance_snapshot,
    load_sortie_configuration,
)


ROOT = Path(__file__).resolve().parent
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段G端到端集成接口.v1.json"


def resource_record(path: Path, loader: Callable[[Path], object]) -> dict[str, object]:
    resource = loader(path)
    return {
        "id": resource.id,
        "source_sha256": canonical_sha256(resource),
        "version": resource.version,
    }


def ship_fixture_record(key: str) -> dict[str, object]:
    paths = SHIP_PATHS[key]
    return {
        "hull_blueprint": resource_record(paths["hull"], load_hull_blueprint),
        "outfit_plan": resource_record(paths["outfit"], load_outfit_plan),
        "ship_instance_snapshot": resource_record(
            paths["instance"], load_ship_instance_snapshot
        ),
        "sortie_configuration": resource_record(
            paths["sortie"], load_sortie_configuration
        ),
    }


def build_report() -> dict[str, object]:
    return {
        "balance_status": "prototype_unbalanced",
        "fixture_level": "contract_fixture",
        "fixtures": {
            "conventional_crewed": ship_fixture_record("conventional_crewed"),
            "unmanned_flagship": ship_fixture_record("unmanned_flagship"),
        },
        "integration": build_result(),
        "report": "gaotian.stage-g-end-to-end-integration-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 G 报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
