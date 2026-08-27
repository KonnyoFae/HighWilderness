"""运行阶段 F 三舰集成测试并核对确定性保存报告。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇阶段F三舰集成测试 import (
    SHIP_PATHS,
    UNMANNED_MODULE_CATALOG,
    build_result,
)
from 高天荒野舰艇数据契约 import (
    canonical_sha256,
    load_hull_blueprint,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_ship_instance_snapshot,
    load_sortie_configuration,
)


ROOT = Path(__file__).resolve().parent
SAVED_REPORT = ROOT / "舰艇数据" / "报告" / "阶段F三舰集成接口.v1.json"


def resource_record(path: Path, loader: Callable[[Path], object]) -> dict[str, object]:
    resource = loader(path)
    return {
        "id": resource.id,
        "source_sha256": canonical_sha256(resource),
        "version": resource.version,
    }


def build_report() -> dict[str, object]:
    fixtures: dict[str, object] = {}
    for key, paths in SHIP_PATHS.items():
        fixtures[key] = {
            "hull_blueprint": resource_record(paths["hull"], load_hull_blueprint),
            "outfit_plan": resource_record(paths["outfit"], load_outfit_plan),
            "sortie_configuration": resource_record(
                paths["sortie"], load_sortie_configuration
            ),
            "ship_instance_snapshot": resource_record(
                paths["instance"], load_ship_instance_snapshot
            ),
        }
    return {
        "balance_status": "prototype_unbalanced",
        "fixture_level": "contract_fixture",
        "fixtures": fixtures,
        "integration": build_result(),
        "report": "gaotian.stage-f-three-canonical-ships-regression/v1",
        "schema": "gaotian.ship/v1alpha1",
        "status": "PASS",
        "unmanned_module_catalog": resource_record(
            UNMANNED_MODULE_CATALOG, load_module_prototype_catalog
        ),
    }


def main() -> None:
    report = build_report()
    if json.loads(SAVED_REPORT.read_text(encoding="utf-8")) != report:
        raise RuntimeError(f"已保存阶段 F 三舰报告已过期：{SAVED_REPORT}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
