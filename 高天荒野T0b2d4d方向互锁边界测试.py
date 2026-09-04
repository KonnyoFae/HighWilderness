"""T0b.2d4.4：验证硬故障之后、时间提交之前的纯方向互锁。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import fixture
from 高天荒野T0b2d4b硬故障运行时投影测试 import directional_state
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇受控推进硬故障适配器 import (
    GovernedPropulsionHardFaultCommand,
    GovernedPropulsionHardFaultOpening,
    commit_governed_propulsion_hard_fault_opening,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput,
    directional_control,
)
from 高天荒野舰艇推进方向互锁边界 import (
    DIRECTION_INTERLOCK_ACTIONS,
    DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID,
    DIRECTION_INTERLOCK_DECISION_INTERFACE_ID,
    DIRECTION_INTERLOCK_POLICY_ID,
    OPPOSING_CHANNEL,
    GovernedPropulsionDirectionInterlockBoundary,
    PropulsionDirectionInterlockDecision,
    resolve_governed_propulsion_direction_interlock,
    validate_governed_propulsion_direction_interlock,
)
from 高天荒野舰艇推进状态合同 import TacticalPropulsionState
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    OPPOSING_CHANNEL_PAIRS,
    TRANSLATION_CHANNELS,
    YAW_CHANNELS,
    ChannelPropulsionCommand,
    DirectionalPropulsionGovernorState,
)


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d4d方向互锁边界接口.v1.json"
SCHEMAS = (
    ROOT / "舰艇数据/模式/高天荒野舰艇推进方向互锁决定契约.v1alpha1.schema.json",
    ROOT / "舰艇数据/模式/高天荒野舰艇受控推进方向互锁边界契约.v1alpha1.schema.json",
)


def refused(action, code: str | None = None) -> None:
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def yaw_control(
    channel: str | None = None,
    value: int = 50,
    *,
    overg: bool = False,
) -> DirectionalPropulsionControlInput:
    commands = () if channel is None else (ChannelPropulsionCommand(channel, None, value),)
    return directional_control(commands, overg_requested=overg)


def translation_control(
    channel: str | None = None,
    notch: str = "full",
    *,
    overg: bool = False,
) -> DirectionalPropulsionControlInput:
    commands = () if channel is None else (ChannelPropulsionCommand(channel, notch, None),)
    return directional_control(commands, overg_requested=overg)


def state_with_outputs(
    context,
    outputs: dict[str, tuple[str, int, int]],
    governor_control: DirectionalPropulsionControlInput,
) -> TacticalPropulsionState:
    base = directional_state(context)
    bindings = {item.actuator_instance_id: item for item in context.bindings}
    engines = []
    for engine in base.engines:
        channel = bindings[engine.actuator_instance_id].command_channels[0]
        specification = outputs.get(channel)
        if specification is None:
            engines.append(engine)
            continue
        phase, actual, target = specification
        if phase == "running":
            assert actual == target and actual > 0
            engines.append(
                replace(
                    engine,
                    phase="running",
                    commanded_notch=(
                        "half" if engine.actuator_category == "main_engine" else None
                    ),
                    target_output_percent=target,
                    actual_output_percent=actual,
                )
            )
        elif phase == "starting":
            assert actual == 0 and target > 0
            engines.append(
                replace(
                    engine,
                    phase="starting",
                    commanded_notch=(
                        "half" if engine.actuator_category == "main_engine" else None
                    ),
                    target_output_percent=target,
                    actual_output_percent=0,
                    ready_at_fixed_step=10,
                    next_transition_step=10,
                )
            )
        elif phase == "stopping":
            assert actual > 0 and target == 0
            engines.append(
                replace(
                    engine,
                    phase="stopping",
                    commanded_notch=(
                        "stop" if engine.actuator_category == "main_engine" else None
                    ),
                    target_output_percent=0,
                    actual_output_percent=actual,
                    next_transition_step=10,
                    response_started_at_fixed_step=0,
                    response_start_output_percent=actual,
                )
            )
        else:
            raise ValueError(phase)
    governors = tuple(
        DirectionalPropulsionGovernorState(command)
        for command in governor_control.channel_commands
    )
    return TacticalPropulsionState(
        tuple(engines), governors, DIRECTIONAL_STATE_INTERFACE_ID
    )


def hard_opening(
    context,
    runtime,
    state,
    command: GovernedPropulsionHardFaultCommand | None = None,
    *,
    step: int = 5,
):
    return commit_governed_propulsion_hard_fault_opening(
        context,
        runtime,
        state,
        command or GovernedPropulsionHardFaultCommand(),
        fixed_step_index=step,
    )


def resolve(context, opening, control, *, step=5):
    result = resolve_governed_propulsion_direction_interlock(
        context,
        opening,
        control,
        fixed_step_index=step,
    )
    validate_governed_propulsion_direction_interlock(result, context, opening)
    return result


def decision(result, channel):
    return next(item for item in result.decisions if item.command_channel == channel)


def check_channel_matrix_and_passthrough() -> dict[str, object]:
    context, runtime, _ = fixture()
    state = directional_state(context)
    opening = hard_opening(context, runtime, state)
    requested = yaw_control(YAW_CHANNELS[0], 50)
    result = resolve(context, opening, requested)
    assert result.requested_control == result.effective_control == requested
    assert not result.blocked_channels
    assert all(item.action == "pass_through" for item in result.decisions)
    assert tuple(item.command_channel for item in result.decisions) == DIRECTIONAL_CHANNELS
    assert tuple(
        (item.command_channel, item.opposing_channel) for item in result.decisions
    ) == tuple((channel, OPPOSING_CHANNEL[channel]) for channel in DIRECTIONAL_CHANNELS)
    expected_ids = {
        channel: tuple(
            item.actuator_instance_id
            for item in context.bindings
            if item.command_channels == (channel,)
        )
        for channel in DIRECTIONAL_CHANNELS
    }
    assert all(
        item.actuator_instance_ids == expected_ids[item.command_channel]
        for item in result.decisions
    )
    assert result.propulsion_state_sha256 == canonical_sha256(opening.state)
    return {
        "channels": len(DIRECTIONAL_CHANNELS),
        "opposing_pairs": len(OPPOSING_CHANNEL_PAIRS),
        "actuators": len(context.bindings),
        "idle_request_passes_unchanged": True,
        "stable_channel_and_actuator_order": True,
        "state_mutated": False,
    }


def check_block_until_exact_zero() -> dict[str, object]:
    context, runtime, _ = fixture()
    old_control = yaw_control(YAW_CHANNELS[0], 50)
    state = state_with_outputs(
        context,
        {YAW_CHANNELS[0]: ("running", 50, 50)},
        old_control,
    )
    opening = hard_opening(context, runtime, state)
    requested = yaw_control(YAW_CHANNELS[1], 100)
    result = resolve(context, opening, requested)
    blocked = decision(result, YAW_CHANNELS[1])
    expected_blockers = tuple(
        item.actuator_instance_id
        for item in context.bindings
        if item.command_channels == (YAW_CHANNELS[0],)
    )
    assert blocked.action == "blocked_until_opposing_zero"
    assert blocked.blocking_actuator_instance_ids == expected_blockers
    assert blocked.effective_command.requested_percent == 0
    assert decision(result, YAW_CHANNELS[0]).effective_command.requested_percent == 0
    assert result.requested_control == requested
    assert result.effective_control != requested
    assert opening.state == state
    assert opening.state.governors == old_control_to_governors(old_control)

    zero_state = directional_state(context)
    released = resolve(
        context,
        hard_opening(context, runtime, zero_state),
        requested,
    )
    assert released.effective_control == requested
    assert not released.blocked_channels

    starting = state_with_outputs(
        context,
        {YAW_CHANNELS[0]: ("starting", 0, 50)},
        old_control,
    )
    canceled_before_output = resolve(
        context,
        hard_opening(context, runtime, starting),
        requested,
    )
    assert canceled_before_output.effective_control == requested
    return {
        "blocked_channel": YAW_CHANNELS[1],
        "blocking_actuators": len(expected_blockers),
        "old_group_stop_passes": True,
        "requested_control_preserved": True,
        "governor_history_preserved": True,
        "released_at_exact_zero": True,
        "zero_output_starting_group_can_be_canceled_atomically": True,
    }


def old_control_to_governors(control):
    return tuple(
        DirectionalPropulsionGovernorState(item)
        for item in control.channel_commands
    )


def check_translation_and_mid_transition_behavior() -> dict[str, object]:
    context, runtime, _ = fixture()
    forward = directional_state(context, "running")
    reverse = translation_control(TRANSLATION_CHANNELS[1], "full")
    blocked = resolve(context, hard_opening(context, runtime, forward), reverse)
    reverse_decision = decision(blocked, TRANSLATION_CHANNELS[1])
    assert reverse_decision.action == "blocked_until_opposing_zero"
    assert reverse_decision.blocking_actuator_instance_ids == (
        "main_engine_port",
        "main_engine_starboard",
    )
    assert not reverse_decision.actuator_instance_ids

    old_control = yaw_control(YAW_CHANNELS[0], 50)
    old_running = state_with_outputs(
        context,
        {YAW_CHANNELS[0]: ("running", 50, 50)},
        old_control,
    )
    opening = hard_opening(context, runtime, old_running)
    stopped = resolve(context, opening, yaw_control())
    assert stopped.effective_control == stopped.requested_control
    resumed = resolve(context, opening, yaw_control(YAW_CHANNELS[0], 25))
    assert resumed.effective_control == resumed.requested_control

    both_running = state_with_outputs(
        context,
        {
            YAW_CHANNELS[0]: ("running", 50, 50),
            YAW_CHANNELS[1]: ("running", 50, 50),
        },
        old_control,
    )
    converging = resolve(
        context,
        hard_opening(context, runtime, both_running),
        yaw_control(YAW_CHANNELS[1], 50),
    )
    assert all(
        item.requested_percent == 0
        for item in converging.effective_control.channel_commands
    )
    return {
        "translation_reverse_without_bound_actuator_still_blocked": True,
        "stop_during_transition_passes": True,
        "return_to_old_direction_passes": True,
        "preexisting_opposed_output_converges_to_stop": True,
        "interlock_emits_engine_trip": False,
    }


def check_hard_fault_and_emergency_order() -> dict[str, object]:
    running_control = translation_control(TRANSLATION_CHANNELS[0], "half")
    context, runtime, _ = fixture()
    running = state_with_outputs(
        context,
        {TRANSLATION_CHANNELS[0]: ("running", 50, 50)},
        running_control,
    )
    emergency_opening = hard_opening(
        context,
        runtime,
        running,
        GovernedPropulsionHardFaultCommand(
            emergency_cut_cause="operator_requested"
        ),
    )
    requested = translation_control(
        TRANSLATION_CHANNELS[1], "full", overg=True
    )
    held = resolve(context, emergency_opening, requested)
    assert all(item.action == "emergency_cut_hold" for item in held.decisions)
    assert all(
        item.requested_percent == 0
        for item in held.effective_control.channel_commands
    )
    assert held.requested_control == requested
    assert not any(item.blocking_actuator_instance_ids for item in held.decisions)

    empty = lambda instance: replace(
        instance,
        operational_state=replace(instance.operational_state, fuel_units=0.0),
    )
    failed_context, failed_runtime, _ = fixture(mutate=empty)
    failed_running = state_with_outputs(
        failed_context,
        {TRANSLATION_CHANNELS[0]: ("running", 50, 50)},
        running_control,
    )
    tripped_opening = hard_opening(
        failed_context, failed_runtime, failed_running
    )
    assert len(tripped_opening.propulsion_events) == len(failed_context.bindings)
    after_trip = resolve(failed_context, tripped_opening, requested)
    assert after_trip.effective_control == requested
    assert not after_trip.blocked_channels
    assert all(item.phase == "tripped" for item in tripped_opening.state.engines)
    return {
        "emergency_cut_holds_all_channels_for_boundary": True,
        "emergency_cut_actions": len(held.decisions),
        "requested_control_survives_emergency_hold": True,
        "hard_trip_clears_direction_blockers_before_interlock": True,
        "direction_interlock_reclassifies_hard_trip": False,
        "overg_bypasses_emergency_or_hard_fault": False,
    }


def check_replay_and_serialization() -> dict[str, object]:
    context, runtime, _ = fixture()
    old_control = yaw_control(YAW_CHANNELS[0], 50)
    state = state_with_outputs(
        context,
        {YAW_CHANNELS[0]: ("running", 50, 50)},
        old_control,
    )
    opening = hard_opening(context, runtime, state)
    requested = yaw_control(YAW_CHANNELS[1], 100)
    traces = []
    for _ in range(3):
        current = opening
        rows = []
        for index, control in enumerate((requested, yaw_control())):
            result = resolve(context, current, control)
            payload = json.loads(json.dumps(result.to_dict()))
            restored = GovernedPropulsionDirectionInterlockBoundary.parse(payload)
            validate_governed_propulsion_direction_interlock(
                restored, context, current
            )
            rows.append(payload)
            if index == 0:
                current = GovernedPropulsionHardFaultOpening.parse(
                    json.loads(json.dumps(current.to_dict()))
                )
        traces.append(rows)
    assert traces[0] == traces[1] == traces[2]
    return {
        "replays": len(traces),
        "boundaries_per_replay": len(traces[0]),
        "reload_boundaries": [0, 1],
        "trace_sha256": canonical_sha256(traces[0]),
    }


def check_negative_contracts() -> dict[str, object]:
    context, runtime, _ = fixture()
    state = directional_state(context, "running")
    opening = hard_opening(context, runtime, state)
    requested = translation_control(TRANSLATION_CHANNELS[1], "full")
    result = resolve(context, opening, requested)
    sample_decision = decision(result, TRANSLATION_CHANNELS[1])
    actions = []

    decision_payload = sample_decision.to_dict()
    for key in decision_payload:
        damaged = deepcopy(decision_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: PropulsionDirectionInterlockDecision.parse(
                damaged
            )
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("action", "trip"),
        ("command_channel", "forward"),
        ("opposing_channel", TRANSLATION_CHANNELS[2]),
        ("actuator_instance_ids", None),
        ("blocking_actuator_instance_ids", []),
    ):
        damaged = deepcopy(decision_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: PropulsionDirectionInterlockDecision.parse(
                damaged
            )
        )
    reversed_blockers = deepcopy(decision_payload)
    reversed_blockers["blocking_actuator_instance_ids"].reverse()
    actions.append(
        lambda: PropulsionDirectionInterlockDecision.parse(reversed_blockers)
    )
    wrong_effective = deepcopy(decision_payload)
    wrong_effective["effective_command"] = wrong_effective["requested_command"]
    actions.append(
        lambda: PropulsionDirectionInterlockDecision.parse(wrong_effective)
    )

    boundary_payload = result.to_dict()
    for key in boundary_payload:
        damaged = deepcopy(boundary_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: GovernedPropulsionDirectionInterlockBoundary.parse(
                damaged
            )
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("fixed_step_index", True),
        ("source_hard_fault_opening_sha256", "bad"),
        ("propulsion_state_sha256", "bad"),
        ("requested_control_sha256", "bad"),
        ("effective_control_sha256", "bad"),
        ("decisions", None),
    ):
        damaged = deepcopy(boundary_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: GovernedPropulsionDirectionInterlockBoundary.parse(
                damaged
            )
        )
    reversed_decisions = deepcopy(boundary_payload)
    reversed_decisions["decisions"].reverse()
    actions.append(
        lambda: GovernedPropulsionDirectionInterlockBoundary.parse(
            reversed_decisions
        )
    )
    wrong_effective_hash = deepcopy(boundary_payload)
    wrong_effective_hash["effective_control_sha256"] = "0" * 64
    actions.append(
        lambda: GovernedPropulsionDirectionInterlockBoundary.parse(
            wrong_effective_hash
        )
    )
    wrong_blocker_owner = deepcopy(boundary_payload)
    reverse_index = DIRECTIONAL_CHANNELS.index(TRANSLATION_CHANNELS[1])
    wrong_blocker_owner["decisions"][reverse_index][
        "blocking_actuator_instance_ids"
    ] = ["thruster_port_aft"]
    actions.append(
        lambda: GovernedPropulsionDirectionInterlockBoundary.parse(
            wrong_blocker_owner
        )
    )

    actions.extend(
        (
            lambda: resolve_governed_propulsion_direction_interlock(
                None, opening, requested, fixed_step_index=5
            ),
            lambda: resolve_governed_propulsion_direction_interlock(
                context, None, requested, fixed_step_index=5
            ),
            lambda: resolve_governed_propulsion_direction_interlock(
                context, opening, None, fixed_step_index=5
            ),
            lambda: resolve_governed_propulsion_direction_interlock(
                context, opening, requested, fixed_step_index=True
            ),
            lambda: resolve_governed_propulsion_direction_interlock(
                context, opening, requested, fixed_step_index=6
            ),
            lambda: validate_governed_propulsion_direction_interlock(
                None, context, opening
            ),
            lambda: validate_governed_propulsion_direction_interlock(
                result,
                context,
                hard_opening(context, runtime, directional_state(context)),
            ),
        )
    )
    for action in actions:
        refused(action)
    return {"strict_negative_cases": len(actions)}


def check_schema_and_isolation() -> dict[str, object]:
    schemas = [load_json(path) for path in SCHEMAS]
    assert [item["$id"] for item in schemas] == [
        DIRECTION_INTERLOCK_DECISION_INTERFACE_ID,
        DIRECTION_INTERLOCK_BOUNDARY_INTERFACE_ID,
    ]
    context, runtime, _ = fixture()
    opening = hard_opening(context, runtime, directional_state(context))
    boundary = resolve(context, opening, yaw_control(YAW_CHANNELS[0], 50))
    samples = (boundary.decisions[0].to_dict(), boundary.to_dict())
    for schema, sample in zip(schemas, samples):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]) == set(sample)

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
        "gaotian.tactical-propulsion-control/v2alpha1#/$defs/channelCommand",
        "gaotian.tactical-propulsion-control/v2alpha1",
        DIRECTION_INTERLOCK_DECISION_INTERFACE_ID,
    }
    for order in (
        ("高天荒野舰艇推进方向互锁边界", "高天荒野舰艇统一战术场景"),
        ("高天荒野舰艇统一战术场景", "高天荒野舰艇推进方向互锁边界"),
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
            "import sys; import 高天荒野舰艇推进方向互锁边界; "
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
        "imports_scene_d3_adapter_or_benchmarks": False,
    }


def collect_evidence() -> dict[str, object]:
    return {
        "channel_matrix": check_channel_matrix_and_passthrough(),
        "block_and_release": check_block_until_exact_zero(),
        "mid_transition": check_translation_and_mid_transition_behavior(),
        "hard_fault_and_emergency": check_hard_fault_and_emergency_order(),
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
                "interface": "gaotian.stage-t0b2d4d-direction-interlock/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
