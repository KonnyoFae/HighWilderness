"""方向主机配平、物理残余副作用与派生快照的阶段 D 回归。"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isclose
from pathlib import Path

from 高天荒野舰艇数据契约 import (
    OutfitPlanInput,
    load_hull_blueprint,
    load_hull_coating_catalog,
    load_json,
    load_material_registry,
    load_module_prototype_catalog,
    load_outfit_plan,
)
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import (
    ACTUATOR_AGGREGATION_POLICY_ID,
    ActuatorInstance,
    aggregate_actuators,
    build_derived_ship_snapshot,
    compile_outfit,
)


ROOT = Path(__file__).resolve().parent


def require_close(actual: float, expected: float) -> None:
    if not isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-9):
        raise AssertionError(f"{actual!r} != {expected!r}")


def actuator(
    instance_id: str,
    category: str,
    thrust_n: float,
    point: tuple[float, float],
    direction: tuple[float, float],
    *,
    fuel_units_per_s: float = 1.0,
    response_time_s: float = 1.0,
) -> ActuatorInstance:
    torque = point[0] * thrust_n * direction[1] - point[1] * thrust_n * direction[0]
    return ActuatorInstance(
        instance_id=instance_id,
        category=category,
        thrust_n=thrust_n,
        application_point_m=point,
        direction_body=direction,
        torque_about_cic_n_m=torque,
        fuel_units_per_s=fuel_units_per_s,
        response_time_s=response_time_s,
    )


def main() -> None:
    # 中轴主机独立输出；离轴两侧按总推力配平，实际力臂仍产生残余偏航。
    main_engines = (
        actuator("center", "main_engine", 50.0, (0.0, -5.0), (0.0, 1.0)),
        actuator("negative", "main_engine", 200.0, (-10.0, -5.0), (0.0, 1.0)),
        actuator("positive", "main_engine", 100.0, (5.0, -5.0), (0.0, 1.0)),
    )
    aggregate = aggregate_actuators(main_engines)
    assert aggregate.policy_id == ACTUATOR_AGGREGATION_POLICY_ID
    forward = aggregate.main("forward")
    require_close(forward.centerline_capacity_n, 50.0)
    require_close(forward.negative_moment_side_capacity_n, 200.0)
    require_close(forward.positive_moment_side_capacity_n, 100.0)
    require_close(forward.balanced_off_axis_thrust_each_side_n, 100.0)
    require_close(forward.total_used_thrust_n, 250.0)
    assert forward.net_force_body_n == (0.0, 250.0)
    require_close(forward.residual_torque_about_cic_n_m, -500.0)
    scales = {use.instance_id: use.output_scale for use in forward.uses}
    assert scales == {"center": 1.0, "negative": 0.5, "positive": 1.0}

    # 一侧主机损失后，另一侧离轴主机同步归零，中轴主机仍然可用。
    damaged = aggregate_actuators(tuple(item for item in main_engines if item.instance_id != "positive"))
    damaged_forward = damaged.main("forward")
    require_close(damaged_forward.total_used_thrust_n, 50.0)
    assert damaged_forward.net_force_body_n == (0.0, 50.0)
    assert {use.instance_id: use.output_scale for use in damaged_forward.uses} == {
        "center": 1.0,
        "negative": 0.0,
    }

    # 横移主机使用同一“推力作用线两侧”规则，不把两侧错误固定为左右舷。
    lateral = aggregate_actuators(
        (
            actuator("fore", "main_engine", 80.0, (0.0, 20.0), (1.0, 0.0)),
            actuator("aft", "main_engine", 120.0, (0.0, -10.0), (1.0, 0.0)),
        )
    ).main("right")
    require_close(lateral.balanced_off_axis_thrust_each_side_n, 80.0)
    require_close(lateral.total_used_thrust_n, 160.0)
    assert lateral.net_force_body_n == (160.0, 0.0)
    require_close(lateral.residual_torque_about_cic_n_m, -800.0)

    # 转向发动机保留未抵消平动力；正负力矩分别形成独立方向能力。
    turn = aggregate_actuators(
        (
            actuator("ccw_a", "maneuver_thruster", 100.0, (0.0, -10.0), (1.0, 0.0)),
            actuator("ccw_b", "maneuver_thruster", 50.0, (0.0, 20.0), (-1.0, 0.0)),
            actuator("cw", "maneuver_thruster", 80.0, (0.0, 10.0), (1.0, 0.0)),
            actuator("zero", "maneuver_thruster", 25.0, (0.0, 0.0), (1.0, 0.0)),
        )
    )
    ccw = turn.turning("counterclockwise")
    require_close(ccw.torque_capacity_n_m, 2_000.0)
    assert ccw.net_force_body_n == (50.0, 0.0)
    cw = turn.turning("clockwise")
    require_close(cw.torque_capacity_n_m, 800.0)
    assert cw.net_force_body_n == (80.0, 0.0)
    assert turn.zero_torque_maneuver_thruster_instances == ("zero",)

    registry = load_material_registry(
        (
            ROOT / "舰艇数据" / "材料" / "结构材质.v1.json",
            ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json",
        )
    )
    hull = compile_hull(
        load_hull_blueprint(ROOT / "舰艇数据" / "船壳蓝图夹具" / "标准155x20单层船壳.v1.json"),
        registry,
    )
    module_catalog = load_module_prototype_catalog(
        ROOT / "舰艇数据" / "模块" / "测试夹具" / "最小模块目录.v1.json"
    )
    coating_catalog = load_hull_coating_catalog(
        ROOT / "舰艇数据" / "涂料" / "船体涂料.v1.json"
    )
    plan_path = ROOT / "舰艇数据" / "舾装方案夹具" / "标准155x20最小舾装.v1.json"
    plan = load_outfit_plan(plan_path)
    compiled = compile_outfit(plan, hull, module_catalog, coating_catalog)
    snapshot = build_derived_ship_snapshot(hull, compiled)
    snapshot_dict = snapshot.to_dict()
    assert snapshot_dict["schema"] == "gaotian.ship/v1alpha1"
    assert snapshot_dict["kind"] == "DerivedShipSnapshot"
    assert snapshot_dict["sources"]["hull_blueprint"]["source_sha256"] == hull.source_sha256
    assert snapshot_dict["sources"]["module_catalog"]["source_sha256"] == compiled.module_catalog_source_sha256
    assert snapshot_dict["actuator_aggregation"] == compiled.actuator_aggregation.to_dict()

    # 任一实际模块位置改变后，舾装与派生快照指纹必须同时改变。
    moved_source = deepcopy(load_json(plan_path))
    next(
        item for item in moved_source["modules"] if item["id"] == "generator"
    )["placement"]["anchor_half_cell"] = [-2, 10]
    moved_outfit = compile_outfit(
        OutfitPlanInput.parse(moved_source), hull, module_catalog, coating_catalog
    )
    moved_snapshot = build_derived_ship_snapshot(hull, moved_outfit)
    assert moved_outfit.source_sha256 != compiled.source_sha256
    assert moved_snapshot.source_sha256 != snapshot.source_sha256

    print(
        json.dumps(
            {
                "actuator_policy": aggregate.policy_id,
                "fixture_snapshot_sha256": snapshot.source_sha256,
                "forward_residual_torque_n_m": forward.residual_torque_about_cic_n_m,
                "forward_total_used_thrust_n": forward.total_used_thrust_n,
                "status": "PASS",
                "turning_residual_force_body_n": list(ccw.net_force_body_n),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
