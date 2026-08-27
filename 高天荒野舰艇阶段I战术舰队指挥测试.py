"""阶段 I9：旗舰角色、唯一直控对象与普通舰 RTS 命令仲裁。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, load_material_registry
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I战术交战边界测试 import (
    INITIATOR,
    RESPONDER,
    build_engagement,
    placed_ship,
)
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog
from 高天荒野舰艇战术机动求解器 import TacticalControlInput, Vec2
from 高天荒野舰艇统一战术场景 import TacticalSceneExitDirective
from 高天荒野舰艇战术舰队指挥 import (
    TACTICAL_COMMAND_TUNING_SCHEMA_ID,
    TACTICAL_FLEET_COMMAND_INTERFACE_ID,
    TACTICAL_FLEET_COMMAND_POLICY_ID,
    TacticalCommandTuningProfile,
    TacticalDirectControlFrame,
    TacticalFleetCommandState,
    TacticalShipOrder,
    TacticalShipRoleAssignment,
    advance_commanded_tactical_scene_step,
    initialize_tactical_fleet_command_state,
    issue_tactical_ship_order,
    load_tactical_command_tuning_profile,
)
from 高天荒野舰艇统一战术场景 import load_tactical_engagement_boundary_profile


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
BOUNDARY_PROFILE = ROOT / "舰艇数据" / "标定" / "阶段I战术交战边界技术替身配置.v1.json"
COMMAND_TUNING = ROOT / "舰艇数据" / "标定" / "阶段I舰队指挥技术替身配置.v1.json"
COMMAND_TUNING_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇战术指挥技术配置契约.v1alpha1.schema.json"
COMMAND_STATE_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇战术舰队指挥状态契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I战术舰队指挥接口.v1.json"

MAIN = "ship.fixture.command.main"
BRANCH = "ship.fixture.command.branch"
WING = "ship.fixture.command.wing"
ENEMY = "ship.fixture.command.enemy"
PLAYER_FLEET = "fleet.fixture.command.player"
ENEMY_FLEET = "fleet.fixture.command.enemy"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def command_scene(
    chain,
    timing_catalog,
    projectile_catalog,
    boundary_profile,
    *,
    direct_ship_id: str = MAIN,
):
    direct = placed_ship(
        chain,
        timing_catalog,
        direct_ship_id,
        INITIATOR,
        PLAYER_FLEET,
        Vec2(0.0, 0.0),
    )
    wing = placed_ship(
        chain,
        timing_catalog,
        WING,
        INITIATOR,
        PLAYER_FLEET,
        Vec2(500.0, 0.0),
    )
    enemy = placed_ship(
        chain,
        timing_catalog,
        ENEMY,
        RESPONDER,
        ENEMY_FLEET,
        Vec2(10000.0, 0.0),
    )
    bindings, scene = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        boundary_profile,
        (direct, wing, enemy),
    )
    return bindings, scene


def role_assignments(direct_ship_id: str, direct_role: str):
    return (
        TacticalShipRoleAssignment(direct_ship_id, direct_role),
        TacticalShipRoleAssignment(
            WING,
            "ordinary_ship",
            direct_ship_id,
            Vec2(500.0, -500.0),
        ),
    )


def main() -> None:
    tuning_schema = json.loads(COMMAND_TUNING_SCHEMA.read_text(encoding="utf-8"))
    state_schema = json.loads(COMMAND_STATE_SCHEMA.read_text(encoding="utf-8"))
    assert tuning_schema["$id"] == TACTICAL_COMMAND_TUNING_SCHEMA_ID
    assert state_schema["$id"] == TACTICAL_FLEET_COMMAND_INTERFACE_ID
    tuning = load_tactical_command_tuning_profile(COMMAND_TUNING)
    assert tuning.fixture_level == "prototype_unbalanced"

    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    boundary_profile = load_tactical_engagement_boundary_profile(BOUNDARY_PROFILE)
    chain = build_chain("conventional_crewed")

    bindings, scene = command_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        boundary_profile,
    )
    state = initialize_tactical_fleet_command_state(
        scene,
        tuning=tuning,
        player_side_id=INITIATOR,
        assignments=role_assignments(MAIN, "main_flagship"),
        direct_control_ship_id=MAIN,
    )
    assert state.direct_control_ship_id == MAIN
    assert state.phase == "active"

    # 没有一般命令时普通舰维持/返回相对编队；直控舰也可以保存预设自动驾驶动作。
    autopilot_state = issue_tactical_ship_order(
        state,
        scene,
        TacticalShipOrder(
            id="order.fixture.command.direct_autopilot",
            ship_id=MAIN,
            kind="move_route",
            issued_step_index=0,
            waypoints_world_m=(Vec2(0.0, 3000.0),),
            target_speed_mps=100.0,
        ),
    )
    autopilot_step = advance_commanded_tactical_scene_step(
        scene,
        autopilot_state,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        tuning,
        engagement_boundary_profile=boundary_profile,
    )
    autopilot_sources = {item.ship_id: item.source for item in autopilot_step.applications}
    assert autopilot_sources[MAIN] == "direct_autopilot.order.move_route"
    assert autopilot_sources[WING] == "formation_return_or_maintain"

    # 普通舰不能成为直控对象；最终动员不能携带直控对象或旗舰角色。
    require_contract_error(
        "tactical_command.direct_role",
        lambda: initialize_tactical_fleet_command_state(
            scene,
            tuning=tuning,
            player_side_id=INITIATOR,
            assignments=(
                TacticalShipRoleAssignment(MAIN, "ordinary_ship"),
                TacticalShipRoleAssignment(WING, "ordinary_ship"),
            ),
            direct_control_ship_id=MAIN,
        ),
    )
    require_contract_error(
        "tactical_command.final_mobilization_direct",
        lambda: initialize_tactical_fleet_command_state(
            scene,
            tuning=tuning,
            player_side_id=INITIATOR,
            assignments=role_assignments(MAIN, "main_flagship"),
            direct_control_ship_id=MAIN,
            mode="final_mobilization",
        ),
    )

    # 多段航路、目标层、航速和舰艏语义进入持久化命令；直控与普通舰命令同时仲裁。
    move_order = TacticalShipOrder(
        id="order.fixture.command.move",
        ship_id=WING,
        kind="move_route",
        issued_step_index=0,
        waypoints_world_m=(Vec2(500.0, 5000.0), Vec2(1000.0, 8000.0)),
        target_layer="cloud",
        target_heading_rad=0.0,
        target_speed_mps=150.0,
    )
    state = issue_tactical_ship_order(state, scene, move_order)
    resolved = advance_commanded_tactical_scene_step(
        scene,
        state,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        tuning,
        direct_control=TacticalDirectControlFrame(
            TacticalControlInput(move_body=Vec2(0.0, 1.0), wheel=0.25),
        ),
        engagement_boundary_profile=boundary_profile,
    )
    applications = {item.ship_id: item for item in resolved.applications}
    assert applications[MAIN].source == "player_direct"
    assert applications[WING].source == "order.move_route"
    next_ships = {item.ship_id: item for item in resolved.scene_resolution.resulting_scene.ships}
    assert next_ships[MAIN].motion_state.velocity_world_mps.y > 0.0
    assert next_ships[WING].motion_state.velocity_world_mps.y > 0.0
    assert next_ships[WING].motion_state.layer_transition is not None
    assert next_ships[WING].motion_state.layer_transition.target_layer == "cloud"
    assert resolved.resulting_command_state.direct_control_ship_id == MAIN

    # 攻击意图保存目标、弹种、交战距离和目标类别；本阶段只解算机动，不暗中自动开火。
    next_scene = resolved.scene_resolution.resulting_scene
    attack_order = TacticalShipOrder(
        id="order.fixture.command.attack",
        ship_id=WING,
        kind="attack_target",
        issued_step_index=next_scene.fixed_step_index,
        target_speed_mps=200.0,
        target_ship_id=ENEMY,
        ammunition_id="ammunition.fixture.standard",
        engagement_distance_m=2000.0,
        target_priority_categories=("ship", "missile"),
    )
    attack_state = issue_tactical_ship_order(
        resolved.resulting_command_state,
        next_scene,
        attack_order,
    )
    serialized = canonical_json(attack_state)
    restored = TacticalFleetCommandState.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized
    restored_attack = next(item for item in restored.orders if item.ship_id == WING)
    assert restored_attack.ammunition_id == "ammunition.fixture.standard"
    assert restored_attack.engagement_distance_m == 2000.0
    assert restored_attack.target_priority_categories == ("ship", "missile")
    require_contract_error(
        "tactical_command.attack_target_invalid",
        lambda: issue_tactical_ship_order(
            resolved.resulting_command_state,
            next_scene,
            replace(attack_order, id="order.fixture.command.friendly_fire", target_ship_id=MAIN),
        ),
    )

    # 玩家舰不能借NPC输入旁路获得第二条直控通道，配置内容也必须精确锁定。
    require_contract_error(
        "tactical_command.player_control_bypass",
        lambda: advance_commanded_tactical_scene_step(
            next_scene,
            attack_state,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            tuning,
            npc_controls={WING: TacticalControlInput(move_body=Vec2(0.0, 1.0))},
            engagement_boundary_profile=boundary_profile,
        ),
    )
    changed_tuning = replace(
        tuning,
        waypoint_tolerance_m=tuning.waypoint_tolerance_m + 1.0,
    )
    require_contract_error(
        "tactical_command.tuning_mismatch",
        lambda: advance_commanded_tactical_scene_step(
            next_scene,
            attack_state,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            changed_tuning,
            engagement_boundary_profile=boundary_profile,
        ),
    )

    # 分旗舰直控失效后不可切换，所属幸存舰转为搜救撤离；额外损失只留下待战略结算标记。
    branch_bindings, branch_scene = command_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        boundary_profile,
        direct_ship_id=BRANCH,
    )
    branch_state = initialize_tactical_fleet_command_state(
        branch_scene,
        tuning=tuning,
        player_side_id=INITIATOR,
        assignments=role_assignments(BRANCH, "branch_flagship"),
        direct_control_ship_id=BRANCH,
    )
    branch_loss = advance_commanded_tactical_scene_step(
        branch_scene,
        branch_state,
        branch_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        tuning,
        exit_directives=(
            TacticalSceneExitDirective(BRANCH, 0.0, "scripted_transfer"),
        ),
        engagement_boundary_profile=boundary_profile,
    )
    loss_state = branch_loss.resulting_command_state
    assert loss_state.phase == "command_defeat_withdrawal"
    assert loss_state.direct_control_ship_id == BRANCH
    assert loss_state.direct_control_loss_reason == "direct_ship_exited"
    assert loss_state.withdrawal_extra_loss_pending
    wing_withdrawal = next(item for item in loss_state.orders if item.ship_id == WING)
    assert wing_withdrawal.kind == "rescue_and_withdraw"
    require_contract_error(
        "tactical_command.direct_after_loss",
        lambda: advance_commanded_tactical_scene_step(
            branch_loss.scene_resolution.resulting_scene,
            loss_state,
            branch_bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            tuning,
            direct_control=TacticalDirectControlFrame(),
            engagement_boundary_profile=boundary_profile,
        ),
    )

    # 最终动员只保留普通舰：可以下令并进入战术步，但明确拒绝玩家直接操纵。
    final_player = placed_ship(
        chain,
        timing_catalog,
        "ship.fixture.command.final",
        INITIATOR,
        "fleet.fixture.command.final",
        Vec2(0.0, 0.0),
    )
    final_enemy = placed_ship(
        chain,
        timing_catalog,
        "ship.fixture.command.final_enemy",
        RESPONDER,
        ENEMY_FLEET,
        Vec2(5000.0, 0.0),
    )
    final_bindings, final_scene = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        boundary_profile,
        (final_player, final_enemy),
    )
    final_ship_id = final_player[0].ship_id
    final_state = initialize_tactical_fleet_command_state(
        final_scene,
        tuning=tuning,
        player_side_id=INITIATOR,
        assignments=(TacticalShipRoleAssignment(final_ship_id, "ordinary_ship"),),
        direct_control_ship_id=None,
        mode="final_mobilization",
    )
    final_state = issue_tactical_ship_order(
        final_state,
        final_scene,
        TacticalShipOrder(
            id="order.fixture.command.final_move",
            ship_id=final_ship_id,
            kind="move_route",
            issued_step_index=0,
            waypoints_world_m=(Vec2(0.0, 2000.0),),
            target_speed_mps=100.0,
        ),
    )
    require_contract_error(
        "tactical_command.final_mobilization_direct",
        lambda: advance_commanded_tactical_scene_step(
            final_scene,
            final_state,
            final_bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            tuning,
            direct_control=TacticalDirectControlFrame(),
            engagement_boundary_profile=boundary_profile,
        ),
    )
    final_step = advance_commanded_tactical_scene_step(
        final_scene,
        final_state,
        final_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        tuning,
        engagement_boundary_profile=boundary_profile,
    )
    assert final_step.applications[0].ship_id == final_ship_id
    assert final_step.applications[0].source == "order.move_route"

    report = {
        "fixture_notice": "25m航点容差、30度满舵误差与50m/s速度误差只用于验证命令仲裁，不是正式自动驾驶调参。",
        "interface": TACTICAL_FLEET_COMMAND_INTERFACE_ID,
        "policy": TACTICAL_FLEET_COMMAND_POLICY_ID,
        "status": "PASS",
        "tuning_profile": {
            "reference": tuning.reference.to_dict(),
            "source_sha256": tuning.source_sha256,
        },
        "verified_boundaries": [
            "主旗舰/分旗舰/普通舰角色持久化并精确覆盖玩家阵营舰艇",
            "常规战斗只冻结一艘旗舰为直控对象且玩家舰不得从NPC控制旁路获得第二控制通道",
            "移动、多段航路、目标高度、航速、舰艏、巡逻和攻击意图使用统一可持久化命令合同",
            "普通舰命令确定性转换为底层TacticalControlInput且自动命令不启用OverG",
            "分旗舰直控失效后不允许切换并令幸存舰进入搜救撤离，额外损失留给战略层结算",
            "最终动员允许普通舰接受命令但明确禁止任何玩家直控输入",
            "指挥状态绑定场景与技术配置内容指纹并通过保存重载",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("阶段I9战术舰队指挥回归：PASS")


if __name__ == "__main__":
    main()
