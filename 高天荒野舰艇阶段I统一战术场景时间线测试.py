"""阶段 I6：舰艇机动、武器边界事件、弹丸与战损重编译的统一时钟回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from math import isclose
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, canonical_sha256, load_material_registry
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I弹药与武器动作测试 import fire_request
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇武器时间与射击队列 import (
    enqueue_continuous_fire,
    initialize_weapon_timeline,
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇战术弹丸世界 import (
    ShipPose2D,
    TacticalProjectileTarget,
    initialize_ship_combat_state,
    load_projectile_profile_catalog,
)
from 高天荒野舰艇战术机动求解器 import (
    TacticalControlInput,
    Vec2,
    build_tactical_ship_model,
    initialize_tactical_motion_state,
)
from 高天荒野舰艇统一战术场景 import (
    PROJECTILE_SUBSTEP_S,
    TACTICAL_SCENE_INTERFACE_ID,
    TACTICAL_SCENE_POLICY_ID,
    TacticalSceneLaunchDirective,
    TacticalSceneShipBinding,
    TacticalSceneState,
    advance_tactical_scene_step,
    initialize_tactical_scene,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
SCENE_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇统一战术场景状态契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I统一战术场景时间线接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def prepared_ship(chain, timing_catalog, ship_id: str, *, queue_fire: bool):
    sortie, instance = live_ship(chain)
    instance = initialize_weapon_timeline(chain.snapshot, instance, timing_catalog)
    if queue_fire:
        instance = enqueue_continuous_fire(
            chain.snapshot,
            sortie,
            instance,
            timing_catalog,
            replace(fire_request(), id=f"sequence.{ship_id}.single", rounds=1),
        ).resulting_instance
    combat = initialize_ship_combat_state(chain.snapshot, instance)
    runtime = compile_runtime_ship_parameters(chain.snapshot, sortie, instance)
    motion = initialize_tactical_motion_state(build_tactical_ship_model(runtime, chain.snapshot))
    binding = TacticalSceneShipBinding(ship_id, chain.snapshot, sortie)
    return binding, combat, motion


def scene_fixture(chain, timing_catalog, projectile_catalog):
    shooter, shooter_combat, shooter_motion = prepared_ship(
        chain, timing_catalog, "ship.fixture.scene.shooter", queue_fire=True
    )
    target, target_combat, target_motion = prepared_ship(
        chain, timing_catalog, "ship.fixture.scene.target", queue_fire=False
    )
    shooter_motion = replace(
        shooter_motion,
        position_world_m=Vec2(0.1, 0.0),
        velocity_world_mps=Vec2(0.0, 100.0),
    )
    target_motion = replace(
        target_motion,
        position_world_m=Vec2(0.0, 200.0),
        velocity_world_mps=Vec2(0.0, -25.0),
    )
    bindings = (shooter, target)
    state = initialize_tactical_scene(
        bindings,
        projectile_catalog,
        timing_catalog,
        initial_motion_states={
            shooter.ship_id: shooter_motion,
            target.ship_id: target_motion,
        },
        initial_combat_states={
            shooter.ship_id: shooter_combat,
            target.ship_id: target_combat,
        },
    )
    directive = TacticalSceneLaunchDirective(
        shooter.ship_id,
        f"sequence.{shooter.ship_id}.single",
        0.0,
        "projectile.fixture.scene.actual_motion",
        target.ship_id,
        0,
        (0.0, 1.0),
    )
    return bindings, state, directive


def main() -> None:
    schema = json.loads(SCENE_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == TACTICAL_SCENE_INTERFACE_ID
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    chain = build_chain("conventional_crewed")

    bindings, state, directive = scene_fixture(chain, timing_catalog, projectile_catalog)
    first = advance_tactical_scene_step(
        state,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(directive,),
    )
    assert len(first.weapon_events) == 1
    assert len(first.spawned_projectiles) == 1
    born = first.spawned_projectiles[0]
    # 发射原点、舰体线速度与战术时间来自真实机动状态，不再由 I5 测试夹具另行注入。
    assert born.position_xy == (-4.9, -10.0)
    assert born.velocity_xy == (0.0, 1005.0)
    assert born.created_time_s == 0.0
    assert first.resulting_scene.projectile_world.projectiles[0].age_s > 0.0
    assert first.resulting_scene.fixed_step_index == 1
    assert isclose(first.resulting_scene.tactical_time_s, 1.0 / 60.0)

    serialized = canonical_json(first.resulting_scene)
    restored = TacticalSceneState.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized
    interpolation_target = TacticalProjectileTarget(
        "ship.fixture.scene.interpolation_probe",
        chain.snapshot,
        first.resulting_scene.ships[0].combat_state,
        ShipPose2D(0.0, (0.0, 0.0), 0.0, (0.0, 0.0), 0.0),
        ShipPose2D(1.0, (10.0, 20.0), 1.0, (4.0, 8.0), 2.0),
    )
    midpoint = interpolation_target.pose_at(0.5)
    assert midpoint.position_xy == (5.0, 10.0)
    assert midpoint.velocity_xy == (2.0, 4.0)
    assert midpoint.heading_rad == 0.5 and midpoint.angular_velocity_rad_s == 1.0
    original_next = advance_tactical_scene_step(
        first.resulting_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
    )
    restored_next = advance_tactical_scene_step(
        restored,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
    )
    assert canonical_json(original_next.resulting_scene) == canonical_json(restored_next.resulting_scene)
    assert canonical_sha256(original_next.to_dict()) == canonical_sha256(restored_next.to_dict())

    current = original_next.resulting_scene
    impact = None
    impact_resolution = None
    for _ in range(30):
        step = advance_tactical_scene_step(
            current,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
        )
        current = step.resulting_scene
        if step.impact_events:
            impact = step.impact_events[0]
            impact_resolution = step
            break
    assert impact is not None and impact_resolution is not None
    assert impact.target_ship_id == "ship.fixture.scene.target"
    assert impact.armor_result.outcome.value == "penetrated"
    assert "main_engine_port" in impact.damaged_module_instance_ids
    target_result = next(item for item in impact_resolution.ship_results if item.ship_id == impact.target_ship_id)
    assert target_result.resulting_runtime.module("main_engine_port").condition == "damaged"

    # 命中所在固定步仍按步首运行时完成；下一步才读取战损后重编译的执行器。
    after_damage = advance_tactical_scene_step(
        current,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        controls={impact.target_ship_id: TacticalControlInput(move_body=Vec2(0.0, 1.0))},
    )
    target_step = next(item for item in after_damage.ship_results if item.ship_id == impact.target_ship_id)
    assert 0.0 < target_step.diagnostics.active_force_body_n.y < 200000.0

    # 下一边界开火在本步运动/命中结算后出生，年龄为零，不会偷跑一个物理步。
    end_bindings, end_state, end_directive_zero = scene_fixture(chain, timing_catalog, projectile_catalog)
    shooter_id = end_directive_zero.source_ship_id
    dt = end_state.fixed_step_s
    shifted_ships = []
    for ship in end_state.ships:
        if ship.ship_id != shooter_id:
            shifted_ships.append(ship)
            continue
        timeline = ship.combat_state.instance.weapon_timeline_state
        assert timeline is not None
        shifted_timeline = replace(
            timeline,
            sequences=tuple(replace(item, next_event_time_s=dt) for item in timeline.sequences),
        )
        shifted_instance = replace(ship.combat_state.instance, weapon_timeline_state=shifted_timeline)
        shifted_ships.append(replace(ship, combat_state=replace(ship.combat_state, instance=shifted_instance)))
    end_state = replace(end_state, ships=tuple(shifted_ships))
    end_directive = replace(
        end_directive_zero,
        tactical_time_s=dt,
        projectile_id="projectile.fixture.scene.end_boundary",
    )
    end_step = advance_tactical_scene_step(
        end_state,
        end_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(end_directive,),
    )
    assert end_step.spawned_projectiles[0].created_time_s == dt
    end_projectile = end_step.resulting_scene.projectile_world.projectiles[0]
    assert end_projectile.created_time_s == dt and end_projectile.age_s == 0.0

    # 场景编排层拒绝缺失发射指令及不能落在固定步边界的武器时间，不作暗中取整。
    _, missing_state, missing_directive = scene_fixture(chain, timing_catalog, projectile_catalog)
    require_contract_error(
        "tactical_scene.launch_directive_missing",
        lambda: advance_tactical_scene_step(
            missing_state,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
        ),
    )
    unmatched_directive = replace(
        missing_directive,
        sequence_id="sequence.ship.fixture.scene.shooter.nonexistent",
        projectile_id="projectile.fixture.scene.unmatched",
    )
    require_contract_error(
        "tactical_scene.launch_directive_unmatched",
        lambda: advance_tactical_scene_step(
            missing_state,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            launch_directives=(missing_directive, unmatched_directive),
        ),
    )
    off_grid_ships = []
    for ship in missing_state.ships:
        if ship.ship_id != shooter_id:
            off_grid_ships.append(ship)
            continue
        timeline = ship.combat_state.instance.weapon_timeline_state
        assert timeline is not None
        off_grid_timeline = replace(
            timeline,
            sequences=tuple(replace(item, next_event_time_s=0.01) for item in timeline.sequences),
        )
        off_grid_ships.append(
            replace(
                ship,
                combat_state=replace(
                    ship.combat_state,
                    instance=replace(ship.combat_state.instance, weapon_timeline_state=off_grid_timeline),
                ),
            )
        )
    off_grid_state = replace(missing_state, ships=tuple(off_grid_ships))
    require_contract_error(
        "tactical_scene.weapon_event_off_grid",
        lambda: advance_tactical_scene_step(
            off_grid_state,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
        ),
    )

    report = {
        "fixed_step_s": state.fixed_step_s,
        "interface": TACTICAL_SCENE_INTERFACE_ID,
        "policy": TACTICAL_SCENE_POLICY_ID,
        "projectile_substep_s": PROJECTILE_SUBSTEP_S,
        "fixture_notice": "沿用阶段I技术替身舰、射速、弹种和损伤换算；本报告只冻结编排语义。",
        "actual_motion_impact": impact.to_dict(),
        "verified_boundaries": [
            "各舰机动、武器时钟和弹丸世界共享唯一场景步号与战术时刻",
            "当前边界开火先生成弹丸，本步末边界开火完成结算后出生且年龄为零",
            "弹丸命中使用目标本步实际首末机动状态插值，不再使用场景外位姿夹具",
            "OverG、燃料和弹丸战损合并回实例，下一固定步由既有运行时编译器重算能力",
            "武器事件必须精确落在固定步边界，缺失或多余的发射指令均拒绝整步提交",
            "完整场景状态可保存重载并继续得到相同结果",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("阶段I6统一战术场景时间线回归：PASS")


if __name__ == "__main__":
    main()
