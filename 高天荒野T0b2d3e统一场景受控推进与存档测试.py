"""d3.3 第三阶段：v6 统一场景时序、最终边界反馈与受控存档连续性。"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from benchmarks.t0.metadata import file_sha256
from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases, PROFILE_PATH
from 高天荒野舰艇数据契约 import canonical_sha256, load_json
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇场景推进结果 import BoundaryScenePropulsionEvent
from 高天荒野舰艇受控推进场景 import (
    GovernedSceneSession,
    build_known_governed_scene,
    load_governed_scene_save,
    save_governed_scene,
    validate_governed_scene_step_payload,
)
from 高天荒野舰艇统一战术场景 import (
    BINDING_VALIDATION_TRUSTED,
    TacticalSceneExitDirective,
    TacticalSceneState,
    advance_tactical_scene_step,
    prepare_tactical_scene_bindings,
)
from 高天荒野舰艇战术弹丸世界 import ProjectileState
import 高天荒野舰艇受控推进无场景适配器 as adapter_module


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d3e统一场景受控推进与存档接口.v1.json"


@lru_cache(maxsize=1)
def governed_cases():
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    return tuple(
        (
            name,
            old,
            source,
            build_known_governed_scene(
                ROOT,
                name,
                source,
                old.bindings,
                profile,
            ),
        )
        for name, old, source in migrated_cases()
    )


def governed_case(name: str):
    return next(item for item in governed_cases() if item[0] == name)


def step(old, session: GovernedSceneSession, scene: TacticalSceneState, **extra):
    return advance_tactical_scene_step(
        scene,
        session.bindings,
        old.timing_catalog,
        old.projectile_catalog,
        old.material_registry,
        propulsion_context=session.propulsion_context,
        binding_validation_mode=BINDING_VALIDATION_TRUSTED,
        **extra,
    )


def check_named_builder_matrix():
    cases = governed_cases()
    ships = sum(len(session.scene.ships) for _, _, _, session in cases)
    actuators = sum(
        len(ship.propulsion_state.engines)
        for _, _, _, session in cases
        for ship in session.scene.ships
    )
    governors = sum(
        len(ship.propulsion_state.governors)
        for _, _, _, session in cases
        for ship in session.scene.ships
    )
    for name, _, _, session in cases:
        assert session.resource_bundle.scene_id == name
        assert session.scene.to_dict()["interface"] == (
            "gaotian.tactical-scene-timeline/v6alpha1"
        )
        assert all(
            governor.last_evaluated_step_index == 0
            for ship in session.scene.ships
            for governor in ship.propulsion_state.governors
        )
        assert TacticalSceneState.parse(session.scene.to_dict()) == session.scene
    assert (len(cases), ships, actuators, governors) == (12, 224, 1224, 1344)
    return {
        "scenes": len(cases),
        "ships": ships,
        "actuators": actuators,
        "governors": governors,
        "initial_governor_clock": 0,
    }


def check_first_command_and_contract():
    _, old, _, session = governed_case("functional_6.motion_only")
    target = session.scene.ships[0].ship_id
    control = directional_control(
        (ChannelPropulsionCommand("translation.forward", "full", None),)
    )
    with patch.object(
        adapter_module,
        "evaluate_whole_ship_propulsion_safety",
        wraps=adapter_module.evaluate_whole_ship_propulsion_safety,
    ) as safety_counter:
        result = step(
            old,
            session,
            session.scene,
            propulsion_controls={target: control},
        )
    payload = result.to_dict()
    validate_governed_scene_step_payload(
        payload,
        session.scene,
        result,
        session.propulsion_context,
    )
    assert payload["interface"] == "gaotian.tactical-scene-step-resolution/v5alpha1"
    assert len(result.propulsion_opening_records) == len(session.scene.ships)
    assert len(result.propulsion_closing_records) == len(session.scene.ships)
    assert safety_counter.call_count == len(session.scene.ships)
    assert result.propulsion_boundaries == ()
    assert all(
        item.diagnostics.to_dict()["soft_governor_status"] == "wired"
        for item in result.ship_results
    )
    assert all(
        item.diagnostics.diagnostic.active_force_body_n.length == 0
        for item in result.ship_results
    )
    assert all(
        governor.last_evaluated_step_index == 1
        for ship in result.resulting_scene.ships
        for governor in ship.propulsion_state.governors
    )
    target_opening = next(
        item for item in result.propulsion_opening_records if item.ship_id == target
    )
    assert target_opening.resulting_control == control
    assert target_opening.source_governors[0].safety_revision == (
        target_opening.resulting_governors[0].safety_revision
    )
    return {
        "opening_records": len(result.propulsion_opening_records),
        "closing_records": len(result.propulsion_closing_records),
        "closing_safety_evaluations": len(result.propulsion_closing_records),
        "first_interval_force_zero": True,
        "diagnostic_v2_wired": True,
    }


def check_joint_refusal_and_overg():
    _, old, _, session = governed_case("functional_6.motion_only")
    profile = replace(
        session.propulsion_context.safety_profile,
        structure_engage_ratio=100.0,
        structure_release_ratio=90.0,
        crew_engage_g=1.00000001,
        crew_release_g=1.000000005,
    )
    context = replace(session.propulsion_context, safety_profile=profile)
    scene = replace(
        session.scene,
        propulsion_safety_profile_sha256=profile.source_sha256,
    )
    target = scene.ships[0].ship_id
    commands = (ChannelPropulsionCommand("translation.forward", "full", None),)
    control = directional_control(commands)
    overg = directional_control(commands, overg_requested=True)
    results = []
    for index in range(5):
        requested = (
            {target: control}
            if index == 0
            else {target: overg}
            if index == 2
            else None
        )
        result = advance_tactical_scene_step(
            scene,
            session.bindings,
            old.timing_catalog,
            old.projectile_catalog,
            old.material_registry,
            propulsion_context=context,
            propulsion_controls=requested,
            binding_validation_mode=BINDING_VALIDATION_TRUSTED,
        )
        results.append(result)
        scene = result.resulting_scene
    refused = next(
        item for item in results[1].propulsion_closing_records if item.ship_id == target
    )
    main_results = tuple(
        item
        for item in refused.safety_result.engine_results
        if item.state.actuator_category == "main_engine"
    )
    assert len(main_results) == 2
    assert all(item.preview.candidate_state.actual_output_percent == 2 for item in main_results)
    assert all(item.upstage_rejected for item in main_results)
    assert any(
        tuple(value for actuator, value in sample.vector.outputs if actuator in {
            item.state.actuator_instance_id for item in main_results
        }) == (2, 2)
        for sample in refused.safety_result.load_samples
    )
    engaged = tuple(
        event.intent.event.kind
        for event in results[1].propulsion_safety_events
        if event.ship_id == target
    )
    released = tuple(
        event.intent.event.kind
        for event in results[2].propulsion_safety_events
        if event.ship_id == target
    )
    assert engaged == ("engine_safety_limit_engaged",)
    assert released == ("engine_safety_limit_released",)
    outputs = tuple(
        tuple(
            engine.actual_output_percent
            for engine in result.resulting_scene.ships[0].propulsion_state.engines
            if engine.actuator_category == "main_engine"
        )
        for result in results
    )
    assert outputs[:4] == ((0, 0), (0, 0), (0, 0), (0, 0))
    assert outputs[4] == (2, 2)
    assert refused.crew_safety_lock_enabled
    engine_events = tuple(
        BoundaryScenePropulsionEvent.parse(item)
        for item in results[4].to_dict()["propulsion_events"]
        if item["ship_id"] == target
    )
    assert len(engine_events) == 2
    assert all(
        item.boundary_phase == "closing"
        and item.event.kind == "engine_output_stage_changed"
        for item in engine_events
    )
    return {
        "joint_candidates": len(main_results),
        "joint_candidate_percent": 2,
        "joint_upstage_rejected": True,
        "crew_lock_bound_to_closing": True,
        "overg_release_boundary": 3,
        "first_post_release_upstage_boundary": 5,
        "actuator_events_after_release": len(engine_events),
    }


def _limited_scene(scene: TacticalSceneState) -> TacticalSceneState:
    target = scene.ships[0]
    governors = tuple(
        replace(
            governor,
            safety_ceiling_percent=25,
            safety_reasons=("crew_limit",),
            safety_limited_since_step=0,
            release_candidate_since_step=None,
            safety_revision=1,
        )
        if governor.command_channel == "translation.forward"
        else governor
        for governor in target.propulsion_state.governors
    )
    ships = (
        replace(
            target,
            propulsion_state=replace(target.propulsion_state, governors=governors),
        ),
        *scene.ships[1:],
    )
    return TacticalSceneState.parse(replace(scene, ships=ships).to_dict())


def check_save_reload_continuity():
    name, old, source, session = governed_case("functional_6.motion_only")
    limited = _limited_scene(session.scene)
    initial_save = save_governed_scene(limited, session.propulsion_context)
    initial_loaded = load_governed_scene_save(
        initial_save,
        root=ROOT,
        scene_id=name,
        source_scene=source,
        source_bindings=old.bindings,
        safety_profile=session.propulsion_context.safety_profile,
    )
    assert initial_loaded.scene == limited
    assert all(
        loaded.runtime_cache is not original.runtime_cache
        for loaded, original in zip(initial_loaded.bindings, session.bindings)
    )
    continuous_bindings = prepare_tactical_scene_bindings(limited, session.bindings)
    continuous_session = replace(session, scene=limited, bindings=continuous_bindings)
    first_continuous = step(old, continuous_session, limited)
    first_reloaded = step(old, initial_loaded, initial_loaded.scene)
    assert first_continuous.to_dict() == first_reloaded.to_dict()
    candidate_scene = first_continuous.resulting_scene
    candidate = next(
        governor
        for governor in candidate_scene.ships[0].propulsion_state.governors
        if governor.command_channel == "translation.forward"
    )
    assert candidate.safety_ceiling_percent == 25
    assert candidate.release_candidate_since_step == 1
    candidate_loaded = load_governed_scene_save(
        save_governed_scene(candidate_scene, session.propulsion_context),
        root=ROOT,
        scene_id=name,
        source_scene=source,
        source_bindings=old.bindings,
        safety_profile=session.propulsion_context.safety_profile,
    )
    continuous_bindings = prepare_tactical_scene_bindings(
        candidate_scene,
        session.bindings,
    )
    continuous_session = replace(
        session,
        scene=candidate_scene,
        bindings=continuous_bindings,
    )
    released_continuous = step(old, continuous_session, candidate_scene)
    released_reloaded = step(old, candidate_loaded, candidate_loaded.scene)
    assert released_continuous.to_dict() == released_reloaded.to_dict()
    release_events = tuple(
        event.intent.event.kind
        for event in released_continuous.propulsion_safety_events
        if event.ship_id == candidate_scene.ships[0].ship_id
    )
    assert release_events == ("engine_safety_limit_released",)
    return {
        "reload_boundaries": [0, 1],
        "limiting_state_equal": True,
        "release_candidate_state_equal": True,
        "release_event_equal": True,
        "runtime_caches_rebuilt": True,
        "released_result_sha256": canonical_sha256(released_continuous.to_dict()),
    }


def check_lifecycle_and_closing_damage():
    _, old, _, session = governed_case("functional_6.motion_only")
    target, shooter = session.scene.ships[:2]
    falling_ship = replace(
        target,
        combat_state=replace(
            target.combat_state,
            instance=replace(
                target.combat_state.instance,
                current_hull_integrity_fraction=0,
            ),
        ),
        motion_state=replace(target.motion_state, hull_integrity_fraction=0),
    )
    falling = replace(
        session.scene,
        ships=(falling_ship, *session.scene.ships[1:]),
    )
    fallen = step(old, session, falling)
    fallen_result = fallen.ship_results[0]
    assert fallen_result.propulsion_delivery_status == "suppressed_falling"
    assert fallen_result.diagnostics.diagnostic.active_force_body_n.length == 0
    assert fallen_result.diagnostics.diagnostic.fuel_units_consumed == 0

    exited = step(
        old,
        session,
        session.scene,
        exit_directives=(
            TacticalSceneExitDirective(
                target.ship_id,
                session.scene.tactical_time_s,
                "scripted_transfer",
            ),
        ),
    )
    assert exited.ship_results[0].propulsion_delivery_status == "suppressed_exited"
    assert exited.ship_results[0].diagnostics is None
    frozen = exited.resulting_scene.ships[0]
    next_exit = step(old, session, exited.resulting_scene)
    assert next_exit.resulting_scene.ships[0].propulsion_state == frozen.propulsion_state
    assert next_exit.resulting_scene.ships[0].propulsion_control == frozen.propulsion_control

    shot = ProjectileState(
        "projectile.d3e.near_hull",
        shooter.ship_id,
        "weapon_upper_port",
        "gtw.munition.fixture.76mm.standard",
        target.ship_id,
        0,
        session.scene.tactical_time_s,
        0,
        (-4.9, target.motion_state.position_world_m.y - 80),
        (0, 1000),
        0,
    )
    impact_scene = replace(
        session.scene,
        projectile_world=replace(
            session.scene.projectile_world,
            projectiles=(shot,),
        ),
    )
    impacted = step(old, session, impact_scene)
    assert impacted.impact_events
    closing = next(
        item
        for item in impacted.propulsion_closing_records
        if item.ship_id == target.ship_id
    )
    ship_result = next(
        item for item in impacted.ship_results if item.ship_id == target.ship_id
    )
    assert closing.runtime_parameters_sha256 == ship_result.resulting_runtime.source_sha256
    assert closing.runtime_parameters_sha256 != (
        ship_result.propulsion_aggregation.request.runtime_parameters_sha256
    )
    assert "main_engine_port" in impacted.impact_events[0].damaged_module_instance_ids
    return {
        "falling_zero_delivery": True,
        "exited_opening_and_closing_records": len(session.scene.ships) - 1,
        "exited_state_frozen_next_step": True,
        "closing_impact_events": len(impacted.impact_events),
        "closing_uses_post_impact_runtime": True,
    }


def check_import_isolation():
    command = (
        "import sys; import 高天荒野舰艇统一战术场景; "
        "assert '高天荒野舰艇整舰推进安全判定' not in sys.modules; "
        "assert '高天荒野舰艇推进向量载荷' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return {
        "legacy_unified_cold_imports_d3_2": False,
        "legacy_unified_cold_imports_vector_sampler": False,
    }


def collect_evidence():
    return {
        "named_builder": check_named_builder_matrix(),
        "first_command_and_contract": check_first_command_and_contract(),
        "joint_refusal_and_overg": check_joint_refusal_and_overg(),
        "save_reload": check_save_reload_continuity(),
        "lifecycle_and_damage": check_lifecycle_and_closing_damage(),
        "import_isolation": check_import_isolation(),
    }


def main():
    evidence = collect_evidence()
    if REPORT.exists():
        report = load_json(REPORT)
        assert report["status"] == "PASS" and report["evidence"] == evidence
        for path, expected in report["implementation_hashes"].items():
            assert file_sha256(ROOT / path) == expected, path
    print(
        json.dumps(
            {
                "status": "PASS",
                "interface": "gaotian.stage-t0b2d3e-governed-scene-save-wiring/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
