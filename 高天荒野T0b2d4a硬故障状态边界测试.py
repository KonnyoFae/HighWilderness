"""d4.1 纯硬故障事实、立即跳闸锁存与显式复位边界。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇推进状态合同 import (
    ENGINE_RUNTIME_STATE_INTERFACE_ID,
    EngineRuntimeState,
    migrate_engine_runtime_state_from_module_mode,
)
from 高天荒野舰艇推进硬故障边界 import (
    EXTERNAL_HARD_FAULT_REASONS,
    HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID,
    HARD_FAULT_POLICY_ID,
    HARD_FAULT_SNAPSHOT_INTERFACE_ID,
    PropulsionHardFaultBoundaryResult,
    PropulsionHardFaultSnapshot,
    apply_propulsion_hard_fault_boundary,
    evaluate_propulsion_hard_availability,
    external_hard_fault_reasons,
    validate_propulsion_hard_fault_boundary_result,
)


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d4a硬故障状态边界接口.v1.json"
SCHEMAS = (
    "舰艇数据/模式/高天荒野舰艇推进硬故障事实契约.v1alpha1.schema.json",
    "舰艇数据/模式/高天荒野舰艇推进硬故障状态边界结果契约.v1alpha1.schema.json",
)


def engine_state(
    phase: str = "running",
    *,
    category: str = "main_engine",
    actuator_id: str = "engine.hard-fault",
) -> EngineRuntimeState:
    if phase == "off":
        return migrate_engine_runtime_state_from_module_mode(
            actuator_id, category, "off", 0
        )
    base = migrate_engine_runtime_state_from_module_mode(
        actuator_id, category, "active", 0
    )
    notch = "full" if category == "main_engine" else None
    if phase == "ready":
        return replace(base, commanded_notch=notch)
    if phase == "starting":
        return replace(
            base,
            phase="starting",
            commanded_notch=notch,
            target_output_percent=100,
            ready_at_fixed_step=60,
            next_transition_step=60,
        )
    if phase == "running":
        return replace(
            base,
            phase="running",
            commanded_notch=notch,
            target_output_percent=100,
            actual_output_percent=50,
            next_transition_step=10,
            response_started_at_fixed_step=0,
            response_start_output_percent=0,
        )
    if phase == "stopping":
        return replace(
            base,
            phase="stopping",
            commanded_notch=notch,
            actual_output_percent=50,
            next_transition_step=10,
            response_started_at_fixed_step=0,
            response_start_output_percent=50,
        )
    if phase == "tripped":
        return replace(
            engine_state("off", category=category, actuator_id=actuator_id),
            phase="tripped",
            commanded_notch=notch,
        )
    raise ValueError(phase)


def facts(
    step: int = 5,
    *,
    actuator_id: str = "engine.hard-fault",
    fuel_available: bool = True,
    power_available: bool = True,
    crew_available: bool = True,
    actuator_destroyed: bool = False,
    host_destroyed: bool = False,
    overg_requested: bool = False,
) -> PropulsionHardFaultSnapshot:
    return PropulsionHardFaultSnapshot(
        step,
        actuator_id,
        fuel_available,
        power_available,
        crew_available,
        actuator_destroyed,
        host_destroyed,
        overg_requested,
    )


def refused(action) -> None:
    try:
        action()
    except ContractError:
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def check_external_reason_matrix() -> dict[str, object]:
    cases = (
        ("fuel_available", False, "fuel_unavailable"),
        ("power_available", False, "power_unavailable"),
        ("crew_available", False, "crew_unavailable"),
        ("actuator_destroyed", True, "actuator_destroyed"),
        ("host_destroyed", True, "host_destroyed"),
    )
    trip_hashes = []
    for field, value, expected_reason in cases:
        snapshot = facts(**{field: value})
        source = engine_state()
        availability = evaluate_propulsion_hard_availability(snapshot, source)
        assert availability.ceiling_percent == 0
        assert availability.reasons == (expected_reason,)
        result = apply_propulsion_hard_fault_boundary(source, snapshot)
        assert result.action == "trip" and result.state.phase == "tripped"
        assert result.state.actual_output_percent == 0
        assert result.state.target_output_percent == 0
        assert result.state.commanded_notch == source.commanded_notch
        assert result.state.ready_at_fixed_step is None
        assert result.state.next_transition_step is None
        assert result.state.response_started_at_fixed_step is None
        assert result.state.response_start_output_percent is None
        assert result.hard_availability.reasons == (
            expected_reason,
            "engine_tripped",
        )
        assert len(result.events) == 1
        event = result.events[0]
        assert event.kind == "engine_tripped"
        assert event.reasons == (expected_reason,)
        assert event.previous_stage_percent == 50
        assert event.resulting_stage_percent == 0
        assert PropulsionHardFaultBoundaryResult.parse(result.to_dict()) == result
        validate_propulsion_hard_fault_boundary_result(result, source)
        trip_hashes.append(canonical_sha256(result))

    combined = facts(
        fuel_available=False,
        power_available=False,
        crew_available=False,
        actuator_destroyed=True,
        host_destroyed=True,
        overg_requested=True,
    )
    assert external_hard_fault_reasons(combined) == EXTERNAL_HARD_FAULT_REASONS
    result = apply_propulsion_hard_fault_boundary(engine_state(), combined)
    assert result.events[0].reasons == EXTERNAL_HARD_FAULT_REASONS
    assert result.hard_availability.reasons == (
        *EXTERNAL_HARD_FAULT_REASONS,
        "engine_tripped",
    )
    return {
        "external_causes": len(cases),
        "combined_reason_order": list(EXTERNAL_HARD_FAULT_REASONS),
        "overg_bypasses_hard_fault": False,
        "trip_hashes": trip_hashes,
    }


def check_phase_and_category_matrix() -> dict[str, object]:
    phases = ("off", "starting", "ready", "running", "stopping")
    matrix = []
    for category in ("main_engine", "maneuver_thruster"):
        for phase in phases:
            source = engine_state(phase, category=category)
            result = apply_propulsion_hard_fault_boundary(
                source, facts(fuel_available=False)
            )
            assert result.action == "trip" and result.state.phase == "tripped"
            assert result.state.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID
            assert result.events[0].previous_phase == phase
            assert result.events[0].resulting_phase == "tripped"
            stage_changed = source.actual_output_percent != 0
            assert (result.events[0].previous_stage_percent is not None) == stage_changed
            matrix.append(f"{category}:{phase}")
    return {
        "cases": len(matrix),
        "categories": 2,
        "source_phases": list(phases),
        "immediate_zero_and_schedule_clear": True,
    }


def check_latch_and_explicit_reset() -> dict[str, object]:
    source = engine_state()
    tripped = apply_propulsion_hard_fault_boundary(
        source, facts(power_available=False)
    ).state

    recovered_facts = facts(step=6)
    latched = apply_propulsion_hard_fault_boundary(tripped, recovered_facts)
    assert latched.action == "latched"
    assert latched.state == tripped and not latched.events
    assert latched.hard_availability.reasons == ("engine_tripped",)
    validate_propulsion_hard_fault_boundary_result(latched, tripped)

    refused(
        lambda: apply_propulsion_hard_fault_boundary(
            tripped,
            facts(step=6, power_available=False),
            reset_requested=True,
        )
    )
    reset = apply_propulsion_hard_fault_boundary(
        tripped, recovered_facts, reset_requested=True
    )
    assert reset.action == "reset" and reset.state.phase == "off"
    assert reset.state.commanded_notch == "stop"
    assert reset.hard_availability.ceiling_percent == 100
    assert reset.events[0].kind == "engine_reset" and not reset.events[0].reasons
    validate_propulsion_hard_fault_boundary_result(reset, tripped)
    available = apply_propulsion_hard_fault_boundary(reset.state, facts(step=7))
    assert available.action == "available" and available.state == reset.state
    assert not available.events

    thruster = engine_state("tripped", category="maneuver_thruster")
    thruster_reset = apply_propulsion_hard_fault_boundary(
        thruster, facts(actuator_id=thruster.actuator_instance_id), reset_requested=True
    )
    assert thruster_reset.state.phase == "off"
    assert thruster_reset.state.commanded_notch is None
    refused(
        lambda: apply_propulsion_hard_fault_boundary(
            engine_state("ready"), facts(), reset_requested=True
        )
    )
    return {
        "availability_recovery_auto_resets": False,
        "blocked_reset_rejected": True,
        "explicit_reset_to_off": True,
        "reset_does_not_start": True,
        "categories": 2,
    }


def check_replay_and_serialization() -> dict[str, object]:
    traces = []
    for reload_at in (None, 1, 3):
        state = engine_state()
        trace = []
        specifications = (
            (facts(step=5), False),
            (facts(step=6, power_available=False), False),
            (facts(step=7), False),
            (facts(step=8), True),
            (facts(step=9), False),
        )
        for index, (snapshot, reset_requested) in enumerate(specifications):
            source = state
            result = apply_propulsion_hard_fault_boundary(
                source, snapshot, reset_requested=reset_requested
            )
            validate_propulsion_hard_fault_boundary_result(result, source)
            trace.append(canonical_sha256(result))
            state = result.state
            if index == reload_at:
                result = PropulsionHardFaultBoundaryResult.parse(
                    json.loads(json.dumps(result.to_dict()))
                )
                state = EngineRuntimeState.parse(
                    json.loads(json.dumps(result.state.to_dict())), "$"
                )
        traces.append(trace)
    assert traces[0] == traces[1] == traces[2]
    return {
        "boundaries_per_replay": 5,
        "replays": 3,
        "reload_boundaries": [1, 3],
        "trace_sha256": canonical_sha256(traces[0]),
    }


def check_negative_contracts() -> dict[str, object]:
    snapshot = facts()
    result = apply_propulsion_hard_fault_boundary(
        engine_state(), facts(fuel_available=False)
    )
    actions = []
    snapshot_payload = snapshot.to_dict()
    for key in snapshot_payload:
        damaged = deepcopy(snapshot_payload)
        del damaged[key]
        actions.append(lambda damaged=damaged: PropulsionHardFaultSnapshot.parse(damaged))
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("fixed_step_index", True),
        ("actuator_instance_id", "INVALID"),
        ("fuel_available", 1),
        ("overg_requested", "false"),
    ):
        damaged = deepcopy(snapshot_payload)
        damaged[key] = value
        actions.append(lambda damaged=damaged: PropulsionHardFaultSnapshot.parse(damaged))

    result_payload = result.to_dict()
    for key in result_payload:
        damaged = deepcopy(result_payload)
        del damaged[key]
        actions.append(lambda damaged=damaged: PropulsionHardFaultBoundaryResult.parse(damaged))
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("action", "stop"),
        ("source_state_sha256", "bad"),
        ("events", None),
        ("hard_availability", None),
    ):
        damaged = deepcopy(result_payload)
        damaged[key] = value
        actions.append(lambda damaged=damaged: PropulsionHardFaultBoundaryResult.parse(damaged))
    damaged = deepcopy(result_payload)
    damaged["hard_availability"]["reasons"] = ["engine_tripped", "fuel_unavailable"]
    actions.append(lambda: PropulsionHardFaultBoundaryResult.parse(damaged))
    damaged_event = deepcopy(result_payload)
    damaged_event["events"][0]["reasons"] = []
    actions.append(lambda: PropulsionHardFaultBoundaryResult.parse(damaged_event))
    reset_payload = apply_propulsion_hard_fault_boundary(
        result.state, facts(step=6), reset_requested=True
    ).to_dict()
    reset_with_live_command = deepcopy(reset_payload)
    reset_with_live_command["state"]["commanded_notch"] = "full"
    actions.append(
        lambda: PropulsionHardFaultBoundaryResult.parse(reset_with_live_command)
    )
    reset_with_wrong_phase = deepcopy(reset_payload)
    reset_with_wrong_phase["events"][0]["previous_phase"] = "ready"
    actions.append(
        lambda: PropulsionHardFaultBoundaryResult.parse(reset_with_wrong_phase)
    )

    actions.extend(
        (
            lambda: apply_propulsion_hard_fault_boundary(None, snapshot),
            lambda: apply_propulsion_hard_fault_boundary(engine_state(), None),
            lambda: apply_propulsion_hard_fault_boundary(
                engine_state(), snapshot, reset_requested=1
            ),
            lambda: evaluate_propulsion_hard_availability(
                facts(actuator_id="engine.other"), engine_state()
            ),
            lambda: apply_propulsion_hard_fault_boundary(
                replace(
                    engine_state("off"),
                    interface_id="gaotian.engine-runtime-state/v1alpha1",
                ),
                snapshot,
            ),
            lambda: validate_propulsion_hard_fault_boundary_result(None, engine_state()),
            lambda: validate_propulsion_hard_fault_boundary_result(
                result, engine_state(actuator_id="engine.other")
            ),
            lambda: validate_propulsion_hard_fault_boundary_result(
                replace(result, source_state_sha256="0" * 64), engine_state()
            ),
        )
    )
    for action in actions:
        refused(action)
    return {"strict_negative_cases": len(actions)}


def check_isolation_and_schemas() -> dict[str, object]:
    for module_order in (
        ("高天荒野舰艇推进硬故障边界", "高天荒野舰艇统一战术场景"),
        ("高天荒野舰艇统一战术场景", "高天荒野舰艇推进硬故障边界"),
    ):
        code = "; ".join(f"import {name}" for name in module_order)
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
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
            "import sys; import 高天荒野舰艇推进硬故障边界; "
            "assert '高天荒野舰艇统一战术场景' not in sys.modules; "
            "assert '高天荒野舰艇运行时参数编译器' not in sys.modules; "
            "assert not any(name.startswith('benchmarks') for name in sys.modules)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr

    schemas = [load_json(ROOT / path) for path in SCHEMAS]
    assert [schema["$id"] for schema in schemas] == [
        HARD_FAULT_SNAPSHOT_INTERFACE_ID,
        HARD_FAULT_BOUNDARY_RESULT_INTERFACE_ID,
    ]
    samples = (
        facts().to_dict(),
        apply_propulsion_hard_fault_boundary(
            engine_state(), facts(fuel_available=False)
        ).to_dict(),
    )
    for schema, sample in zip(schemas, samples):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]) == set(sample)
    result_schema = schemas[1]
    references = []

    def visit(value) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                references.append(value["$ref"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result_schema)
    assert references == [
        "gaotian.propulsion-state-event/v1alpha1",
        HARD_FAULT_SNAPSHOT_INTERFACE_ID,
        "gaotian.tactical-propulsion-state/v3alpha1#/$defs/engineRuntimeState",
    ]
    return {
        "schemas": 2,
        "references_checked": len(references),
        "cold_import_orders": 2,
        "production_imports_scene_runtime_or_benchmarks": False,
    }


def collect_evidence() -> dict[str, object]:
    return {
        "external_reason_matrix": check_external_reason_matrix(),
        "phase_and_category_matrix": check_phase_and_category_matrix(),
        "latch_and_reset": check_latch_and_explicit_reset(),
        "replay": check_replay_and_serialization(),
        "negative_contracts": check_negative_contracts(),
        "isolation": check_isolation_and_schemas(),
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
                "interface": "gaotian.stage-t0b2d4a-hard-fault-boundary/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
