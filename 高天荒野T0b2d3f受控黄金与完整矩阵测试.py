"""d3.3 第四阶段：独立受控黄金、十二场景三次重放与长序列重载矩阵。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
import sys

from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import (
    advance_scenario_step,
    controls_for_step,
    guidance_inputs_for_step,
    launch_directives_for_step,
)
from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases, PROFILE_PATH
from 高天荒野T0b2d2b4场景接线与新黄金测试 import case as actual_case
from 高天荒野T0b2d2b4场景接线与新黄金测试 import step as actual_step
from 高天荒野舰艇受控推进场景 import (
    GovernedSceneSession,
    build_known_governed_scene,
    load_governed_scene_save,
    save_governed_scene,
    validate_governed_scene_step_payload,
)
from 高天荒野舰艇数据契约 import canonical_sha256
from 高天荒野舰艇定向推进控制桥 import (
    directional_control,
    migrate_known_t0_control_to_directional,
)
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇统一战术场景 import (
    BINDING_VALIDATION_TRUSTED,
    TacticalSceneState,
    advance_tactical_scene_step,
    prepare_tactical_scene_bindings,
)


ROOT = Path(__file__).resolve().parent
GOLDEN_PATH = ROOT / "contracts/web_bridge/t0-governed-propulsion-step-golden.v1.json"
ACTUAL_GOLDEN_PATH = ROOT / "contracts/web_bridge/t0-actual-propulsion-step-golden.v1.json"
AUTHORITY_GOLDEN_PATH = ROOT / "contracts/web_bridge/t0-authority-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据/报告/阶段T0b2d3f受控黄金与完整矩阵接口.v1.json"
ACTUAL_GOLDEN_SHA256 = "6636afd396df0dd1ec17906f4b8fbe0d23d1a88e49d47dae64d1872f259d78ed"
AUTHORITY_GOLDEN_SHA256 = "e7e5cf3dd494e5d8390f2aeabfbb4d2e6f4426562901879dbc7268ee68b5f095"


@lru_cache(maxsize=12)
def governed_case(name: str):
    profile = load_propulsion_safety_profile(PROFILE_PATH)
    name, old, source = next(item for item in migrated_cases() if item[0] == name)
    return old, source, build_known_governed_scene(
        ROOT,
        name,
        source,
        old.bindings,
        profile,
    )


def governed_step(
    old,
    session: GovernedSceneSession,
    scene: TacticalSceneState,
    controls=None,
):
    directives = launch_directives_for_step(old, scene)
    return advance_tactical_scene_step(
        scene,
        session.bindings,
        old.timing_catalog,
        old.projectile_catalog,
        old.material_registry,
        propulsion_context=session.propulsion_context,
        propulsion_controls=controls,
        guidance_catalog=old.guidance_catalog,
        guidance_inputs=guidance_inputs_for_step(old, scene, directives),
        launch_directives=directives,
        continuous_damage_profile=(
            old.continuous_damage_profile
            if old.load_stage == "scripted_damage_and_recompile"
            else None
        ),
        binding_validation_mode=BINDING_VALIDATION_TRUSTED,
    )


def _motion_payload(scene: TacticalSceneState):
    return tuple(
        {
            "ship_id": ship.ship_id,
            "motion_state": ship.motion_state.to_dict(),
        }
        for ship in scene.ships
    )


def _count_events(payload, counts: Counter):
    for key, value in payload.items():
        if key.endswith("_events") or key == "spawned_projectiles":
            counts[key] += len(value)


def _governed_replay(name: str):
    old, _, initial = governed_case(name)
    scene = initial.scene
    session = replace(
        initial,
        bindings=prepare_tactical_scene_bindings(scene, initial.bindings),
    )
    trace = []
    inputs = []
    counts = Counter()
    closing_records = 0
    for step_index in range(12):
        controls = {
            ship_id: migrate_known_t0_control_to_directional(control)
            for ship_id, control in controls_for_step(old, scene).items()
        }
        inputs.append({key: value.to_dict() for key, value in sorted(controls.items())})
        source = scene
        result = governed_step(old, session, scene, controls)
        scene = result.resulting_scene
        payload = result.to_dict()
        if step_index in (0, 1, 11):
            validate_governed_scene_step_payload(
                payload,
                source,
                result,
                session.propulsion_context,
            )
        assert len(result.propulsion_closing_records) == sum(
            ship.lifecycle_state.physical_status != "exited" for ship in source.ships
        )
        assert all(
            governor.last_evaluated_step_index == step_index + 1
            for ship in scene.ships
            if ship.lifecycle_state.physical_status != "exited"
            for governor in ship.propulsion_state.governors
        )
        closing_records += len(result.propulsion_closing_records)
        _count_events(payload, counts)
        trace.append(
            {
                "result_sha256": canonical_sha256(payload),
                "scene_sha256": canonical_sha256(scene),
            }
        )
    return {
        "closing_records": closing_records,
        "event_counts": dict(sorted(counts.items())),
        "final_motion_sha256": canonical_sha256(_motion_payload(scene)),
        "governor_boundary": 12,
        "initial_scene_sha256": canonical_sha256(initial.scene),
        "input_stream_sha256": canonical_sha256(inputs),
        "resource_bundle_sha256": canonical_sha256(initial.resource_bundle),
        "resulting_scene_sha256": canonical_sha256(scene),
        "step_trace_sha256": canonical_sha256(trace),
        "steps": 12,
    }, scene


def _actual_reference(name: str):
    old, _, initial = actual_case(name)
    scene = initial.scene
    session = replace(
        initial,
        bindings=prepare_tactical_scene_bindings(scene, initial.bindings),
    )
    counts = Counter()
    for _ in range(12):
        controls = {
            ship_id: migrate_known_t0_control_to_directional(control)
            for ship_id, control in controls_for_step(old, scene).items()
        }
        result = actual_step(old, session, scene, controls)
        scene = result.resulting_scene
        _count_events(result.to_dict(), counts)
    return scene, dict(sorted(counts.items()))


def collect_matrix():
    stored_actual = json.loads(ACTUAL_GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = {}
    total_ship_steps = 0
    total_closing_records = 0
    for name, _, _ in migrated_cases():
        replays = [_governed_replay(name) for _ in range(3)]
        assert replays[0][0] == replays[1][0] == replays[2][0]
        case, governed_scene = replays[0]
        actual_scene, actual_counts = _actual_reference(name)
        assert canonical_sha256(actual_scene) == stored_actual["cases"][name]["resulting_scene_sha256"]
        assert case["initial_scene_sha256"] != stored_actual["cases"][name]["initial_scene_sha256"]
        assert case["resulting_scene_sha256"] != stored_actual["cases"][name]["resulting_scene_sha256"]
        assert _motion_payload(governed_scene) == _motion_payload(actual_scene)
        assert case["event_counts"].get("propulsion_events", 0) == actual_counts.get(
            "propulsion_events", 0
        )
        assert case["event_counts"].get("propulsion_safety_events", 0) == 0
        cases[name] = case
        total_ship_steps += len(governed_scene.ships) * 12
        total_closing_records += case["closing_records"]
    golden = {
        "cases": cases,
        "interface": "gaotian.t0-governed-propulsion-step-golden/v1",
        "replays": 3,
        "scene_interface": "gaotian.tactical-scene-timeline/v6alpha1",
        "step_interface": "gaotian.tactical-scene-step-resolution/v5alpha1",
    }
    evidence = {
        "closing_records_per_replay": total_closing_records,
        "comparison_to_actual_v5": {
            "governed_safety_event_free_cases": 12,
            "motion_and_fuel_equal_cases": 12,
            "native_propulsion_event_count_equal_cases": 12,
            "serialization_and_result_hashes_intentionally_different_cases": 12,
        },
        "replays": 3,
        "scenes": 12,
        "ship_steps_per_replay": total_ship_steps,
        "steps_per_scene_per_replay": 12,
    }
    return golden, evidence


def check_long_sequence_and_reload():
    name = "functional_6.motion_only"
    old, source, initial = governed_case(name)
    profile = initial.propulsion_context.safety_profile
    context = initial.propulsion_context
    target_ship = initial.scene.ships[0]
    limited_governors = tuple(
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
        for governor in target_ship.propulsion_state.governors
    )
    initial_scene = TacticalSceneState.parse(
        replace(
            initial.scene,
            ships=(
                replace(
                    target_ship,
                    propulsion_state=replace(
                        target_ship.propulsion_state,
                        governors=limited_governors,
                    ),
                ),
                *initial.scene.ships[1:],
            ),
        ).to_dict()
    )
    target = initial_scene.ships[0].ship_id
    commands = {
        0: ("full", False),
        2: ("full", True),
        130: ("half", False),
        170: ("half", True),
        210: ("stop", False),
        280: ("quarter", False),
    }
    traces = []
    summaries = []
    for repetition, reload_at in enumerate((None, 121, 200)):
        scene = initial_scene
        session = replace(
            initial,
            scene=scene,
            bindings=prepare_tactical_scene_bindings(scene, initial.bindings),
            propulsion_context=context,
        )
        trace = []
        counts = Counter()
        samples = {}
        for step_index in range(331):
            requested = None
            if step_index in commands:
                notch, overg = commands[step_index]
                requested = {
                    target: directional_control(
                        (ChannelPropulsionCommand("translation.forward", notch, None),),
                        overg_requested=overg,
                    )
                }
            result = governed_step(old, session, scene, requested)
            scene = result.resulting_scene
            payload = result.to_dict()
            _count_events(payload, counts)
            trace.append(
                {
                    "result_sha256": canonical_sha256(payload),
                    "scene_sha256": canonical_sha256(scene),
                }
            )
            if scene.fixed_step_index in (1, 2, 3, 5, 121, 131, 171, 200, 211, 281, 331):
                ship = scene.ships[0]
                governor = next(
                    item
                    for item in ship.propulsion_state.governors
                    if item.command_channel == "translation.forward"
                )
                samples[str(scene.fixed_step_index)] = {
                    "actual_main_outputs": [
                        engine.actual_output_percent
                        for engine in ship.propulsion_state.engines
                        if engine.actuator_category == "main_engine"
                    ],
                    "fuel_units": ship.motion_state.fuel_units,
                    "release_candidate_since_step": governor.release_candidate_since_step,
                    "safety_ceiling_percent": governor.safety_ceiling_percent,
                    "safety_reasons": list(governor.safety_reasons),
                }
            if reload_at is not None and scene.fixed_step_index == reload_at:
                session = load_governed_scene_save(
                    save_governed_scene(scene, context),
                    root=ROOT,
                    scene_id=name,
                    source_scene=source,
                    source_bindings=old.bindings,
                    safety_profile=profile,
                )
                assert session.scene == scene
                scene = session.scene
        traces.append(canonical_sha256(trace))
        summaries.append(
            {
                "event_counts": dict(sorted(counts.items())),
                "final_scene_sha256": canonical_sha256(scene),
                "samples": samples,
            }
        )
    assert len(set(traces)) == 1
    assert summaries[0] == summaries[1] == summaries[2]
    safety_counts = summaries[0]["event_counts"].get("propulsion_safety_events", 0)
    assert safety_counts >= 1
    return {
        "event_counts": summaries[0]["event_counts"],
        "final_scene_sha256": summaries[0]["final_scene_sha256"],
        "reload_boundaries": [121, 200],
        "replays": 3,
        "samples": summaries[0]["samples"],
        "steps_per_replay": 331,
        "trace_sha256": traces[0],
    }


def check_legacy_and_authority_isolation():
    assert file_sha256(ACTUAL_GOLDEN_PATH) == ACTUAL_GOLDEN_SHA256
    assert file_sha256(AUTHORITY_GOLDEN_PATH) == AUTHORITY_GOLDEN_SHA256
    name, old, _ = next(iter(migrated_cases()))
    scene = old.initial_scene
    results = []
    for _ in range(12):
        result = advance_scenario_step(old, scene)
        scene = result.resulting_scene
        results.append(canonical_sha256(result.to_dict()))
    return {
        "actual_propulsion_golden_sha256": ACTUAL_GOLDEN_SHA256,
        "authority_golden_sha256": AUTHORITY_GOLDEN_SHA256,
        "legacy_probe_result_trace_sha256": canonical_sha256(results),
        "legacy_probe_scene_interface": old.initial_scene.to_dict()["interface"],
    }


def collect_evidence():
    golden, matrix = collect_matrix()
    evidence = {
        "legacy_isolation": check_legacy_and_authority_isolation(),
        "long_sequence_and_reload": check_long_sequence_and_reload(),
        "matrix": matrix,
        "official_performance_runs_executed": 0,
    }
    return golden, evidence


def main():
    if "--emit-matrix" in sys.argv:
        golden, matrix = collect_matrix()
        print(
            json.dumps(
                {"golden": golden, "matrix": matrix},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if "--emit-long" in sys.argv:
        print(
            json.dumps(
                check_long_sequence_and_reload(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    golden, evidence = collect_evidence()
    if "--emit" in sys.argv:
        print(
            json.dumps(
                {"evidence": evidence, "golden": golden},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    assert json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) == golden
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for relative, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / relative) == expected, relative
    print(
        json.dumps(
            {
                "evidence": evidence,
                "golden_cases": len(golden["cases"]),
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
