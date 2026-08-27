"""阶段 E5：最小武器、弹药库、火控与传感器契约及运行时回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    COMBAT_SYSTEM_MODULE_CONTRACT_ID,
    ContractError,
    ModulePrototype,
    ModulePrototypeCatalog,
    RuntimePowerPolicyInput,
    canonical_json,
    canonical_sha256,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_sortie_configuration,
    merge_module_prototype_catalogs,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇运行时参数编译器 import (
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
BASE_MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
COMBAT_MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "战斗系统模块目录.v1.json"
HULL_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
OUTFIT_FIXTURE = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20战斗系统舾装.v1.json"
SORTIE_FIXTURE = ROOT / "舰艇数据" / "出航配置夹具" / "标准155x20战斗系统出航.v1.json"
SCHEMA_FILE = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"


def require_close(actual: float, expected: float) -> None:
    if not isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-9):
        raise AssertionError(f"{actual!r} != {expected!r}")


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def module_source(source: dict[str, object], category: str) -> dict[str, object]:
    return next(item for item in source["modules"] if item["category"] == category)


def combined_catalog() -> ModulePrototypeCatalog:
    base = load_module_prototype_catalog(BASE_MODULE_CATALOG)
    combat = load_module_prototype_catalog(COMBAT_MODULE_CATALOG)
    return merge_module_prototype_catalogs(
        (base, combat),
        id="gtw.module_catalog.fixture.e5_combined",
        version=1,
        name="阶段E5组合模块契约目录",
        fixture_level="contract_fixture",
    )


def build_chain():
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    hull = compile_hull(load_hull_blueprint(HULL_FIXTURE), registry)
    catalog = combined_catalog()
    outfit = compile_outfit(load_outfit_plan(OUTFIT_FIXTURE), hull, catalog, coatings)
    snapshot = build_derived_ship_snapshot(hull, outfit)
    sortie = compile_sortie_configuration(
        snapshot, load_sortie_configuration(SORTIE_FIXTURE)
    )
    instance = initialize_ship_instance_snapshot(snapshot, sortie)
    return catalog, outfit, snapshot, sortie, instance


def replace_module_durability(instance, instance_id: str, durability: float):
    return replace(
        instance,
        module_states=tuple(
            replace(item, current_durability_points=durability)
            if item.instance_id == instance_id
            else item
            for item in instance.module_states
        ),
    )


def main() -> None:
    schema = load_json(SCHEMA_FILE)
    module_category_enum = schema["$defs"]["modulePrototype"]["properties"][
        "category"
    ]["enum"]
    assert {"weapon", "ammunition_magazine", "fire_control", "sensor"} <= set(
        module_category_enum
    )

    source = load_json(COMBAT_MODULE_CATALOG)
    catalog = load_module_prototype_catalog(COMBAT_MODULE_CATALOG)
    assert len(catalog.modules) == 4
    assert canonical_json(catalog) == COMBAT_MODULE_CATALOG.read_text(encoding="utf-8")
    assert {item.category for item in catalog.modules} == {
        "ammunition_magazine",
        "fire_control",
        "sensor",
        "weapon",
    }

    by_category = {item.category: item for item in catalog.modules}
    weapon = by_category["weapon"]
    magazine = by_category["ammunition_magazine"]
    fire_control = by_category["fire_control"]
    sensor = by_category["sensor"]
    weapon_capability = weapon.capability.to_dict()
    magazine_capability = magazine.capability.to_dict()
    assert weapon_capability["compatible_munition_ids"] == magazine_capability[
        "compatible_munition_ids"
    ]
    assert weapon_capability["ready_round_capacity"] == 1
    assert weapon_capability["fire_control_requirement"] == "solution"
    assert magazine_capability["inventory_scope"] == "ship_shared"
    assert fire_control.power.consumer_category == "fire_control"
    assert sensor.power.consumer_category == "sensors"
    assert weapon.power.consumer_category == "weapons_and_active_defense"
    assert weapon.automation.automated_functions == ("weapon.aim", "weapon.fire")
    assert "weapon.reload" not in weapon.automation.automated_functions

    manual_weapon = deepcopy(module_source(source, "weapon"))
    manual_weapon["id"] = "gtw.module.fixture.manual_weapon_probe"
    manual_weapon["automation"]["level"] = "manual"
    manual_weapon["automation"]["automated_functions"] = []
    manual_weapon["power"] = {
        "active_load_kw": 0.0,
        "consumer_category": None,
        "generation_kw": 0.0,
        "standby_load_kw": 0.0,
    }
    parsed_manual_weapon = ModulePrototype.parse(manual_weapon, "$.manual_weapon")
    assert parsed_manual_weapon.automation.level == "manual"
    assert parsed_manual_weapon.power.consumer_category is None

    invalid_range = deepcopy(module_source(source, "weapon"))
    invalid_range["capability"]["maximum_range_m"] = 0.0
    require_contract_error(
        "module.weapon_range",
        lambda: ModulePrototype.parse(invalid_range, "$.invalid_range"),
    )

    no_munitions = deepcopy(module_source(source, "weapon"))
    no_munitions["capability"]["compatible_munition_ids"] = []
    require_contract_error(
        "module.compatible_munitions",
        lambda: ModulePrototype.parse(no_munitions, "$.no_munitions"),
    )

    local_magazine = deepcopy(module_source(source, "ammunition_magazine"))
    local_magazine["capability"]["inventory_scope"] = "local"
    require_contract_error(
        "module.ammunition_inventory_scope",
        lambda: ModulePrototype.parse(local_magazine, "$.local_magazine"),
    )

    invalid_fire_control = deepcopy(module_source(source, "fire_control"))
    invalid_fire_control["capability"]["supported_requirements"] = ["none"]
    require_contract_error(
        "module.fire_control_supported_requirements",
        lambda: ModulePrototype.parse(invalid_fire_control, "$.invalid_fire_control"),
    )

    invalid_sensor = deepcopy(module_source(source, "sensor"))
    invalid_sensor["capability"]["supported_modes"] = ["magic"]
    require_contract_error(
        "module.sensor_modes",
        lambda: ModulePrototype.parse(invalid_sensor, "$.invalid_sensor"),
    )

    wrong_power = deepcopy(module_source(source, "weapon"))
    wrong_power["power"]["consumer_category"] = "sensors"
    require_contract_error(
        "module.combat_power_category",
        lambda: ModulePrototype.parse(wrong_power, "$.wrong_power"),
    )

    internal_only_weapon = deepcopy(module_source(source, "weapon"))
    internal_only_weapon["installation"]["top_footprint_half_cells"] = []
    internal_only_weapon["installation"]["deck_rule"] = "any"
    require_contract_error(
        "module.weapon_geometry",
        lambda: ModulePrototype.parse(internal_only_weapon, "$.internal_only_weapon"),
    )

    internal_sensor = deepcopy(module_source(source, "sensor"))
    internal_sensor["installation"]["internal_deck_span"] = 1
    internal_sensor["installation"]["internal_footprint_half_cells"] = [[0, 0]]
    internal_sensor["installation"]["top_footprint_half_cells"] = []
    internal_sensor["installation"]["deck_rule"] = "any"
    require_contract_error(
        "module.sensor_geometry",
        lambda: ModulePrototype.parse(internal_sensor, "$.internal_sensor"),
    )

    combined, outfit, snapshot, sortie, instance = build_chain()
    assert len(combined.modules) == 13
    assert canonical_json(outfit.normalized_plan) == OUTFIT_FIXTURE.read_text(
        encoding="utf-8"
    )
    compiled = {item.id: item for item in outfit.instances}
    assert compiled["weapon"].internal_cells
    assert compiled["weapon"].top_cells
    assert not compiled["sensor"].internal_cells
    assert compiled["sensor"].top_cells
    assert compiled["ammunition_magazine"].internal_cells
    assert compiled["fire_control"].internal_cells

    baseline = compile_runtime_ship_parameters(snapshot, sortie, instance)
    assert baseline.module("ammunition_magazine").active_available
    assert not baseline.module("weapon").active_available
    assert not baseline.module("fire_control").active_available
    assert not baseline.module("sensor").active_available

    combat_events = (
        "ship.fire_control_required",
        "ship.sensor_scan_required",
        "ship.weapon_fire_requested",
    )
    active = compile_runtime_ship_parameters(
        snapshot, sortie, instance, active_automatic_events=combat_events
    )
    for instance_id in ("weapon", "fire_control", "sensor"):
        module = active.module(instance_id)
        assert module.automatically_activated
        assert module.active_available
        assert module.powered
    require_close(active.module("weapon").function_efficiency("weapon.aim"), 1.0)
    require_close(active.module("weapon").function_efficiency("weapon.fire"), 1.0)
    assert 0.0 < active.module("weapon").function_efficiency("weapon.reload") < 1.0
    require_close(
        active.module("fire_control").function_efficiency("fire_control.solution"),
        1.0,
    )
    assert (
        0.0
        < active.module("fire_control").function_efficiency(
            "fire_control.guidance"
        )
        < 1.0
    )
    require_close(active.module("sensor").function_efficiency("sensor.search"), 1.0)
    assert 0.0 < active.module("sensor").function_efficiency("sensor.track") < 1.0

    damaged_instance = replace_module_durability(instance, "weapon", 50.0)
    damaged = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        damaged_instance,
        active_automatic_events=combat_events,
    )
    require_close(damaged.module("weapon").function_efficiency("weapon.aim"), 0.5)
    require_close(damaged.module("weapon").function_efficiency("weapon.fire"), 1.0)
    assert (
        damaged.module("weapon").function_efficiency("weapon.reload")
        < active.module("weapon").function_efficiency("weapon.reload")
    )

    weapon_power_off = replace(
        instance,
        power_policy=RuntimePowerPolicyInput(
            "strict_categories",
            instance.power_policy.category_order,
            ("weapons_and_active_defense",),
        ),
    )
    power_disabled = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        weapon_power_off,
        active_automatic_events=combat_events,
    )
    assert not power_disabled.module("weapon").powered
    require_close(
        power_disabled.module("weapon").function_efficiency("weapon.fire"), 0.0
    )
    assert power_disabled.module("fire_control").active_available
    assert power_disabled.module("sensor").active_available

    destroyed_magazine = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        replace_module_durability(instance, "ammunition_magazine", 0.0),
        active_automatic_events=combat_events,
    )
    require_close(
        destroyed_magazine.module("ammunition_magazine").function_efficiency(
            "ammunition.inventory"
        ),
        0.0,
    )
    require_close(
        destroyed_magazine.module("ammunition_magazine").function_efficiency(
            "ammunition.feed"
        ),
        0.0,
    )

    print(
        json.dumps(
            {
                "active_combat_load_kw": active.power.requested_load_kw,
                "combat_contract": COMBAT_SYSTEM_MODULE_CONTRACT_ID,
                "combat_catalog": f"{catalog.id}@{catalog.version}",
                "combat_catalog_sha256": canonical_sha256(catalog),
                "combined_catalog_sha256": canonical_sha256(combined),
                "compiled_combat_instance_count": 4,
                "outfit": f"{outfit.normalized_plan.id}@{outfit.normalized_plan.version}",
                "outfit_source_sha256": outfit.source_sha256,
                "status": "PASS",
                "weapon_reload_efficiency": active.module("weapon").function_efficiency(
                    "weapon.reload"
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
