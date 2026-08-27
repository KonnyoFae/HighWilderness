"""OutfitPlan 与无界面舾装编译器的规范输入、占格和基础派生回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ModulePrototypeCatalog,
    OutfitPlanInput,
    canonical_json,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
)
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
MULTI_HULL_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20双层分离上层船壳.v1.json"
OUTFIT_FIXTURE = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20最小舾装.v1.json"
SCHEMA_FILE = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def module_by_category(source: dict[str, object], category: str) -> dict[str, object]:
    return next(item for item in source["modules"] if item["category"] == category)


def instance_by_id(source: dict[str, object], instance_id: str) -> dict[str, object]:
    return next(item for item in source["modules"] if item["id"] == instance_id)


def main() -> None:
    schema = load_json(SCHEMA_FILE)
    assert "#/$defs/outfitPlan" in {entry["$ref"] for entry in schema["oneOf"]}
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coating_catalog = load_hull_coating_catalog(COATING_CATALOG)
    module_catalog = load_module_prototype_catalog(MODULE_CATALOG)
    hull = compile_hull(load_hull_blueprint(HULL_FIXTURE), registry)
    multi_hull = compile_hull(load_hull_blueprint(MULTI_HULL_FIXTURE), registry)
    plan_source = load_json(OUTFIT_FIXTURE)
    module_source = load_json(MODULE_CATALOG)
    plan = load_outfit_plan(OUTFIT_FIXTURE)
    assert canonical_json(plan) == OUTFIT_FIXTURE.read_text(encoding="utf-8")
    compiled = compile_outfit(plan, hull, module_catalog, coating_catalog)

    assert compiled.to_dict()["compiler_interface"] == "gaotian.outfit-compiler/v1alpha1"
    assert len(compiled.instances) == 10
    require_close(compiled.module_mass_kg, 10_100.0)
    require_close(compiled.design_mass_kg, 2_090_350.0)
    require_close(compiled.module_inertia_kg_m2, 5_879_583.333333333)
    require_close(compiled.design_inertia_kg_m2, 3_285_053_541.666667)
    require_close(compiled.lift_force_n, 30_000_000.0)
    assert compiled.lift_margin_n > 0.0
    require_close(compiled.generation_kw, 1_000.0)
    assert dict(compiled.active_load_kw_by_category) == {
        "damage_control": 50.0,
        "weapons_and_active_defense": 0.0,
        "fire_control": 0.0,
        "sensors": 0.0,
    }
    assert dict(compiled.minimum_crew) == {
        "officer": 1,
        "ordinary": 3,
        "technical_officer": 1,
        "veteran_damage_control": 1,
    }
    assert dict(compiled.standard_crew) == {
        "officer": 2,
        "ordinary": 4,
        "technical_officer": 2,
        "veteran_damage_control": 2,
    }
    assert dict(compiled.crew_capacity) == {
        "officer": 2,
        "ordinary": 10,
        "technical_officer": 2,
        "veteran_damage_control": 2,
    }
    remote = next(instance for instance in compiled.instances if instance.id == "remote_core")
    assert remote.placement_kind == "hosted"
    assert remote.host_instance_id == "cic"
    assert remote.anchor_m == (0.0, 0.0)
    assert remote.internal_cells == ()
    assert len(compiled.actuators) == 3
    main_engine = next(item for item in compiled.actuators if item.instance_id == "main_engine")
    assert main_engine.direction_body == (0.0, 1.0)
    require_close(main_engine.torque_about_cic_n_m, 0.0)
    thrusters = [item for item in compiled.actuators if item.category == "maneuver_thruster"]
    assert len(thrusters) == 2
    for thruster in thrusters:
        require_close(thruster.torque_about_cic_n_m, 275_000.0)
    forward = compiled.actuator_aggregation.main("forward")
    require_close(forward.centerline_capacity_n, 100_000.0)
    require_close(forward.total_used_thrust_n, 100_000.0)
    assert forward.net_force_body_n == (0.0, 100_000.0)
    require_close(forward.residual_torque_about_cic_n_m, 0.0)
    counterclockwise = compiled.actuator_aggregation.turning("counterclockwise")
    require_close(counterclockwise.torque_capacity_n_m, 550_000.0)
    assert counterclockwise.net_force_body_n == (0.0, 0.0)
    require_close(
        compiled.actuator_aggregation.turning("clockwise").torque_capacity_n_m,
        0.0,
    )
    assert [warning.code for warning in compiled.warnings] == [
        "outfit.external_rcs_unresolved",
        "outfit.no_clockwise_turning_torque",
    ]
    snapshot = build_derived_ship_snapshot(hull, compiled)
    snapshot_dict = snapshot.to_dict()
    assert snapshot_dict["kind"] == "DerivedShipSnapshot"
    assert snapshot_dict["design"]["mass_kg"] == compiled.design_mass_kg
    assert snapshot_dict["sources"]["outfit_plan"]["source_sha256"] == compiled.source_sha256
    assert snapshot.source_sha256 == build_derived_ship_snapshot(hull, compiled).source_sha256

    # 数组顺序不构成语义；规范化后实例、指纹和派生结果保持一致。
    reversed_plan = deepcopy(plan_source)
    reversed_plan["modules"].reverse()
    reversed_compiled = compile_outfit(
        OutfitPlanInput.parse(reversed_plan), hull, module_catalog, coating_catalog
    )
    assert reversed_compiled.source_sha256 == compiled.source_sha256
    assert reversed_compiled.to_dict() == compiled.to_dict()

    # 远离 CIC 的同一模块必须提高惯量，证明聚合值来自实例位置。
    moved_plan = deepcopy(plan_source)
    instance_by_id(moved_plan, "generator")["placement"]["anchor_half_cell"] = [-2, 10]
    moved = compile_outfit(OutfitPlanInput.parse(moved_plan), hull, module_catalog, coating_catalog)
    assert moved.module_inertia_kg_m2 > compiled.module_inertia_kg_m2
    assert moved.source_sha256 != compiled.source_sha256

    # 顶挂与内部格是不同占用层，同一复合模块可以同时占用二者。
    compound_modules = deepcopy(module_source)
    quarters_module = module_by_category(compound_modules, "crew_quarters")
    quarters_module["installation"]["top_footprint_half_cells"] = [[0, 0]]
    compound = compile_outfit(
        plan,
        hull,
        ModulePrototypeCatalog.parse(compound_modules),
        coating_catalog,
    )
    compound_quarters = next(item for item in compound.instances if item.id == "crew_quarters")
    assert compound_quarters.internal_cells == ((0, -1, 0),)
    assert compound_quarters.top_cells == ((0, -1, 0),)

    # 大型内部模块跨层时在每层复用同一二维轮廓。
    cross_deck_modules = deepcopy(module_source)
    module_by_category(cross_deck_modules, "generator")["installation"]["internal_deck_span"] = 2
    multi_plan = deepcopy(plan_source)
    multi_plan["hull_blueprint"] = {
        "id": "gtw.hull.fixture.standard_155x20_two_deck_split_upper",
        "version": 1,
    }
    cross_deck = compile_outfit(
        OutfitPlanInput.parse(multi_plan),
        multi_hull,
        ModulePrototypeCatalog.parse(cross_deck_modules),
        coating_catalog,
    )
    cross_generator = next(item for item in cross_deck.instances if item.id == "generator")
    assert cross_generator.internal_cells == ((0, -1, 2), (1, -1, 2))

    hull_mismatch = deepcopy(plan_source)
    hull_mismatch["hull_blueprint"]["version"] = 2
    require_contract_error(
        "outfit.hull_reference_mismatch",
        lambda: compile_outfit(
            OutfitPlanInput.parse(hull_mismatch), hull, module_catalog, coating_catalog
        ),
    )

    missing_module = deepcopy(plan_source)
    instance_by_id(missing_module, "generator")["prototype"]["id"] = "gtw.module.fixture.missing"
    require_contract_error(
        "resource.reference_missing",
        lambda: compile_outfit(
            OutfitPlanInput.parse(missing_module), hull, module_catalog, coating_catalog
        ),
    )

    duplicate_id = deepcopy(plan_source)
    duplicate_id["modules"][1]["id"] = duplicate_id["modules"][0]["id"]
    require_contract_error(
        "outfit.instance_id_duplicate", lambda: OutfitPlanInput.parse(duplicate_id)
    )

    invalid_rotation = deepcopy(plan_source)
    instance_by_id(invalid_rotation, "generator")["placement"]["rotation_deg"] = 45
    require_contract_error(
        "outfit.rotation_invalid", lambda: OutfitPlanInput.parse(invalid_rotation)
    )

    overlap = deepcopy(plan_source)
    instance_by_id(overlap, "generator")["placement"]["anchor_half_cell"] = [0, 4]
    require_contract_error(
        "outfit.internal_overlap",
        lambda: compile_outfit(OutfitPlanInput.parse(overlap), hull, module_catalog, coating_catalog),
    )

    side_overlap = deepcopy(plan_source)
    right = instance_by_id(side_overlap, "thruster_right")["placement"]
    right["edge_index"] = 6
    right["start_slot_index"] = 15
    require_contract_error(
        "outfit.side_overlap",
        lambda: compile_outfit(
            OutfitPlanInput.parse(side_overlap), hull, module_catalog, coating_catalog
        ),
    )

    inward_exhaust = deepcopy(plan_source)
    instance_by_id(inward_exhaust, "thruster_right")["placement"]["rotation_deg"] = 0
    require_contract_error(
        "outfit.side_clearance_hull_conflict",
        lambda: compile_outfit(
            OutfitPlanInput.parse(inward_exhaust), hull, module_catalog, coating_catalog
        ),
    )

    top_modules = deepcopy(module_source)
    top_quarters = module_by_category(top_modules, "crew_quarters")
    top_quarters["installation"]["internal_footprint_half_cells"] = []
    top_quarters["installation"]["internal_deck_span"] = 0
    top_quarters["installation"]["top_footprint_half_cells"] = [[0, 0]]
    top_quarters["installation"]["deck_rule"] = "local_exposed_top"
    covered_top_plan = deepcopy(multi_plan)
    instance_by_id(covered_top_plan, "crew_quarters")["placement"]["anchor_half_cell"] = [-2, 4]
    require_contract_error(
        "outfit.top_cell_invalid",
        lambda: compile_outfit(
            OutfitPlanInput.parse(covered_top_plan),
            multi_hull,
            ModulePrototypeCatalog.parse(top_modules),
            coating_catalog,
        ),
    )

    wrong_host = deepcopy(plan_source)
    instance_by_id(wrong_host, "remote_core")["placement"]["host_instance_id"] = "generator"
    require_contract_error(
        "outfit.host_slot_missing",
        lambda: compile_outfit(
            OutfitPlanInput.parse(wrong_host), hull, module_catalog, coating_catalog
        ),
    )

    occupied_host = deepcopy(plan_source)
    second_remote = deepcopy(instance_by_id(occupied_host, "remote_core"))
    second_remote["id"] = "remote_core.second"
    occupied_host["modules"].append(second_remote)
    require_contract_error(
        "outfit.host_slot_occupied",
        lambda: compile_outfit(
            OutfitPlanInput.parse(occupied_host), hull, module_catalog, coating_catalog
        ),
    )

    moved_cic = deepcopy(plan_source)
    instance_by_id(moved_cic, "cic")["placement"]["anchor_half_cell"] = [0, 2]
    require_contract_error(
        "outfit.cic_origin",
        lambda: compile_outfit(
            OutfitPlanInput.parse(moved_cic), hull, module_catalog, coating_catalog
        ),
    )

    low_lift_modules = deepcopy(module_source)
    module_by_category(low_lift_modules, "lift_fuel_tank")["capability"]["lift_force_n"] = 1.0
    require_contract_error(
        "outfit.insufficient_lift",
        lambda: compile_outfit(
            plan, hull, ModulePrototypeCatalog.parse(low_lift_modules), coating_catalog
        ),
    )

    stealth_plan = deepcopy(plan_source)
    stealth_plan["hull_coating"]["id"] = "gtw.coating.hull.stealth"
    require_contract_error(
        "coating.not_runtime_usable",
        lambda: compile_outfit(
            OutfitPlanInput.parse(stealth_plan), hull, module_catalog, coating_catalog
        ),
    )

    result = {
        "actuators": [actuator.to_dict() for actuator in compiled.actuators],
        "actuator_policy": compiled.actuator_aggregation.policy_id,
        "compiler_interface": compiled.to_dict()["compiler_interface"],
        "design_inertia_kg_m2": compiled.design_inertia_kg_m2,
        "design_mass_kg": compiled.design_mass_kg,
        "fixture": f"{plan.id}@{plan.version}",
        "instance_count": len(compiled.instances),
        "lift_margin_n": compiled.lift_margin_n,
        "module_inertia_kg_m2": compiled.module_inertia_kg_m2,
        "source_sha256": compiled.source_sha256,
        "snapshot_interface": snapshot_dict["compiler_interfaces"]["derived_snapshot"],
        "snapshot_sha256": snapshot.source_sha256,
        "status": "PASS",
        "warnings": [warning.code for warning in compiled.warnings],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
