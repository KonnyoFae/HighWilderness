"""阶段 G：从规范蓝图到连续战术状态演化的端到端集成回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isclose

from 高天荒野舰艇阶段F三舰集成测试 import (
    ALL_COMBAT_EVENTS,
    BASE_MODULE_CATALOG,
    COATING_CATALOG,
    COMBAT_MODULE_CATALOG,
    SHIP_PATHS,
    build_chain,
)
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ModulePrototypeCatalog,
    OutfitPlanInput,
    ShipInstanceSnapshotInput,
    canonical_sha256,
    load_hull_coating_catalog,
    load_json,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_sortie_configuration,
    merge_module_prototype_catalogs,
)
from 高天荒野舰艇无界面舾装编译器 import (
    build_derived_ship_snapshot,
    compile_outfit,
)
from 高天荒野舰艇运行时参数编译器 import (
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
)
from 高天荒野舰艇战术机动求解器 import (
    PROTOTYPE_TACTICAL_ENVIRONMENT,
    TacticalControlInput,
    Vec2,
    build_tactical_ship_model,
    calculate_tactical_drag,
    commit_tactical_state_to_instance,
    initialize_tactical_motion_state,
    integrate_tactical_step,
    query_tactical_rcs_to_observer,
    request_layer_transition,
)


def require_close(actual: float, expected: float, tolerance: float = 1.0e-8) -> None:
    if not isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def deterministic_blueprint_probe() -> dict[str, object]:
    first = build_chain("conventional_crewed")
    second = build_chain("conventional_crewed")
    assert first.hull.to_dict() == second.hull.to_dict()
    assert first.outfit.to_dict() == second.outfit.to_dict()
    assert first.snapshot.to_dict() == second.snapshot.to_dict()
    assert first.sortie.to_dict() == second.sortie.to_dict()
    assert first.runtime.to_dict() == second.runtime.to_dict()
    return {
        "derived_snapshot_sha256": first.snapshot.source_sha256,
        "hull_compilation_sha256": canonical_sha256(first.hull),
        "outfit_compilation_sha256": canonical_sha256(first.outfit),
        "runtime_parameters_sha256": canonical_sha256(first.runtime),
    }


def inertia_and_full_load_probe() -> dict[str, object]:
    chain = build_chain("conventional_crewed")
    coatings = load_hull_coating_catalog(COATING_CATALOG)

    # 同一远端火控模块向 CIC 移近后，质量不变但设计惯量必须下降。
    moved_source = deepcopy(load_json(SHIP_PATHS["conventional_crewed"]["outfit"]))
    next(item for item in moved_source["modules"] if item["id"] == "fire_control")[
        "placement"
    ]["anchor_half_cell"] = [-2, 8]
    moved = compile_outfit(
        OutfitPlanInput.parse(moved_source), chain.hull, chain.module_catalog, coatings
    )
    require_close(moved.design_mass_kg, chain.outfit.design_mass_kg)
    expected_remote_module_delta = 750.0 * ((5.0**2 + 40.0**2) - (5.0**2 + 20.0**2))
    require_close(
        chain.outfit.design_inertia_kg_m2 - moved.design_inertia_kg_m2,
        expected_remote_module_delta,
    )

    cargo = chain.sortie.cargo_contributions[0]
    require_close(cargo.mass_kg, 25_000.0)
    require_close(cargo.inertia_kg_m2, 25_000.0 * (5.0**2 + 10.0**2))
    unloaded_instance = replace(
        chain.instance,
        operational_state=replace(chain.instance.operational_state, bulk_cargo=()),
    )
    unloaded = compile_runtime_ship_parameters(
        chain.snapshot, chain.sortie, unloaded_instance
    )
    require_close(chain.runtime.current_mass_kg - unloaded.current_mass_kg, 25_000.0)
    require_close(
        chain.runtime.current_inertia_kg_m2 - unloaded.current_inertia_kg_m2,
        cargo.inertia_kg_m2,
    )
    assert chain.runtime.current_lift_margin_n < unloaded.current_lift_margin_n

    loaded_model = build_tactical_ship_model(chain.runtime, chain.snapshot)
    unloaded_model = build_tactical_ship_model(unloaded, chain.snapshot)
    loaded_next, _ = integrate_tactical_step(
        loaded_model,
        initialize_tactical_motion_state(loaded_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    unloaded_next, _ = integrate_tactical_step(
        unloaded_model,
        initialize_tactical_motion_state(unloaded_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    assert loaded_next.velocity_world_mps.y < unloaded_next.velocity_world_mps.y
    return {
        "cargo_inertia_kg_m2": cargo.inertia_kg_m2,
        "cargo_mass_kg": cargo.mass_kg,
        "loaded_first_step_velocity_mps": loaded_next.velocity_world_mps.y,
        "remote_module_inertia_delta_kg_m2": expected_remote_module_delta,
        "unloaded_first_step_velocity_mps": unloaded_next.velocity_world_mps.y,
    }


def continuous_state_evolution_probe() -> dict[str, object]:
    chain = build_chain("conventional_crewed")
    initial_forward = chain.runtime.actuator_aggregation.main("forward")
    require_close(initial_forward.total_used_thrust_n, 200_000.0)
    require_close(initial_forward.residual_torque_about_cic_n_m, 0.0)
    require_close(initial_forward.positive_moment_side_capacity_n, 100_000.0)
    require_close(initial_forward.negative_moment_side_capacity_n, 100_000.0)

    # 第一步真实消耗燃料，并回写同一个舰艇实例而不重绑出航来源。
    model = build_tactical_ship_model(chain.runtime, chain.snapshot)
    first_state, first_diagnostics = integrate_tactical_step(
        model,
        initialize_tactical_motion_state(model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    after_fuel = commit_tactical_state_to_instance(model, first_state)
    assert after_fuel.sortie_configuration_sha256 == chain.sortie.source_sha256
    assert after_fuel.operational_state.fuel_units < chain.instance.operational_state.fuel_units
    fuel_runtime = compile_runtime_ship_parameters(
        chain.snapshot, chain.sortie, after_fuel
    )
    require_close(fuel_runtime.current_mass_kg, chain.runtime.current_mass_kg)

    # 在同一实例上叠加单侧主机受损、单台转向机摧毁、人员伤亡和卸货。
    evolved_source = after_fuel.to_dict()
    next(
        item for item in evolved_source["module_states"]
        if item["instance_id"] == "main_engine_starboard"
    )["current_durability_points"] = 50.0
    next(
        item for item in evolved_source["module_states"]
        if item["instance_id"] == "thruster_starboard_fore"
    )["current_durability_points"] = 0.0
    next(
        item for item in evolved_source["operational_state"]["crew"]
        if item["crew_type"] == "ordinary"
    )["count"] = 6
    next(
        item for item in evolved_source["operational_state"]["crew"]
        if item["crew_type"] == "technical_officer"
    )["count"] = 2
    evolved_source["operational_state"]["bulk_cargo"] = []
    evolved_instance = ShipInstanceSnapshotInput.parse(evolved_source)
    evolved = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        evolved_instance,
        active_automatic_events=ALL_COMBAT_EVENTS,
    )
    assert evolved.sortie_configuration_sha256 == chain.sortie.source_sha256
    assert evolved.derived_snapshot_sha256 == chain.snapshot.source_sha256

    # 受损一侧只能提供 75%，完好侧自动降至同样推力，合计 150kN 且不留下偏航。
    damaged_forward = evolved.actuator_aggregation.main("forward")
    require_close(damaged_forward.total_used_thrust_n, 150_000.0)
    require_close(damaged_forward.balanced_off_axis_thrust_each_side_n, 75_000.0)
    require_close(damaged_forward.residual_torque_about_cic_n_m, 0.0)
    assert {item.used_thrust_n for item in damaged_forward.uses} == {75_000.0}

    # 摧毁一台顺逆时针组内转向机，只削弱对应方向并保留真实残余横向力。
    initial_ccw = chain.runtime.actuator_aggregation.turning("counterclockwise")
    damaged_ccw = evolved.actuator_aggregation.turning("counterclockwise")
    require_close(damaged_ccw.torque_capacity_n_m, 0.5 * initial_ccw.torque_capacity_n_m)
    assert damaged_ccw.net_force_body_n == (10_000.0, 0.0)
    require_close(
        evolved.actuator_aggregation.turning("clockwise").torque_capacity_n_m,
        chain.runtime.actuator_aggregation.turning("clockwise").torque_capacity_n_m,
    )

    assert dict(evolved.crew_type_fulfillment)["ordinary"] < 1.0
    assert dict(evolved.crew_type_fulfillment)["technical_officer"] < 1.0
    assert evolved.module("weapon_upper_port").function_efficiency("weapon.reload") < 1.0
    assert evolved.module("fire_control").function_efficiency("fire_control.guidance") < 1.0
    assert evolved.module("sensor_upper_starboard").function_efficiency("sensor.track") < 1.0
    require_close(chain.runtime.current_mass_kg - evolved.current_mass_kg, 25_000.0)

    # 换层由同一受损实例继续推进；完成前保持上层，完成后一次切换到云层。
    quick_environment = replace(
        PROTOTYPE_TACTICAL_ENVIRONMENT,
        upper_cloud_transition_s=0.05,
    )
    evolved_model = build_tactical_ship_model(
        evolved, chain.snapshot, environment=quick_environment
    )
    transitioning = request_layer_transition(
        evolved_model,
        initialize_tactical_motion_state(evolved_model),
        "cloud",
    )
    for _ in range(2):
        transitioning, _ = integrate_tactical_step(
            evolved_model, transitioning, TacticalControlInput()
        )
        assert transitioning.height_layer == "upper"
    transitioning, _ = integrate_tactical_step(
        evolved_model, transitioning, TacticalControlInput()
    )
    assert transitioning.height_layer == "cloud"
    assert transitioning.layer_transition is None
    final_instance = commit_tactical_state_to_instance(evolved_model, transitioning)
    final_runtime = compile_runtime_ship_parameters(
        chain.snapshot, chain.sortie, final_instance
    )
    assert final_runtime.height_layer == "cloud"
    assert final_runtime.sortie_configuration_sha256 == chain.sortie.source_sha256
    assert final_runtime.instance_snapshot.sortie_configuration == chain.instance.sortie_configuration
    return {
        "damaged_forward_total_thrust_n": damaged_forward.total_used_thrust_n,
        "damaged_turning_residual_force_body_n": list(damaged_ccw.net_force_body_n),
        "final_height_layer": final_runtime.height_layer,
        "fuel_units_consumed_first_step": first_diagnostics.fuel_units_consumed,
        "initial_forward_total_thrust_n": initial_forward.total_used_thrust_n,
        "state_hashes": {
            "after_fuel": canonical_sha256(after_fuel),
            "after_losses_and_unload": canonical_sha256(evolved_instance),
            "after_layer_transition": canonical_sha256(final_instance),
        },
        "weapon_reload_efficiency_after_casualties": evolved.module(
            "weapon_upper_port"
        ).function_efficiency("weapon.reload"),
    }


def structure_lock_and_overg_probe() -> dict[str, object]:
    conventional = build_chain("conventional_crewed")
    base_source = deepcopy(load_json(BASE_MODULE_CATALOG))
    next(item for item in base_source["modules"] if item["category"] == "main_engine")[
        "capability"
    ]["thrust_n"] = 1_000_000_000.0
    high_base = ModulePrototypeCatalog.parse(base_source)
    high_catalog = merge_module_prototype_catalogs(
        (high_base, load_module_prototype_catalog(COMBAT_MODULE_CATALOG)),
        id="gtw.module_catalog.fixture.stage_g_high_thrust_crewed",
        version=1,
        name="阶段G有人舰高推力关系探针目录",
        fixture_level="contract_fixture",
    )
    high_outfit = compile_outfit(
        load_outfit_plan(SHIP_PATHS["conventional_crewed"]["outfit"]),
        conventional.hull,
        high_catalog,
        load_hull_coating_catalog(COATING_CATALOG),
    )
    high_snapshot = build_derived_ship_snapshot(conventional.hull, high_outfit)
    high_sortie = compile_sortie_configuration(
        high_snapshot,
        load_sortie_configuration(SHIP_PATHS["conventional_crewed"]["sortie"]),
    )
    high_instance = initialize_ship_instance_snapshot(high_snapshot, high_sortie)
    high_runtime = compile_runtime_ship_parameters(
        high_snapshot, high_sortie, high_instance
    )
    require_close(
        high_runtime.safe_longitudinal_mps2,
        conventional.hull.safe_longitudinal_mps2,
    )
    assert high_runtime.crew_safety_lock_enabled
    high_model = build_tactical_ship_model(high_runtime, high_snapshot)
    _, normal = integrate_tactical_step(
        high_model,
        initialize_tactical_motion_state(high_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    _, crewed_overg = integrate_tactical_step(
        high_model,
        initialize_tactical_motion_state(high_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0), overg=True),
    )
    assert normal.structure_ratio <= 1.0 + 1.0e-7
    assert normal.command_scale < 1.0
    assert crewed_overg.structure_ratio > 1.0
    assert crewed_overg.crew_g <= 12.0 + 1.0e-7
    assert crewed_overg.command_scale < 1.0

    unmanned = build_chain("unmanned_flagship")
    unmanned_model = build_tactical_ship_model(unmanned.runtime, unmanned.snapshot)
    _, unmanned_normal = integrate_tactical_step(
        unmanned_model,
        initialize_tactical_motion_state(unmanned_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    _, unmanned_overg = integrate_tactical_step(
        unmanned_model,
        initialize_tactical_motion_state(unmanned_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0), overg=True),
    )
    assert not unmanned.runtime.crew_safety_lock_enabled
    assert unmanned_normal.structure_ratio <= 1.0 + 1.0e-7
    assert unmanned_overg.command_scale == 1.0
    assert unmanned_overg.structure_ratio > 1.0
    assert unmanned_overg.hull_integrity_damage > 0.0
    return {
        "crewed_normal_command_scale": normal.command_scale,
        "crewed_overg_command_scale": crewed_overg.command_scale,
        "crewed_overg_g": crewed_overg.crew_g,
        "derived_safe_longitudinal_g": high_runtime.safe_longitudinal_mps2 / 9.80665,
        "unmanned_overg_command_scale": unmanned_overg.command_scale,
        "unmanned_overg_g": unmanned_overg.crew_g,
    }


def environment_and_explanation_probe() -> dict[str, object]:
    chain = build_chain("conventional_crewed")
    snapshot = chain.snapshot.to_dict()
    sortie = chain.sortie.to_dict()
    runtime = chain.runtime.to_dict()

    # 设计器、出航解释和运行时必须展示同一批数值与来源指纹。
    require_close(snapshot["design"]["mass_kg"], chain.outfit.design_mass_kg)
    require_close(snapshot["design"]["inertia_kg_m2"], chain.outfit.design_inertia_kg_m2)
    require_close(sortie["design"]["mass_kg"], snapshot["design"]["mass_kg"])
    require_close(sortie["current"]["mass_kg"], runtime["current"]["mass_kg"])
    require_close(sortie["current"]["inertia_kg_m2"], runtime["current"]["inertia_kg_m2"])
    require_close(snapshot["lift"]["force_n"], runtime["lift"]["current_force_n"])
    require_close(
        chain.hull.safe_longitudinal_mps2,
        runtime["structure"]["safe_longitudinal_mps2"],
    )
    assert snapshot["actuator_aggregation"] == runtime["actuator_aggregation"]
    assert sortie["sources"]["derived_snapshot_sha256"] == chain.snapshot.source_sha256
    assert runtime["sources"]["derived_snapshot_sha256"] == chain.snapshot.source_sha256
    assert runtime["sources"]["sortie_configuration_sha256"] == chain.sortie.source_sha256

    model = build_tactical_ship_model(chain.runtime, chain.snapshot)
    state = initialize_tactical_motion_state(model)
    fast_upper = replace(state, velocity_world_mps=Vec2(0.0, 340.0))
    fast_cloud = replace(fast_upper, height_layer="cloud")
    upper_drag = calculate_tactical_drag(model, fast_upper)
    cloud_drag = calculate_tactical_drag(model, fast_cloud)
    assert upper_drag.force_world_n.y < 0.0
    assert cloud_drag.breakdown.drag_force_n > upper_drag.breakdown.drag_force_n
    front_rcs = query_tactical_rcs_to_observer(model, state, Vec2(0.0, 10_000.0))
    side_rcs = query_tactical_rcs_to_observer(model, state, Vec2(10_000.0, 0.0))
    assert side_rcs.known_total_rcs_m2 > front_rcs.known_total_rcs_m2
    assert not front_rcs.complete
    assert front_rcs.unresolved_external_rcs_instances
    return {
        "cloud_drag_force_n_at_340_mps": cloud_drag.breakdown.drag_force_n,
        "design_runtime_explanations_match": True,
        "front_known_rcs_m2": front_rcs.known_total_rcs_m2,
        "side_known_rcs_m2": side_rcs.known_total_rcs_m2,
        "upper_drag_force_n_at_340_mps": upper_drag.breakdown.drag_force_n,
    }


def build_result() -> dict[str, object]:
    return {
        "determinism": deterministic_blueprint_probe(),
        "environment_and_explanations": environment_and_explanation_probe(),
        "inertia_and_full_load": inertia_and_full_load_probe(),
        "interface": "gaotian.stage-g-end-to-end-integration/v1",
        "state_evolution": continuous_state_evolution_probe(),
        "status": "PASS",
        "structure_lock_and_overg": structure_lock_and_overg_probe(),
    }


def main() -> None:
    print(json.dumps(build_result(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
