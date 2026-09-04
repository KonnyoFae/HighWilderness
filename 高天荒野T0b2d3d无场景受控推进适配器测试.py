"""d3.3 第二阶段：无场景受控推进适配器、抑制口径与精确逐舰重放。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
from 高天荒野舰艇推进状态合同 import TacticalPropulsionState
from 高天荒野舰艇推进通道合同 import (
    DIRECTIONAL_CHANNELS,
    DIRECTIONAL_STATE_INTERFACE_ID,
    ChannelPropulsionCommand,
    DirectionalPropulsionGovernorState,
)
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput,
    automatic_linear_brake_control,
    directional_control,
)
from 高天荒野舰艇受控推进场景合同 import (
    GovernedPropulsionClosingRecord,
    GovernedPropulsionOpeningRecord,
    GovernedScenePropulsionSafetyEvent,
)
from 高天荒野舰艇受控推进无场景适配器 import (
    GovernedPropulsionClosingOutcome,
    GovernedPropulsionDeliveryLoadSampler,
    GovernedPropulsionOpeningOutcome,
    commit_governed_propulsion_opening,
    evaluate_governed_propulsion_closing,
    initialize_governed_propulsion_state,
    integrate_governed_propulsion_interval,
    validate_governed_propulsion_closing_replay,
    validate_governed_propulsion_interval_replay,
    validate_governed_propulsion_opening_replay,
)
from 高天荒野舰艇推进向量载荷 import WholeShipVectorLoadSampler
from 高天荒野舰艇整舰推进安全判定 import PropulsionOutputVector
from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
import 高天荒野舰艇战术机动求解器 as dynamics
import 高天荒野舰艇受控推进无场景适配器 as adapter_module
from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import (
    engines as physical_engines,
    fixture,
)
from 高天荒野T0b2d2b4场景接线与新黄金测试 import case
from 高天荒野T0b2d3b整舰安全判定测试 import PROFILE


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d3d无场景受控推进适配器接口.v1.json"


def refused(action, code=None):
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError("非法输入必须以 ContractError 拒绝")


def propulsion_state(context, engines, control, clock):
    commands = {item.command_channel: item for item in control.channel_commands}
    governors = tuple(
        replace(
            DirectionalPropulsionGovernorState.initial(channel),
            command=commands[channel],
            last_evaluated_step_index=clock,
        )
        for channel in DIRECTIONAL_CHANNELS
    )
    return TacticalPropulsionState(tuple(engines), governors, DIRECTIONAL_STATE_INTERFACE_ID)


def basic_initialized_fixture():
    context, runtime, model = fixture()
    control = directional_control()
    state = propulsion_state(context, physical_engines(context), control, None)
    motion = dynamics.initialize_tactical_motion_state(model)
    initialized = initialize_governed_propulsion_state(
        context,
        state,
        control,
        PROFILE,
        model,
        motion,
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    return context, runtime, model, motion, control, initialized


def check_named_initialization_matrix():
    scenes = ships = actuators = governors = load_evaluations = 0
    maximum_structure_ratio = 0.0
    maximum_crew_g = 0.0
    for name, _, _ in migrated_cases():
        _, _, session = case(name)
        binding_by_id = {item.ship_id: item for item in session.bindings}
        for ship in session.scene.ships:
            binding = binding_by_id[ship.ship_id]
            runtime = compile_runtime_ship_parameters(
                binding.snapshot,
                binding.sortie,
                ship.combat_state.instance,
                active_automatic_events=continuous_damage_automatic_events(ship.combat_state.instance),
            )
            model = dynamics.build_tactical_ship_model(runtime, binding.snapshot)
            outcome = initialize_governed_propulsion_state(
                session.propulsion_context.ship(ship.ship_id).aggregation_context,
                ship.propulsion_state,
                ship.propulsion_control,
                session.propulsion_context.safety_profile,
                model,
                ship.motion_state,
                crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
            )
            result = outcome.safety_result
            assert all(item.last_evaluated_step_index == 0 for item in outcome.state.governors)
            assert not result.remaining_soft_reasons and not result.event_intents
            assert all(item.safety_ceiling_percent == 100 for item in outcome.state.governors)
            assert outcome.state.engines == ship.propulsion_state.engines
            ships += 1
            actuators += len(outcome.state.engines)
            governors += len(outcome.state.governors)
            load_evaluations += len(result.load_samples)
            maximum_structure_ratio = max(maximum_structure_ratio, *(item.structure_ratio for item in result.load_samples))
            maximum_crew_g = max(maximum_crew_g, *(item.crew_g for item in result.load_samples))
        scenes += 1
    assert (scenes, ships, actuators, governors, load_evaluations) == (12, 224, 1224, 1344, 224)
    return {
        "scenes": scenes,
        "ships": ships,
        "actuators": actuators,
        "governors": governors,
        "load_evaluations": load_evaluations,
        "maximum_structure_ratio": maximum_structure_ratio,
        "maximum_crew_g": maximum_crew_g,
    }


def check_open_integrate_close_and_replay():
    context, runtime, model, motion0, stop, initialized = basic_initialized_fixture()
    assert not any(binding.command_channels == ("translation.left",) for binding in context.bindings)
    forward = directional_control((
        ChannelPropulsionCommand("translation.forward", "full", None),
        ChannelPropulsionCommand("translation.left", "quarter", None),
    ))
    with patch.object(
        adapter_module,
        "evaluate_whole_ship_propulsion_safety",
        side_effect=AssertionError("opening evaluated safety"),
    ):
        opening = commit_governed_propulsion_opening(
            context,
            initialized.state,
            stop,
            forward,
            fixed_step_index=0,
        )
        automatic_brake = automatic_linear_brake_control(
            lateral_velocity_body_mps=0.0,
            longitudinal_velocity_body_mps=-10.0,
            available_translation_channels=tuple(
                channel
                for channel in DIRECTIONAL_CHANNELS[:4]
                if any(channel in binding.command_channels for binding in context.bindings)
            ),
        ).control
        automatic_opening = commit_governed_propulsion_opening(
            context,
            initialized.state,
            stop,
            automatic_brake,
            fixed_step_index=0,
        )
    assert automatic_opening.control.automatic_brake
    assert next(
        item for item in automatic_opening.record.governor_commands
        if item.command_channel == "translation.forward"
    ).commanded_notch == "quarter"
    assert len(opening.record.engine_results) == len(context.bindings)
    assert next(
        item for item in opening.record.governor_commands if item.command_channel == "translation.left"
    ).requested_percent == 25
    assert tuple(item.actual_output_percent for item in opening.state.engines) == tuple(
        item.actual_output_percent for item in initialized.state.engines
    )
    safety_fields = (
        "safety_ceiling_percent",
        "safety_reasons",
        "safety_limited_since_step",
        "release_candidate_since_step",
        "last_evaluated_step_index",
        "safety_revision",
    )
    for before, after in zip(opening.record.source_governors, opening.record.resulting_governors):
        assert tuple(getattr(before, key) for key in safety_fields) == tuple(
            getattr(after, key) for key in safety_fields
        )
    interval0 = integrate_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion0,
        opening.state,
        forward,
        propulsion_delivery_status="delivered",
    )
    assert interval0.delivered_request == interval0.aggregation.request
    assert interval0.diagnostics.to_dict()["soft_governor_status"] == "wired"
    assert interval0.diagnostics.source_propulsion_state_sha256 == canonical_sha256(opening.state)
    validate_governed_propulsion_opening_replay(
        opening,
        context,
        initialized.state,
        stop,
        forward,
        fixed_step_index=0,
    )
    validate_governed_propulsion_interval_replay(
        interval0,
        context,
        runtime,
        model,
        motion0,
        opening.state,
        forward,
        propulsion_delivery_status="delivered",
    )
    with patch.object(
        adapter_module,
        "evaluate_whole_ship_propulsion_safety",
        wraps=adapter_module.evaluate_whole_ship_propulsion_safety,
    ) as closing_counter:
        closing1 = evaluate_governed_propulsion_closing(
            context,
            opening.state,
            forward,
            PROFILE,
            runtime,
            model,
            interval0.resulting_motion,
            fixed_step_index=1,
            propulsion_delivery_status="delivered",
            crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
        )
    assert closing_counter.call_count == 1
    assert not closing1.safety_events
    validate_governed_propulsion_closing_replay(
        closing1,
        context,
        opening.state,
        forward,
        PROFILE,
        runtime,
        model,
        interval0.resulting_motion,
        fixed_step_index=1,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )

    reloaded_opening = GovernedPropulsionOpeningOutcome(
        TacticalPropulsionState.parse(opening.state.to_dict(), "$.state"),
        DirectionalPropulsionControlInput.parse(forward.to_dict()),
        GovernedPropulsionOpeningRecord.parse(opening.record.to_dict()),
    )
    reloaded_closing = GovernedPropulsionClosingOutcome(
        TacticalPropulsionState.parse(closing1.state.to_dict(), "$.state"),
        GovernedPropulsionClosingRecord.parse(closing1.record.to_dict()),
        tuple(GovernedScenePropulsionSafetyEvent.parse(item.to_dict()) for item in closing1.safety_events),
    )
    assert reloaded_opening == opening and reloaded_closing == closing1

    opening1 = commit_governed_propulsion_opening(
        context,
        closing1.state,
        forward,
        forward,
        fixed_step_index=1,
    )
    interval1 = integrate_governed_propulsion_interval(
        context,
        runtime,
        model,
        interval0.resulting_motion,
        opening1.state,
        forward,
        propulsion_delivery_status="delivered",
    )
    assert interval1.aggregation.request.force_body_n == (0.0, 0.0)
    closing2 = evaluate_governed_propulsion_closing(
        context,
        opening1.state,
        forward,
        PROFILE,
        runtime,
        model,
        interval1.resulting_motion,
        fixed_step_index=2,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    forward_engines = {
        binding.actuator_instance_id
        for binding in context.bindings
        if binding.command_channels == ("translation.forward",)
    }
    assert all(
        engine.actual_output_percent == (2 if engine.actuator_instance_id in forward_engines else 0)
        for engine in closing2.state.engines
    )
    assert sum(
        event.kind == "engine_output_stage_changed"
        for result in closing2.record.safety_result.engine_results
        for event in result.events
    ) == len(forward_engines)
    return {
        "opening_engine_results": len(opening.record.engine_results),
        "automatic_brake_control_audited": True,
        "unbound_channel_command_audited": True,
        "opening_safety_evaluations": 0,
        "closing_safety_evaluations": 1,
        "opening_actual_output_unchanged": True,
        "interval_source_force_body_n": list(interval1.aggregation.request.force_body_n),
        "first_committed_upstage_step": 2,
        "committed_upstage_percent": 2,
        "opening_interval_closing_replayed": 3,
        "serialized_boundary_round_trips": 2,
    }


def high_thrust(value):
    for module in value["modules"]:
        if module["category"] == "main_engine":
            module["capability"]["thrust_n"] = 1_000_000_000.0


def check_safety_events_overg_and_suppression():
    context, runtime, model = fixture(catalog_mutate=high_thrust)
    control = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
    values = {
        binding.actuator_instance_id: 100
        if binding.command_channels == ("translation.forward",)
        else 0
        for binding in context.bindings
    }
    source = propulsion_state(context, physical_engines(context, values), control, 0)
    motion0 = replace(
        dynamics.initialize_tactical_motion_state(model),
        velocity_world_mps=dynamics.Vec2(3.0, 10.0),
    )
    motion1 = replace(motion0, fixed_step_index=1)
    limited = evaluate_governed_propulsion_closing(
        context,
        source,
        control,
        PROFILE,
        runtime,
        model,
        motion1,
        fixed_step_index=1,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    forward_governor = next(
        item for item in limited.state.governors if item.command_channel == "translation.forward"
    )
    assert forward_governor.safety_ceiling_percent < 100
    assert [item.intent.command_channel for item in limited.safety_events] == ["translation.forward"]
    assert limited.safety_events[0].intent.event.kind == "engine_safety_limit_engaged"

    overg = replace(control, overg_requested=True)
    opened = commit_governed_propulsion_opening(
        context,
        limited.state,
        control,
        overg,
        fixed_step_index=1,
    )
    opened_governor = next(
        item for item in opened.state.governors if item.command_channel == "translation.forward"
    )
    assert opened_governor.safety_ceiling_percent == forward_governor.safety_ceiling_percent
    assert opened_governor.safety_revision == forward_governor.safety_revision
    released = evaluate_governed_propulsion_closing(
        context,
        opened.state,
        overg,
        PROFILE,
        runtime,
        model,
        replace(motion0, fixed_step_index=2),
        fixed_step_index=2,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    released_governor = next(
        item for item in released.state.governors if item.command_channel == "translation.forward"
    )
    assert released_governor.safety_ceiling_percent == 100
    assert [item.intent.event.kind for item in released.safety_events] == ["engine_safety_limit_released"]

    suppressed_interval = integrate_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion0,
        source,
        control,
        propulsion_delivery_status="suppressed_falling",
    )
    assert suppressed_interval.aggregation.request.force_body_n[1] > 0
    assert suppressed_interval.delivered_request.force_body_n == (0.0, 0.0)
    assert suppressed_interval.diagnostics.diagnostic.active_force_body_n == dynamics.Vec2()
    assert suppressed_interval.diagnostics.diagnostic.drag_force_world_n.length > 0
    assert suppressed_interval.diagnostics.diagnostic.fuel_units_consumed == 0
    suppressed = evaluate_governed_propulsion_closing(
        context,
        source,
        control,
        PROFILE,
        runtime,
        model,
        suppressed_interval.resulting_motion,
        fixed_step_index=1,
        propulsion_delivery_status="suppressed_falling",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    assert all(item.safety_ceiling_percent == 100 for item in suppressed.state.governors)
    assert not suppressed.safety_events and not suppressed.record.safety_result.remaining_soft_reasons
    physical = WholeShipVectorLoadSampler(context, model, motion1)
    suppressed_sampler = GovernedPropulsionDeliveryLoadSampler(physical, "suppressed_uncommanded")
    full_vector = PropulsionOutputVector(tuple(sorted(values.items())))
    zero_vector = PropulsionOutputVector(tuple((key, 0) for key, _ in full_vector.outputs))
    sample = suppressed_sampler(full_vector)
    zero_sample = physical(zero_vector)
    assert (sample.structure_ratio, sample.crew_g) == (zero_sample.structure_ratio, zero_sample.crew_g)
    assert sample.vector == full_vector and sample.load_context_sha256 == suppressed_sampler.source_sha256
    return {
        "engaged_safety_events": len(limited.safety_events),
        "opening_preserved_safety_revision": True,
        "overg_release_boundary": 2,
        "released_safety_events": len(released.safety_events),
        "suppressed_interval_force_zero": True,
        "suppressed_interval_fuel_zero": True,
        "suppressed_load_uses_zero_propulsion_and_original_drag": True,
    }


def check_negative_replay_and_isolation():
    context, runtime, model, motion, stop, initialized = basic_initialized_fixture()
    forward = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
    opening = commit_governed_propulsion_opening(
        context,
        initialized.state,
        stop,
        forward,
        fixed_step_index=0,
    )
    interval = integrate_governed_propulsion_interval(
        context,
        runtime,
        model,
        motion,
        opening.state,
        forward,
        propulsion_delivery_status="delivered",
    )
    closing = evaluate_governed_propulsion_closing(
        context,
        opening.state,
        forward,
        PROFILE,
        runtime,
        model,
        interval.resulting_motion,
        fixed_step_index=1,
        propulsion_delivery_status="delivered",
        crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
    )
    rejected = 0

    def reject(action, code=None):
        nonlocal rejected
        refused(action, code)
        rejected += 1

    reject(
        lambda: commit_governed_propulsion_opening(
            context,
            initialized.state,
            stop,
            forward,
            fixed_step_index=1,
        ),
        "governed_adapter.governor_clock",
    )
    due_engines = tuple(
        replace(
            engine,
            phase="running",
            commanded_notch="full",
            target_output_percent=100,
            next_transition_step=2,
            response_started_at_fixed_step=0,
            response_start_output_percent=0,
        )
        if engine.actuator_category == "main_engine"
        else engine
        for engine in opening.state.engines
    )
    due_governors = tuple(replace(item, last_evaluated_step_index=2) for item in opening.state.governors)
    due = replace(opening.state, engines=due_engines, governors=due_governors)
    reject(
        lambda: commit_governed_propulsion_opening(
            context,
            due,
            forward,
            forward,
            fixed_step_index=2,
        ),
        "propulsion_time.committed_schedule",
    )
    forged_opening = replace(opening.record, source_propulsion_state_sha256="f" * 64)
    forged_opening_outcome = GovernedPropulsionOpeningOutcome(opening.state, opening.control, forged_opening)
    reject(
        lambda: validate_governed_propulsion_opening_replay(
            forged_opening_outcome,
            context,
            initialized.state,
            stop,
            forward,
            fixed_step_index=0,
        ),
        "governed_adapter.opening_replay",
    )
    forged_closing = replace(closing.record, motion_state_sha256="e" * 64)
    forged_closing_outcome = GovernedPropulsionClosingOutcome(closing.state, forged_closing, closing.safety_events)
    reject(
        lambda: validate_governed_propulsion_closing_replay(
            forged_closing_outcome,
            context,
            opening.state,
            forward,
            PROFILE,
            runtime,
            model,
            interval.resulting_motion,
            fixed_step_index=1,
            propulsion_delivery_status="delivered",
            crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
        ),
        "governed_adapter.closing_replay",
    )
    reject(
        lambda: integrate_governed_propulsion_interval(
            context,
            runtime,
            model,
            motion,
            opening.state,
            forward,
            propulsion_delivery_status="suppressed_exited",
        ),
        "governed_adapter.exited_frozen",
    )
    tripped = replace(opening.state.engines[0], phase="tripped", commanded_notch="stop",
        target_output_percent=0, actual_output_percent=0, ready_at_fixed_step=None,
        next_transition_step=None, response_started_at_fixed_step=None, response_start_output_percent=None)
    tripped_state = replace(opening.state, engines=(tripped,) + opening.state.engines[1:])
    reject(
        lambda: integrate_governed_propulsion_interval(
            context,
            runtime,
            model,
            motion,
            tripped_state,
            forward,
            propulsion_delivery_status="delivered",
        ),
        "governed_adapter.tripped_unwired",
    )
    wrong_runtime = fixture(profile="minimum_legal")[1]
    reject(
        lambda: evaluate_governed_propulsion_closing(
            context,
            opening.state,
            forward,
            PROFILE,
            wrong_runtime,
            model,
            interval.resulting_motion,
            fixed_step_index=1,
            propulsion_delivery_status="delivered",
            crew_safety_lock_enabled=runtime.crew_safety_lock_enabled,
        ),
        "governed_adapter.final_runtime_model",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import sys; import 高天荒野舰艇受控推进无场景适配器; "
            "assert '高天荒野舰艇统一战术场景' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return {
        "negative_cases": rejected,
        "strictly_due_opening_rejected": True,
        "opening_and_closing_tamper_rejected": True,
        "exited_and_tripped_rejected": True,
        "unified_scene_imported": False,
    }


def collect_evidence():
    return {
        "named_initialization": check_named_initialization_matrix(),
        "opening_interval_closing": check_open_integrate_close_and_replay(),
        "safety_and_suppression": check_safety_events_overg_and_suppression(),
        "negative_and_isolation": check_negative_replay_and_isolation(),
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
                "interface": "gaotian.stage-t0b2d3d-scene-free-governed-propulsion-adapter/v1",
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
