"""阶段 I8：阵营/舰队归属和按被动应战方层级判定的战术交战边界。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, load_material_registry
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I统一战术场景时间线测试 import prepared_ship
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog
from 高天荒野舰艇战术机动求解器 import Vec2
from 高天荒野舰艇统一战术场景 import (
    TACTICAL_ENGAGEMENT_BOUNDARY_SCHEMA_ID,
    TACTICAL_ENGAGEMENT_POLICY_ID,
    TACTICAL_SCENE_INTERFACE_ID,
    TacticalEngagementDefinition,
    TacticalSceneExitDirective,
    TacticalSceneState,
    advance_tactical_scene_step,
    initialize_tactical_scene,
    load_tactical_engagement_boundary_profile,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
BOUNDARY_PROFILE = ROOT / "舰艇数据" / "标定" / "阶段I战术交战边界技术替身配置.v1.json"
BOUNDARY_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇战术交战边界配置契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I战术交战边界接口.v1.json"

INITIATOR = "side.fixture.initiator"
RESPONDER = "side.fixture.responder"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def placed_ship(
    chain,
    timing_catalog,
    ship_id: str,
    side_id: str,
    fleet_id: str,
    position: Vec2,
    *,
    layer: str = "upper",
    velocity: Vec2 = Vec2(),
):
    binding, combat, motion = prepared_ship(
        chain,
        timing_catalog,
        ship_id,
        queue_fire=False,
    )
    binding = replace(binding, side_id=side_id, fleet_id=fleet_id)
    operational = replace(combat.instance.operational_state, height_layer=layer)
    combat = replace(
        combat,
        instance=replace(combat.instance, operational_state=operational),
    )
    motion = replace(
        motion,
        position_world_m=position,
        velocity_world_mps=velocity,
        height_layer=layer,
    )
    return binding, combat, motion


def build_engagement(
    chain,
    timing_catalog,
    projectile_catalog,
    profile,
    ships,
):
    bindings = tuple(item[0] for item in ships)
    return bindings, initialize_tactical_scene(
        bindings,
        projectile_catalog,
        timing_catalog,
        initial_motion_states={item[0].ship_id: item[2] for item in ships},
        initial_combat_states={item[0].ship_id: item[1] for item in ships},
        engagement_definition=TacticalEngagementDefinition(INITIATOR, RESPONDER),
        engagement_boundary_profile=profile,
    )


def pair(
    chain,
    timing_catalog,
    distance_m: float,
    *,
    initiator_layer: str = "upper",
    responder_layer: str = "upper",
    initiator_velocity: Vec2 = Vec2(),
    responder_velocity: Vec2 = Vec2(),
):
    return (
        placed_ship(
            chain,
            timing_catalog,
            "ship.fixture.engagement.initiator",
            INITIATOR,
            "fleet.fixture.initiator",
            Vec2(0.0, 0.0),
            layer=initiator_layer,
            velocity=initiator_velocity,
        ),
        placed_ship(
            chain,
            timing_catalog,
            "ship.fixture.engagement.responder",
            RESPONDER,
            "fleet.fixture.responder",
            Vec2(distance_m, 0.0),
            layer=responder_layer,
            velocity=responder_velocity,
        ),
    )


def main() -> None:
    schema = json.loads(BOUNDARY_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == TACTICAL_ENGAGEMENT_BOUNDARY_SCHEMA_ID
    profile = load_tactical_engagement_boundary_profile(BOUNDARY_PROFILE)
    assert profile.fixture_level == "prototype_unbalanced"
    assert profile.distance_m("upper") == 50000.0
    assert profile.distance_m("cloud") == profile.distance_m("rain") == 25000.0
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    chain = build_chain("conventional_crewed")

    # 边界点计入接触；超过任意小量才拒绝创建战术场景。
    upper_bindings, upper = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        pair(chain, timing_catalog, 50000.0),
    )
    assert upper.engagement_state is not None
    assert upper.engagement_state.status == "active"
    assert upper.engagement_state.qualifying_pair_count == 1
    assert upper.engagement_state.closest_cross_side_distance_m == 50000.0
    require_contract_error(
        "tactical_engagement.not_in_contact",
        lambda: build_engagement(
            chain,
            timing_catalog,
            projectile_catalog,
            profile,
            pair(chain, timing_catalog, 50000.01),
        ),
    )
    cloud_bindings, cloud = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        pair(chain, timing_catalog, 25000.0, responder_layer="cloud"),
    )
    assert cloud.engagement_state is not None
    assert cloud.engagement_state.qualifying_pair_count == 1
    require_contract_error(
        "tactical_engagement.not_in_contact",
        lambda: build_engagement(
            chain,
            timing_catalog,
            projectile_catalog,
            profile,
            pair(chain, timing_catalog, 25000.01, responder_layer="rain"),
        ),
    )

    # 相同40km距离，只交换被动应战舰高度层，结果即不同；主动方层级不替代被动方。
    _, responder_upper = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        pair(
            chain,
            timing_catalog,
            40000.0,
            initiator_layer="cloud",
            responder_layer="upper",
        ),
    )
    assert responder_upper.engagement_state is not None
    assert responder_upper.engagement_state.status == "active"
    require_contract_error(
        "tactical_engagement.not_in_contact",
        lambda: build_engagement(
            chain,
            timing_catalog,
            projectile_catalog,
            profile,
            pair(
                chain,
                timing_catalog,
                40000.0,
                initiator_layer="upper",
                responder_layer="cloud",
            ),
        ),
    )

    # 从边界内向相反方向运动，一步后全部敌对舰对超距，自动结束战术时间线。
    separating_bindings, separating = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        pair(
            chain,
            timing_catalog,
            49999.0,
            initiator_velocity=Vec2(-100.0, 0.0),
            responder_velocity=Vec2(100.0, 0.0),
        ),
    )
    separated = advance_tactical_scene_step(
        separating,
        separating_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        engagement_boundary_profile=profile,
    )
    assert separated.resulting_scene.engagement_state is not None
    assert separated.resulting_scene.engagement_state.status == "disengaged"
    assert separated.resulting_scene.engagement_state.termination_reason == "separation"
    assert len(separated.engagement_events) == 1
    require_contract_error(
        "tactical_engagement.closed",
        lambda: advance_tactical_scene_step(
            separated.resulting_scene,
            separating_bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            engagement_boundary_profile=profile,
        ),
    )

    # 多舰战逐对判定：远舰不会让近舰仍在范围内的战斗提前结束。
    near = placed_ship(
        chain,
        timing_catalog,
        "ship.fixture.engagement.initiator_near",
        INITIATOR,
        "fleet.fixture.initiator",
        Vec2(10000.0, 0.0),
    )
    far = placed_ship(
        chain,
        timing_catalog,
        "ship.fixture.engagement.initiator_far",
        INITIATOR,
        "fleet.fixture.initiator.detached",
        Vec2(70000.0, 0.0),
    )
    responder = placed_ship(
        chain,
        timing_catalog,
        "ship.fixture.engagement.responder_multi",
        RESPONDER,
        "fleet.fixture.responder",
        Vec2(0.0, 0.0),
    )
    multi_bindings, multi = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        (near, far, responder),
    )
    assert multi.engagement_state is not None
    assert multi.engagement_state.qualifying_pair_count == 1
    multi_after_near_exit = advance_tactical_scene_step(
        multi,
        multi_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        exit_directives=(
            TacticalSceneExitDirective(near[0].ship_id, 0.0, "scripted_transfer"),
        ),
        engagement_boundary_profile=profile,
    )
    assert multi_after_near_exit.resulting_scene.engagement_state is not None
    assert multi_after_near_exit.resulting_scene.engagement_state.status == "disengaged"

    # 一方不再有可战舰时是resolved，不与双方仍完整但距离拉开的disengaged混淆。
    resolved_bindings, resolved_scene = build_engagement(
        chain,
        timing_catalog,
        projectile_catalog,
        profile,
        pair(chain, timing_catalog, 1000.0),
    )
    responder_id = next(item.ship_id for item in resolved_scene.ships if item.side_id == RESPONDER)
    resolved = advance_tactical_scene_step(
        resolved_scene,
        resolved_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        exit_directives=(
            TacticalSceneExitDirective(responder_id, 0.0, "scripted_transfer"),
        ),
        engagement_boundary_profile=profile,
    )
    assert resolved.resulting_scene.engagement_state is not None
    assert resolved.resulting_scene.engagement_state.status == "resolved"
    assert resolved.resulting_scene.engagement_state.termination_reason == "responding_side_no_combat_capable_ship"

    serialized = canonical_json(upper)
    restored = TacticalSceneState.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized
    assert {item.fleet_id for item in restored.ships} == {
        "fleet.fixture.initiator",
        "fleet.fixture.responder",
    }
    changed_profile = replace(
        profile,
        layer_distances_m=tuple(
            (layer, value + (1.0 if layer == "upper" else 0.0))
            for layer, value in profile.layer_distances_m
        ),
    )
    require_contract_error(
        "tactical_engagement.profile_mismatch",
        lambda: advance_tactical_scene_step(
            upper,
            upper_bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            engagement_boundary_profile=changed_profile,
        ),
    )
    changed_binding = replace(upper_bindings[0], fleet_id="fleet.fixture.changed")
    require_contract_error(
        "tactical_scene.affiliation_mismatch",
        lambda: advance_tactical_scene_step(
            upper,
            (changed_binding, upper_bindings[1]),
            timing_catalog,
            projectile_catalog,
            registry,
            engagement_boundary_profile=profile,
        ),
    )

    report = {
        "boundary_profile": {
            "reference": profile.reference.to_dict(),
            "source_sha256": profile.source_sha256,
            "layer_distances_m": dict(profile.layer_distances_m),
        },
        "fixture_notice": "50/25km来自用户当前边界设定，但配置仍标记prototype_unbalanced，可在原则不变时换版调整。",
        "interface": TACTICAL_SCENE_INTERFACE_ID,
        "policy": TACTICAL_ENGAGEMENT_POLICY_ID,
        "verified_boundaries": [
            "舰艇持久化阵营与舰队归属并以绑定内容校验",
            "上层50000m、云层/雨层25000m边界点计入交战，超过后才脱离",
            "每一敌对舰对都使用被动应战舰当前层级，不使用主动方或全场统一层级",
            "多舰战只要任意一组敌对舰仍在边界内就继续，全部超距才disengaged",
            "一方无可战舰为resolved，与双方仍存续但距离脱离严格区分",
            "边界配置引用与内容指纹锁定，场景保存重载保持确定性",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("阶段I8战术交战边界回归：PASS")


if __name__ == "__main__":
    main()
