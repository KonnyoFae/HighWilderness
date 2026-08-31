"""阶段 E4：真实运行时参数到机动、气动、RCS 与换层状态的端到端回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    ModulePrototypeCatalog,
    ShipInstanceSnapshotInput,
    SortieConfigurationInput,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
    load_ship_instance_snapshot,
    load_sortie_configuration,
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
from 高天荒野舰艇战术机动求解器 import (
    FIXED_STEP_POLICY_ID,
    PROTOTYPE_TACTICAL_ENVIRONMENT,
    TACTICAL_DYNAMICS_INTERFACE_ID,
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


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
COATING_CATALOG = ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
MODULE_CATALOG = ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
HULL_FIXTURE = ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"
OUTFIT_FIXTURE = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20最小舾装.v1.json"
SORTIE_FIXTURE = ROOT / "舰艇数据" / "出航配置夹具" / "标准155x20载货出航.v1.json"
INSTANCE_FIXTURE = ROOT / "舰艇数据" / "舰艇实例夹具" / "标准155x20完好实例.v1.json"


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


def build_snapshot(module_catalog: ModulePrototypeCatalog | None = None):
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    modules = module_catalog or load_module_prototype_catalog(MODULE_CATALOG)
    hull = compile_hull(load_hull_blueprint(HULL_FIXTURE), registry)
    outfit = compile_outfit(load_outfit_plan(OUTFIT_FIXTURE), hull, modules, coatings)
    return build_derived_ship_snapshot(hull, outfit)


def compile_standard_runtime(snapshot):
    sortie = compile_sortie_configuration(
        snapshot, load_sortie_configuration(SORTIE_FIXTURE)
    )
    instance = load_ship_instance_snapshot(INSTANCE_FIXTURE)
    return sortie, instance, compile_runtime_ship_parameters(snapshot, sortie, instance)


def main() -> None:
    snapshot = build_snapshot()
    sortie, instance, runtime = compile_standard_runtime(snapshot)
    model = build_tactical_ship_model(runtime, snapshot)
    assert model.to_dict()["interface"] == TACTICAL_DYNAMICS_INTERFACE_ID
    assert model.to_dict()["fixed_step_policy"] == FIXED_STEP_POLICY_ID
    assert model.runtime.current_mass_kg == runtime.current_mass_kg
    assert model.runtime.current_inertia_kg_m2 == runtime.current_inertia_kg_m2
    assert model.actuator_aggregation.main("forward").total_used_thrust_n == 100_000.0
    assert (
        model.actuator_aggregation.turning("counterclockwise").torque_capacity_n_m
        == 550_000.0
    )

    # 正式步进直接读取实际模块聚合；零速首步没有气动阻力，燃料按实际执行器消耗。
    state = initialize_tactical_motion_state(model)
    next_state, forward_diagnostics = integrate_tactical_step(
        model,
        state,
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    require_close(
        next_state.velocity_world_mps.y,
        100_000.0 / runtime.current_mass_kg * model.tuning.fixed_step_s,
    )
    require_close(
        forward_diagnostics.fuel_units_consumed,
        model.tuning.fixed_step_s,
    )
    assert next_state.fixed_step_index == 1
    try:
        integrate_tactical_step(
            model,
            state,
            TacticalControlInput(),
            dt=1.0 / 30.0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("正式求解器不得接受任意可变时间步")

    # 同一真实气动缓存按速度方向和当前高度层查询；云/雨层阻力高于上层。
    fast_upper = replace(state, velocity_world_mps=Vec2(0.0, 340.0))
    fast_cloud = replace(fast_upper, height_layer="cloud")
    upper_drag = calculate_tactical_drag(model, fast_upper)
    cloud_drag = calculate_tactical_drag(model, fast_cloud)
    assert upper_drag.force_world_n.y < 0.0
    assert cloud_drag.breakdown.drag_force_n > upper_drag.breakdown.drag_force_n
    assert upper_drag.breakdown.mach == 1.0

    # RCS 读取同一船壳方向缓存和实际涂料；未标定外挂只使结果成为已知下限。
    front_rcs = query_tactical_rcs_to_observer(
        model, state, Vec2(0.0, 10_000.0)
    )
    side_rcs = query_tactical_rcs_to_observer(
        model, state, Vec2(10_000.0, 0.0)
    )
    assert front_rcs.bearing_body_deg == 0.0
    assert side_rcs.bearing_body_deg == 90.0
    assert side_rcs.known_total_rcs_m2 > front_rcs.known_total_rcs_m2
    assert not front_rcs.complete
    assert front_rcs.unresolved_external_rcs_instances

    # 换层期间继续使用出发层环境；完成后才一次切换当前离散高度层。
    quick_environment = replace(
        PROTOTYPE_TACTICAL_ENVIRONMENT,
        upper_cloud_transition_s=0.05,
        cloud_rain_transition_s=0.05,
    )
    quick_model = build_tactical_ship_model(
        runtime, snapshot, environment=quick_environment
    )
    transitioning = request_layer_transition(
        quick_model,
        initialize_tactical_motion_state(quick_model),
        "rain",
    )
    assert transitioning.layer_transition is not None
    require_close(transitioning.layer_transition.duration_s, 0.10)
    for _ in range(5):
        transitioning, _ = integrate_tactical_step(
            quick_model, transitioning, TacticalControlInput()
        )
        assert transitioning.height_layer == "upper"
    transitioning, _ = integrate_tactical_step(
        quick_model, transitioning, TacticalControlInput()
    )
    assert transitioning.height_layer == "rain"
    assert transitioning.layer_transition is None
    committed = commit_tactical_state_to_instance(quick_model, transitioning)
    assert committed.operational_state.height_layer == "rain"

    # 没有正升力余量时，真实受损实例不能从雨层发起上升。
    no_lift_source = instance.to_dict()
    no_lift_source["operational_state"]["height_layer"] = "rain"
    next(
        item
        for item in no_lift_source["module_states"]
        if item["instance_id"] == "lift_tank"
    )["current_durability_points"] = 0.0
    no_lift_instance = ShipInstanceSnapshotInput.parse(no_lift_source)
    no_lift_runtime = compile_runtime_ship_parameters(
        snapshot, sortie, no_lift_instance
    )
    no_lift_model = build_tactical_ship_model(no_lift_runtime, snapshot)
    require_contract_error(
        "tactical.insufficient_lift_for_ascent",
        lambda: request_layer_transition(
            no_lift_model,
            initialize_tactical_motion_state(no_lift_model),
            "upper",
        ),
    )

    # 高推力仍必须来自模块原型与实际布局，用它验证结构限幅、12G 锁与无人 OverG。
    high_thrust_source = load_json(MODULE_CATALOG)
    next(
        item
        for item in high_thrust_source["modules"]
        if item["category"] == "main_engine"
    )["capability"]["thrust_n"] = 1_000_000_000.0
    high_snapshot = build_snapshot(ModulePrototypeCatalog.parse(high_thrust_source))
    crewed_sortie = compile_sortie_configuration(
        high_snapshot, load_sortie_configuration(SORTIE_FIXTURE)
    )
    crewed_instance = initialize_ship_instance_snapshot(high_snapshot, crewed_sortie)
    crewed_runtime = compile_runtime_ship_parameters(
        high_snapshot, crewed_sortie, crewed_instance
    )
    crewed_model = build_tactical_ship_model(crewed_runtime, high_snapshot)
    _, normal_high = integrate_tactical_step(
        crewed_model,
        initialize_tactical_motion_state(crewed_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0)),
    )
    assert normal_high.structure_ratio <= 1.0 + 1.0e-7
    assert normal_high.command_scale < 1.0
    _, crewed_overg = integrate_tactical_step(
        crewed_model,
        initialize_tactical_motion_state(crewed_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0), overg=True),
    )
    assert crewed_overg.crew_g <= 12.0 + 1.0e-7
    assert crewed_overg.command_scale < 1.0
    assert crewed_overg.structure_ratio > 1.0

    remote_source = deepcopy(load_json(SORTIE_FIXTURE))
    remote_source["control_mode"] = "remote_core"
    remote_source["active_remote_core_instance_id"] = "remote_core"
    remote_source["crew"] = []
    remote_sortie = compile_sortie_configuration(
        high_snapshot, SortieConfigurationInput.parse(remote_source)
    )
    remote_instance = initialize_ship_instance_snapshot(high_snapshot, remote_sortie)
    remote_runtime = compile_runtime_ship_parameters(
        high_snapshot, remote_sortie, remote_instance
    )
    remote_model = build_tactical_ship_model(remote_runtime, high_snapshot)
    remote_next, unmanned_overg = integrate_tactical_step(
        remote_model,
        initialize_tactical_motion_state(remote_model),
        TacticalControlInput(move_body=Vec2(0.0, 1.0), overg=True),
    )
    assert unmanned_overg.command_scale == 1.0
    assert unmanned_overg.structure_ratio > crewed_overg.structure_ratio
    assert unmanned_overg.hull_integrity_damage > 0.0
    assert remote_next.hull_integrity_fraction < 1.0
    committed_overg = commit_tactical_state_to_instance(remote_model, remote_next)
    assert (
        committed_overg.current_hull_integrity_fraction
        == remote_next.hull_integrity_fraction
    )

    wrong_runtime = replace(
        runtime,
        _core=replace(
            runtime.stable_core,
            derived_snapshot_sha256="0" * 64,
        ),
    )
    require_contract_error(
        "tactical.derived_snapshot_mismatch",
        lambda: build_tactical_ship_model(wrong_runtime, snapshot),
    )

    print(
        json.dumps(
            {
                "cloud_drag_force_n_at_340_mps": cloud_drag.breakdown.drag_force_n,
                "fixed_step_policy": FIXED_STEP_POLICY_ID,
                "front_known_rcs_m2": front_rcs.known_total_rcs_m2,
                "interface": TACTICAL_DYNAMICS_INTERFACE_ID,
                "runtime_parameters_sha256": model.runtime_parameters_sha256,
                "side_known_rcs_m2": side_rcs.known_total_rcs_m2,
                "status": "PASS",
                "unmanned_overg_hull_damage": unmanned_overg.hull_integrity_damage,
                "upper_drag_force_n_at_340_mps": upper_drag.breakdown.drag_force_n,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
