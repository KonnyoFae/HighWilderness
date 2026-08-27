"""ShipInstanceSnapshot、供电、人员和运行时执行器重聚合回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇数据契约 import (
    ContractError,
    ModulePrototypeCatalog,
    RuntimePowerPolicyInput,
    ShipInstanceSnapshotInput,
    SortieConfigurationInput,
    canonical_json,
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
    DAMAGE_RESPONSE_POLICY_ID,
    compile_runtime_ship_parameters,
    initialize_ship_instance_snapshot,
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


def state(source: dict[str, object], instance_id: str) -> dict[str, object]:
    return next(
        item for item in source["module_states"] if item["instance_id"] == instance_id
    )


def crew_item(source: dict[str, object], crew_type: str) -> dict[str, object]:
    return next(item for item in source["crew"] if item["crew_type"] == crew_type)


def operational_crew_item(
    source: dict[str, object], crew_type: str
) -> dict[str, object]:
    return next(
        item
        for item in source["operational_state"]["crew"]
        if item["crew_type"] == crew_type
    )


def build_snapshot(module_catalog: ModulePrototypeCatalog | None = None):
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    coatings = load_hull_coating_catalog(COATING_CATALOG)
    modules = module_catalog or load_module_prototype_catalog(MODULE_CATALOG)
    hull = compile_hull(load_hull_blueprint(HULL_FIXTURE), registry)
    outfit = compile_outfit(load_outfit_plan(OUTFIT_FIXTURE), hull, modules, coatings)
    return build_derived_ship_snapshot(hull, outfit)


def compile_sortie(snapshot, source: dict[str, object] | None = None):
    configuration = (
        load_sortie_configuration(SORTIE_FIXTURE)
        if source is None
        else SortieConfigurationInput.parse(source)
    )
    return compile_sortie_configuration(snapshot, configuration)


def main() -> None:
    schema = load_json(SCHEMA_FILE)
    assert "#/$defs/shipInstanceSnapshot" in {
        entry["$ref"] for entry in schema["oneOf"]
    }
    snapshot = build_snapshot()
    sortie = compile_sortie(snapshot)
    instance_source = load_json(INSTANCE_FIXTURE)
    instance = load_ship_instance_snapshot(INSTANCE_FIXTURE)
    assert canonical_json(instance) == INSTANCE_FIXTURE.read_text(encoding="utf-8")
    assert initialize_ship_instance_snapshot(snapshot, sortie) == instance

    runtime = compile_runtime_ship_parameters(snapshot, sortie, instance)
    assert runtime.to_dict()["interface"] == "gaotian.runtime-ship-parameters/v1alpha1"
    assert runtime.to_dict()["damage_response_policy"] == DAMAGE_RESPONSE_POLICY_ID
    require_close(runtime.current_mass_kg, 2_140_350.0)
    require_close(runtime.current_inertia_kg_m2, 3_291_303_541.666667)
    require_close(runtime.current_lift_force_n, 30_000_000.0)
    require_close(runtime.current_lift_margin_n, 9_010_336.6725)
    assert runtime.lift_sufficient
    assert runtime.cic_control_available
    assert not runtime.remote_control_available
    assert runtime.fuel_available
    require_close(runtime.current_hull_integrity_fraction, 1.0)
    assert runtime.terminal_failures == ()
    require_close(runtime.safe_longitudinal_mps2, snapshot.hull.safe_longitudinal_mps2)
    require_close(runtime.safe_lateral_mps2, snapshot.hull.safe_lateral_mps2)
    require_close(runtime.safe_yaw_rate_rad_s, snapshot.hull.safe_yaw_rate_rad_s)
    require_close(runtime.power.generation_kw, 1_000.0)
    require_close(runtime.power.requested_load_kw, 0.0)
    require_close(runtime.power.supplied_load_kw, 0.0)
    require_close(runtime.power.remaining_generation_kw, 1_000.0)
    assert runtime.module("damage_control").powered
    assert runtime.module("damage_control").operating_mode == "standby"
    assert runtime.module("damage_control").stored_operating_mode == "standby"
    assert not runtime.module("damage_control").automatically_activated
    assert not runtime.module("damage_control").active_available
    assert runtime.module("remote_core").operating_mode == "standby"
    require_close(
        runtime.actuator_aggregation.main("forward").total_used_thrust_n,
        100_000.0,
    )

    # 自动事件只临时提升待机模块的有效模式，不改写实例快照中的持久状态。
    damage_event_runtime = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        instance,
        active_automatic_events=("ship.damage_control_required",),
    )
    assert damage_event_runtime.active_automatic_events == (
        "ship.damage_control_required",
    )
    damage_event_module = damage_event_runtime.module("damage_control")
    assert damage_event_module.stored_operating_mode == "standby"
    assert damage_event_module.operating_mode == "active"
    assert damage_event_module.automatically_activated
    assert damage_event_module.active_available
    require_close(damage_event_runtime.power.requested_load_kw, 50.0)
    require_close(damage_event_runtime.power.supplied_load_kw, 50.0)
    require_close(damage_event_runtime.power.remaining_generation_kw, 950.0)
    require_close(
        damage_event_module.function_efficiency("damage_control.firefighting"),
        1.0,
    )

    damaged_understaffed_damage_source = deepcopy(instance_source)
    state(damaged_understaffed_damage_source, "damage_control")[
        "current_durability_points"
    ] = 50.0
    operational_crew_item(
        damaged_understaffed_damage_source, "veteran_damage_control"
    )["count"] = 1
    damaged_understaffed_damage = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        ShipInstanceSnapshotInput.parse(damaged_understaffed_damage_source),
        active_automatic_events=("ship.damage_control_required",),
    )
    require_close(
        damaged_understaffed_damage.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        ),
        0.25,
    )

    # 显式关闭优先于自动事件，供玩家保留强制断电/停机控制权。
    damage_off_source = deepcopy(instance_source)
    state(damage_off_source, "damage_control")["operating_mode"] = "off"
    damage_off_runtime = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        ShipInstanceSnapshotInput.parse(damage_off_source),
        active_automatic_events=("ship.damage_control_required",),
    )
    damage_off_module = damage_off_runtime.module("damage_control")
    assert damage_off_module.stored_operating_mode == "off"
    assert damage_off_module.operating_mode == "off"
    assert not damage_off_module.automatically_activated
    assert not damage_off_module.active_available
    require_close(damage_off_runtime.power.requested_load_kw, 0.0)
    require_close(
        runtime.actuator_aggregation.turning("counterclockwise").torque_capacity_n_m,
        550_000.0,
    )

    # 最低船员按全舰人员池/标准总需求形成比例；自动化子功能不受该比例削减。
    minimum_source = deepcopy(load_json(SORTIE_FIXTURE))
    crew_item(minimum_source, "officer")["count"] = 1
    crew_item(minimum_source, "ordinary")["count"] = 3
    crew_item(minimum_source, "technical_officer")["count"] = 1
    crew_item(minimum_source, "veteran_damage_control")["count"] = 1
    minimum_sortie = compile_sortie(snapshot, minimum_source)
    minimum_instance_source = initialize_ship_instance_snapshot(
        snapshot, minimum_sortie
    ).to_dict()
    state(minimum_instance_source, "damage_control")["operating_mode"] = "active"
    minimum_instance = ShipInstanceSnapshotInput.parse(minimum_instance_source)
    minimum_runtime = compile_runtime_ship_parameters(
        snapshot, minimum_sortie, minimum_instance
    )
    assert dict(minimum_runtime.crew_type_fulfillment) == {
        "officer": 0.5,
        "ordinary": 0.75,
        "technical_officer": 0.5,
        "veteran_damage_control": 0.5,
    }
    require_close(
        minimum_runtime.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        ),
        0.5,
    )
    require_close(
        minimum_runtime.actuator_aggregation.main("forward").total_used_thrust_n,
        100_000.0,
    )

    # 完全无人遥控时手动损管归零，但已声明自动化的推进功能仍可工作。
    remote_source = deepcopy(load_json(SORTIE_FIXTURE))
    remote_source["control_mode"] = "remote_core"
    remote_source["active_remote_core_instance_id"] = "remote_core"
    remote_source["crew"] = []
    remote_sortie = compile_sortie(snapshot, remote_source)
    remote_instance = initialize_ship_instance_snapshot(snapshot, remote_sortie)
    remote_runtime = compile_runtime_ship_parameters(
        snapshot, remote_sortie, remote_instance
    )
    assert remote_runtime.remote_control_available
    assert not remote_runtime.crew_safety_lock_enabled
    assert remote_runtime.module("remote_core").operating_mode == "active"
    require_close(
        remote_runtime.module("damage_control").function_efficiency(
            "damage_control.firefighting"
        ),
        0.0,
    )
    require_close(
        remote_runtime.actuator_aggregation.main("forward").total_used_thrust_n,
        100_000.0,
    )

    # 推进使用原型声明的非线性分段曲线：50% 耐久对应 75% 输出。
    damaged_source = deepcopy(instance_source)
    state(damaged_source, "main_engine")["current_durability_points"] = 50.0
    damaged_runtime = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(damaged_source)
    )
    assert damaged_runtime.module("main_engine").condition == "damaged"
    assert dict(
        damaged_runtime.module("main_engine").damage_function_multipliers
    ) == {"engine.throttle": 0.75}
    require_close(
        damaged_runtime.actuator_aggregation.main("forward").total_used_thrust_n,
        75_000.0,
    )

    # 发电采用自身的线性响应；战损倍率与人员/自动化倍率在功能层相乘。
    damaged_generator_source = deepcopy(instance_source)
    state(damaged_generator_source, "generator")["current_durability_points"] = 50.0
    damaged_generator = compile_runtime_ship_parameters(
        snapshot,
        sortie,
        ShipInstanceSnapshotInput.parse(damaged_generator_source),
    )
    require_close(
        damaged_generator.module("generator").function_efficiency(
            "generator.regulation"
        ),
        0.5,
    )
    require_close(damaged_generator.power.generation_kw, 500.0)
    require_close(damaged_generator.power.remaining_generation_kw, 500.0)

    # 灵烷贮槽采用明确的归零失效阈值，部分耐久损失不擅自线性削减升力。
    damaged_lift_source = deepcopy(instance_source)
    state(damaged_lift_source, "lift_tank")["current_durability_points"] = 1.0
    damaged_lift = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(damaged_lift_source)
    )
    require_close(
        damaged_lift.module("lift_tank").function_efficiency("lift_tank.lift"),
        1.0,
    )
    require_close(damaged_lift.current_lift_force_n, 30_000_000.0)

    damaged_cic_source = deepcopy(instance_source)
    state(damaged_cic_source, "cic")["current_durability_points"] = 1.0
    damaged_cic = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(damaged_cic_source)
    )
    assert damaged_cic.cic_control_available

    destroyed_thruster_source = deepcopy(instance_source)
    state(destroyed_thruster_source, "thruster_right")["current_durability_points"] = 0.0
    destroyed_thruster = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(destroyed_thruster_source)
    )
    require_close(
        destroyed_thruster.actuator_aggregation.turning(
            "counterclockwise"
        ).torque_capacity_n_m,
        275_000.0,
    )

    engine_off_source = deepcopy(instance_source)
    state(engine_off_source, "main_engine")["operating_mode"] = "off"
    engine_off = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(engine_off_source)
    )
    require_close(
        engine_off.actuator_aggregation.main("forward").total_used_thrust_n, 0.0
    )

    cic_destroyed_source = deepcopy(instance_source)
    state(cic_destroyed_source, "cic")["current_durability_points"] = 0.0
    cic_destroyed = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(cic_destroyed_source)
    )
    assert not cic_destroyed.cic_control_available
    assert not cic_destroyed.module("remote_core").host_available
    assert "cic_control_lost" in cic_destroyed.terminal_failures

    lift_destroyed_source = deepcopy(instance_source)
    state(lift_destroyed_source, "lift_tank")["current_durability_points"] = 0.0
    lift_destroyed = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(lift_destroyed_source)
    )
    require_close(lift_destroyed.current_lift_force_n, 0.0)
    assert not lift_destroyed.lift_sufficient
    assert "insufficient_lift" in lift_destroyed.terminal_failures

    hull_destroyed_source = deepcopy(instance_source)
    hull_destroyed_source["current_hull_integrity_fraction"] = 0.0
    hull_destroyed = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(hull_destroyed_source)
    )
    assert "hull_structure_collapsed" in hull_destroyed.terminal_failures

    # 当前资源状态可以在不修改或重绑初始出航配置的情况下演化。
    zero_fuel_source = deepcopy(instance_source)
    zero_fuel_source["operational_state"]["fuel_units"] = 0.0
    zero_fuel_instance = ShipInstanceSnapshotInput.parse(zero_fuel_source)
    zero_fuel = compile_runtime_ship_parameters(
        snapshot, sortie, zero_fuel_instance
    )
    assert not zero_fuel.fuel_available
    assert zero_fuel.actuators == ()
    assert zero_fuel.sortie_configuration_sha256 == sortie.source_sha256

    changed_load_source = deepcopy(instance_source)
    changed_load_source["operational_state"]["height_layer"] = "rain"
    changed_load_source["operational_state"]["bulk_cargo"][0]["mass_kg"] = 10_000.0
    operational_crew_item(changed_load_source, "ordinary")["count"] = 1
    changed_load = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(changed_load_source)
    )
    assert changed_load.height_layer == "rain"
    require_close(changed_load.current_mass_kg, runtime.current_mass_kg - 40_000.0)
    assert changed_load.current_inertia_kg_m2 < runtime.current_inertia_kg_m2
    require_close(dict(changed_load.crew_type_fulfillment)["ordinary"], 0.25)
    assert changed_load.crew_safety_lock_enabled

    critical_shortage_source = deepcopy(instance_source)
    operational_crew_item(critical_shortage_source, "ordinary")["count"] = 1
    critical_shortage = compile_runtime_ship_parameters(
        snapshot, sortie, ShipInstanceSnapshotInput.parse(critical_shortage_source)
    )
    assert dict(critical_shortage.module("main_engine").crew_allocations) == {
        "ordinary": 1.0
    }
    assert critical_shortage.module("thruster_left").crew_allocations == ()
    assert critical_shortage.module("thruster_right").crew_allocations == ()

    excessive_current_fuel = deepcopy(instance_source)
    excessive_current_fuel["operational_state"]["fuel_units"] = 1_001.0
    require_contract_error(
        "instance.fuel_capacity_exceeded",
        lambda: compile_runtime_ship_parameters(
            snapshot,
            sortie,
            ShipInstanceSnapshotInput.parse(excessive_current_fuel),
        ),
    )

    # 人工提高用电负载，验证整类跳闸与安全供电逐台规则。
    power_source = deepcopy(load_json(MODULE_CATALOG))
    by_category = {item["category"]: item for item in power_source["modules"]}
    by_category["damage_control"]["power"].update(
        {"active_load_kw": 650.0, "consumer_category": "damage_control"}
    )
    by_category["maneuver_thruster"]["power"].update(
        {
            "active_load_kw": 200.0,
            "consumer_category": "weapons_and_active_defense",
        }
    )
    by_category["cic"]["power"].update(
        {"active_load_kw": 200.0, "consumer_category": "fire_control"}
    )
    by_category["cargo_hold"]["power"].update(
        {"active_load_kw": 100.0, "consumer_category": "sensors"}
    )
    power_snapshot = build_snapshot(ModulePrototypeCatalog.parse(power_source))
    power_sortie = compile_sortie(power_snapshot)
    strict_instance_source = initialize_ship_instance_snapshot(
        power_snapshot, power_sortie
    ).to_dict()
    state(strict_instance_source, "damage_control")["operating_mode"] = "active"
    strict_instance = ShipInstanceSnapshotInput.parse(strict_instance_source)
    strict = compile_runtime_ship_parameters(
        power_snapshot, power_sortie, strict_instance
    )
    require_close(strict.power.requested_load_kw, 1_350.0)
    require_close(strict.power.supplied_load_kw, 950.0)
    assert set(strict.power.powered_instance_ids) == {
        "cargo_hold",
        "cic",
        "damage_control",
    }
    assert not strict.module("thruster_left").powered
    assert not strict.module("thruster_right").powered
    require_close(
        strict.actuator_aggregation.turning("counterclockwise").torque_capacity_n_m,
        0.0,
    )

    safe_policy = RuntimePowerPolicyInput(
        "safe_nearest_to_cic",
        (
            "damage_control",
            "weapons_and_active_defense",
            "fire_control",
            "sensors",
        ),
        (),
    )
    safe_instance_source = initialize_ship_instance_snapshot(
        power_snapshot, power_sortie, power_policy=safe_policy
    ).to_dict()
    state(safe_instance_source, "damage_control")["operating_mode"] = "active"
    safe_instance = ShipInstanceSnapshotInput.parse(safe_instance_source)
    safe = compile_runtime_ship_parameters(power_snapshot, power_sortie, safe_instance)
    assert set(safe.power.powered_instance_ids) == {
        "cargo_hold",
        "damage_control",
        "thruster_left",
    }
    assert safe.module("thruster_left").powered
    assert not safe.module("thruster_right").powered
    require_close(
        safe.actuator_aggregation.turning("counterclockwise").torque_capacity_n_m,
        275_000.0,
    )

    reordered_policy = RuntimePowerPolicyInput(
        "strict_categories",
        (
            "weapons_and_active_defense",
            "damage_control",
            "fire_control",
            "sensors",
        ),
        (),
    )
    reordered = compile_runtime_ship_parameters(
        power_snapshot,
        power_sortie,
        initialize_ship_instance_snapshot(
            power_snapshot, power_sortie, power_policy=reordered_policy
        ),
    )
    assert set(reordered.power.powered_instance_ids) == {
        "cargo_hold",
        "cic",
        "thruster_left",
        "thruster_right",
    }

    disabled_policy = RuntimePowerPolicyInput(
        "strict_categories",
        (
            "damage_control",
            "weapons_and_active_defense",
            "fire_control",
            "sensors",
        ),
        ("damage_control",),
    )
    disabled_instance_source = initialize_ship_instance_snapshot(
        power_snapshot, power_sortie, power_policy=disabled_policy
    ).to_dict()
    state(disabled_instance_source, "damage_control")["operating_mode"] = "active"
    disabled = compile_runtime_ship_parameters(
        power_snapshot,
        power_sortie,
        ShipInstanceSnapshotInput.parse(disabled_instance_source),
    )
    assert not disabled.module("damage_control").powered
    assert disabled.module("thruster_left").powered
    assert disabled.module("thruster_right").powered

    generator_destroyed_source = initialize_ship_instance_snapshot(
        power_snapshot, power_sortie
    ).to_dict()
    state(generator_destroyed_source, "generator")["current_durability_points"] = 0.0
    generator_destroyed = compile_runtime_ship_parameters(
        power_snapshot,
        power_sortie,
        ShipInstanceSnapshotInput.parse(generator_destroyed_source),
    )
    require_close(generator_destroyed.power.generation_kw, 0.0)
    assert generator_destroyed.power.powered_instance_ids == ()
    require_close(
        generator_destroyed.actuator_aggregation.turning(
            "counterclockwise"
        ).torque_capacity_n_m,
        0.0,
    )

    # 引用、状态集合、耐久与供电策略错误必须稳定拒绝。
    wrong_hash = deepcopy(instance_source)
    wrong_hash["derived_ship_snapshot_sha256"] = "0" * 64
    require_contract_error(
        "runtime.derived_snapshot_hash_mismatch",
        lambda: compile_runtime_ship_parameters(
            snapshot, sortie, ShipInstanceSnapshotInput.parse(wrong_hash)
        ),
    )

    missing_state = deepcopy(instance_source)
    missing_state["module_states"].pop()
    require_contract_error(
        "runtime.module_state_set_mismatch",
        lambda: compile_runtime_ship_parameters(
            snapshot, sortie, ShipInstanceSnapshotInput.parse(missing_state)
        ),
    )

    excessive_durability = deepcopy(instance_source)
    state(excessive_durability, "cic")["current_durability_points"] = 101.0
    require_contract_error(
        "runtime.module_durability_exceeded",
        lambda: compile_runtime_ship_parameters(
            snapshot, sortie, ShipInstanceSnapshotInput.parse(excessive_durability)
        ),
    )

    invalid_priority = deepcopy(instance_source)
    invalid_priority["power_policy"]["category_order"] = [
        "damage_control",
        "weapons_and_active_defense",
        "fire_control",
        "fire_control",
    ]
    require_contract_error(
        "instance.power_category_permutation",
        lambda: ShipInstanceSnapshotInput.parse(invalid_priority),
    )

    invalid_hull_integrity = deepcopy(instance_source)
    invalid_hull_integrity["current_hull_integrity_fraction"] = 1.01
    require_contract_error(
        "instance.hull_integrity_fraction",
        lambda: ShipInstanceSnapshotInput.parse(invalid_hull_integrity),
    )

    print(
        json.dumps(
            {
                "actuator_counterclockwise_torque_n_m": runtime.actuator_aggregation.turning(
                    "counterclockwise"
                ).torque_capacity_n_m,
                "compiler_interface": runtime.to_dict()["interface"],
                "damage_response_policy": DAMAGE_RESPONSE_POLICY_ID,
                "instance_fixture": f"{instance.id}@{instance.version}",
                "instance_source_sha256": runtime.instance_snapshot_sha256,
                "power_policy": runtime.power.policy_id,
                "runtime_lift_margin_n": runtime.current_lift_margin_n,
                "safe_mode_powered_instances": list(safe.power.powered_instance_ids),
                "status": "PASS",
                "strict_mode_powered_instances": list(strict.power.powered_instance_ids),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
