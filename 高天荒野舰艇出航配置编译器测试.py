"""SortieConfiguration、货仓载荷与当前质量/惯量派生回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ModulePrototypeCatalog,
    SortieConfigurationInput,
    canonical_json,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_sortie_configuration,
)
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    build_derived_ship_snapshot,
    compile_outfit,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
HULL_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
OUTFIT_FIXTURE = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20最小舾装.v1.json"
SORTIE_FIXTURE = ROOT / "舰艇数据" / "出航配置夹具" / "标准155x20载货出航.v1.json"
SCHEMA_FILE = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"


def require_close(actual: float, expected: float) -> None:
    if not isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-7):
        raise AssertionError(f"{actual!r} != {expected!r}")


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def crew_item(source: dict[str, object], crew_type: str) -> dict[str, object]:
    return next(item for item in source["crew"] if item["crew_type"] == crew_type)


def main() -> None:
    schema = load_json(SCHEMA_FILE)
    assert "#/$defs/sortieConfiguration" in {entry["$ref"] for entry in schema["oneOf"]}
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coating_catalog = load_hull_coating_catalog(COATING_CATALOG)
    module_catalog = load_module_prototype_catalog(MODULE_CATALOG)
    hull = compile_hull(load_hull_blueprint(HULL_FIXTURE), registry)
    outfit = compile_outfit(
        load_outfit_plan(OUTFIT_FIXTURE), hull, module_catalog, coating_catalog
    )
    snapshot = build_derived_ship_snapshot(hull, outfit)
    source = load_json(SORTIE_FIXTURE)
    configuration = load_sortie_configuration(SORTIE_FIXTURE)
    assert canonical_json(configuration) == SORTIE_FIXTURE.read_text(encoding="utf-8")
    compiled = compile_sortie_configuration(snapshot, configuration)

    require_close(compiled.design_mass_kg, 2_090_350.0)
    require_close(compiled.cargo_mass_kg, 50_000.0)
    require_close(compiled.current_mass_kg, 2_140_350.0)
    require_close(compiled.design_inertia_kg_m2, 3_285_053_541.666667)
    require_close(compiled.cargo_inertia_kg_m2, 6_250_000.0)
    require_close(compiled.current_inertia_kg_m2, 3_291_303_541.666667)
    require_close(compiled.current_lift_margin_n, 9_010_336.6725)
    require_close(compiled.fuel_capacity_units, 1_000.0)
    require_close(compiled.fuel_units, 800.0)
    assert compiled.crew_present
    assert compiled.crew_safety_lock_enabled
    assert compiled.warnings == ()
    cargo = compiled.cargo_contributions[0]
    assert cargo.application_point_m == (5.0, 10.0)
    require_close(cargo.inertia_kg_m2, 6_250_000.0)

    # 燃料与人员数量改变不改变质量或惯量。
    no_fuel_source = deepcopy(source)
    no_fuel_source["fuel_units"] = 0.0
    no_fuel = compile_sortie_configuration(
        snapshot, SortieConfigurationInput.parse(no_fuel_source)
    )
    require_close(no_fuel.current_mass_kg, compiled.current_mass_kg)
    require_close(no_fuel.current_inertia_kg_m2, compiled.current_inertia_kg_m2)

    minimum_source = deepcopy(source)
    crew_item(minimum_source, "officer")["count"] = 1
    crew_item(minimum_source, "ordinary")["count"] = 3
    crew_item(minimum_source, "technical_officer")["count"] = 1
    crew_item(minimum_source, "veteran_damage_control")["count"] = 1
    minimum = compile_sortie_configuration(
        snapshot, SortieConfigurationInput.parse(minimum_source)
    )
    require_close(minimum.current_mass_kg, compiled.current_mass_kg)
    assert len(minimum.warnings) == 4

    # 遥控核心模式允许零船员，并解除乘员 G 安全锁。
    remote_source = deepcopy(source)
    remote_source["control_mode"] = "remote_core"
    remote_source["active_remote_core_instance_id"] = "remote_core"
    remote_source["crew"] = []
    remote = compile_sortie_configuration(
        snapshot, SortieConfigurationInput.parse(remote_source)
    )
    assert not remote.crew_present
    assert not remote.crew_safety_lock_enabled

    # 货物质量与惯量按实际质量线性增加。
    light_source = deepcopy(source)
    light_source["bulk_cargo"][0]["mass_kg"] = 25_000.0
    light = compile_sortie_configuration(
        snapshot, SortieConfigurationInput.parse(light_source)
    )
    require_close(light.cargo_mass_kg, 25_000.0)
    require_close(light.cargo_inertia_kg_m2, 3_125_000.0)

    reversed_source = deepcopy(source)
    reversed_source["crew"].reverse()
    reversed_configuration = SortieConfigurationInput.parse(reversed_source)
    assert reversed_configuration == configuration
    assert compile_sortie_configuration(snapshot, reversed_configuration).to_dict() == compiled.to_dict()

    mismatch = deepcopy(source)
    mismatch["outfit_plan"]["version"] = 2
    require_contract_error(
        "sortie.outfit_reference_mismatch",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(mismatch)
        ),
    )

    excess_crew = deepcopy(source)
    crew_item(excess_crew, "ordinary")["count"] = 11
    require_contract_error(
        "sortie.crew_capacity_exceeded",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(excess_crew)
        ),
    )

    missing_minimum = deepcopy(source)
    crew_item(missing_minimum, "officer")["count"] = 0
    require_contract_error(
        "value.minimum", lambda: SortieConfigurationInput.parse(missing_minimum)
    )

    crew_shortfall = deepcopy(source)
    crew_shortfall["crew"] = [
        item for item in crew_shortfall["crew"] if item["crew_type"] != "officer"
    ]
    require_contract_error(
        "sortie.minimum_crew_shortfall",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(crew_shortfall)
        ),
    )

    remote_mode_missing_id = deepcopy(source)
    remote_mode_missing_id["control_mode"] = "remote_core"
    require_contract_error(
        "sortie.remote_core_mode",
        lambda: SortieConfigurationInput.parse(remote_mode_missing_id),
    )

    remote_invalid = deepcopy(remote_source)
    remote_invalid["active_remote_core_instance_id"] = "generator"
    require_contract_error(
        "sortie.remote_core_instance_invalid",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(remote_invalid)
        ),
    )

    excess_fuel = deepcopy(source)
    excess_fuel["fuel_units"] = 1_001.0
    require_contract_error(
        "sortie.fuel_capacity_exceeded",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(excess_fuel)
        ),
    )

    missing_storage = deepcopy(source)
    missing_storage["bulk_cargo"][0]["storage_instance_id"] = "missing"
    require_contract_error(
        "sortie.storage_instance_missing",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(missing_storage)
        ),
    )

    wrong_storage = deepcopy(source)
    wrong_storage["bulk_cargo"][0]["storage_instance_id"] = "generator"
    require_contract_error(
        "sortie.storage_not_cargo_hold",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(wrong_storage)
        ),
    )

    excess_cargo = deepcopy(source)
    excess_cargo["bulk_cargo"][0]["mass_kg"] = 100_001.0
    require_contract_error(
        "sortie.cargo_capacity_exceeded",
        lambda: compile_sortie_configuration(
            snapshot, SortieConfigurationInput.parse(excess_cargo)
        ),
    )

    duplicate_crew = deepcopy(source)
    duplicate_crew["crew"].append(deepcopy(duplicate_crew["crew"][0]))
    require_contract_error(
        "sortie.crew_type_duplicate",
        lambda: SortieConfigurationInput.parse(duplicate_crew),
    )

    duplicate_cargo = deepcopy(source)
    duplicate_cargo["bulk_cargo"].append(deepcopy(duplicate_cargo["bulk_cargo"][0]))
    require_contract_error(
        "sortie.cargo_id_duplicate",
        lambda: SortieConfigurationInput.parse(duplicate_cargo),
    )

    # 放大同一货仓的契约容量后，当前载荷仍会因升力不足被拒绝。
    high_capacity_modules = deepcopy(load_json(MODULE_CATALOG))
    next(
        item for item in high_capacity_modules["modules"] if item["category"] == "cargo_hold"
    )["capability"]["bulk_cargo_capacity_kg"] = 2_000_000.0
    high_capacity_catalog = ModulePrototypeCatalog.parse(high_capacity_modules)
    high_capacity_outfit = compile_outfit(
        load_outfit_plan(OUTFIT_FIXTURE), hull, high_capacity_catalog, coating_catalog
    )
    high_capacity_snapshot = build_derived_ship_snapshot(hull, high_capacity_outfit)
    overloaded = deepcopy(source)
    overloaded["bulk_cargo"][0]["mass_kg"] = 1_100_000.0
    require_contract_error(
        "sortie.insufficient_lift",
        lambda: compile_sortie_configuration(
            high_capacity_snapshot, SortieConfigurationInput.parse(overloaded)
        ),
    )

    print(
        json.dumps(
            {
                "cargo_inertia_kg_m2": compiled.cargo_inertia_kg_m2,
                "cargo_mass_kg": compiled.cargo_mass_kg,
                "compiler_interface": compiled.to_dict()["compiler_interface"],
                "current_inertia_kg_m2": compiled.current_inertia_kg_m2,
                "current_lift_margin_n": compiled.current_lift_margin_n,
                "current_mass_kg": compiled.current_mass_kg,
                "fixture": f"{configuration.id}@{configuration.version}",
                "source_sha256": compiled.source_sha256,
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
