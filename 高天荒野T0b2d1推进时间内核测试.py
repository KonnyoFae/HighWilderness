from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import ceil
from pathlib import Path
from typing import Callable

from benchmarks.t0.contracts import load_benchmark_plan
from benchmarks.t0.diagnostics import (
    load_authority_step_golden,
    verify_authority_step_golden,
)
from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import (
    ContractError,
    ModuleCapability,
    canonical_sha256,
    load_json,
)
from 高天荒野舰艇推进安全判定器 import THRUST_OUTPUT_STAGES_PERCENT
from 高天荒野舰艇推进时间内核 import (
    FIXED_STEP_HZ,
    PROPULSION_TIME_BOUNDARY_INTERFACE_ID,
    PropulsionTimeBoundaryResult,
    PropulsionTimeCommand,
    advance_propulsion_time_boundary,
)
from 高天荒野舰艇推进状态合同 import (
    EngineRuntimeState,
    migrate_engine_runtime_state_from_module_mode,
)


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "contracts" / "web_bridge" / "t0-benchmark-plan.v1.json"
GOLDEN_PATH = ROOT / "contracts" / "web_bridge" / "t0-authority-step-golden.v1.json"
STATE_SCHEMA_PATH = (
    ROOT / "舰艇数据" / "模式" / "高天荒野舰艇推进状态契约.v2alpha1.schema.json"
)
RESULT_SCHEMA_PATH = (
    ROOT
    / "舰艇数据"
    / "模式"
    / "高天荒野舰艇推进时间边界结果契约.v1alpha1.schema.json"
)
REPORT_PATH = ROOT / "舰艇数据" / "报告" / "阶段T0b2d1推进时间内核接口.v1.json"


def require_contract_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        if error.code != code:
            raise AssertionError(f"预期错误 {code}，实际为 {error.code}: {error}") from error
    else:
        raise AssertionError(f"预期抛出 {code}")


def propulsion_capability(
    category: str,
    *,
    startup_time_s: float,
    response_time_s: float,
    version: int = 2,
) -> ModuleCapability:
    value = {
        "fuel_units_per_s": 1.0,
        "kind": category,
        "local_thrust_axis": "+Y",
        "response_time_s": response_time_s,
        "thrust_n": 1000.0,
    }
    if version == 2:
        value["startup_time_s"] = startup_time_s
    return ModuleCapability.parse(
        value,
        "$.capability",
        propulsion_capability_version=version,
    )


def main_capability(response_time_s: float = 1.0) -> ModuleCapability:
    return propulsion_capability(
        "main_engine",
        startup_time_s=1.0,
        response_time_s=response_time_s,
    )


def thruster_capability(response_time_s: float = 1.0) -> ModuleCapability:
    return propulsion_capability(
        "maneuver_thruster",
        startup_time_s=0.0,
        response_time_s=response_time_s,
    )


def test_boundary_contract_and_capability_gate() -> dict[str, object]:
    result_schema = load_json(RESULT_SCHEMA_PATH)
    state_schema = load_json(STATE_SCHEMA_PATH)
    assert result_schema["$id"] == PROPULSION_TIME_BOUNDARY_INTERFACE_ID
    assert result_schema["additionalProperties"] is False
    assert result_schema["properties"]["state"]["$ref"] == (
        "gaotian.tactical-propulsion-state/v2alpha1#/$defs/engineRuntimeState"
    )
    engine_definition = state_schema["$defs"]["engineRuntimeState"]
    assert {
        "response_start_output_percent",
        "response_started_at_fixed_step",
    }.issubset(engine_definition["required"])

    state = migrate_engine_runtime_state_from_module_mode(
        "engine.contract",
        "main_engine",
        "off",
        0,
    )
    result = advance_propulsion_time_boundary(
        state,
        main_capability(),
        0,
        PropulsionTimeCommand.main_engine("full"),
    )
    assert PropulsionTimeBoundaryResult.parse(result.to_dict()).to_dict() == result.to_dict()

    v1_capability = propulsion_capability(
        "main_engine",
        startup_time_s=0.0,
        response_time_s=1.0,
        version=1,
    )
    require_contract_error(
        "propulsion_time.capability_version",
        lambda: advance_propulsion_time_boundary(
            state,
            v1_capability,
            0,
            PropulsionTimeCommand.main_engine("full"),
        ),
    )
    for capability in (main_capability(0.5), thruster_capability(0.2)):
        command = (
            PropulsionTimeCommand.main_engine("full")
            if capability.kind == "main_engine"
            else PropulsionTimeCommand.maneuver_thruster(100)
        )
        candidate_state = migrate_engine_runtime_state_from_module_mode(
            f"engine.unschedulable.{capability.kind}",
            capability.kind,
            "off",
            0,
        )
        require_contract_error(
            "propulsion_time.response_unschedulable",
            lambda capability=capability, command=command, candidate_state=candidate_state: (
                advance_propulsion_time_boundary(
                    candidate_state,
                    capability,
                    0,
                    command,
                )
            ),
        )
    require_contract_error(
        "propulsion_time.capability_category",
        lambda: advance_propulsion_time_boundary(
            state,
            thruster_capability(),
            0,
            PropulsionTimeCommand.main_engine("full"),
        ),
    )
    require_contract_error(
        "propulsion_time.command_category",
        lambda: advance_propulsion_time_boundary(
            state,
            main_capability(),
            0,
            PropulsionTimeCommand.maneuver_thruster(100),
        ),
    )

    result_extra = result.to_dict()
    result_extra["implicit_default"] = True
    require_contract_error(
        "object.keys",
        lambda: PropulsionTimeBoundaryResult.parse(result_extra),
    )
    invalid_schedule = migrate_engine_runtime_state_from_module_mode(
        "engine.invalid.schedule",
        "main_engine",
        "active",
        0,
    ).to_dict()
    invalid_schedule.update(
        {
            "actual_output_percent": 0,
            "next_transition_step": 2,
            "phase": "running",
            "target_output_percent": 100,
        }
    )
    require_contract_error(
        "propulsion_state.engine_invariant",
        lambda: EngineRuntimeState.parse(invalid_schedule, "$.invalid_schedule"),
    )
    return {
        "fixed_step_hz": FIXED_STEP_HZ,
        "result_roundtrips": 1,
        "strict_negative_cases": 7,
        "unschedulable_compatibility_capabilities_rejected": 2,
    }


def test_cold_start_and_adjacent_response() -> dict[str, object]:
    capability = main_capability()
    state = migrate_engine_runtime_state_from_module_mode(
        "engine.cold.main",
        "main_engine",
        "off",
        0,
    )
    command = PropulsionTimeCommand.main_engine("full")
    results: list[PropulsionTimeBoundaryResult] = []
    for fixed_step_index in range(0, 121):
        result = advance_propulsion_time_boundary(
            state,
            capability,
            fixed_step_index,
            command,
        )
        state = result.state
        results.append(result)

    assert results[0].state.phase == "starting"
    assert results[0].state.ready_at_fixed_step == 60
    assert [item.state.actual_output_percent for item in results[:60]] == [0] * 60
    assert results[60].state.phase == "running"
    assert results[60].state.actual_output_percent == 0
    assert [item.kind for item in results[60].events] == ["engine_start_completed"]

    stage_events = [
        event
        for result in results
        for event in result.events
        if event.kind == "engine_output_stage_changed"
    ]
    assert tuple(item.resulting_stage_percent for item in stage_events) == (
        THRUST_OUTPUT_STAGES_PERCENT[1:]
    )
    assert tuple(item.fixed_step_index for item in stage_events) == tuple(
        60 + ceil(60 * stage / 100)
        for stage in THRUST_OUTPUT_STAGES_PERCENT[1:]
    )
    assert stage_events[-1].fixed_step_index - 60 == 60
    assert state.actual_output_percent == 100
    assert state.next_transition_step is None
    assert all(
        sum(event.kind == "engine_output_stage_changed" for event in result.events)
        <= 1
        for result in results
    )

    thruster = migrate_engine_runtime_state_from_module_mode(
        "thruster.zero.startup",
        "maneuver_thruster",
        "off",
        0,
    )
    thruster_result = advance_propulsion_time_boundary(
        thruster,
        thruster_capability(),
        0,
        PropulsionTimeCommand.maneuver_thruster(100),
    )
    assert thruster_result.state.phase == "running"
    assert thruster_result.state.ready_at_fixed_step == 0
    assert thruster_result.state.actual_output_percent == 0
    assert [item.kind for item in thruster_result.events] == [
        "engine_start_requested",
        "engine_start_completed",
    ]
    return {
        "adjacent_upstage_events": len(stage_events),
        "cold_start_completed_at_step": 60,
        "full_output_completed_at_step": stage_events[-1].fixed_step_index,
        "maneuver_thruster_zero_startup": True,
        "response_steps": 60,
    }


def test_mid_command_and_stop() -> dict[str, object]:
    capability = main_capability()
    state = migrate_engine_runtime_state_from_module_mode(
        "engine.response.change",
        "main_engine",
        "active",
        0,
    )
    full = PropulsionTimeCommand.main_engine("full")
    quarter = PropulsionTimeCommand.main_engine("quarter")
    stop = PropulsionTimeCommand.main_engine("stop")

    result = advance_propulsion_time_boundary(state, capability, 0, full)
    state = result.state
    result_at_12: PropulsionTimeBoundaryResult | None = None
    for fixed_step_index in range(1, 13):
        result = advance_propulsion_time_boundary(
            state,
            capability,
            fixed_step_index,
            quarter if fixed_step_index == 12 else full,
        )
        state = result.state
        if fixed_step_index == 12:
            result_at_12 = result
    assert result_at_12 is not None
    assert state.actual_output_percent == 20
    assert state.target_output_percent == 25
    assert state.response_started_at_fixed_step == 12
    assert state.response_start_output_percent == 20
    assert state.next_transition_step == 15
    assert sum(
        item.kind == "engine_output_stage_changed" for item in result_at_12.events
    ) == 1
    for fixed_step_index in range(13, 16):
        result = advance_propulsion_time_boundary(
            state,
            capability,
            fixed_step_index,
            quarter,
        )
        state = result.state
    assert state.actual_output_percent == state.target_output_percent == 25
    assert state.response_started_at_fixed_step is None

    settled_full = EngineRuntimeState(
        "engine.stop.full",
        "main_engine",
        "running",
        "full",
        100,
        100,
        0,
        None,
        None,
        None,
    )
    result = advance_propulsion_time_boundary(settled_full, capability, 200, stop)
    state = result.state
    assert state.phase == "stopping"
    assert state.next_transition_step == 203
    assert [item.kind for item in result.events] == ["engine_stop_requested"]
    stage_events = []
    final_events = []
    for fixed_step_index in range(201, 261):
        result = advance_propulsion_time_boundary(
            state,
            capability,
            fixed_step_index,
            stop,
        )
        state = result.state
        stage_events.extend(
            item
            for item in result.events
            if item.kind == "engine_output_stage_changed"
        )
        if fixed_step_index == 260:
            final_events = [item.kind for item in result.events]
    assert tuple(item.resulting_stage_percent for item in stage_events) == tuple(
        reversed(THRUST_OUTPUT_STAGES_PERCENT[:-1])
    )
    assert tuple(item.fixed_step_index for item in stage_events) == tuple(
        200 + ceil(60 * abs(stage - 100) / 100)
        for stage in reversed(THRUST_OUTPUT_STAGES_PERCENT[:-1])
    )
    assert state.phase == "ready"
    assert state.actual_output_percent == 0
    assert final_events == ["engine_output_stage_changed", "engine_stopped"]

    missed = advance_propulsion_time_boundary(
        migrate_engine_runtime_state_from_module_mode(
            "engine.missed",
            "main_engine",
            "active",
            0,
        ),
        capability,
        0,
        full,
    ).state
    require_contract_error(
        "propulsion_time.missed_boundary",
        lambda: advance_propulsion_time_boundary(missed, capability, 3, full),
    )
    tripped = replace(
        migrate_engine_runtime_state_from_module_mode(
            "engine.tripped",
            "main_engine",
            "off",
            0,
        ),
        phase="tripped",
    )
    require_contract_error(
        "propulsion_time.tripped_command",
        lambda: advance_propulsion_time_boundary(tripped, capability, 0, full),
    )
    return {
        "adjacent_downstage_events": len(stage_events),
        "mid_command_origin_percent": 20,
        "mid_command_target_percent": 25,
        "missed_boundary_and_tripped_rejected": 2,
        "stop_completed_at_step": 260,
    }


def _replay_hash() -> str:
    capability = main_capability()
    state = migrate_engine_runtime_state_from_module_mode(
        "engine.replay",
        "main_engine",
        "off",
        0,
    )
    timeline = []
    for fixed_step_index in range(0, 161):
        if fixed_step_index < 73:
            notch = "full"
        elif fixed_step_index < 91:
            notch = "quarter"
        else:
            notch = "stop"
        result = advance_propulsion_time_boundary(
            state,
            capability,
            fixed_step_index,
            PropulsionTimeCommand.main_engine(notch),
        )
        state = result.state
        timeline.append(result.to_dict())
    return canonical_sha256(timeline)


def test_replay_and_scene_isolation() -> dict[str, object]:
    replay_hashes = tuple(_replay_hash() for _ in range(3))
    assert len(set(replay_hashes)) == 1
    unified_text = (ROOT / "高天荒野舰艇统一战术场景.py").read_text(encoding="utf-8")
    kernel_text = (ROOT / "高天荒野舰艇推进时间内核.py").read_text(encoding="utf-8")
    assert "高天荒野舰艇推进时间内核" not in unified_text
    assert "高天荒野舰艇统一战术场景" not in kernel_text
    golden = load_authority_step_golden(GOLDEN_PATH)
    assert verify_authority_step_golden(
        ROOT,
        load_benchmark_plan(PLAN_PATH),
        GOLDEN_PATH,
    )["cases"] == golden["cases"]
    return {
        "authority_golden": "12_of_12_PASS",
        "deterministic_replays": 3,
        "propulsion_mechanics_wired": False,
        "replay_sha256": replay_hashes[0],
        "tactical_scene_imports_time_kernel": False,
    }


def main() -> None:
    contract_evidence = test_boundary_contract_and_capability_gate()
    startup_evidence = test_cold_start_and_adjacent_response()
    response_evidence = test_mid_command_and_stop()
    isolation_evidence = test_replay_and_scene_isolation()

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["interface"] == "gaotian.stage-t0b2d1-propulsion-time-kernel/v1"
    assert report["status"] == "PASS"
    assert report["contract_evidence"] == contract_evidence
    assert report["startup_evidence"] == startup_evidence
    assert report["response_evidence"] == response_evidence
    assert report["isolation_evidence"] == isolation_evidence
    assert report["authority_golden"] == "12_of_12_PASS"
    assert report["official_performance_runs_executed"] == 0
    assert report["next_slice"] == "T0b.2d2_propulsion_scene_integration"
    for relative_path in (
        "舰艇数据/模式/高天荒野舰艇推进状态契约.v2alpha1.schema.json",
        "舰艇数据/模式/高天荒野舰艇推进时间边界结果契约.v1alpha1.schema.json",
        "高天荒野T0b2d1推进时间内核测试.py",
        "高天荒野T0b2推进响应与权威性能优化规划.md",
        "高天荒野Web客户端编辑器与战术验证实施计划.md",
        "高天荒野舰艇推进时间内核.py",
        "高天荒野舰艇推进状态合同.py",
        "高天荒野舰艇统一战术场景.py",
    ):
        assert report["implementation_hashes"][relative_path] == file_sha256(
            ROOT / relative_path
        )
    print(
        json.dumps(
            {
                "authority_golden": "12_of_12_PASS",
                "interface": "gaotian.stage-t0b2d1-propulsion-time-kernel-test/v1",
                "replays": isolation_evidence["deterministic_replays"],
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
