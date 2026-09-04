"""T0b.2d4.5：验证完整无场景硬故障、互锁、时间、交付与软安全组合。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from benchmarks.t0.metadata import file_sha256
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import engines as physical_engines, fixture
from 高天荒野T0b2d3b整舰安全判定测试 import PROFILE
from 高天荒野T0b2d3d无场景受控推进适配器测试 import (
    basic_initialized_fixture,
    propulsion_state,
)
from 高天荒野T0b2d4d方向互锁边界测试 import (
    state_with_outputs,
    translation_control,
    yaw_control,
)
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇受控推进完整安全适配器 import (
    FULLY_GOVERNED_CLOSING_INTERFACE_ID,
    FULLY_GOVERNED_OPENING_INTERFACE_ID,
    FULLY_GOVERNED_POLICY_ID,
    FullyGovernedPropulsionClosing,
    FullyGovernedPropulsionOpening,
    commit_fully_governed_propulsion_opening,
    evaluate_fully_governed_propulsion_closing,
    integrate_fully_governed_propulsion_interval,
    validate_fully_governed_propulsion_closing,
    validate_fully_governed_propulsion_interval,
    validate_fully_governed_propulsion_opening,
)
from 高天荒野舰艇受控推进硬故障适配器 import (
    GovernedPropulsionHardFaultCommand,
)
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    TRANSLATION_CHANNELS,
    YAW_CHANNELS,
    ChannelPropulsionCommand,
)
import 高天荒野舰艇战术机动求解器 as dynamics


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d4e完整受控推进适配器接口.v1.json"
SCHEMAS = (
    ROOT / "舰艇数据/模式/高天荒野舰艇完整受控推进开边界契约.v1alpha1.schema.json",
    ROOT / "舰艇数据/模式/高天荒野舰艇完整受控推进收边界契约.v1alpha1.schema.json",
)


def refused(action, code: str | None = None) -> None:
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def with_clock(state, step=0):
    return replace(
        state,
        governors=tuple(
            replace(item, last_evaluated_step_index=step)
            for item in state.governors
        ),
    )


def open_step(
    context,
    runtime,
    state,
    source_control,
    requested_control,
    hard_command=None,
    *,
    step=0,
):
    result = commit_fully_governed_propulsion_opening(
        context,
        runtime,
        state,
        source_control,
        requested_control,
        hard_command or GovernedPropulsionHardFaultCommand(),
        fixed_step_index=step,
    )
    validate_fully_governed_propulsion_opening(
        result,
        context,
        runtime,
        state,
        hard_command or GovernedPropulsionHardFaultCommand(),
    )
    return result


def close_step(context, runtime, model, motion, opening, profile=PROFILE, *, status="delivered"):
    result = evaluate_fully_governed_propulsion_closing(
        context,
        opening,
        profile,
        runtime,
        model,
        motion,
        fixed_step_index=opening.fixed_step_index + 1,
        propulsion_delivery_status=status,
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    validate_fully_governed_propulsion_closing(
        result, context, profile, runtime, model, motion
    )
    return result


def check_order_and_normal_step() -> dict[str, object]:
    context, runtime, model, motion, source_control, initialized = (
        basic_initialized_fixture()
    )
    requested = translation_control(TRANSLATION_CHANNELS[0], "dead_slow")
    opening = open_step(
        context,
        runtime,
        initialized.state,
        source_control,
        requested,
    )
    assert all(item.action == "available" for item in opening.hard_fault_opening.hard_fault_results)
    assert not opening.direction_interlock.blocked_channels
    assert opening.requested_control == requested
    assert tuple(item.command for item in opening.state.governors) == requested.channel_commands
    assert all(item.last_evaluated_step_index == 0 for item in opening.state.governors)
    assert opening.propulsion_events == opening.hard_fault_events + opening.time_events
    assert opening.resulting_state_sha256 == canonical_sha256(opening.state)

    interval = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status="delivered",
    )
    validate_fully_governed_propulsion_interval(
        interval, context, runtime, model, motion, opening
    )
    assert interval.resulting_motion.fixed_step_index == 1
    closing = close_step(
        context, runtime, model, interval.resulting_motion, opening
    )
    assert closing.state.interface_id == initialized.state.interface_id
    assert tuple(item.command for item in closing.state.governors) == requested.channel_commands
    assert all(item.last_evaluated_step_index == 1 for item in closing.state.governors)
    assert closing.final_runtime_sha256 == runtime.source_sha256
    return {
        "pipeline_order": [
            "hard_fault",
            "direction_interlock",
            "opening_time",
            "physical_delivery",
            "closing_time_and_soft_safety",
        ],
        "actuators": len(context.bindings),
        "source_step": 0,
        "resulting_step": 1,
        "requested_governor_commands_persist": True,
        "old_d3_adapter_changed": False,
    }


def check_fault_reset_and_emergency() -> dict[str, object]:
    context, runtime, model = fixture()
    source_control = directional_control()
    base = propulsion_state(
        context, physical_engines(context), source_control, 0
    )
    port = next(item for item in base.engines if item.actuator_instance_id == "main_engine_port")
    tripped = replace(
        base,
        engines=tuple(
            replace(
                item,
                phase="tripped",
                target_output_percent=0,
                actual_output_percent=0,
                ready_at_fixed_step=None,
                next_transition_step=None,
                response_started_at_fixed_step=None,
                response_start_output_percent=None,
            )
            if item == port
            else item
            for item in base.engines
        ),
    )
    requested = translation_control(TRANSLATION_CHANNELS[0], "full")
    reset_command = GovernedPropulsionHardFaultCommand(("main_engine_port",))
    reset = open_step(
        context,
        runtime,
        tripped,
        source_control,
        requested,
        reset_command,
    )
    reset_engine = next(
        item for item in reset.state.engines if item.actuator_instance_id == "main_engine_port"
    )
    other_engine = next(
        item
        for item in reset.state.engines
        if item.actuator_instance_id == "main_engine_starboard"
    )
    assert reset_engine.phase == "off" and reset_engine.target_output_percent == 0
    assert other_engine.target_output_percent == 100
    assert any(item.action == "reset" for item in reset.hard_fault_opening.hard_fault_results)

    running = state_with_outputs(
        context,
        {TRANSLATION_CHANNELS[0]: ("running", 50, 50)},
        translation_control(TRANSLATION_CHANNELS[0], "half"),
    )
    running = with_clock(running)
    emergency = open_step(
        context,
        runtime,
        running,
        translation_control(TRANSLATION_CHANNELS[0], "half"),
        requested,
        GovernedPropulsionHardFaultCommand(
            emergency_cut_cause="safety_system_requested"
        ),
    )
    assert all(item.target_output_percent == 0 for item in emergency.state.engines)
    assert all(
        item.action == "emergency_cut_hold"
        for item in emergency.direction_interlock.decisions
    )
    assert not emergency.time_events

    empty = lambda instance: replace(
        instance,
        operational_state=replace(instance.operational_state, fuel_units=0.0),
    )
    failed_context, failed_runtime, failed_model = fixture(mutate=empty)
    failed_source = propulsion_state(
        failed_context,
        physical_engines(failed_context),
        source_control,
        0,
    )
    failed = open_step(
        failed_context,
        failed_runtime,
        failed_source,
        source_control,
        translation_control(TRANSLATION_CHANNELS[0], "full", overg=True),
    )
    assert all(item.phase == "tripped" for item in failed.state.engines)
    assert len(failed.hard_fault_events) == len(failed_context.bindings)
    assert not failed.time_events
    failed_motion = dynamics.initialize_tactical_motion_state(failed_model)
    interval = integrate_fully_governed_propulsion_interval(
        failed_context,
        failed_runtime,
        failed_model,
        failed_motion,
        failed,
        propulsion_delivery_status="delivered",
    )
    assert interval.aggregation.request.force_body_n == (0.0, 0.0)
    closing = close_step(
        failed_context,
        failed_runtime,
        failed_model,
        interval.resulting_motion,
        failed,
    )
    assert all(item.phase == "tripped" for item in closing.state.engines)
    return {
        "explicit_reset_held_off_for_opening_boundary": True,
        "healthy_peer_can_receive_command": True,
        "emergency_cut_prevents_same_boundary_restart": True,
        "hard_trip_events_precede_time_events": True,
        "tripped_actuators_survive_interval_and_closing": True,
        "overg_bypasses_hard_fault": False,
    }


def check_interlock_delivery_and_soft_safety() -> dict[str, object]:
    context, runtime, model = fixture()
    old_control = yaw_control(YAW_CHANNELS[0], 50)
    source = with_clock(
        state_with_outputs(
            context,
            {YAW_CHANNELS[0]: ("running", 50, 50)},
            old_control,
        )
    )
    requested = yaw_control(YAW_CHANNELS[1], 100)
    opening = open_step(
        context, runtime, source, old_control, requested
    )
    assert opening.direction_interlock.blocked_channels == (YAW_CHANNELS[1],)
    assert tuple(item.command for item in opening.state.governors) == requested.channel_commands
    assert next(
        item
        for item in opening.state.engines
        if item.actuator_instance_id == "thruster_port_aft"
    ).target_output_percent == 0
    assert all(
        item.target_output_percent == 0
        for item in opening.state.engines
        if item.actuator_instance_id in {"thruster_port_fore", "thruster_starboard_aft"}
    )

    motion = dynamics.initialize_tactical_motion_state(model)
    delivered = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status="delivered",
    )
    assert delivered.aggregation.request.torque_n_m != 0.0
    assert delivered.aggregation.request.fuel_units_per_s > 0.0
    suppressed = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status="suppressed_uncommanded",
    )
    assert suppressed.delivered_request.force_body_n == (0.0, 0.0)

    strict_profile = replace(
        PROFILE,
        id="gtw.safety.vector.d4.strict",
        structure_engage_ratio=0.0001,
        structure_release_ratio=0.00005,
        crew_engage_g=0.0001,
        crew_release_g=0.00005,
    )
    closing = close_step(
        context,
        runtime,
        model,
        suppressed.resulting_motion,
        opening,
        strict_profile,
        status="suppressed_uncommanded",
    )
    assert closing.safety_result.event_intents
    assert tuple(item.command for item in closing.state.governors) == requested.channel_commands
    assert tuple(item.command for item in closing.safety_result.governors) == opening.direction_interlock.effective_control.channel_commands
    assert any(item.safety_reasons for item in closing.state.governors)
    assert opening.state.governors != closing.state.governors
    return {
        "opposed_new_direction_delivered": False,
        "old_direction_downstage_target_committed": True,
        "physical_interval_uses_old_actual_output": True,
        "lifecycle_suppression_zeroes_delivery": True,
        "soft_safety_history_updated": True,
        "requested_commands_restored_after_shadow_safety": True,
    }


def check_replay_and_serialization() -> dict[str, object]:
    context, runtime, model, motion, source_control, initialized = (
        basic_initialized_fixture()
    )
    requested = translation_control(TRANSLATION_CHANNELS[0], "dead_slow")
    traces = []
    for _ in range(3):
        opening = open_step(
            context,
            runtime,
            initialized.state,
            source_control,
            requested,
        )
        opening = FullyGovernedPropulsionOpening.parse(
            json.loads(json.dumps(opening.to_dict()))
        )
        interval = integrate_fully_governed_propulsion_interval(
            context,
            runtime,
            model,
            motion,
            opening,
            propulsion_delivery_status="delivered",
        )
        closing = close_step(
            context, runtime, model, interval.resulting_motion, opening
        )
        closing = FullyGovernedPropulsionClosing.parse(
            json.loads(json.dumps(closing.to_dict()))
        )
        validate_fully_governed_propulsion_closing(
            closing,
            context,
            PROFILE,
            runtime,
            model,
            interval.resulting_motion,
        )
        traces.append(
            {
                "opening": opening.to_dict(),
                "interval": {
                    "aggregation": interval.aggregation.to_dict(),
                    "delivered_request": interval.delivered_request.to_dict(),
                    "resulting_motion": interval.resulting_motion.to_dict(),
                    "diagnostics": interval.diagnostics.to_dict(),
                },
                "closing": closing.to_dict(),
            }
        )
    assert traces[0] == traces[1] == traces[2]
    return {
        "replays": len(traces),
        "boundaries_per_replay": 3,
        "reload_boundaries": [0, 2],
        "trace_sha256": canonical_sha256(traces[0]),
    }


def check_negative_contracts() -> dict[str, object]:
    context, runtime, model, motion, source_control, initialized = (
        basic_initialized_fixture()
    )
    requested = translation_control(TRANSLATION_CHANNELS[0], "dead_slow")
    opening = open_step(
        context,
        runtime,
        initialized.state,
        source_control,
        requested,
    )
    interval = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status="delivered",
    )
    closing = close_step(
        context, runtime, model, interval.resulting_motion, opening
    )
    actions = []

    opening_payload = opening.to_dict()
    for key in opening_payload:
        damaged = deepcopy(opening_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: FullyGovernedPropulsionOpening.parse(damaged)
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("fixed_step_index", True),
        ("source_state_sha256", "bad"),
        ("resulting_state_sha256", "bad"),
        ("time_results", None),
    ):
        damaged = deepcopy(opening_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: FullyGovernedPropulsionOpening.parse(damaged)
        )
    reversed_time = deepcopy(opening_payload)
    reversed_time["time_results"].reverse()
    actions.append(lambda: FullyGovernedPropulsionOpening.parse(reversed_time))
    wrong_result_hash = deepcopy(opening_payload)
    wrong_result_hash["resulting_state_sha256"] = "0" * 64
    actions.append(lambda: FullyGovernedPropulsionOpening.parse(wrong_result_hash))
    wrong_source_control = deepcopy(opening_payload)
    wrong_source_control["source_control"] = requested.to_dict()
    actions.append(lambda: FullyGovernedPropulsionOpening.parse(wrong_source_control))

    closing_payload = closing.to_dict()
    for key in closing_payload:
        damaged = deepcopy(closing_payload)
        del damaged[key]
        actions.append(
            lambda damaged=damaged: FullyGovernedPropulsionClosing.parse(damaged)
        )
    for key, value in (
        ("extra", True),
        ("interface", "unknown"),
        ("policy", "unknown"),
        ("fixed_step_index", True),
        ("source_opening_sha256", "bad"),
        ("resulting_state_sha256", "bad"),
        ("final_runtime_sha256", "bad"),
        ("final_motion_sha256", "bad"),
        ("crew_safety_lock_enabled", 1),
        ("propulsion_delivery_status", "exited_frozen"),
    ):
        damaged = deepcopy(closing_payload)
        damaged[key] = value
        actions.append(
            lambda damaged=damaged: FullyGovernedPropulsionClosing.parse(damaged)
        )
    wrong_closing_hash = deepcopy(closing_payload)
    wrong_closing_hash["resulting_state_sha256"] = "0" * 64
    actions.append(lambda: FullyGovernedPropulsionClosing.parse(wrong_closing_hash))

    actions.extend(
        (
            lambda: commit_fully_governed_propulsion_opening(
                context,
                runtime,
                initialized.state,
                source_control,
                requested,
                GovernedPropulsionHardFaultCommand(),
                fixed_step_index=True,
            ),
            lambda: commit_fully_governed_propulsion_opening(
                context,
                runtime,
                initialized.state,
                requested,
                requested,
                GovernedPropulsionHardFaultCommand(),
                fixed_step_index=0,
            ),
            lambda: validate_fully_governed_propulsion_opening(
                None,
                context,
                runtime,
                initialized.state,
                GovernedPropulsionHardFaultCommand(),
            ),
            lambda: integrate_fully_governed_propulsion_interval(
                context,
                runtime,
                model,
                replace(motion, fixed_step_index=1),
                opening,
                propulsion_delivery_status="delivered",
            ),
            lambda: integrate_fully_governed_propulsion_interval(
                context,
                runtime,
                model,
                motion,
                opening,
                propulsion_delivery_status="exited_frozen",
            ),
            lambda: validate_fully_governed_propulsion_interval(
                None, context, runtime, model, motion, opening
            ),
            lambda: evaluate_fully_governed_propulsion_closing(
                context,
                opening,
                PROFILE,
                runtime,
                model,
                interval.resulting_motion,
                fixed_step_index=2,
                propulsion_delivery_status="delivered",
                crew_safety_lock_enabled=True,
            ),
            lambda: validate_fully_governed_propulsion_closing(
                None,
                context,
                PROFILE,
                runtime,
                model,
                interval.resulting_motion,
            ),
        )
    )
    for action in actions:
        refused(action)
    return {"strict_negative_cases": len(actions)}


def check_schema_and_isolation() -> dict[str, object]:
    schemas = [load_json(path) for path in SCHEMAS]
    assert [item["$id"] for item in schemas] == [
        FULLY_GOVERNED_OPENING_INTERFACE_ID,
        FULLY_GOVERNED_CLOSING_INTERFACE_ID,
    ]
    context, runtime, model, motion, source_control, initialized = (
        basic_initialized_fixture()
    )
    opening = open_step(
        context,
        runtime,
        initialized.state,
        source_control,
        translation_control(TRANSLATION_CHANNELS[0], "dead_slow"),
    )
    interval = integrate_fully_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening,
        propulsion_delivery_status="delivered",
    )
    closing = close_step(
        context, runtime, model, interval.resulting_motion, opening
    )
    for schema, sample in zip(schemas, (opening.to_dict(), closing.to_dict())):
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
        "gaotian.governed-propulsion-direction-interlock/v1alpha1",
        "gaotian.governed-propulsion-hard-fault-opening/v1alpha1",
        "gaotian.tactical-propulsion-control/v2alpha1",
        "gaotian.tactical-propulsion-state/v3alpha1",
        "gaotian.governed-propulsion-time-result/v1alpha1",
        FULLY_GOVERNED_OPENING_INTERFACE_ID,
        "gaotian.whole-ship-propulsion-safety-result/v1alpha1",
    }
    for order in (
        ("高天荒野舰艇受控推进完整安全适配器", "高天荒野舰艇统一战术场景"),
        ("高天荒野舰艇统一战术场景", "高天荒野舰艇受控推进完整安全适配器"),
    ):
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", "; ".join(f"import {name}" for name in order)],
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
            "import sys; import 高天荒野舰艇受控推进完整安全适配器; "
            "assert '高天荒野舰艇统一战术场景' not in sys.modules; "
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
        "imports_scene_or_benchmarks": False,
    }


def collect_evidence() -> dict[str, object]:
    return {
        "normal_step": check_order_and_normal_step(),
        "fault_reset_emergency": check_fault_reset_and_emergency(),
        "interlock_delivery_soft_safety": check_interlock_delivery_and_soft_safety(),
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
                "interface": "gaotian.stage-t0b2d4e-fully-governed-adapter/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
