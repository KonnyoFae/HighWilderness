"""阶段 D 首切片：模块原型契约、规范化和稳定错误码回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ModulePrototype,
    ModulePrototypeCatalog,
    ResourceReference,
    canonical_json,
    canonical_sha256,
    load_json,
    load_module_prototype_catalog,
)


ROOT = Path(__file__).resolve().parent
MODULE_FIXTURE = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
SCHEMA_FILE = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def main() -> None:
    schema = load_json(SCHEMA_FILE)
    assert {entry["$ref"] for entry in schema["oneOf"]} >= {
        "#/$defs/modulePrototypeCatalog",
        "#/$defs/hullBlueprint",
    }

    source = load_json(MODULE_FIXTURE)
    catalog = load_module_prototype_catalog(MODULE_FIXTURE)
    assert catalog.fixture_level == "contract_fixture"
    assert len(catalog.modules) == 9
    assert canonical_json(catalog) == MODULE_FIXTURE.read_text(encoding="utf-8")
    assert ModulePrototypeCatalog.parse(json.loads(canonical_json(catalog))) == catalog

    categories = {module.category for module in catalog.modules}
    assert categories == {
        "cargo_hold",
        "cic",
        "lift_fuel_tank",
        "main_engine",
        "maneuver_thruster",
        "generator",
        "damage_control",
        "crew_quarters",
        "remote_core",
    }
    cic = catalog.module(ResourceReference("gtw.module.fixture.cic", 1))
    quarters = catalog.module(ResourceReference("gtw.module.fixture.crew_quarters", 1))
    damage_control = catalog.module(ResourceReference("gtw.module.fixture.damage_control", 1))
    cargo_hold = catalog.module(ResourceReference("gtw.module.fixture.cargo_hold", 1))
    generator = catalog.module(ResourceReference("gtw.module.fixture.generator", 1))
    lift_tank = catalog.module(
        ResourceReference("gtw.module.fixture.lift_fuel_tank", 1)
    )
    main_engine = catalog.module(
        ResourceReference("gtw.module.fixture.main_engine", 1)
    )
    remote_core = catalog.module(ResourceReference("gtw.module.fixture.remote_core", 1))
    assert cic.installation.provided_slots == ("cic_internal",)
    assert remote_core.installation.host_slot == "cic_internal"
    assert remote_core.installation.internal_footprint_half_cells == ()
    assert cic.minimum_crew_counts() == {"officer": 1}
    assert cic.standard_crew_counts() == {"officer": 2}
    assert quarters.minimum_crew_counts() == {}
    assert damage_control.power.consumer_category == "damage_control"
    assert damage_control.default_operating_mode == "standby"
    assert damage_control.automatic_activation_events == (
        "ship.damage_control_required",
    )
    assert remote_core.default_operating_mode == "standby"
    assert remote_core.automatic_activation_events == (
        "ship.remote_control_selected",
    )
    assert cargo_hold.capability.to_dict() == {
        "bulk_cargo_capacity_kg": 100_000.0,
        "kind": "cargo_hold",
    }
    assert cargo_hold.minimum_crew_counts() == {}
    assert not cargo_hold.counts_toward_departure_minimum
    assert main_engine.damage_output_fraction("engine.throttle", 0.0) == 0.0
    assert main_engine.damage_output_fraction("engine.throttle", 0.5) == 0.75
    assert main_engine.damage_output_fraction("engine.throttle", 0.75) == 0.875
    assert generator.damage_output_fraction("generator.regulation", 0.5) == 0.5
    assert lift_tank.damage_output_fraction("lift_tank.lift", 0.01) == 1.0
    assert lift_tank.damage_output_fraction("lift_tank.lift", 0.0) == 0.0

    stepwise_generator = deepcopy(
        next(item for item in source["modules"] if item["category"] == "generator")
    )
    stepwise_generator["damage_responses"][0]["model"] = "stepwise"
    parsed_stepwise = ModulePrototype.parse(stepwise_generator, "$.stepwise_generator")
    assert (
        parsed_stepwise.damage_output_fraction("generator.regulation", 0.49) == 0.0
    )
    assert (
        parsed_stepwise.damage_output_fraction("generator.regulation", 0.5) == 0.5
    )

    # 半格偏移允许偶数宽模块以包围盒中心作为锚点，不要求锚点落在某个格心。
    even_width = deepcopy(next(item for item in source["modules"] if item["category"] == "crew_quarters"))
    even_width["installation"]["internal_footprint_half_cells"] = [[-1, 0], [1, 0]]
    parsed_even = ModulePrototype.parse(even_width, "$.even_width")
    assert parsed_even.installation.internal_footprint_half_cells == ((-1, 0), (1, 0))

    # 顶挂层使用同一半格语义；本测试只验证契约，不把该探针写入正式模块目录。
    top_probe = deepcopy(even_width)
    top_probe["installation"]["internal_footprint_half_cells"] = []
    top_probe["installation"]["internal_deck_span"] = 0
    top_probe["installation"]["top_footprint_half_cells"] = [[-1, 0], [1, 0]]
    top_probe["installation"]["deck_rule"] = "local_exposed_top"
    parsed_top = ModulePrototype.parse(top_probe, "$.top_probe")
    assert parsed_top.installation.top_footprint_half_cells == ((-1, 0), (1, 0))

    unknown_field = deepcopy(source["modules"][0])
    unknown_field["aggregate_mass_override"] = 1
    require_contract_error(
        "object.extra_keys", lambda: ModulePrototype.parse(unknown_field, "$.unknown")
    )

    off_center = deepcopy(even_width)
    off_center["installation"]["internal_footprint_half_cells"] = [[0, 0], [2, 0]]
    require_contract_error(
        "module.footprint_not_centered",
        lambda: ModulePrototype.parse(off_center, "$.off_center"),
    )

    mixed_parity = deepcopy(next(item for item in source["modules"] if item["category"] == "main_engine"))
    mixed_parity["installation"]["exhaust_clearance_half_cells"] = [[0, -1]]
    require_contract_error(
        "module.half_cell_parity",
        lambda: ModulePrototype.parse(mixed_parity, "$.mixed_parity"),
    )

    missing_category = deepcopy(next(item for item in source["modules"] if item["category"] == "damage_control"))
    missing_category["power"]["consumer_category"] = None
    require_contract_error(
        "module.power_category_missing",
        lambda: ModulePrototype.parse(missing_category, "$.missing_category"),
    )

    wrong_damage_category = deepcopy(next(item for item in source["modules"] if item["category"] == "damage_control"))
    wrong_damage_category["power"]["consumer_category"] = "sensors"
    require_contract_error(
        "module.damage_control_power_category",
        lambda: ModulePrototype.parse(wrong_damage_category, "$.wrong_damage_category"),
    )

    invalid_activation_default = deepcopy(
        next(item for item in source["modules"] if item["category"] == "damage_control")
    )
    invalid_activation_default["default_operating_mode"] = "active"
    require_contract_error(
        "module.activation_event_default_mode",
        lambda: ModulePrototype.parse(
            invalid_activation_default, "$.invalid_activation_default"
        ),
    )

    wrong_damage_function = deepcopy(
        next(item for item in source["modules"] if item["category"] == "main_engine")
    )
    wrong_damage_function["damage_responses"][0]["function_id"] = "engine.wrong"
    require_contract_error(
        "module.damage_response_function_set",
        lambda: ModulePrototype.parse(
            wrong_damage_function, "$.wrong_damage_function"
        ),
    )

    invalid_damage_endpoints = deepcopy(
        next(item for item in source["modules"] if item["category"] == "generator")
    )
    invalid_damage_endpoints["damage_responses"][0]["points"][0][
        "output_fraction"
    ] = 0.1
    require_contract_error(
        "module.damage_response_endpoints",
        lambda: ModulePrototype.parse(
            invalid_damage_endpoints, "$.invalid_damage_endpoints"
        ),
    )

    binary_with_points = deepcopy(
        next(item for item in source["modules"] if item["category"] == "lift_fuel_tank")
    )
    binary_with_points["damage_responses"][0]["points"] = [
        {"durability_fraction": 0.0, "output_fraction": 0.0},
        {"durability_fraction": 1.0, "output_fraction": 1.0},
    ]
    require_contract_error(
        "module.binary_damage_response_has_points",
        lambda: ModulePrototype.parse(binary_with_points, "$.binary_with_points"),
    )

    wrong_remote_host = deepcopy(next(item for item in source["modules"] if item["category"] == "remote_core"))
    wrong_remote_host["installation"]["host_slot"] = "wrong_slot"
    require_contract_error(
        "module.remote_core_host",
        lambda: ModulePrototype.parse(wrong_remote_host, "$.wrong_remote_host"),
    )

    wrong_departure_flag = deepcopy(next(item for item in source["modules"] if item["category"] == "generator"))
    wrong_departure_flag["counts_toward_departure_minimum"] = False
    require_contract_error(
        "module.departure_minimum_category",
        lambda: ModulePrototype.parse(wrong_departure_flag, "$.wrong_departure_flag"),
    )

    crewed_full_automation = deepcopy(
        next(item for item in source["modules"] if item["category"] == "cic")
    )
    crewed_full_automation["automation"]["level"] = "full"
    require_contract_error(
        "module.full_automation_has_crew",
        lambda: ModulePrototype.parse(crewed_full_automation, "$.crewed_full"),
    )

    duplicate = deepcopy(source)
    duplicate["modules"].append(deepcopy(duplicate["modules"][0]))
    require_contract_error(
        "resource.duplicate", lambda: ModulePrototypeCatalog.parse(duplicate)
    )

    missing_unmanned_variant = deepcopy(source)
    missing_unmanned_variant["modules"][0]["automation"]["unmanned_variant"] = {
        "id": "gtw.module.fixture.missing_unmanned",
        "version": 1,
    }
    missing_unmanned_variant["modules"][0]["automation"][
        "engineering_microclusters_required"
    ] = 1
    require_contract_error(
        "resource.reference_missing",
        lambda: ModulePrototypeCatalog.parse(missing_unmanned_variant),
    )

    minimum_crew_totals: dict[str, int] = {}
    standard_crew_totals: dict[str, int] = {}
    for module in catalog.modules:
        for crew_type, count in module.minimum_crew_counts().items():
            minimum_crew_totals[crew_type] = minimum_crew_totals.get(crew_type, 0) + count
        for crew_type, count in module.standard_crew_counts().items():
            standard_crew_totals[crew_type] = standard_crew_totals.get(crew_type, 0) + count

    result = {
        "catalog": f"{catalog.id}@{catalog.version}",
        "categories": sorted(categories),
        "fixture_level": catalog.fixture_level,
        "minimum_crew_totals_for_one_each": minimum_crew_totals,
        "module_count": len(catalog.modules),
        "source_sha256": canonical_sha256(catalog),
        "standard_crew_totals_for_one_each": standard_crew_totals,
        "status": "PASS",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
