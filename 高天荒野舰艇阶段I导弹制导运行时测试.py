"""阶段 I10b：确定性导引头状态机、限过载转向与弹丸世界闭环回归。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import hypot
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError, canonical_json, load_material_registry
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇导弹制导 import (
    MISSILE_GUIDANCE_RUNTIME_INTERFACE_ID,
    MISSILE_GUIDANCE_RUNTIME_POLICY_ID,
    MissileGuidanceProfile,
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
    advance_missile_guidance_step,
    initialize_missile_guidance_state,
    load_missile_guidance_profile_catalog,
)
from 高天荒野舰艇战术弹丸世界 import (
    ShipPose2D,
    TacticalProjectileTarget,
    advance_projectile_world,
    load_projectile_profile_catalog,
)
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step


ROOT = Path(__file__).resolve().parent
GUIDANCE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I导弹制导技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I导弹制导运行时接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def runtime_input(
    projectile_id: str,
    *,
    track: bool = True,
    emitting: bool = True,
    illumination: bool = True,
) -> MissileGuidanceRuntimeInput:
    return MissileGuidanceRuntimeInput(projectile_id, track, emitting, illumination)


def profile_catalog_for(
    source: dict[str, object],
    *,
    seeker_kind: str,
    launch_support: str,
    activation_distance_m: float | None,
    allowed_height_layers: list[str],
    target_loss_behavior: str = "last_known_position_then_self_destruct",
    target_memory_s: float = 2.0,
) -> MissileGuidanceProfileCatalog:
    catalog_source = deepcopy(source)
    profile = catalog_source["profiles"][0]
    assert isinstance(profile, dict)
    profile.update(
        {
            "activation_distance_m": activation_distance_m,
            "allowed_height_layers": allowed_height_layers,
            "launch_support": launch_support,
            "seeker_kind": seeker_kind,
            "target_loss_behavior": target_loss_behavior,
            "target_memory_s": target_memory_s,
        }
    )
    return MissileGuidanceProfileCatalog.parse(catalog_source)


def initial_state(catalog: MissileGuidanceProfileCatalog, projectile_id: str):
    state = initialize_missile_guidance_state(
        catalog,
        projectile_id=projectile_id,
        munition_id="gtw.munition.fixture.76mm.standard",
        source_ship_id="ship.fixture.shooter",
        intended_target_ship_id="ship.fixture.target",
        launch_time_s=0.0,
    )
    assert state is not None
    return state


def main() -> None:
    source = json.loads(GUIDANCE_CATALOG.read_text(encoding="utf-8"))
    active_catalog = load_missile_guidance_profile_catalog(GUIDANCE_CATALOG)
    active_state = initial_state(active_catalog, "projectile.fixture.guidance.active")
    facts = runtime_input(active_state.projectile_id)

    # 进入开机距离后在同一子步完成搜索与锁定，并按横向过载上限改变方向但不改速度模长。
    acquired = advance_missile_guidance_step(
        active_state,
        active_catalog,
        facts,
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(1000.0, 1000.0),
        target_height_layer="upper",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert acquired.resulting_state.phase == "tracking"
    assert [item.reason for item in acquired.events] == [
        "seeker_activated",
        "target_acquired",
    ]
    assert acquired.resulting_velocity_xy[1] > 0.0
    assert abs(hypot(*acquired.resulting_velocity_xy) - 100.0) < 1.0e-8
    repeated = advance_missile_guidance_step(
        active_state,
        active_catalog,
        facts,
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(1000.0, 1000.0),
        target_height_layer="upper",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert repeated == acquired

    lost_to_memory = advance_missile_guidance_step(
        acquired.resulting_state,
        active_catalog,
        runtime_input(active_state.projectile_id, track=False),
        position_xy=(10.0, 0.0),
        velocity_xy=acquired.resulting_velocity_xy,
        target_position_xy=(900.0, 1200.0),
        target_height_layer="upper",
        tactical_time_s=0.1,
        duration_s=0.1,
    )
    assert lost_to_memory.resulting_state.phase == "memory"
    assert lost_to_memory.resulting_state.last_known_target_position_xy == (1000.0, 1000.0)
    assert lost_to_memory.events[0].reason == "target_lost_memory"
    reacquired = advance_missile_guidance_step(
        lost_to_memory.resulting_state,
        active_catalog,
        facts,
        position_xy=(20.0, 2.0),
        velocity_xy=lost_to_memory.resulting_velocity_xy,
        target_position_xy=(880.0, 1180.0),
        target_height_layer="upper",
        tactical_time_s=1.0,
        duration_s=0.1,
    )
    assert reacquired.resulting_state.phase == "tracking"
    assert reacquired.events[0].reason == "target_reacquired"

    lost_again = advance_missile_guidance_step(
        reacquired.resulting_state,
        active_catalog,
        runtime_input(active_state.projectile_id, track=False),
        position_xy=(30.0, 4.0),
        velocity_xy=reacquired.resulting_velocity_xy,
        target_position_xy=(870.0, 1170.0),
        target_height_layer="upper",
        tactical_time_s=1.1,
        duration_s=0.1,
    )
    memory_expired = advance_missile_guidance_step(
        lost_again.resulting_state,
        active_catalog,
        runtime_input(active_state.projectile_id, track=False),
        position_xy=(40.0, 6.0),
        velocity_xy=lost_again.resulting_velocity_xy,
        target_position_xy=(850.0, 1150.0),
        target_height_layer="upper",
        tactical_time_s=3.2,
        duration_s=0.1,
    )
    assert memory_expired.resulting_state.phase == "lost"
    assert memory_expired.events[0].reason == "target_memory_expired"
    self_destruct = advance_missile_guidance_step(
        memory_expired.resulting_state,
        active_catalog,
        runtime_input(active_state.projectile_id, track=False),
        position_xy=(50.0, 8.0),
        velocity_xy=memory_expired.resulting_velocity_xy,
        target_position_xy=(840.0, 1140.0),
        target_height_layer="upper",
        tactical_time_s=6.1,
        duration_s=0.1,
    )
    assert self_destruct.self_destruct
    assert self_destruct.events[0].reason == "self_destruct_deadline_reached"

    passive_catalog = profile_catalog_for(
        source,
        seeker_kind="passive_radar",
        launch_support="continuous_illumination",
        activation_distance_m=None,
        allowed_height_layers=["upper", "cloud", "rain"],
        target_loss_behavior="self_destruct",
        target_memory_s=0.0,
    )
    passive_state = initial_state(passive_catalog, "projectile.fixture.guidance.passive")
    passive_without_support = advance_missile_guidance_step(
        passive_state,
        passive_catalog,
        runtime_input(passive_state.projectile_id, illumination=False),
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="upper",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert passive_without_support.resulting_state.phase == "searching"
    passive_locked = advance_missile_guidance_step(
        passive_without_support.resulting_state,
        passive_catalog,
        runtime_input(passive_state.projectile_id),
        position_xy=(10.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="upper",
        tactical_time_s=0.1,
        duration_s=0.1,
    )
    assert passive_locked.resulting_state.phase == "tracking"
    passive_lost = advance_missile_guidance_step(
        passive_locked.resulting_state,
        passive_catalog,
        runtime_input(passive_state.projectile_id, illumination=False),
        position_xy=(20.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="upper",
        tactical_time_s=0.2,
        duration_s=0.1,
    )
    assert passive_lost.resulting_state.phase == "lost"
    assert passive_lost.events[0].reason == "target_lost"

    anti_catalog = profile_catalog_for(
        source,
        seeker_kind="anti_radiation",
        launch_support="optional_fire_control",
        activation_distance_m=None,
        allowed_height_layers=["upper", "cloud", "rain"],
    )
    anti_state = initial_state(anti_catalog, "projectile.fixture.guidance.anti_radiation")
    anti_locked = advance_missile_guidance_step(
        anti_state,
        anti_catalog,
        runtime_input(anti_state.projectile_id, track=False, emitting=True),
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="rain",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert anti_locked.resulting_state.phase == "tracking"
    anti_memory = advance_missile_guidance_step(
        anti_locked.resulting_state,
        anti_catalog,
        runtime_input(anti_state.projectile_id, track=True, emitting=False),
        position_xy=(10.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(110.0, 0.0),
        target_height_layer="rain",
        tactical_time_s=0.1,
        duration_s=0.1,
    )
    assert anti_memory.resulting_state.phase == "memory"

    optical_catalog = profile_catalog_for(
        source,
        seeker_kind="electro_optical",
        launch_support="optional_fire_control",
        activation_distance_m=None,
        allowed_height_layers=["upper"],
    )
    optical_state = initial_state(optical_catalog, "projectile.fixture.guidance.optical")
    optical_cloud = advance_missile_guidance_step(
        optical_state,
        optical_catalog,
        runtime_input(optical_state.projectile_id),
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="cloud",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert optical_cloud.resulting_state.phase == "searching"
    optical_upper = advance_missile_guidance_step(
        optical_cloud.resulting_state,
        optical_catalog,
        runtime_input(optical_state.projectile_id),
        position_xy=(10.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(100.0, 0.0),
        target_height_layer="upper",
        tactical_time_s=0.1,
        duration_s=0.1,
    )
    assert optical_upper.resulting_state.phase == "tracking"

    active_search_catalog = profile_catalog_for(
        source,
        seeker_kind="active_radar",
        launch_support="optional_fire_control",
        activation_distance_m=None,
        allowed_height_layers=["upper", "cloud", "rain"],
    )
    active_search_state = initial_state(
        active_search_catalog,
        "projectile.fixture.guidance.search_range",
    )
    outside_search_range = advance_missile_guidance_step(
        active_search_state,
        active_search_catalog,
        runtime_input(active_search_state.projectile_id),
        position_xy=(0.0, 0.0),
        velocity_xy=(100.0, 0.0),
        target_position_xy=(6000.0, 0.0),
        target_height_layer="upper",
        tactical_time_s=0.0,
        duration_s=0.1,
    )
    assert outside_search_range.resulting_state.phase == "searching"

    require_contract_error(
        "missile_guidance.runtime_input_binding",
        lambda: advance_missile_guidance_step(
            active_state,
            active_catalog,
            runtime_input("projectile.fixture.guidance.other"),
            position_xy=(0.0, 0.0),
            velocity_xy=(100.0, 0.0),
            target_position_xy=(100.0, 0.0),
            target_height_layer="upper",
            tactical_time_s=0.0,
            duration_s=0.1,
        ),
    )
    invalid_profile = deepcopy(source["profiles"][0])
    invalid_profile["target_memory_s"] = 6.0
    invalid_profile["self_destruct_delay_s"] = 5.0
    require_contract_error(
        "missile_guidance.memory_after_self_destruct",
        lambda: MissileGuidanceProfile.parse(invalid_profile, "$.profile"),
    )

    # 统一场景把显式事实传入弹丸世界，并向上返回状态转换事件。
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    chain = build_chain("conventional_crewed")
    bindings, scene, directive = scene_fixture(chain, timing_catalog, projectile_catalog)
    require_contract_error(
        "missile_guidance.runtime_input_missing",
        lambda: advance_tactical_scene_step(
            scene,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            guidance_catalog=active_catalog,
            launch_directives=(directive,),
        ),
    )
    scene_step = advance_tactical_scene_step(
        scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        guidance_catalog=active_catalog,
        guidance_inputs=(runtime_input(directive.projectile_id),),
        launch_directives=(directive,),
    )
    assert [item.reason for item in scene_step.guidance_events] == [
        "seeker_activated",
        "target_acquired",
    ]
    guided = scene_step.resulting_scene.projectile_world.projectiles[0]
    assert guided.guidance_state is not None
    assert guided.guidance_state.phase == "tracking"

    # 自毁期限由弹丸世界消费为确定性过期事件；事件时间落在固定子步边界。
    scene_time = scene_step.resulting_scene.tactical_time_s
    lost_state = replace(
        guided.guidance_state,
        phase="lost",
        updated_time_s=scene_time,
        tracked_target_ship_id=None,
        target_lost_time_s=scene_time,
        self_destruct_deadline_s=scene_time + 0.01,
    )
    lost_world = replace(
        scene_step.resulting_scene.projectile_world,
        projectiles=(replace(guided, guidance_state=lost_state),),
    )
    target_ship = next(
        item
        for item in scene_step.resulting_scene.ships
        if item.ship_id == guided.target_ship_id
    )
    target_binding = next(item for item in bindings if item.ship_id == guided.target_ship_id)
    motion = target_ship.motion_state
    target = TacticalProjectileTarget(
        target_ship.ship_id,
        target_binding.snapshot,
        target_ship.combat_state,
        ShipPose2D(
            scene_time,
            (motion.position_world_m.x, motion.position_world_m.y),
            motion.heading_rad,
            (motion.velocity_world_mps.x, motion.velocity_world_mps.y),
            motion.yaw_rate_radps,
        ),
        density_kg_m3=0.0,
        sound_speed_mps=340.0,
        height_layer=motion.height_layer,
    )
    destructed = advance_projectile_world(
        lost_world,
        projectile_catalog,
        (target,),
        registry,
        target_tactical_time_s=scene_time + 0.02,
        density_kg_m3=0.0,
        sound_speed_mps=340.0,
        fixed_step_s=0.01,
        guidance_catalog=active_catalog,
        guidance_inputs=(runtime_input(guided.id, track=False),),
    )
    assert not destructed.resulting_world.projectiles
    assert destructed.expired_events[0].reason == "guidance_self_destruct"
    assert destructed.guidance_events[-1].reason == "self_destruct_deadline_reached"
    require_contract_error(
        "missile_guidance.catalog_required",
        lambda: advance_projectile_world(
            lost_world,
            projectile_catalog,
            (target,),
            registry,
            target_tactical_time_s=scene_time + 0.01,
            density_kg_m3=0.0,
            sound_speed_mps=340.0,
            fixed_step_s=0.01,
        ),
    )

    report = {
        "deterministic_repeat_equal": repeated == acquired,
        "catalog": {
            "reference": active_catalog.reference.to_dict(),
            "sha256": active_catalog.source_sha256,
        },
        "interface": MISSILE_GUIDANCE_RUNTIME_INTERFACE_ID,
        "policy": MISSILE_GUIDANCE_RUNTIME_POLICY_ID,
        "scene_guidance_events": [item.to_dict() for item in scene_step.guidance_events],
        "self_destruct_events": {
            "expired": [item.to_dict() for item in destructed.expired_events],
            "guidance": [item.to_dict() for item in destructed.guidance_events],
        },
        "status": "PASS",
        "tested_error_codes": [
            "missile_guidance.memory_after_self_destruct",
            "missile_guidance.catalog_required",
            "missile_guidance.runtime_input_binding",
            "missile_guidance.runtime_input_missing",
        ],
        "tested_runtime_paths": [
            "active_radar_activation_tracking_memory_reacquisition_lost_self_destruct",
            "passive_radar_continuous_illumination",
            "active_radar_search_range_gate",
            "anti_radiation_emitter_loss_memory",
            "electro_optical_height_layer_gate",
            "projectile_world_fixed_substep_steering_and_expiration",
            "unified_tactical_scene_event_propagation",
        ],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
