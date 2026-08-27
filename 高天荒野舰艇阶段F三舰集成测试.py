"""阶段 F：三条规范蓝图测试舰的端到端集成与派生关系回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from math import isclose
from pathlib import Path

from 高天荒野舰艇出航配置编译器 import (
    CompiledSortieState,
    compile_sortie_configuration,
)
from 高天荒野舰艇数据契约 import (
    HullBlueprintInput,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    ShipInstanceSnapshotInput,
    SortieConfigurationInput,
    canonical_json,
    canonical_sha256,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_ship_instance_snapshot,
    load_sortie_configuration,
    merge_module_prototype_catalogs,
)
from 高天荒野舰艇无界面船壳编译器 import CompiledHull, compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledOutfit,
    DerivedShipSnapshot,
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇运行时参数编译器 import (
    RuntimeShipParameters,
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)
from 高天荒野舰艇战术机动求解器 import (
    TacticalControlInput,
    Vec2,
    build_tactical_ship_model,
    initialize_tactical_motion_state,
    integrate_tactical_step,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
BASE_MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
COMBAT_MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "战斗系统模块目录.v1.json"
UNMANNED_MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "阶段F无人化模块目录.v1.json"

SHIP_PATHS = {
    "minimum_legal": {
        "hull": ROOT / "舰艇数据" / "船壳蓝图夹具" / "阶段F最小合法舰船壳.v1.json",
        "outfit": ROOT / "舰艇数据" / "舾装方案夹具" / "阶段F最小合法舰舾装.v1.json",
        "sortie": ROOT / "舰艇数据" / "出航配置夹具" / "阶段F最小合法舰出航.v1.json",
        "instance": ROOT / "舰艇数据" / "舰艇实例夹具" / "阶段F最小合法舰完好实例.v1.json",
    },
    "conventional_crewed": {
        "hull": ROOT / "舰艇数据" / "船壳蓝图夹具" / "阶段F常规有人战舰船壳.v1.json",
        "outfit": ROOT / "舰艇数据" / "舾装方案夹具" / "阶段F常规有人战舰舾装.v1.json",
        "sortie": ROOT / "舰艇数据" / "出航配置夹具" / "阶段F常规有人战舰出航.v1.json",
        "instance": ROOT / "舰艇数据" / "舰艇实例夹具" / "阶段F常规有人战舰完好实例.v1.json",
    },
    "unmanned_flagship": {
        "hull": ROOT / "舰艇数据" / "船壳蓝图夹具" / "阶段F完全无人旗舰船壳.v1.json",
        "outfit": ROOT / "舰艇数据" / "舾装方案夹具" / "阶段F完全无人旗舰舾装.v1.json",
        "sortie": ROOT / "舰艇数据" / "出航配置夹具" / "阶段F完全无人旗舰出航.v1.json",
        "instance": ROOT / "舰艇数据" / "舰艇实例夹具" / "阶段F完全无人旗舰完好实例.v1.json",
    },
}

ALL_COMBAT_EVENTS = (
    "ship.damage_control_required",
    "ship.fire_control_required",
    "ship.sensor_scan_required",
    "ship.weapon_fire_requested",
)


@dataclass(frozen=True)
class ShipChain:
    key: str
    module_catalog: ModulePrototypeCatalog
    hull: CompiledHull
    outfit: CompiledOutfit
    snapshot: DerivedShipSnapshot
    sortie: CompiledSortieState
    instance: ShipInstanceSnapshotInput
    runtime: RuntimeShipParameters


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def merged_catalog(key: str) -> ModulePrototypeCatalog:
    base = load_module_prototype_catalog(BASE_MODULE_CATALOG)
    if key == "minimum_legal":
        return base
    combat = load_module_prototype_catalog(COMBAT_MODULE_CATALOG)
    catalogs = [base, combat]
    if key == "unmanned_flagship":
        catalogs.append(load_module_prototype_catalog(UNMANNED_MODULE_CATALOG))
    return merge_module_prototype_catalogs(
        catalogs,
        id=f"gtw.module_catalog.fixture.stage_f_{key}_combined",
        version=1,
        name=f"阶段F·{key}·组合模块目录",
        fixture_level="contract_fixture",
    )


def build_chain(key: str, *, load_saved_instance: bool = True) -> ShipChain:
    paths = SHIP_PATHS[key]
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    catalog = merged_catalog(key)
    hull = compile_hull(load_hull_blueprint(paths["hull"]), registry)
    outfit = compile_outfit(load_outfit_plan(paths["outfit"]), hull, catalog, coatings)
    snapshot = build_derived_ship_snapshot(hull, outfit)
    sortie = compile_sortie_configuration(
        snapshot, load_sortie_configuration(paths["sortie"])
    )
    initialized = initialize_ship_instance_snapshot(snapshot, sortie)
    instance = (
        load_ship_instance_snapshot(paths["instance"])
        if load_saved_instance
        else initialized
    )
    if load_saved_instance and canonical_json(instance) != canonical_json(initialized):
        raise AssertionError(f"{key} 保存的完好实例不再等于规范输入初始化结果")
    runtime = compile_runtime_ship_parameters(snapshot, sortie, instance)
    return ShipChain(key, catalog, hull, outfit, snapshot, sortie, instance, runtime)


def turning_summary(chain: ShipChain) -> dict[str, object]:
    results = {
        item.direction: item
        for item in chain.outfit.actuator_aggregation.turning_directions
    }
    assert results["counterclockwise"].torque_capacity_n_m > 0.0
    assert results["clockwise"].torque_capacity_n_m > 0.0
    for item in results.values():
        require_close(item.net_force_body_n[0], 0.0)
        require_close(item.net_force_body_n[1], 0.0)
    return {
        direction: {
            "net_force_body_n": list(item.net_force_body_n),
            "torque_capacity_n_m": item.torque_capacity_n_m,
        }
        for direction, item in sorted(results.items())
    }


def assert_canonical_sources() -> None:
    loaders = {
        "hull": load_hull_blueprint,
        "outfit": load_outfit_plan,
        "sortie": load_sortie_configuration,
        "instance": load_ship_instance_snapshot,
    }
    for paths in SHIP_PATHS.values():
        for kind, loader in loaders.items():
            path = paths[kind]
            assert canonical_json(loader(path)) == path.read_text(encoding="utf-8")
    assert canonical_json(load_module_prototype_catalog(UNMANNED_MODULE_CATALOG)) == (
        UNMANNED_MODULE_CATALOG.read_text(encoding="utf-8")
    )


def test_minimum_legal(chain: ShipChain) -> dict[str, object]:
    assert len(chain.hull.decks) == 1
    assert dict(chain.outfit.minimum_crew) == {
        "officer": 1,
        "ordinary": 5,
        "technical_officer": 1,
        "veteran_damage_control": 1,
    }
    assert dict(chain.outfit.standard_crew) == {
        "officer": 2,
        "ordinary": 6,
        "technical_officer": 2,
        "veteran_damage_control": 2,
    }
    assert dict(chain.sortie.crew) == dict(chain.outfit.standard_crew)
    return turning_summary(chain)


def test_conventional_crewed(chain: ShipChain) -> dict[str, object]:
    assert len(chain.hull.decks) == 2
    assert len(chain.hull.decks[1].region_ids) == 2
    assert dict(chain.sortie.crew) == dict(chain.outfit.standard_crew)
    weapon = next(item for item in chain.outfit.instances if item.id == "weapon_upper_port")
    sensor = next(item for item in chain.outfit.instances if item.id == "sensor_upper_starboard")
    assert weapon.base_deck_level == sensor.base_deck_level == 1
    assert weapon.anchor_m[0] < 0.0 < sensor.anchor_m[0]

    active = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        chain.instance,
        active_automatic_events=ALL_COMBAT_EVENTS,
    )
    assert active.power.category_order == (
        "damage_control",
        "weapons_and_active_defense",
        "fire_control",
        "sensors",
    )
    assert all(active.module(item).active_available for item in (
        "damage_control", "weapon_upper_port", "fire_control", "sensor_upper_starboard"
    ))

    damaged_source = chain.instance.to_dict()
    next(
        item for item in damaged_source["module_states"]
        if item["instance_id"] == "weapon_upper_port"
    )["current_durability_points"] = 50.0
    damaged = ShipInstanceSnapshotInput.parse(damaged_source)
    damaged_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        damaged,
        active_automatic_events=("ship.weapon_fire_requested",),
    )
    multipliers = dict(
        damaged_runtime.module("weapon_upper_port").damage_function_multipliers
    )
    require_close(multipliers["weapon.aim"], 0.5)
    require_close(multipliers["weapon.fire"], 1.0)
    require_close(multipliers["weapon.reload"], 0.5)
    return {
        "combat_power_order": list(active.power.category_order),
        "turning": turning_summary(chain),
        "weapon_half_durability_outputs": multipliers,
    }


def test_unmanned_flagship(chain: ShipChain) -> dict[str, object]:
    assert dict(chain.outfit.minimum_crew) == {}
    assert dict(chain.outfit.standard_crew) == {}
    assert chain.sortie.crew == ()
    assert not chain.runtime.crew_safety_lock_enabled
    assert chain.runtime.remote_control_available
    assert all(
        item.prototype.automation.level == "full"
        for item in chain.outfit.instances
    )

    active = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        chain.instance,
        active_automatic_events=ALL_COMBAT_EVENTS,
    )
    for instance_id, functions in {
        "damage_control": ("damage_control.firefighting",),
        "fire_control": ("fire_control.guidance", "fire_control.solution"),
        "sensor": ("sensor.search", "sensor.track"),
        "weapon": ("weapon.aim", "weapon.fire", "weapon.reload"),
    }.items():
        result = active.module(instance_id)
        assert result.active_available
        assert result.crew_allocations == ()
        assert all(result.function_efficiency(function) == 1.0 for function in functions)

    # 把无人损管替换为普通手动损管后，零船员舰即便收到损管事件也不能灭火。
    manual_source = deepcopy(load_json(SHIP_PATHS["unmanned_flagship"]["outfit"]))
    next(
        item for item in manual_source["modules"] if item["id"] == "damage_control"
    )["prototype"] = {"id": "gtw.module.fixture.damage_control", "version": 1}
    manual_outfit = compile_outfit(
        OutfitPlanInput.parse(manual_source),
        chain.hull,
        chain.module_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    manual_snapshot = build_derived_ship_snapshot(chain.hull, manual_outfit)
    manual_sortie = compile_sortie_configuration(
        manual_snapshot,
        load_sortie_configuration(SHIP_PATHS["unmanned_flagship"]["sortie"]),
    )
    manual_instance = initialize_ship_instance_snapshot(manual_snapshot, manual_sortie)
    manual_runtime = compile_runtime_ship_parameters(
        manual_snapshot,
        manual_sortie,
        manual_instance,
        active_automatic_events=("ship.damage_control_required",),
    )
    require_close(
        manual_runtime.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        ),
        0.0,
    )

    model = build_tactical_ship_model(chain.runtime, chain.snapshot)
    next_state, diagnostics = integrate_tactical_step(
        model,
        initialize_tactical_motion_state(model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0), overg=True),
    )
    assert diagnostics.command_scale == 1.0
    assert diagnostics.structure_ratio > 1.0
    assert diagnostics.crew_g > 12.0
    assert diagnostics.hull_integrity_damage > 0.0
    assert next_state.hull_integrity_fraction < 1.0
    return {
        "crew_g": diagnostics.crew_g,
        "hull_integrity_damage_per_step": diagnostics.hull_integrity_damage,
        "manual_damage_control_output_without_crew": 0.0,
        "overg_command_scale": diagnostics.command_scale,
        "structure_ratio": diagnostics.structure_ratio,
        "turning": turning_summary(chain),
    }


def acceptance_probes(chains: dict[str, ShipChain]) -> dict[str, object]:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    minimum = chains["minimum_legal"]

    moved_source = deepcopy(load_json(SHIP_PATHS["minimum_legal"]["outfit"]))
    next(item for item in moved_source["modules"] if item["id"] == "main_engine")[
        "placement"
    ]["anchor_half_cell"] = [0, -10]
    moved = compile_outfit(
        OutfitPlanInput.parse(moved_source), minimum.hull, minimum.module_catalog, coatings
    )
    require_close(moved.design_mass_kg, minimum.outfit.design_mass_kg)
    assert moved.design_inertia_kg_m2 != minimum.outfit.design_inertia_kg_m2

    mass_source = deepcopy(load_json(BASE_MODULE_CATALOG))
    next(item for item in mass_source["modules"] if item["category"] == "main_engine")[
        "mass_kg"
    ] += 500.0
    heavy_catalog = ModulePrototypeCatalog.parse(mass_source)
    heavy = compile_outfit(
        minimum.outfit.normalized_plan, minimum.hull, heavy_catalog, coatings
    )
    require_close(heavy.design_mass_kg - minimum.outfit.design_mass_kg, 500.0)
    assert heavy.design_inertia_kg_m2 > minimum.outfit.design_inertia_kg_m2

    material_source = deepcopy(load_json(SHIP_PATHS["minimum_legal"]["hull"]))
    material_source["decks"][0]["structure_material"] = {
        "id": "gtw.material.structure.aluminum_alloy",
        "version": 1,
    }
    light_hull = compile_hull(HullBlueprintInput.parse(material_source), registry)
    assert light_hull.hull_mass_kg < minimum.hull.hull_mass_kg
    assert light_hull.safe_longitudinal_g != minimum.hull.safe_longitudinal_g

    geometry_source = deepcopy(load_json(SHIP_PATHS["minimum_legal"]["hull"]))
    for vertex in geometry_source["decks"][0]["regions"][0]["vertices_m"]:
        vertex[1] = -45.0 if vertex[1] < 0.0 else 45.0
    long_hull = compile_hull(HullBlueprintInput.parse(geometry_source), registry)
    assert long_hull.hull_mass_kg > minimum.hull.hull_mass_kg
    assert long_hull.hull_inertia_kg_m2 > minimum.hull.hull_inertia_kg_m2

    conventional = chains["conventional_crewed"]
    empty_source = deepcopy(load_json(SHIP_PATHS["conventional_crewed"]["sortie"]))
    empty_source["bulk_cargo"] = []
    empty_sortie = compile_sortie_configuration(
        conventional.snapshot, SortieConfigurationInput.parse(empty_source)
    )
    empty_instance = initialize_ship_instance_snapshot(conventional.snapshot, empty_sortie)
    empty_runtime = compile_runtime_ship_parameters(
        conventional.snapshot, empty_sortie, empty_instance
    )
    require_close(conventional.runtime.current_mass_kg - empty_runtime.current_mass_kg, 25000.0)
    assert conventional.runtime.current_inertia_kg_m2 > empty_runtime.current_inertia_kg_m2
    assert conventional.runtime.current_lift_margin_n < empty_runtime.current_lift_margin_n

    return {
        "cargo_mass_delta_kg": conventional.runtime.current_mass_kg - empty_runtime.current_mass_kg,
        "deck_geometry_changes_hull_inertia": True,
        "engine_position_changes_design_inertia": True,
        "module_mass_changes_design_mass_and_inertia": True,
        "structure_material_changes_mass_and_safe_g": True,
    }


def ship_report(chain: ShipChain) -> dict[str, object]:
    return {
        "design_inertia_kg_m2": chain.outfit.design_inertia_kg_m2,
        "design_mass_kg": chain.outfit.design_mass_kg,
        "hull_source_sha256": chain.hull.source_sha256,
        "instance_source_sha256": canonical_sha256(chain.instance),
        "lift_margin_n": chain.runtime.current_lift_margin_n,
        "runtime_inertia_kg_m2": chain.runtime.current_inertia_kg_m2,
        "runtime_mass_kg": chain.runtime.current_mass_kg,
        "safe_longitudinal_g": chain.runtime.safe_longitudinal_mps2 / 9.80665,
        "snapshot_source_sha256": chain.snapshot.source_sha256,
    }


def build_result() -> dict[str, object]:
    assert_canonical_sources()
    chains = {key: build_chain(key) for key in SHIP_PATHS}
    details = {
        "minimum_legal": test_minimum_legal(chains["minimum_legal"]),
        "conventional_crewed": test_conventional_crewed(chains["conventional_crewed"]),
        "unmanned_flagship": test_unmanned_flagship(chains["unmanned_flagship"]),
    }
    return {
        "acceptance_probes": acceptance_probes(chains),
        "interface": "gaotian.stage-f-three-canonical-ships/v1",
        "ships": {key: ship_report(chain) for key, chain in chains.items()},
        "status": "PASS",
        "test_details": details,
    }


def main() -> None:
    print(json.dumps(build_result(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
