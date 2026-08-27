"""阶段 I12a：战术观测、雷达辐射、火控通道与制导事实闭环回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    ShipInstanceSnapshotInput,
    canonical_sha256,
    load_material_registry,
)
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇阶段I战术舰队指挥测试 import (
    BOUNDARY_PROFILE,
    COMMAND_TUNING,
    ENEMY,
    INITIATOR,
    MAIN,
    command_scene,
    role_assignments,
)
from 高天荒野舰艇武器时间与射击队列 import (
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
    initialize_missile_guidance_state,
    load_missile_guidance_profile_catalog,
)
from 高天荒野舰艇战术弹丸世界 import (
    ProjectileState,
    load_projectile_profile_catalog,
)
from 高天荒野舰艇统一战术场景 import (
    advance_tactical_scene_step,
    load_tactical_engagement_boundary_profile,
)
from 高天荒野舰艇战术舰队指挥 import (
    advance_commanded_tactical_scene_step,
    initialize_tactical_fleet_command_state,
    load_tactical_command_tuning_profile,
)
from 高天荒野舰艇战术观测与火控 import (
    TACTICAL_OBSERVATION_INTERFACE_ID,
    TACTICAL_OBSERVATION_POLICY_ID,
    FireControlAssignment,
    ProjectileSeekerObservationOutcome,
    SensorObservationOutcome,
    TacticalObservationShipContext,
    TacticalObservationStepInput,
    generate_guidance_runtime_inputs,
    resolve_tactical_observation_step,
)


ROOT = Path(__file__).resolve().parent
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
PROJECTILE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I弹丸与损伤技术替身配置.v1.json"
GUIDANCE_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I导弹制导技术替身配置.v1.json"
OBSERVATION_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇战术观测与火控结果契约.v1alpha1.schema.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I战术观测与火控输入接口.v1.json"

SOURCE = "ship.fixture.i12a.source"
TARGET = "ship.fixture.i12a.target"
SENSOR = "sensor_upper_starboard"
FIRE_CONTROL = "fire_control"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def context(
    chain,
    ship_id: str,
    position_xy: tuple[float, float],
    *,
    instance: ShipInstanceSnapshotInput | None = None,
    active_events: tuple[str, ...] = (),
) -> TacticalObservationShipContext:
    current = chain.instance if instance is None else instance
    runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        chain.sortie,
        current,
        active_automatic_events=active_events,
    )
    return TacticalObservationShipContext(
        ship_id,
        chain.snapshot,
        runtime,
        position_xy,
        "operational",
    )


def observation(
    outcome_id: str,
    observer_ship_id: str,
    target_ship_id: str,
    *,
    mode: str = "fire_control_lock",
    available: bool = True,
    reason: str = "observed",
) -> SensorObservationOutcome:
    return SensorObservationOutcome(
        outcome_id,
        0.0,
        observer_ship_id,
        target_ship_id,
        SENSOR,
        mode,
        available,
        reason,
    )


def assignment(
    assignment_id: str,
    source_ship_id: str,
    target_ship_id: str,
    requirements: tuple[str, ...] = ("solution",),
) -> FireControlAssignment:
    return FireControlAssignment(
        assignment_id,
        0.0,
        source_ship_id,
        target_ship_id,
        SENSOR,
        FIRE_CONTROL,
        requirements,
    )


def disabled_sensor_instance(chain) -> ShipInstanceSnapshotInput:
    source = chain.instance.to_dict()
    next(
        item
        for item in source["module_states"]
        if item["instance_id"] == SENSOR
    )["operating_mode"] = "off"
    return ShipInstanceSnapshotInput.parse(source)


def guidance_fixture():
    base = load_missile_guidance_profile_catalog(GUIDANCE_CATALOG)
    template = base.profiles[0]
    profiles = []
    projectiles = []
    for seeker_kind in (
        "active_radar",
        "anti_radiation",
        "electro_optical",
        "passive_radar",
    ):
        munition_id = f"gtw.munition.fixture.i12a.{seeker_kind}"
        profile = replace(
            template,
            munition_id=munition_id,
            seeker_kind=seeker_kind,
            launch_support=(
                "continuous_illumination"
                if seeker_kind == "passive_radar"
                else "optional_fire_control"
            ),
            activation_distance_m=None,
        )
        profiles.append(profile)
    catalog = MissileGuidanceProfileCatalog(
        "gtw.missile_guidance.fixture.i12a",
        1,
        "阶段I12a四导引头事实夹具",
        "contract_fixture",
        tuple(sorted(profiles, key=lambda item: item.munition_id)),
    )
    for profile in catalog.profiles:
        projectile_id = f"projectile.fixture.i12a.{profile.seeker_kind}"
        state = initialize_missile_guidance_state(
            catalog,
            projectile_id=projectile_id,
            munition_id=profile.munition_id,
            source_ship_id=SOURCE,
            intended_target_ship_id=TARGET,
            launch_time_s=0.0,
        )
        assert state is not None
        projectiles.append(
            ProjectileState(
                projectile_id,
                SOURCE,
                "weapon_upper_port",
                profile.munition_id,
                TARGET,
                0,
                0.0,
                0.0,
                (0.0, 0.0),
                (100.0, 0.0),
                0.0,
                state,
            )
        )
    return catalog, tuple(projectiles)


def main() -> None:
    schema = json.loads(OBSERVATION_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$id"] == TACTICAL_OBSERVATION_INTERFACE_ID
    empty_shape = TacticalObservationStepInput().to_dict()
    assert empty_shape["interface"] == TACTICAL_OBSERVATION_INTERFACE_ID
    assert empty_shape["policy"] == TACTICAL_OBSERVATION_POLICY_ID

    chain = build_chain("conventional_crewed")
    active_events = ("ship.fire_control_required", "ship.sensor_scan_required")
    contexts = (
        context(chain, SOURCE, (0.0, 0.0), active_events=active_events),
        context(chain, TARGET, (1000.0, 0.0), active_events=active_events),
    )

    # 实际雷达锁定与火控模块共同形成可追溯通道；重复执行完全一致。
    direct_input = TacticalObservationStepInput(
        sensor_observation_outcomes=(
            observation("observation.fixture.i12a.lock", SOURCE, TARGET),
            observation(
                "observation.fixture.i12a.target_emission",
                TARGET,
                SOURCE,
                mode="active_search",
                available=False,
                reason="electronic_interference",
            ),
        ),
        fire_control_assignments=(
            assignment(
                "assignment.fixture.i12a.continuous",
                SOURCE,
                TARGET,
                ("solution", "continuous_guidance"),
            ),
        ),
    )
    direct = resolve_tactical_observation_step(
        contexts,
        direct_input,
        tactical_time_s=0.0,
    )
    repeated = resolve_tactical_observation_step(
        contexts,
        direct_input,
        tactical_time_s=0.0,
    )
    assert repeated == direct
    assert len(direct.observation_events) == 2
    assert len(direct.radar_emission_events) == 2
    assert direct.radar_emission_events[1].emitter_ship_id == TARGET
    assert direct.fire_control_support_events[0].requirements == (
        "continuous_guidance",
        "solution",
    )

    # 四类导引头事实来自弹载结果、母舰持续照射或目标真实辐射，不再手填布尔值。
    guidance_catalog, projectiles = guidance_fixture()
    generated = generate_guidance_runtime_inputs(
        projectiles,
        contexts,
        guidance_catalog,
        direct,
        (
            ProjectileSeekerObservationOutcome(
                "observation.fixture.i12a.active_seeker",
                0.0,
                "projectile.fixture.i12a.active_radar",
                TARGET,
                True,
                "observed",
            ),
            ProjectileSeekerObservationOutcome(
                "observation.fixture.i12a.optical_jammed",
                0.0,
                "projectile.fixture.i12a.electro_optical",
                TARGET,
                False,
                "electronic_interference",
            ),
        ),
        tactical_time_s=0.0,
    )
    generated_by_kind = {
        item.seeker_kind: item for item in generated.events
    }
    assert generated_by_kind["active_radar"].target_track_available
    assert generated_by_kind["anti_radiation"].target_radar_emitting
    assert not generated_by_kind["electro_optical"].target_track_available
    assert generated_by_kind["passive_radar"].target_track_available
    assert generated_by_kind["passive_radar"].continuous_illumination_available

    # 显式关闭无法被自动事件唤醒；仪表距离与火控通道容量均严格门禁。
    off_contexts = (
        context(
            chain,
            SOURCE,
            (0.0, 0.0),
            instance=disabled_sensor_instance(chain),
            active_events=active_events,
        ),
        contexts[1],
    )
    require_contract_error(
        "tactical_observation.sensor_unavailable",
        lambda: resolve_tactical_observation_step(
            off_contexts,
            TacticalObservationStepInput(
                sensor_observation_outcomes=(
                    observation("observation.fixture.i12a.off", SOURCE, TARGET),
                ),
            ),
            tactical_time_s=0.0,
        ),
    )
    far_contexts = (
        contexts[0],
        context(chain, TARGET, (100001.0, 0.0), active_events=active_events),
    )
    require_contract_error(
        "tactical_observation.sensor_out_of_range",
        lambda: resolve_tactical_observation_step(
            far_contexts,
            TacticalObservationStepInput(
                sensor_observation_outcomes=(
                    observation("observation.fixture.i12a.far", SOURCE, TARGET),
                ),
            ),
            tactical_time_s=0.0,
        ),
    )
    channel_targets = tuple(
        context(
            chain,
            f"ship.fixture.i12a.channel_target_{index}",
            (1000.0 * index, 0.0),
            active_events=active_events,
        )
        for index in range(1, 4)
    )
    channel_observations = tuple(
        observation(
            f"observation.fixture.i12a.channel_{index}",
            SOURCE,
            target.ship_id,
            mode="track",
        )
        for index, target in enumerate(channel_targets, 1)
    )
    channel_assignments = tuple(
        assignment(
            f"assignment.fixture.i12a.channel_{index}",
            SOURCE,
            target.ship_id,
        )
        for index, target in enumerate(channel_targets, 1)
    )
    require_contract_error(
        "tactical_observation.fire_control_channels_exceeded",
        lambda: resolve_tactical_observation_step(
            (contexts[0], *channel_targets),
            TacticalObservationStepInput(
                channel_observations,
                channel_assignments,
            ),
            tactical_time_s=0.0,
        ),
    )

    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = load_projectile_profile_catalog(PROJECTILE_CATALOG)
    scene_guidance_catalog = load_missile_guidance_profile_catalog(GUIDANCE_CATALOG)
    bindings, scene, directive = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    scene_input = TacticalObservationStepInput(
        sensor_observation_outcomes=(
            observation(
                "observation.fixture.i12a.scene_lock",
                directive.source_ship_id,
                directive.target_ship_id,
            ),
        ),
        fire_control_assignments=(
            assignment(
                "assignment.fixture.i12a.scene_solution",
                directive.source_ship_id,
                directive.target_ship_id,
            ),
        ),
        seeker_observation_outcomes=(
            ProjectileSeekerObservationOutcome(
                "observation.fixture.i12a.scene_seeker",
                0.0,
                directive.projectile_id,
                directive.target_ship_id,
                True,
                "observed",
            ),
        ),
    )
    scene_result = advance_tactical_scene_step(
        scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        guidance_catalog=scene_guidance_catalog,
        observation_step_input=scene_input,
        launch_directives=(directive,),
    )
    scene_repeat = advance_tactical_scene_step(
        scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        guidance_catalog=scene_guidance_catalog,
        observation_step_input=scene_input,
        launch_directives=(directive,),
    )
    assert scene_repeat == scene_result
    assert scene_result.generated_guidance_fact_events[0].target_track_available
    assert any(item.reason == "target_acquired" for item in scene_result.guidance_events)
    scene_output = scene_result.to_dict()
    assert "sensor_observation_events" in scene_output
    assert "generated_guidance_fact_events" in scene_output
    lost_support = advance_tactical_scene_step(
        scene_result.resulting_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        guidance_catalog=scene_guidance_catalog,
        observation_step_input=TacticalObservationStepInput(),
    )
    assert not lost_support.generated_guidance_fact_events[0].target_track_available
    assert any(
        item.reason in {"target_lost", "target_lost_memory"}
        for item in lost_support.guidance_events
    )
    require_contract_error(
        "tactical_observation.weapon_fire_control_support_missing",
        lambda: advance_tactical_scene_step(
            scene,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            guidance_catalog=scene_guidance_catalog,
            observation_step_input=replace(
                scene_input,
                fire_control_assignments=(),
            ),
            launch_directives=(directive,),
        ),
    )
    require_contract_error(
        "tactical_observation.manual_guidance_mixed",
        lambda: advance_tactical_scene_step(
            scene,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            guidance_catalog=scene_guidance_catalog,
            guidance_inputs=(
                MissileGuidanceRuntimeInput(
                    directive.projectile_id,
                    True,
                    False,
                    False,
                ),
            ),
            observation_step_input=scene_input,
            launch_directives=(directive,),
        ),
    )

    # 舰队指挥包装层只透传同一输入，场景事件保持一致语义。
    boundary_profile = load_tactical_engagement_boundary_profile(BOUNDARY_PROFILE)
    tuning = load_tactical_command_tuning_profile(COMMAND_TUNING)
    command_bindings, command_state_scene = command_scene(
        chain,
        timing_catalog,
        projectile_catalog,
        boundary_profile,
    )
    command_state = initialize_tactical_fleet_command_state(
        command_state_scene,
        tuning=tuning,
        player_side_id=INITIATOR,
        assignments=role_assignments(MAIN, "main_flagship"),
        direct_control_ship_id=MAIN,
    )
    command_observation = TacticalObservationStepInput(
        sensor_observation_outcomes=(
            observation(
                "observation.fixture.i12a.command",
                MAIN,
                ENEMY,
                mode="active_search",
                available=False,
                reason="not_observed",
            ),
        ),
    )
    commanded = advance_commanded_tactical_scene_step(
        command_state_scene,
        command_state,
        command_bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        tuning,
        observation_step_input=command_observation,
        engagement_boundary_profile=boundary_profile,
    )
    assert commanded.scene_resolution.sensor_observation_events[0].outcome_id == (
        "observation.fixture.i12a.command"
    )

    report = {
        "deterministic_repeat_equal": scene_repeat == scene_result,
        "direct_resolution": {
            "fire_control_support_events": [
                item.to_dict() for item in direct.fire_control_support_events
            ],
            "radar_emission_events": [
                item.to_dict() for item in direct.radar_emission_events
            ],
            "sensor_observation_events": [
                item.to_dict() for item in direct.observation_events
            ],
        },
        "generated_guidance_facts": [item.to_dict() for item in generated.events],
        "interface": TACTICAL_OBSERVATION_INTERFACE_ID,
        "policy": TACTICAL_OBSERVATION_POLICY_ID,
        "scene_result_sha256": canonical_sha256(scene_result.to_dict()),
        "status": "PASS",
        "tested_error_codes": [
            "tactical_observation.sensor_unavailable",
            "tactical_observation.sensor_out_of_range",
            "tactical_observation.fire_control_channels_exceeded",
            "tactical_observation.weapon_fire_control_support_missing",
            "tactical_observation.manual_guidance_mixed",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
