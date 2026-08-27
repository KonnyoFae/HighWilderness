"""阶段 I7：战术舰艇失能、坠落、指挥权与显式场景退出回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, load_material_registry
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I统一战术场景时间线测试 import prepared_ship, scene_fixture
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog
from 高天荒野舰艇战术机动求解器 import TacticalControlInput, Vec2
from 高天荒野舰艇统一战术场景 import (
    TACTICAL_SCENE_INTERFACE_ID,
    TACTICAL_SCENE_POLICY_ID,
    TacticalSceneExitDirective,
    TacticalSceneLaunchDirective,
    TacticalSceneState,
    advance_tactical_scene_step,
    derive_tactical_ship_lifecycle,
    initialize_tactical_scene,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I战术生命周期接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def replace_module(combat, instance_ids: set[str], *, durability: float | None = None, operating_mode: str | None = None):
    states = []
    for item in combat.instance.module_states:
        if item.instance_id not in instance_ids:
            states.append(item)
            continue
        states.append(
            replace(
                item,
                current_durability_points=(
                    item.current_durability_points
                    if durability is None
                    else durability
                ),
                operating_mode=(
                    item.operating_mode
                    if operating_mode is None
                    else operating_mode
                ),
            )
        )
    return replace(combat, instance=replace(combat.instance, module_states=tuple(states)))


def one_ship_scene(chain, timing_catalog, projectile_catalog, ship_id: str, combat_transform):
    binding, combat, motion = prepared_ship(
        chain,
        timing_catalog,
        ship_id,
        queue_fire=False,
    )
    combat = combat_transform(combat)
    state = initialize_tactical_scene(
        (binding,),
        projectile_catalog,
        timing_catalog,
        initial_motion_states={ship_id: motion},
        initial_combat_states={ship_id: combat},
    )
    return binding, state


def main() -> None:
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    chain = build_chain("conventional_crewed")

    # CIC只是关闭而未毁：不伪造物理坠落，但撤销场景指挥权。
    control_id = "ship.fixture.lifecycle.control_lost"
    control_binding, control_scene = one_ship_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        control_id,
        lambda combat: replace_module(combat, {"cic"}, operating_mode="off"),
    )
    control_lifecycle = control_scene.ships[0].lifecycle_state
    assert control_lifecycle.physical_status == "operational"
    assert control_lifecycle.command_status == "uncommanded"
    assert control_lifecycle.failure_causes == ("cic_control_unavailable",)
    require_contract_error(
        "tactical_scene.command_unavailable",
        lambda: advance_tactical_scene_step(
            control_scene,
            (control_binding,),
            timing_catalog,
            projectile_catalog,
            registry,
            controls={control_id: TacticalControlInput(move_body=Vec2(0.0, 1.0))},
        ),
    )

    # 船壳崩溃、CIC耐久归零、升力不足分别独立触发不可逆的失控坠落。
    hull_id = "ship.fixture.lifecycle.hull"
    _, hull_scene = one_ship_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        hull_id,
        lambda combat: replace(
            combat,
            instance=replace(combat.instance, current_hull_integrity_fraction=0.0),
        ),
    )
    assert hull_scene.ships[0].lifecycle_state.failure_causes == ("hull_structure_collapsed",)

    cic_id = "ship.fixture.lifecycle.cic"
    _, cic_scene = one_ship_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        cic_id,
        lambda combat: replace_module(combat, {"cic"}, durability=0.0),
    )
    assert cic_scene.ships[0].lifecycle_state.physical_status == "falling"
    assert "cic_destroyed" in cic_scene.ships[0].lifecycle_state.failure_causes

    lift_ids = {
        item.id
        for item in chain.snapshot.outfit.instances
        if item.prototype.category == "lift_fuel_tank"
    }
    lift_id = "ship.fixture.lifecycle.lift"
    _, lift_scene = one_ship_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        lift_id,
        lambda combat: replace_module(combat, lift_ids, durability=0.0),
    )
    assert lift_scene.ships[0].lifecycle_state.failure_causes == ("insufficient_lift",)

    # 完全无人旗舰失去遥控核心但CIC仍完好：不会物理坠落，却无本舰人员接管。
    unmanned = build_chain("unmanned_flagship")
    remote_instance = replace(
        unmanned.instance,
        module_states=tuple(
            replace(item, current_durability_points=0.0)
            if item.instance_id == "remote_core"
            else item
            for item in unmanned.instance.module_states
        ),
    )
    remote_runtime = compile_runtime_ship_parameters(
        unmanned.snapshot,
        unmanned.sortie,
        remote_instance,
    )
    remote_lifecycle = derive_tactical_ship_lifecycle(
        remote_runtime,
        unmanned.sortie,
        step_index=0,
    )
    assert remote_lifecycle.physical_status == "operational"
    assert remote_lifecycle.command_status == "uncommanded"
    assert remote_lifecycle.failure_causes == ("remote_control_lost",)

    # 实际炮弹在统一场景中摧毁CIC：本步结束即转为坠落并取消未执行武器队列。
    shooter, shooter_combat, shooter_motion = prepared_ship(
        chain,
        timing_catalog,
        "ship.fixture.lifecycle.shooter",
        queue_fire=True,
    )
    target, target_combat, target_motion = prepared_ship(
        chain,
        timing_catalog,
        "ship.fixture.lifecycle.target",
        queue_fire=False,
    )
    target_combat = replace_module(target_combat, {"cic"}, durability=1.0)
    shooter_motion = replace(
        shooter_motion,
        position_world_m=Vec2(-200.0, 10.0),
    )
    target_motion = replace(target_motion, position_world_m=Vec2(0.0, 0.0))
    battle = initialize_tactical_scene(
        (shooter, target),
        projectile_catalog,
        timing_catalog,
        initial_motion_states={shooter.ship_id: shooter_motion, target.ship_id: target_motion},
        initial_combat_states={shooter.ship_id: shooter_combat, target.ship_id: target_combat},
    )
    launch = TacticalSceneLaunchDirective(
        shooter.ship_id,
        f"sequence.{shooter.ship_id}.single",
        0.0,
        "projectile.fixture.lifecycle.cic_hit",
        target.ship_id,
        0,
        (1.0, 0.0),
    )
    step = advance_tactical_scene_step(
        battle,
        (shooter, target),
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(launch,),
    )
    impact = None
    lifecycle_event = None
    for _ in range(30):
        if step.impact_events:
            impact = step.impact_events[0]
            lifecycle_event = next(
                (
                    item
                    for item in step.lifecycle_events
                    if item.ship_id == target.ship_id
                    and item.resulting_state.physical_status == "falling"
                ),
                None,
            )
            break
        step = advance_tactical_scene_step(
            step.resulting_scene,
            (shooter, target),
            timing_catalog,
            projectile_catalog,
            registry,
        )
    assert impact is not None and "cic" in impact.damaged_module_instance_ids
    assert lifecycle_event is not None
    target_state = next(item for item in step.resulting_scene.ships if item.ship_id == target.ship_id)
    assert target_state.lifecycle_state.failure_causes == ("cic_destroyed",)
    assert target_state.combat_state.instance.weapon_timeline_state is not None
    assert target_state.combat_state.instance.weapon_timeline_state.sequences == ()
    require_contract_error(
        "tactical_scene.command_unavailable",
        lambda: advance_tactical_scene_step(
            step.resulting_scene,
            (shooter, target),
            timing_catalog,
            projectile_catalog,
            registry,
            controls={target.ship_id: TacticalControlInput(move_body=Vec2(0.0, 1.0))},
        ),
    )

    # 坠出场景由外部边界判定显式确认；不在本阶段虚构坠落秒数。
    exit_time = step.resulting_scene.tactical_time_s
    exited = advance_tactical_scene_step(
        step.resulting_scene,
        (shooter, target),
        timing_catalog,
        projectile_catalog,
        registry,
        exit_directives=(
            TacticalSceneExitDirective(target.ship_id, exit_time, "fell_below_scene"),
        ),
    )
    exited_target = next(item for item in exited.resulting_scene.ships if item.ship_id == target.ship_id)
    assert exited_target.lifecycle_state.physical_status == "exited"
    assert exited_target.lifecycle_state.exit_reason == "fell_below_scene"
    assert next(item for item in exited.ship_results if item.ship_id == target.ship_id).diagnostics is None
    serialized = canonical_json(exited.resulting_scene)
    assert canonical_json(TacticalSceneState.parse(json.loads(serialized))) == serialized

    # 仍在飞行且以离场舰为目标的弹丸按target_left_scene确定性终止。
    purge_bindings, purge_scene, purge_launch = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    purge_first = advance_tactical_scene_step(
        purge_scene,
        purge_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        launch_directives=(purge_launch,),
    )
    purge_target = purge_launch.target_ship_id
    purge_time = purge_first.resulting_scene.tactical_time_s
    purged = advance_tactical_scene_step(
        purge_first.resulting_scene,
        purge_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        exit_directives=(
            TacticalSceneExitDirective(purge_target, purge_time, "distance_disengaged"),
        ),
    )
    assert purged.resulting_scene.projectile_world.projectiles == ()
    assert any(item.reason == "target_left_scene" for item in purged.expired_events)
    operational_id = "ship.fixture.lifecycle.invalid_fall_exit"
    operational_binding, operational_scene = one_ship_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        operational_id,
        lambda combat: combat,
    )
    require_contract_error(
        "tactical_scene.ship_not_falling",
        lambda: advance_tactical_scene_step(
            operational_scene,
            (operational_binding,),
            timing_catalog,
            projectile_catalog,
            registry,
            exit_directives=(
                TacticalSceneExitDirective(operational_id, 0.0, "fell_below_scene"),
            ),
        ),
    )

    report = {
        "interface": TACTICAL_SCENE_INTERFACE_ID,
        "policy": TACTICAL_SCENE_POLICY_ID,
        "fixture_notice": "本阶段只冻结失能、指挥权与退出合同，不设定坠落耗时、救生率或撤离距离。",
        "cic_impact": impact.to_dict(),
        "verified_boundaries": [
            "CIC关闭或暂时不可用只撤销指挥权，CIC耐久归零才触发物理坠落",
            "船壳完整度归零、CIC摧毁和升力不足分别独立触发不可逆坠落",
            "完全无人舰失去遥控核心后无人员接管但不会凭空物理坠毁",
            "命中引发的坠落在步末武器事件前生效并取消活动武器序列",
            "坠落舰拒绝操纵输入；水平运动只保留惯性与既有气动，不伪造垂直坠落时长",
            "离场由固定步边界显式确认，目标离场后相关弹丸以target_left_scene终止",
            "生命周期随统一场景保存重载并保持确定性",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("阶段I7战术生命周期回归：PASS")


if __name__ == "__main__":
    main()
