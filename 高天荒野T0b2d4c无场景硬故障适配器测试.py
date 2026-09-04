"""d4.3 无场景硬故障开边界、显式复位与独立紧急断推。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇受控推进硬故障适配器 import (
    EMERGENCY_CUT_ACTIONS,
    EMERGENCY_CUT_CAUSES,
    EMERGENCY_CUT_POLICY_ID,
    EMERGENCY_CUT_RESULT_INTERFACE_ID,
    GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID,
    GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID,
    GOVERNED_HARD_FAULT_OPENING_POLICY_ID,
    GovernedPropulsionHardFaultCommand,
    GovernedPropulsionHardFaultOpening,
    PropulsionEmergencyCutResult,
    apply_emergency_propulsion_cut,
    commit_governed_propulsion_hard_fault_opening,
    validate_emergency_propulsion_cut_result,
    validate_governed_propulsion_hard_fault_opening,
)
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import mutate_modules
from 高天荒野T0b2d4b硬故障运行时投影测试 import (
    directional_state,
    electric_catalog,
    fixture,
    manual_catalog,
)


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d4c无场景硬故障适配器接口.v1.json"
SCHEMAS = (
    ROOT / "舰艇数据/模式/高天荒野舰艇受控推进硬故障命令契约.v1alpha1.schema.json",
    ROOT / "舰艇数据/模式/高天荒野舰艇推进紧急断推结果契约.v1alpha1.schema.json",
    ROOT / "舰艇数据/模式/高天荒野舰艇受控推进硬故障开边界契约.v1alpha1.schema.json",
)


def refused(action, code=None) -> None:
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def commit(context, runtime, state, command=None, step=5):
    command = command or GovernedPropulsionHardFaultCommand()
    result = commit_governed_propulsion_hard_fault_opening(
        context, runtime, state, command, fixed_step_index=step
    )
    validate_governed_propulsion_hard_fault_opening(
        result, context, runtime, state
    )
    return result


def rows(result):
    return {
        item.snapshot.actuator_instance_id: item
        for item in result.hard_fault_results
    }


def check_fault_precedes_time_and_delivery() -> dict[str, object]:
    context, runtime, _ = fixture()
    source = directional_state(context, "running")
    baseline = commit(context, runtime, source)
    assert baseline.state == source
    assert all(item.action == "available" for item in baseline.hard_fault_results)
    assert not baseline.propulsion_events and not baseline.emergency_cut_results

    empty_context, empty_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            operational_state=replace(
                instance.operational_state, fuel_units=0.0
            ),
        )
    )
    empty_source = directional_state(empty_context, "running")
    tripped = commit(empty_context, empty_runtime, empty_source)
    assert all(item.action == "trip" for item in tripped.hard_fault_results)
    assert all(
        engine.phase == "tripped"
        and engine.actual_output_percent == 0
        and engine.target_output_percent == 0
        and engine.next_transition_step is None
        for engine in tripped.state.engines
    )
    assert len(tripped.propulsion_events) == len(tripped.state.engines)
    assert tuple(event.sort_key for event in tripped.propulsion_events) == tuple(
        sorted(event.sort_key for event in tripped.propulsion_events)
    )
    assert all(
        event.kind == "engine_tripped"
        and event.reasons == ("fuel_unavailable",)
        for event in tripped.propulsion_events
    )

    power_context, power_runtime, _ = fixture(
        catalog_mutate=electric_catalog
    )
    power_result = commit(
        power_context,
        power_runtime,
        directional_state(power_context, "running"),
    )
    power_rows = rows(power_result)
    assert power_rows["main_engine_port"].action == "trip"
    assert power_rows["main_engine_starboard"].action == "trip"
    assert all(
        item.action == "available"
        for key, item in power_rows.items()
        if key.startswith("thruster_")
    )

    crew_context, crew_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            operational_state=replace(instance.operational_state, crew=()),
        ),
        catalog_mutate=manual_catalog,
    )
    crew_rows = rows(
        commit(
            crew_context,
            crew_runtime,
            directional_state(crew_context),
        )
    )
    assert crew_rows["main_engine_port"].action == "trip"
    assert crew_rows["main_engine_starboard"].action == "trip"
    return {
        "actuators": len(baseline.state.engines),
        "fuel_trip_before_following_stages": True,
        "phase_power_trip_is_per_actuator": True,
        "manual_crew_trip_is_per_actuator": True,
        "aggregate_thrust_used_as_fault_source": False,
        "time_advanced": False,
        "physical_load_evaluated": False,
    }


def check_explicit_atomic_reset() -> dict[str, object]:
    empty_context, empty_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            operational_state=replace(
                instance.operational_state, fuel_units=0.0
            ),
        )
    )
    tripped = commit(
        empty_context,
        empty_runtime,
        directional_state(empty_context, "running"),
    ).state
    blocked = GovernedPropulsionHardFaultCommand(
        ("main_engine_port",)
    )
    refused(
        lambda: commit(empty_context, empty_runtime, tripped, blocked, 6),
        "hard_fault.reset_blocked",
    )

    context, recovered_runtime, _ = fixture()
    reset = commit(context, recovered_runtime, tripped, blocked, 6)
    reset_rows = rows(reset)
    assert reset_rows["main_engine_port"].action == "reset"
    assert reset_rows["main_engine_port"].state.phase == "off"
    assert reset_rows["main_engine_port"].state.commanded_notch == "stop"
    assert all(
        item.action == "latched"
        for key, item in reset_rows.items()
        if key != "main_engine_port"
    )
    assert len(reset.propulsion_events) == 1
    assert reset.propulsion_events[0].kind == "engine_reset"
    assert reset.state.governors == tripped.governors
    assert reset.state.engines[0].target_output_percent == 0

    refused(
        lambda: commit(
            context,
            recovered_runtime,
            directional_state(context),
            blocked,
            5,
        ),
        "hard_fault.reset_not_tripped",
    )
    return {
        "blocked_reset_is_atomic": True,
        "selective_reset_count": 1,
        "unselected_latches_preserved": len(reset.state.engines) - 1,
        "reset_starts_engine": False,
        "governor_history_changed": False,
    }


def check_emergency_cut_is_not_fault() -> dict[str, object]:
    context, runtime, _ = fixture()
    source = directional_state(context, "running")
    command = GovernedPropulsionHardFaultCommand(
        emergency_cut_cause="operator_requested"
    )
    result = commit(context, runtime, source, command)
    assert [item.action for item in result.emergency_cut_results] == [
        "cut",
        "cut",
        "already_zero",
        "already_zero",
        "already_zero",
        "already_zero",
    ]
    assert all(
        item.action == "available" for item in result.hard_fault_results
    )
    assert not result.propulsion_events
    assert result.state.governors == source.governors
    assert all(
        engine.actual_output_percent == 0
        and engine.target_output_percent == 0
        and engine.phase == "ready"
        for engine in result.state.engines
    )
    for cut, hard in zip(
        result.emergency_cut_results, result.hard_fault_results
    ):
        validate_emergency_propulsion_cut_result(cut, hard.state)

    starting = directional_state(context, "starting").engines[0]
    cancelled = apply_emergency_propulsion_cut(
        starting, fixed_step_index=5, cause="safety_system_requested"
    )
    assert cancelled.action == "cut" and cancelled.state.phase == "off"
    tripped_engine = replace(
        directional_state(context, "off").engines[0], phase="tripped"
    )
    preserved = apply_emergency_propulsion_cut(
        tripped_engine, fixed_step_index=5, cause="operator_requested"
    )
    assert preserved.action == "tripped_preserved"
    assert preserved.state == tripped_engine

    empty_context, empty_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance,
            operational_state=replace(
                instance.operational_state, fuel_units=0.0
            ),
        )
    )
    precedence = commit(
        empty_context,
        empty_runtime,
        directional_state(empty_context, "running"),
        command,
    )
    assert all(
        item.action == "trip" for item in precedence.hard_fault_results
    )
    assert all(
        item.action == "tripped_preserved"
        for item in precedence.emergency_cut_results
    )
    assert all(
        not item.snapshot.overg_requested
        for item in precedence.hard_fault_results
    )
    return {
        "causes": list(EMERGENCY_CUT_CAUSES),
        "actions": list(EMERGENCY_CUT_ACTIONS),
        "running_actuators_cut_immediately": 2,
        "already_zero_audits": 4,
        "starting_cancels_to_off": True,
        "tripped_state_preserved": True,
        "hard_fault_precedes_emergency_cut": True,
        "emergency_cut_emits_engine_trip": False,
        "governor_commands_changed": False,
        "overg_bypasses_fault": False,
    }


def check_damage_and_host_boundaries() -> dict[str, object]:
    partial_context, partial_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance,
            {"main_engine_port": {"current_durability_points": 50.0}},
        )
    )
    partial = rows(
        commit(
            partial_context,
            partial_runtime,
            directional_state(partial_context),
        )
    )["main_engine_port"]
    assert partial.action == "available"

    destroyed_context, destroyed_runtime, _ = fixture(
        mutate=lambda instance: mutate_modules(
            instance,
            {"main_engine_port": {"current_durability_points": 0.0}},
        )
    )
    destroyed = rows(
        commit(
            destroyed_context,
            destroyed_runtime,
            directional_state(destroyed_context),
        )
    )["main_engine_port"]
    assert destroyed.action == "trip"
    assert destroyed.events[0].reasons == ("actuator_destroyed",)

    hull_context, hull_runtime, _ = fixture(
        mutate=lambda instance: replace(
            instance, current_hull_integrity_fraction=0.0
        )
    )
    hull = commit(
        hull_context, hull_runtime, directional_state(hull_context)
    )
    assert all(item.action == "trip" for item in hull.hard_fault_results)
    assert all(
        item.events[0].reasons == ("host_destroyed",)
        for item in hull.hard_fault_results
    )
    return {
        "partial_damage_trips": False,
        "destroyed_actuator_trips": True,
        "destroyed_hull_trips_all": True,
    }


def check_replay_and_serialization() -> dict[str, object]:
    context, runtime, _ = fixture()
    source = directional_state(context, "running")
    commands = (
        GovernedPropulsionHardFaultCommand(),
        GovernedPropulsionHardFaultCommand(
            emergency_cut_cause="operator_requested"
        ),
    )
    traces = []
    for reload_at in (None, 0, 1):
        trace = []
        for index, command in enumerate(commands):
            result = commit_governed_propulsion_hard_fault_opening(
                context, runtime, source, command, fixed_step_index=5
            )
            if index == reload_at:
                result = GovernedPropulsionHardFaultOpening.parse(
                    json.loads(json.dumps(result.to_dict()))
                )
            validate_governed_propulsion_hard_fault_opening(
                result, context, runtime, source
            )
            trace.append(canonical_sha256(result))
        traces.append(trace)
    assert traces[0] == traces[1] == traces[2]
    return {
        "replays": len(traces),
        "boundaries_per_replay": len(traces[0]),
        "reload_boundaries": [0, 1],
        "trace_sha256": canonical_sha256(traces[0]),
    }


def check_negative_contracts() -> dict[str, object]:
    context, runtime, _ = fixture()
    source = directional_state(context, "running")
    command = GovernedPropulsionHardFaultCommand(
        emergency_cut_cause="operator_requested"
    )
    result = commit(context, runtime, source, command)
    cut = result.emergency_cut_results[0]
    actions = []

    command_payload = command.to_dict()
    for key in command_payload:
        damaged = deepcopy(command_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: GovernedPropulsionHardFaultCommand.parse(
                damaged
            )
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("emergency_cut_cause", "fuel_unavailable"),
        ("reset_actuator_instance_ids", None),
        ("reset_actuator_instance_ids", ["z", "a"]),
        ("reset_actuator_instance_ids", ["a", "a"]),
    ):
        damaged = deepcopy(command_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: GovernedPropulsionHardFaultCommand.parse(
                damaged
            )
        )
    combined = deepcopy(command_payload)
    combined["reset_actuator_instance_ids"] = ["main_engine_port"]
    actions.append(
        lambda: GovernedPropulsionHardFaultCommand.parse(combined)
    )

    cut_payload = cut.to_dict()
    for key in cut_payload:
        damaged = deepcopy(cut_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: PropulsionEmergencyCutResult.parse(damaged)
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("action", "trip"),
        ("cause", "fuel_unavailable"),
        ("fixed_step_index", True),
        ("source_state_sha256", "bad"),
    ):
        damaged = deepcopy(cut_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: PropulsionEmergencyCutResult.parse(damaged)
        )
    nonzero = deepcopy(cut_payload)
    nonzero["state"]["actual_output_percent"] = 5
    actions.append(lambda: PropulsionEmergencyCutResult.parse(nonzero))

    opening_payload = result.to_dict()
    for key in opening_payload:
        damaged = deepcopy(opening_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: GovernedPropulsionHardFaultOpening.parse(
                damaged
            )
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("fixed_step_index", True),
        ("source_state_sha256", "bad"),
        ("resulting_state_sha256", "bad"),
        ("hard_fault_results", None),
        ("emergency_cut_results", None),
    ):
        damaged = deepcopy(opening_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: GovernedPropulsionHardFaultOpening.parse(
                damaged
            )
        )
    reversed_hard = deepcopy(opening_payload)
    reversed_hard["hard_fault_results"].reverse()
    actions.append(
        lambda: GovernedPropulsionHardFaultOpening.parse(reversed_hard)
    )
    wrong_cut_cause = deepcopy(opening_payload)
    wrong_cut_cause["emergency_cut_results"][0]["cause"] = (
        "safety_system_requested"
    )
    actions.append(
        lambda: GovernedPropulsionHardFaultOpening.parse(wrong_cut_cause)
    )
    wrong_state_hash = deepcopy(opening_payload)
    wrong_state_hash["resulting_state_sha256"] = "0" * 64
    actions.append(
        lambda: GovernedPropulsionHardFaultOpening.parse(wrong_state_hash)
    )
    wrong_source_hash = deepcopy(opening_payload)
    wrong_source_hash["source_state_sha256"] = "0" * 64
    actions.append(
        lambda: GovernedPropulsionHardFaultOpening.parse(wrong_source_hash)
    )
    unknown_reset = commit(
        context,
        runtime,
        source,
        GovernedPropulsionHardFaultCommand(),
    ).to_dict()
    unknown_reset["command"]["reset_actuator_instance_ids"] = ["unknown"]
    actions.append(
        lambda: GovernedPropulsionHardFaultOpening.parse(unknown_reset)
    )

    actions.extend(
        (
            lambda: apply_emergency_propulsion_cut(
                None, fixed_step_index=5, cause="operator_requested"
            ),
            lambda: apply_emergency_propulsion_cut(
                source.engines[0], fixed_step_index=True, cause="operator_requested"
            ),
            lambda: apply_emergency_propulsion_cut(
                source.engines[0], fixed_step_index=5, cause="fuel_unavailable"
            ),
            lambda: validate_emergency_propulsion_cut_result(
                None, source.engines[0]
            ),
            lambda: commit_governed_propulsion_hard_fault_opening(
                context, runtime, source, None, fixed_step_index=5
            ),
            lambda: commit_governed_propulsion_hard_fault_opening(
                context, runtime, source, command, fixed_step_index=True
            ),
            lambda: commit_governed_propulsion_hard_fault_opening(
                context,
                runtime,
                source,
                GovernedPropulsionHardFaultCommand(("unknown",)),
                fixed_step_index=5,
            ),
            lambda: validate_governed_propulsion_hard_fault_opening(
                None, context, runtime, source
            ),
            lambda: validate_governed_propulsion_hard_fault_opening(
                result,
                context,
                runtime,
                directional_state(context, "ready"),
            ),
        )
    )
    for action in actions:
        refused(action)
    return {"strict_negative_cases": len(actions)}


def check_schema_and_isolation() -> dict[str, object]:
    schemas = [load_json(path) for path in SCHEMAS]
    assert [item["$id"] for item in schemas] == [
        GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID,
        EMERGENCY_CUT_RESULT_INTERFACE_ID,
        GOVERNED_HARD_FAULT_OPENING_INTERFACE_ID,
    ]
    context, runtime, _ = fixture()
    source = directional_state(context, "running")
    command = GovernedPropulsionHardFaultCommand(
        emergency_cut_cause="operator_requested"
    )
    opening = commit(context, runtime, source, command)
    samples = (
        command.to_dict(),
        opening.emergency_cut_results[0].to_dict(),
        opening.to_dict(),
    )
    for schema, sample in zip(schemas, samples):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]) == set(
            sample
        )

    references = []

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                references.append(value["$ref"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for schema in schemas:
        visit(schema)
    assert set(references) == {
        "gaotian.tactical-propulsion-state/v3alpha1#/$defs/engineRuntimeState",
        GOVERNED_HARD_FAULT_COMMAND_INTERFACE_ID,
        EMERGENCY_CUT_RESULT_INTERFACE_ID,
        "gaotian.propulsion-hard-fault-boundary-result/v1alpha1",
        "gaotian.propulsion-hard-fact-runtime-projection/v1alpha1",
        "gaotian.tactical-propulsion-state/v3alpha1",
    }

    for order in (
        ("高天荒野舰艇受控推进硬故障适配器", "高天荒野舰艇统一战术场景"),
        ("高天荒野舰艇统一战术场景", "高天荒野舰艇受控推进硬故障适配器"),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-c",
                "; ".join(f"import {name}" for name in order),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import sys; import 高天荒野舰艇受控推进硬故障适配器; "
            "assert '高天荒野舰艇统一战术场景' not in sys.modules; "
            "assert '高天荒野舰艇受控推进无场景适配器' not in sys.modules; "
            "assert '高天荒野舰艇受控推进时间边界' not in sys.modules; "
            "assert not any(name.startswith('benchmarks') for name in sys.modules)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    return {
        "schemas": len(schemas),
        "references_checked": len(references),
        "cold_import_orders": 2,
        "imports_scene_d3_adapter_time_or_benchmarks": False,
    }


def collect_evidence() -> dict[str, object]:
    return {
        "boundary_order": check_fault_precedes_time_and_delivery(),
        "explicit_reset": check_explicit_atomic_reset(),
        "emergency_cut": check_emergency_cut_is_not_fault(),
        "damage_and_host": check_damage_and_host_boundaries(),
        "replay": check_replay_and_serialization(),
        "negative_contracts": check_negative_contracts(),
        "schema_and_isolation": check_schema_and_isolation(),
    }


def main() -> None:
    evidence = collect_evidence()
    report = load_json(REPORT)
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for path, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / path) == expected, path
    print(
        json.dumps(
            {
                "status": "PASS",
                "interface": "gaotian.stage-t0b2d4c-no-scene-hard-fault-adapter/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
