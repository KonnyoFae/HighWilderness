"""阶段 I11b：显式人员伤亡、持久账本与运行时回写回归。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import (
    ContractError,
    CrewCasualtyStatusInput,
    ShipCrewCasualtyStateInput,
    ShipInstanceSnapshotInput,
    canonical_json,
    load_material_registry,
)
from 高天荒野舰艇阶段F三舰集成测试 import build_chain
from 高天荒野舰艇阶段I武器时间与射击队列测试 import live_ship
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture
from 高天荒野舰艇阶段I持续毁伤与损管测试 import (
    find_impact_boundary,
    fire_projectile_catalog,
    scene_ship,
)
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
from 高天荒野舰艇武器时间与射击队列 import (
    load_weapon_timing_profile_catalog,
)
from 高天荒野舰艇持续毁伤 import (
    FireIgnitionOutcome,
    load_continuous_damage_profile,
)
from 高天荒野舰艇人员伤亡 import (
    CREW_CASUALTY_INTERFACE_ID,
    CREW_CASUALTY_POLICY_ID,
    CrewCasualtyBreakdown,
    CrewCasualtyOutcome,
    apply_crew_casualty_outcomes,
    persons_aboard_count,
    validate_crew_casualty_capacity,
    validate_instance_crew_casualty_state,
)
from 高天荒野舰艇实例设计状态 import transition_current_design
from 高天荒野舰艇统一战术场景 import (
    TacticalSceneState,
    advance_tactical_scene_step,
)


ROOT = Path(__file__).resolve().parent
MAIN_SCHEMA = ROOT / "舰艇数据" / "模式" / "高天荒野舰艇数据契约.v1alpha1.schema.json"
CONTINUOUS_PROFILE = ROOT / "舰艇数据" / "标定" / "阶段I持续毁伤技术替身配置.v1.json"
TIMING_CATALOG = ROOT / "舰艇数据" / "标定" / "阶段I武器时间技术替身配置.v1.json"
STRUCTURE_CATALOG = ROOT / "舰艇数据" / "材料" / "结构材质.v1.json"
ARMOR_CATALOG = ROOT / "舰艇数据" / "材料" / "基础装甲材质.v1.json"
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段I人员伤亡与运行时回写接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def status_map(instance: ShipInstanceSnapshotInput):
    state = instance.crew_casualty_state
    assert state is not None
    return {item.crew_type: item for item in state.crew_statuses}


def main() -> None:
    schema = json.loads(MAIN_SCHEMA.read_text(encoding="utf-8"))
    assert "crewCasualtyStatus" in schema["$defs"]
    assert "shipCrewCasualtyState" in schema["$defs"]
    chain = build_chain("conventional_crewed")
    sortie, legacy_instance = live_ship(chain)
    assert "crew_casualty_state" not in legacy_instance.to_dict()

    direct_outcome = CrewCasualtyOutcome(
        "casualty.fixture.stage_i11b.direct",
        "projectile_impact",
        "projectile.fixture.stage_i11b.direct",
        0.1,
        "ship.fixture.stage_i11b.direct",
        "damage_control",
        (
            CrewCasualtyBreakdown("ordinary", 0, 1),
            CrewCasualtyBreakdown("veteran_damage_control", 1, 0),
        ),
    )
    direct = apply_crew_casualty_outcomes(
        legacy_instance,
        (direct_outcome,),
        ship_id="ship.fixture.stage_i11b.direct",
        target_tactical_time_s=0.2,
    )
    repeated = apply_crew_casualty_outcomes(
        legacy_instance,
        (direct_outcome,),
        ship_id="ship.fixture.stage_i11b.direct",
        target_tactical_time_s=0.2,
    )
    assert repeated == direct
    direct_status = status_map(direct.resulting_instance)
    assert direct_status["ordinary"].fit_for_duty_count == 9
    assert direct_status["ordinary"].dead_count == 1
    assert direct_status["veteran_damage_control"].fit_for_duty_count == 1
    assert direct_status["veteran_damage_control"].wounded_count == 1
    assert dict(
        (item.crew_type, item.count)
        for item in direct.resulting_instance.operational_state.crew
    )["veteran_damage_control"] == 1
    validate_instance_crew_casualty_state(direct.resulting_instance)

    serialized = canonical_json(direct.resulting_instance)
    restored = ShipInstanceSnapshotInput.parse(json.loads(serialized))
    assert canonical_json(restored) == serialized
    advanced_clock = apply_crew_casualty_outcomes(
        direct.resulting_instance,
        (),
        ship_id="ship.fixture.stage_i11b.direct",
        target_tactical_time_s=0.3,
    )
    assert advanced_clock.resulting_instance.crew_casualty_state is not None
    assert advanced_clock.resulting_instance.crew_casualty_state.tactical_time_s == 0.3
    assert not advanced_clock.events

    base_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        legacy_instance,
        active_automatic_events=("ship.damage_control_required",),
    )
    casualty_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        direct.resulting_instance,
        active_automatic_events=("ship.damage_control_required",),
    )
    assert base_runtime.module("damage_control").function_efficiency(
        "damage_control.firefighting"
    ) == 1.0
    assert casualty_runtime.module("damage_control").function_efficiency(
        "damage_control.firefighting"
    ) == 0.5

    require_contract_error(
        "crew_casualty.insufficient_fit_crew",
        lambda: apply_crew_casualty_outcomes(
            legacy_instance,
            (
                CrewCasualtyOutcome(
                    "casualty.fixture.stage_i11b.overdraw",
                    "projectile_impact",
                    "projectile.fixture.stage_i11b.overdraw",
                    0.1,
                    "ship.fixture.stage_i11b.direct",
                    None,
                    (CrewCasualtyBreakdown("ordinary", 11, 0),),
                ),
            ),
            ship_id="ship.fixture.stage_i11b.direct",
            target_tactical_time_s=0.2,
        ),
    )
    require_contract_error(
        "crew_casualty.source_duplicate",
        lambda: apply_crew_casualty_outcomes(
            legacy_instance,
            (
                direct_outcome,
                replace(
                    direct_outcome,
                    outcome_id="casualty.fixture.stage_i11b.direct.duplicate",
                ),
            ),
            ship_id="ship.fixture.stage_i11b.direct",
            target_tactical_time_s=0.2,
        ),
    )
    require_contract_error(
        "crew_casualty.time_reversed",
        lambda: apply_crew_casualty_outcomes(
            direct.resulting_instance,
            (),
            ship_id="ship.fixture.stage_i11b.direct",
            target_tactical_time_s=0.1,
        ),
    )
    require_contract_error(
        "crew_casualty.source_before_state",
        lambda: apply_crew_casualty_outcomes(
            direct.resulting_instance,
            (
                replace(
                    direct_outcome,
                    outcome_id="casualty.fixture.stage_i11b.before_state",
                ),
            ),
            ship_id="ship.fixture.stage_i11b.direct",
            target_tactical_time_s=0.3,
        ),
    )
    mismatched = replace(
        direct.resulting_instance,
        crew_casualty_state=replace(
            direct.resulting_instance.crew_casualty_state,
            crew_statuses=tuple(
                CrewCasualtyStatusInput(
                    item.crew_type,
                    item.fit_for_duty_count + (1 if item.crew_type == "ordinary" else 0),
                    item.wounded_count,
                    item.dead_count,
                )
                for item in direct.resulting_instance.crew_casualty_state.crew_statuses
            ),
        ),
    )
    require_contract_error(
        "crew_casualty.fit_mismatch",
        lambda: validate_instance_crew_casualty_state(mismatched),
    )

    all_wounded_outcome = CrewCasualtyOutcome(
        "casualty.fixture.stage_i11b.all_wounded",
        "projectile_impact",
        "projectile.fixture.stage_i11b.all_wounded",
        0.1,
        "ship.fixture.stage_i11b.all_wounded",
        None,
        tuple(
            CrewCasualtyBreakdown(item.crew_type, item.count, 0)
            for item in legacy_instance.operational_state.crew
        ),
    )
    all_wounded = apply_crew_casualty_outcomes(
        legacy_instance,
        (all_wounded_outcome,),
        ship_id="ship.fixture.stage_i11b.all_wounded",
        target_tactical_time_s=0.1,
    ).resulting_instance
    assert not all_wounded.operational_state.crew
    assert persons_aboard_count(all_wounded) == sum(
        item.count for item in legacy_instance.operational_state.crew
    )
    all_wounded_runtime = compile_runtime_ship_parameters(
        chain.snapshot,
        sortie,
        all_wounded,
    )
    assert all_wounded_runtime.crew_safety_lock_enabled
    assert all_wounded_runtime.module("cic").manual_staffing_fraction == 0.0
    assert all_wounded_runtime.cic_control_available
    require_contract_error(
        "crew_casualty.capacity_exceeded",
        lambda: validate_crew_casualty_capacity(
            all_wounded,
            {
                item.crew_type: 0
                for item in all_wounded.crew_casualty_state.crew_statuses
            },
        ),
    )

    unmanned_chain = build_chain("unmanned_flagship")
    unmanned_sortie = unmanned_chain.sortie
    unmanned_instance = unmanned_chain.instance
    assert not unmanned_instance.operational_state.crew
    assert not compile_runtime_ship_parameters(
        unmanned_chain.snapshot,
        unmanned_sortie,
        unmanned_instance,
    ).crew_safety_lock_enabled
    require_contract_error(
        "crew_casualty.insufficient_fit_crew",
        lambda: apply_crew_casualty_outcomes(
            unmanned_instance,
            (
                CrewCasualtyOutcome(
                    "casualty.fixture.stage_i11b.unmanned",
                    "projectile_impact",
                    "projectile.fixture.stage_i11b.unmanned",
                    0.1,
                    "ship.fixture.stage_i11b.unmanned",
                    None,
                    (CrewCasualtyBreakdown("ordinary", 1, 0),),
                ),
            ),
            ship_id="ship.fixture.stage_i11b.unmanned",
            target_tactical_time_s=0.1,
        ),
    )
    transitioned = transition_current_design(
        chain.snapshot,
        direct.resulting_instance,
        chain.snapshot,
    )
    assert transitioned.crew_casualty_state == direct.resulting_instance.crew_casualty_state

    # 实际命中来源：同一步伤亡在步末生命周期和运行时重编译前生效。
    timing_catalog = load_weapon_timing_profile_catalog(TIMING_CATALOG)
    projectile_catalog = fire_projectile_catalog()
    profile = load_continuous_damage_profile(CONTINUOUS_PROFILE)
    registry = load_material_registry((STRUCTURE_CATALOG, ARMOR_CATALOG))
    bindings, initial_scene, launch_directive = scene_fixture(
        chain,
        timing_catalog,
        projectile_catalog,
    )
    before_impact, probe, impact_launch = find_impact_boundary(
        initial_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        profile,
        launch_directive,
    )
    impact = probe.impact_events[0]
    impacted_module = impact.damaged_module_instance_ids[0]
    scene_outcome = CrewCasualtyOutcome(
        "casualty.fixture.stage_i11b.scene_impact",
        "projectile_impact",
        impact.projectile_id,
        impact.tactical_time_s,
        impact.target_ship_id,
        impacted_module,
        (CrewCasualtyBreakdown("officer", 2, 0),),
    )
    casualty_scene = advance_tactical_scene_step(
        before_impact,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        crew_casualty_outcomes=(scene_outcome,),
        launch_directives=impact_launch,
    )
    assert len(casualty_scene.crew_casualty_events) == 1
    assert scene_ship(
        probe.resulting_scene,
        impact.target_ship_id,
    ).combat_state.instance.crew_casualty_state is None
    casualty_target = scene_ship(casualty_scene.resulting_scene, impact.target_ship_id)
    casualty_target_status = status_map(casualty_target.combat_state.instance)
    assert casualty_target_status["officer"].fit_for_duty_count == 0
    assert casualty_target_status["officer"].wounded_count == 2
    assert casualty_target.lifecycle_state.command_status == "scene_command"
    target_result = next(
        item
        for item in casualty_scene.ship_results
        if item.ship_id == impact.target_ship_id
    )
    assert target_result.resulting_runtime.crew_safety_lock_enabled
    assert target_result.resulting_runtime.module("cic").manual_staffing_fraction == 0.0
    assert target_result.resulting_runtime.cic_control_available
    serialized_scene = canonical_json(casualty_scene.resulting_scene)
    restored_scene = TacticalSceneState.parse(json.loads(serialized_scene))
    assert canonical_json(restored_scene) == serialized_scene

    require_contract_error(
        "crew_casualty.source_module_unmatched",
        lambda: advance_tactical_scene_step(
            before_impact,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            crew_casualty_outcomes=(
                replace(
                    scene_outcome,
                    outcome_id="casualty.fixture.stage_i11b.bad_module",
                    target_module_instance_id="damage_control",
                ),
            ),
            launch_directives=impact_launch,
        ),
    )
    require_contract_error(
        "crew_casualty.impact_source_unmatched",
        lambda: advance_tactical_scene_step(
            before_impact,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            crew_casualty_outcomes=(
                replace(
                    scene_outcome,
                    outcome_id="casualty.fixture.stage_i11b.bad_source",
                    source_id="projectile.fixture.stage_i11b.missing",
                ),
            ),
            launch_directives=impact_launch,
        ),
    )

    # 既有火灾的本步实际毁伤事件可以作为伤亡来源；fire_started 不能旁路。
    ignition = FireIgnitionOutcome(
        impact.projectile_id,
        "fire.fixture.stage_i11b.scene",
        impact.target_ship_id,
        impacted_module,
        1.0,
        10.0,
    )
    ignited = advance_tactical_scene_step(
        before_impact,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        fire_ignition_outcomes=(ignition,),
        launch_directives=impact_launch,
    )
    fire_probe = advance_tactical_scene_step(
        ignited.resulting_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
    )
    fire_damage = next(
        item
        for item in fire_probe.continuous_damage_events
        if item.event_kind == "fire_damage_applied"
    )
    fire_outcome = CrewCasualtyOutcome(
        "casualty.fixture.stage_i11b.fire",
        "fire_damage",
        fire_damage.fire_incident_id,
        fire_damage.tactical_time_s,
        fire_damage.ship_id,
        fire_damage.target_module_instance_id,
        (CrewCasualtyBreakdown("veteran_damage_control", 1, 0),),
    )
    fire_casualty = advance_tactical_scene_step(
        ignited.resulting_scene,
        bindings,
        timing_catalog,
        projectile_catalog,
        registry,
        continuous_damage_profile=profile,
        crew_casualty_outcomes=(fire_outcome,),
    )
    assert [item.source_kind for item in fire_casualty.crew_casualty_events] == [
        "fire_damage"
    ]
    assert status_map(
        scene_ship(
            fire_casualty.resulting_scene,
            fire_damage.ship_id,
        ).combat_state.instance
    )["veteran_damage_control"].wounded_count == 1
    require_contract_error(
        "crew_casualty.fire_source_unmatched",
        lambda: advance_tactical_scene_step(
            before_impact,
            bindings,
            timing_catalog,
            projectile_catalog,
            registry,
            continuous_damage_profile=profile,
            fire_ignition_outcomes=(ignition,),
            crew_casualty_outcomes=(
                replace(
                    fire_outcome,
                    outcome_id="casualty.fixture.stage_i11b.fire_started_only",
                    source_tactical_time_s=impact.tactical_time_s,
                ),
            ),
            launch_directives=impact_launch,
        ),
    )

    report = {
        "deterministic_repeat_equal": repeated == direct,
        "direct_events": [item.to_dict() for item in direct.events],
        "fire_events": [item.to_dict() for item in fire_casualty.crew_casualty_events],
        "interface": CREW_CASUALTY_INTERFACE_ID,
        "legacy_instance_shape_preserved": "crew_casualty_state"
        not in legacy_instance.to_dict(),
        "policy": CREW_CASUALTY_POLICY_ID,
        "scene_events": [item.to_dict() for item in casualty_scene.crew_casualty_events],
        "status": "PASS",
        "tested_error_codes": [
            "crew_casualty.fire_source_unmatched",
            "crew_casualty.capacity_exceeded",
            "crew_casualty.fit_mismatch",
            "crew_casualty.impact_source_unmatched",
            "crew_casualty.insufficient_fit_crew",
            "crew_casualty.source_before_state",
            "crew_casualty.source_duplicate",
            "crew_casualty.source_module_unmatched",
            "crew_casualty.time_reversed",
        ],
        "tested_paths": [
            "explicit_projectile_casualty",
            "explicit_fire_casualty",
            "persistent_fit_wounded_dead_ledger",
            "runtime_staffing_reduction",
            "wounded_people_keep_safety_lock",
            "automated_cic_survives_officer_casualties",
            "uncrewed_ship_rejects_casualty",
            "refit_preserves_ledger",
            "scene_save_reload",
        ],
    }
    REPORT_PATH.write_text(canonical_json(report), encoding="utf-8")
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8"))["status"] == "PASS"


if __name__ == "__main__":
    main()
