"""d3.2 纯整舰判定与真实载荷：不冒充已接线场景或正式性能矩阵。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from math import isclose
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from benchmarks.t0.metadata import file_sha256
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile, PropulsionHardAvailability
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS, ChannelPropulsionCommand, DirectionalPropulsionGovernorState
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进状态合同 import EngineRuntimeState
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand
from 高天荒野舰艇受控推进时间边界 import preview_governed_propulsion_time_boundary
from 高天荒野舰艇整舰推进安全判定 import (
    PropulsionOutputVector, WholeShipPropulsionLoadSample, WholeShipActuatorBoundary,
    WholeShipPropulsionSafetyResult, evaluate_whole_ship_propulsion_safety,
    validate_whole_ship_safety_result, ChannelSafetyEventIntent,
)
from 高天荒野舰艇推进向量载荷 import WholeShipVectorLoadSampler, prepare_whole_ship_actuator_boundaries
from 高天荒野T0b2d3a受控时间边界测试 import capability, initial, commit
from 高天荒野T0b2d2b3实际推进聚合与积分测试 import fixture, engines as physical_engines, mutate_modules
from 高天荒野舰艇实际推进聚合器 import aggregate_actual_propulsion, compile_actual_propulsion_context
import 高天荒野舰艇战术机动求解器 as dynamics

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "舰艇数据/报告/阶段T0b2d3b整舰向量安全接口.v1.json"
CHANNELS = {"engine.a": "translation.forward", "engine.b": "yaw.counterclockwise"}
PROFILE = PropulsionSafetyProfile("gtw.safety.vector.test", 1.0, .8, 12.0, 10.0, 3)
SCHEMA_NAMES = (
    "高天荒野舰艇推进输出向量契约.v1alpha1.schema.json",
    "高天荒野舰艇整舰推进载荷样本契约.v1alpha1.schema.json",
    "高天荒野舰艇定向安全事件意图契约.v1alpha1.schema.json",
    "高天荒野舰艇整舰推进安全结果契约.v1alpha1.schema.json",
)


def controls(channels=CHANNELS, *, overg=False):
    supplied = []
    for channel in DIRECTIONAL_CHANNELS:
        if channel in channels.values():
            supplied.append(ChannelPropulsionCommand(channel, "full", None) if channel.startswith("translation.")
                else ChannelPropulsionCommand(channel, None, 100))
    return directional_control(supplied, overg_requested=overg)


def governors(control):
    return tuple(DirectionalPropulsionGovernorState(c) for c in control.channel_commands)


def states(values, channels=CHANNELS):
    result = []
    for key, channel in sorted(channels.items()):
        category = "main_engine" if channel.startswith("translation.") else "maneuver_thruster"
        state = initial(category, name=key)
        state = replace(state, commanded_notch="full" if category == "main_engine" else None)
        if values.get(key, 0):
            state = replace(state, phase="running", actual_output_percent=values[key], target_output_percent=values[key])
        result.append(state)
    return tuple(result)


def arguments(source, previous, n, load, channels=CHANNELS, *, control=None, hard=None, profile=PROFILE, crew_lock=True):
    control = control or controls(channels)
    by_channel = {c.command_channel: c for c in control.channel_commands}
    boundaries = []
    for state in source:
        channel = channels[state.actuator_instance_id]
        c = by_channel[channel]
        cap = capability(state.actuator_category)
        preview = preview_governed_propulsion_time_boundary(state, cap, n,
            PropulsionTimeCommand(c.commanded_notch, c.target_output_percent))
        boundaries.append(WholeShipActuatorBoundary(channel, cap, preview,
            hard[state.actuator_instance_id] if hard else PropulsionHardAvailability()))
    context_hash = canonical_sha256({"scenario": "synthetic_explicit_vector_callback", "step": n})
    def evaluator(vector):
        ratio, crew = load(dict(vector.outputs))
        return WholeShipPropulsionLoadSample(context_hash, vector, ratio, crew)
    return dict(profile=profile, previous_governors=previous, controls=control, actuators=tuple(boundaries),
        fixed_step_index=n, load_context_sha256=context_hash, load_evaluator=evaluator,
        crew_safety_lock_enabled=crew_lock)


def run(source, previous, n, load, channels=CHANNELS, **kwargs):
    args = arguments(source, previous, n, load, channels, **kwargs)
    result = evaluate_whole_ship_propulsion_safety(**args)
    assert WholeShipPropulsionSafetyResult.parse(result.to_dict()) == result
    return result


def next_states(result):
    return tuple(r.state for r in result.engine_results)


def g(result, channel="translation.forward"):
    return next(x for x in result.governors if x.command_channel == channel)


def check_joint_candidates():
    control = controls()
    previous, source = governors(control), states({})
    safe = lambda v: (.1, 1)
    first = run(source, previous, 0, safe)
    assert len(first.load_samples) == 1
    source, previous = next_states(first), first.governors
    joint_bad = lambda v: (.1 + .3 * sum(v.values()), 1)
    assert joint_bad({"engine.a": 2, "engine.b": 0})[0] < 1
    assert joint_bad({"engine.a": 0, "engine.b": 2})[0] < 1
    blocked = run(source, previous, 2, joint_bad)
    assert len(blocked.load_samples) == 2 and all(r.upstage_rejected for r in blocked.engine_results)
    assert blocked.committed_vector == blocked.current_vector
    assert g(blocked).safety_ceiling_percent == g(blocked, "yaw.counterclockwise").safety_ceiling_percent == 0
    assert len(blocked.event_intents) == 2 and not blocked.remaining_soft_reasons
    assert not any(e.kind == "engine_output_stage_changed" for r in blocked.engine_results for e in r.events)
    # 两个分别不安全但互相抵消的候选须按联合结果允许。
    balanced = lambda v: (.1 + abs(v["engine.a"] - v["engine.b"]) * .6, 1)
    assert balanced({"engine.a": 2, "engine.b": 0})[0] > 1
    allowed = run(source, previous, 2, balanced)
    assert allowed.committed_vector.outputs == (("engine.a", 2), ("engine.b", 2))
    assert len(allowed.load_samples) == 2 and not allowed.event_intents
    # 同组高输出发动机不能掩盖另一台低输出发动机的到期升阶。
    same = {"engine.a": "translation.forward", "engine.b": "translation.forward"}
    ready = run(states({"engine.a": 100}, same), governors(controls(same)), 0, safe, same)
    asynchronous = run(next_states(ready), ready.governors, 2, lambda v: (.5 + .3 * v["engine.b"], 1), same)
    assert asynchronous.current_vector.outputs == (("engine.a", 100), ("engine.b", 0))
    assert asynchronous.load_samples[1].vector.outputs == (("engine.a", 100), ("engine.b", 2))
    assert asynchronous.committed_vector == asynchronous.current_vector
    assert g(asynchronous).safety_ceiling_percent == 0
    assert next_states(asynchronous)[0].actual_output_percent == 100
    return {"steady_load_calls": 1, "joint_candidate_calls": 2, "separately_safe_joint_unsafe_rejected": True,
        "separately_unsafe_joint_safe_allowed": True, "asynchronous_group_vector_preserved": True}


def check_downstage_and_nonmonotonic():
    source, previous = states({"engine.a": 50, "engine.b": 50}), governors(controls())
    load = lambda v: (sum(v.values()) / 60, 1)
    lowered = run(source, previous, 0, load)
    assert lowered.downstage_search_caps == (50, 45, 40, 35, 30)
    assert len(lowered.load_samples) == 5 and lowered.safe_downstage_found
    assert g(lowered).safety_ceiling_percent == g(lowered, "yaw.counterclockwise").safety_ceiling_percent == 30
    assert lowered.remaining_soft_reasons == ("structure_limit",)
    assert all(r.state.actual_output_percent == 50 and r.effective_target_percent == 30 for r in lowered.engine_results)
    event_count = len(lowered.event_intents)
    current = lowered
    for n in range(1, 13):
        current = run(next_states(current), current.governors, n, load)
        event_count += len(current.event_intents)
    assert current.committed_vector.outputs == (("engine.a", 30), ("engine.b", 30))
    assert event_count == 2  # 稳定限幅不得每步产生 changed 事件。
    # 非单调载荷仍从高到低枚举，不做单调二分。
    one = {"engine.a": "translation.forward"}
    nonmonotonic = run(states({"engine.a": 100}, one), governors(controls(one)), 0,
        lambda v: (.5 if v["engine.a"] in (35, 10) else 2, 1), one)
    assert nonmonotonic.safe_downstage_found and g(nonmonotonic).safety_ceiling_percent == 35
    assert nonmonotonic.downstage_search_caps[-1] == 35
    impossible = run(states({"engine.a": 100}, one), governors(controls(one)), 0, lambda v: (2, 13), one)
    assert impossible.safe_downstage_found is False and len(impossible.downstage_search_caps) == 22
    assert len(impossible.load_samples) == 22 and g(impossible).safety_ceiling_percent == 0
    assert impossible.remaining_soft_reasons == ("structure_limit", "crew_limit")
    zero = run(states({}, one), governors(controls(one)), 0, lambda v: (2, 13), one)
    assert len(zero.load_samples) == 1 and zero.downstage_search_caps == (0,)
    # 强制下降也可能破坏配平；保留真实已到期下降，不伪装成即时安全。
    same = {"engine.a": "translation.forward", "engine.b": "translation.forward"}
    source = states({"engine.a": 50, "engine.b": 50}, same)
    stopped_a = commit(source[0], capability(), 0, PropulsionTimeCommand.main_engine("full"), 0).state
    imbalance = run((stopped_a, source[1]), governors(controls(same)), 3,
        lambda v: (.1 + abs(v["engine.a"] - v["engine.b"]) * .3, 1), same)
    assert imbalance.committed_vector.outputs == (("engine.a", 45), ("engine.b", 50))
    assert imbalance.remaining_soft_reasons == ("structure_limit",)
    assert imbalance.safe_downstage_found and g(imbalance).safety_ceiling_percent == 45
    assert len(imbalance.load_samples) == 3
    return {"highest_safe_cap": 30, "target_reached_after_steps": 12, "initial_downstage_load_calls": 5,
        "stable_limit_event_count": event_count, "nonmonotonic_highest_safe_cap": 35,
        "no_safe_candidate_max_calls": 22, "zero_output_unsafe_calls": 1,
        "mandatory_downstage_imbalance_explicit": True}


def check_hysteresis_and_hard_bounds():
    one = {"engine.a": "translation.forward"}
    source = states({"engine.a": 25}, one)
    previous = tuple(replace(x, safety_ceiling_percent=25, safety_reasons=("structure_limit", "crew_limit"),
        safety_limited_since_step=0, last_evaluated_step_index=0, safety_revision=1) if x.command_channel == "translation.forward"
        else replace(x, last_evaluated_step_index=0) for x in governors(controls(one)))
    result = run(source, previous, 1, lambda v: (.7, 11), one)
    assert g(result).release_candidate_since_step is None
    result = run(next_states(result), result.governors, 2, lambda v: (.7, 9), one)
    assert g(result).release_candidate_since_step == 2
    result = run(next_states(result), result.governors, 3, lambda v: (.7, 11), one)
    assert g(result).release_candidate_since_step is None
    for n in (4, 5, 6):
        result = run(next_states(result), result.governors, n, lambda v: (.7, 9), one)
    assert g(result).safety_ceiling_percent == 100 and g(result).command.commanded_notch == "full"
    assert result.committed_vector.outputs == (("engine.a", 25),)
    assert result.engine_results[0].state.next_transition_step == 9
    assert [e.event.kind for e in result.event_intents] == ["engine_safety_limit_released"]
    skipped = run(source, previous, 1, lambda v: (.7, 9), one)
    skipped = run(next_states(skipped), skipped.governors, 3, lambda v: (.7, 9), one)
    assert g(skipped).release_candidate_since_step == 3
    # overg 仅清软限制，硬上限仍约束有效目标。
    bypass = run(source, previous, 1, lambda v: (2, 15), one, control=controls(one, overg=True),
        hard={"engine.a": PropulsionHardAvailability(5, ("power_unavailable",))})
    assert g(bypass).safety_ceiling_percent == 100 and bypass.engine_results[0].effective_target_percent == 5
    assert bypass.engine_results[0].state.actual_output_percent == 25 and not bypass.remaining_soft_reasons
    assert g(bypass).command.commanded_notch == "full"
    disabled = run(source, previous, 1, lambda v: (.9, 20), one, crew_lock=False)
    assert g(disabled).safety_reasons == ("structure_limit",) and g(disabled).safety_ceiling_percent == 25
    assert g(disabled).release_candidate_since_step is None
    assert disabled.event_intents[0].event.kind == "engine_safety_limit_changed"
    # 同一组内硬上限也必须逐执行器使用，不能由另一台的 100% 掩盖。
    same = {"engine.a": "translation.forward", "engine.b": "translation.forward"}
    started = run(states({}, same), governors(controls(same)), 0, lambda v: (.1, 1), same)
    blocked = run(next_states(started), started.governors, 2, lambda v: (.1, 1), same,
        control=controls(same, overg=True), hard={"engine.a": PropulsionHardAvailability(0, ("fuel_unavailable",)),
            "engine.b": PropulsionHardAvailability()})
    assert blocked.committed_vector.outputs == (("engine.a", 0), ("engine.b", 2))
    assert blocked.engine_results[0].effective_target_percent == 0 and blocked.engine_results[1].effective_target_percent == 100
    tripped = replace(initial(name="engine.a", mode="off"), phase="tripped", commanded_notch="full")
    still_tripped = run((tripped,), governors(controls(one)), 0, lambda v: (.1, 1), one,
        control=controls(one, overg=True), hard={"engine.a": PropulsionHardAvailability(0, ("engine_tripped",))})
    assert still_tripped.engine_results[0].state.phase == "tripped"
    assert still_tripped.engine_results[0].effective_target_percent == 0
    return {"release_boundary": 6, "actual_on_release": 25, "next_upstage_boundary": 9,
        "all_reason_hold_and_reset": True, "skipped_step_restarts_hold": True,
        "overg_preserves_hard_target": 5, "crew_lock_only_removes_crew_reason": True,
        "per_actuator_hard_limits_preserved": True, "overg_cannot_reset_tripped_engine": True}


def check_replay():
    traces, milestones, totals = [], {}, {}
    for reload_at in (None, 4, 45):
        source, previous = states({}), governors(controls())
        trace, counts = [], {"engaged": 0, "changed": 0, "released": 0}
        for n in range(181):
            if n < 9:
                load = lambda v: (.1 + .3 * sum(v.values()), 1)
            elif 41 <= n < 61:
                load = lambda v: (.1 + sum(v.values()) / 35, 13)
            else:
                load = lambda v: (.1, 1)
            hard = {"engine.a": PropulsionHardAvailability(0, ("power_unavailable",)) if 85 <= n < 115 else PropulsionHardAvailability(),
                "engine.b": PropulsionHardAvailability()}
            args = arguments(source, previous, n, load, control=controls(overg=80 <= n < 96), hard=hard)
            result = evaluate_whole_ship_propulsion_safety(**args)
            if n % 15 == 0:
                validate_whole_ship_safety_result(result.to_dict(), **args)
            trace.append(canonical_sha256(result))
            for event in result.event_intents:
                counts[event.event.kind.removeprefix("engine_safety_limit_")] += 1
            source, previous = next_states(result), result.governors
            if n in (2, 5, 7, 41, 61, 63, 85, 115, 180):
                milestones[str(n)] = {"actual": [s.actual_output_percent for s in source],
                    "ceilings": [g(result).safety_ceiling_percent, g(result, "yaw.counterclockwise").safety_ceiling_percent]}
            if n == reload_at:
                restored = WholeShipPropulsionSafetyResult.parse(json.loads(json.dumps(result.to_dict())))
                source = tuple(EngineRuntimeState.parse(json.loads(json.dumps(s.to_dict())), "$") for s in source)
                previous = tuple(DirectionalPropulsionGovernorState.parse(json.loads(json.dumps(gv.to_dict())), "$") for gv in previous)
                assert next_states(restored) == source and restored.governors == previous
        assert all(s.actual_output_percent == 100 for s in source)
        assert previous[0].command.commanded_notch == "full"
        assert counts["engaged"] > 0 and counts["released"] > 0
        traces.append(trace)
        totals = counts
    assert traces[0] == traces[1] == traces[2]
    return {"boundaries_per_replay": 181, "replays": 3, "reload_boundaries": [4, 45],
        "trace_sha256": canonical_sha256(traces[0]), "event_counts": totals, "milestones": milestones}


def close(a, b):
    assert isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), (a, b)


def check_real_loads():
    cases, maximum_error = 0, 0.0
    for profile in ("minimum_legal", "conventional_crewed", "unmanned_flagship"):
        context, runtime, model = fixture(profile)
        motion = replace(dynamics.initialize_tactical_motion_state(model), velocity_world_mps=dynamics.Vec2(3, 10), heading_rad=.7, yaw_rate_radps=.02)
        sampler = WholeShipVectorLoadSampler(context, model, motion)
        before = canonical_sha256([runtime.to_dict(), motion.to_dict()])
        for stage in (0, 2, 5, 35, 100):
            values = {b.actuator_instance_id: stage if b.command_channels[0] == "translation.forward" else 0 for b in context.bindings}
            vector = PropulsionOutputVector(tuple(sorted(values.items())))
            expected = aggregate_actual_propulsion(context, runtime, physical_engines(context, values), 0).request
            with patch.object(dynamics, "allocate_tactical_actuation", side_effect=AssertionError("continuous allocation")), \
                 patch.object(dynamics, "_choose_command_scale", side_effect=AssertionError("continuous safety search")), \
                 patch.object(dynamics, "integrate_actual_tactical_step", side_effect=AssertionError("sampler integrated motion")), \
                 patch("高天荒野舰艇无界面舾装编译器.aggregate_actuators", side_effect=AssertionError("post-output rebalance")), \
                 patch.object(dynamics, "_load_metrics", wraps=dynamics._load_metrics) as counter:
                request, fraction = sampler.request_for(vector)
                sample = sampler(vector)
                assert counter.call_count == 1
            assert request == expected
            _, diag = dynamics.integrate_actual_tactical_step(model, motion, request)
            close(sample.structure_ratio, diag.structure_ratio)
            close(sample.crew_g, diag.crew_g)
            close(fraction, diag.fuel_delivery_fraction)
            maximum_error = max(maximum_error, abs(sample.structure_ratio - diag.structure_ratio), abs(sample.crew_g - diag.crew_g))
            cases += 1
        assert before == canonical_sha256([runtime.to_dict(), motion.to_dict()])
        assert sampler.source_sha256 != WholeShipVectorLoadSampler(context, model, replace(motion, yaw_rate_radps=.03)).source_sha256
    # 异步阶段保留真实力矩，不重新配平。
    context, runtime, model = fixture()
    sampler = WholeShipVectorLoadSampler(context, model, dynamics.initialize_tactical_motion_state(model))
    values = {b.actuator_instance_id: {"main_engine_port": 100, "main_engine_starboard": 5}.get(b.actuator_instance_id, 0) for b in context.bindings}
    request, _ = sampler.request_for(PropulsionOutputVector(tuple(sorted(values.items()))))
    assert request.force_body_n == (0.0, 105000.0) and request.torque_n_m == -475000.0
    return {"profiles": 3, "vector_cases": cases, "maximum_metric_error": maximum_error,
        "asymmetric_force_y_n": request.force_body_n[1], "asymmetric_torque_n_m": request.torque_n_m,
        "sample_load_calls": 1, "no_allocation_rebalance_or_integration": True}


def check_real_availability():
    mutations = [
        ("damaged", lambda i: mutate_modules(i, {"main_engine_port": {"current_durability_points": 50.0}}), None),
        ("destroyed_yaw", lambda i: mutate_modules(i, {"thruster_starboard_fore": {"current_durability_points": 0.0}}), None),
        ("empty_fuel", lambda i: replace(i, operational_state=replace(i.operational_state, fuel_units=0.0)), None),
        ("partial_fuel", lambda i: replace(i, operational_state=replace(i.operational_state, fuel_units=.01)), None),
        ("off", lambda i: mutate_modules(i, {m.instance_id: {"operating_mode": "off"} for m in i.module_states}), None),
    ]
    def manual(value):
        for m in value["modules"]:
            if m["category"] == "main_engine":
                m["automation"].update(level="manual", automated_functions=[])
    def electric(value):
        for m in value["modules"]:
            if m["category"] == "main_engine":
                m["power"].update(consumer_category="sensors", active_load_kw=1000000.0)
    mutations += [("no_crew", lambda i: replace(i, operational_state=replace(i.operational_state, crew=())), manual),
        ("no_power", None, electric)]
    evidence = {}
    for name, mutate, catalog_mutate in mutations:
        c, r, m = fixture(mutate=mutate, catalog_mutate=catalog_mutate)
        motion = dynamics.initialize_tactical_motion_state(m)
        sampler = WholeShipVectorLoadSampler(c, m, motion)
        values = {b.actuator_instance_id: 100 if b.command_channels[0] in ("translation.forward", "yaw.counterclockwise") else 0 for b in c.bindings}
        vector = PropulsionOutputVector(tuple(sorted(values.items())))
        request, fraction = sampler.request_for(vector)
        expected = aggregate_actual_propulsion(c, r, physical_engines(c, values), 0).request
        assert request == expected
        sample = sampler(vector)
        _, diag = dynamics.integrate_actual_tactical_step(m, motion, request)
        close(sample.structure_ratio, diag.structure_ratio)
        close(sample.crew_g, diag.crew_g)
        close(fraction, diag.fuel_delivery_fraction)
        if name in ("empty_fuel", "off"):
            assert request.force_body_n == (0, 0) and request.torque_n_m == 0
        if name == "partial_fuel":
            assert 0 < fraction < 1
        if name in ("no_crew", "no_power"):
            assert request.force_body_n[1] == 0
        evidence[name] = {"force_body_n": list(request.force_body_n), "torque_n_m": request.torque_n_m,
            "fuel_delivery_fraction": fraction}
    return evidence


def check_real_overload_governor():
    def high_thrust(value):
        for module in value["modules"]:
            if module["category"] == "main_engine":
                module["capability"]["thrust_n"] = 1_000_000_000.0
    evidence = {}
    for profile, mutation in (("unmanned_flagship", None), ("conventional_crewed", high_thrust)):
        c, r, m = fixture(profile, catalog_mutate=mutation)
        control = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
        previous = governors(control)
        values = {b.actuator_instance_id: 100 if b.command_channels[0] == "translation.forward" else 0 for b in c.bindings}
        source = physical_engines(c, values)
        sampler = WholeShipVectorLoadSampler(c, m, dynamics.initialize_tactical_motion_state(m))
        hard = {b.actuator_instance_id: PropulsionHardAvailability() for b in c.bindings}
        boundaries = prepare_whole_ship_actuator_boundaries(c, source, control, hard, 0)
        args = dict(profile=PROFILE, previous_governors=previous, controls=control, actuators=boundaries,
            fixed_step_index=0, load_context_sha256=sampler.source_sha256, load_evaluator=sampler,
            crew_safety_lock_enabled=r.crew_safety_lock_enabled)
        limited = evaluate_whole_ship_propulsion_safety(**args)
        validate_whole_ship_safety_result(limited.to_dict(), **args)
        assert limited.safe_downstage_found and g(limited).safety_ceiling_percent < 100
        assert limited.current_vector == limited.committed_vector
        assert "structure_limit" in limited.remaining_soft_reasons
        if mutation:
            assert "crew_limit" in limited.remaining_soft_reasons
        overg = replace(control, overg_requested=True)
        bypass = evaluate_whole_ship_propulsion_safety(**{**args, "controls": overg})
        assert not bypass.remaining_soft_reasons and g(bypass).safety_ceiling_percent == 100
        assert bypass.committed_vector == limited.committed_vector
        evidence[profile] = {"safety_ceiling_percent": g(limited).safety_ceiling_percent,
            "remaining_reasons": list(limited.remaining_soft_reasons),
            "load_calls": len(limited.load_samples), "actual_not_instantly_clipped": True}
    return evidence


def check_negative_cases():
    safe = lambda v: (.1, 1)
    args = arguments(states({}), governors(controls()), 0, safe)
    good = evaluate_whole_ship_propulsion_safety(**args)
    actions = []
    for value in (True, 5.0, 1, -1, float("nan")):
        actions.append(lambda value=value: PropulsionOutputVector((("engine.a", value),)))
    actions += [lambda: PropulsionOutputVector(()), lambda: PropulsionOutputVector((("b", 0), ("a", 0))),
        lambda: PropulsionOutputVector((("a", 0), ("a", 2))), lambda: PropulsionOutputVector((("bad id", 0),)),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "actuators": args["actuators"][::-1]}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "actuators": args["actuators"] * 2}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "actuators": ()}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "previous_governors": good.governors}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "previous_governors": args["previous_governors"][::-1]}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "fixed_step_index": True}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "fixed_step_index": 1}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "crew_safety_lock_enabled": 1}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "load_context_sha256": "bad"}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "load_evaluator": None}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "load_evaluator": lambda v: {}}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "load_evaluator": lambda v: WholeShipPropulsionLoadSample("0" * 64, v, .1, 1)}),
        lambda: evaluate_whole_ship_propulsion_safety(**{**args, "load_evaluator": lambda v: WholeShipPropulsionLoadSample(args["load_context_sha256"], PropulsionOutputVector((("engine.a", 0),)), .1, 1)}),
        lambda: WholeShipPropulsionLoadSample("0" * 64, good.current_vector, float("inf"), 1),
        lambda: WholeShipPropulsionLoadSample("0" * 64, good.current_vector, .1, True),
        lambda: replace(args["actuators"][0], command_channel="yaw.clockwise"),
        lambda: replace(args["actuators"][0], capability=capability(response=1.1)),
        lambda: replace(args["actuators"][0], hard_availability=PropulsionHardAvailability(5.0, ("power_unavailable",))),
    ]
    invalid_clock = list(good.governors)
    invalid_clock[0] = replace(invalid_clock[0], last_evaluated_step_index=1)
    actions.append(lambda: evaluate_whole_ship_propulsion_safety(**{**args, "previous_governors": tuple(invalid_clock)}))
    bad_target = list(args["previous_governors"])
    bad_target[0] = replace(bad_target[0], safety_ceiling_percent=0, safety_reasons=("structure_limit",), safety_limited_since_step=0)
    actions.append(lambda: evaluate_whole_ship_propulsion_safety(**{**args, "previous_governors": tuple(bad_target)}))
    payload = good.to_dict()
    for key in payload:
        damaged = deepcopy(payload)
        del damaged[key]
        actions.append(lambda damaged=damaged: WholeShipPropulsionSafetyResult.parse(damaged))
    for key, value in (("extra", 1), ("interface", "unknown"), ("policy", "unknown"), ("fixed_step_index", True),
        ("input_sha256", "bad"), ("governors", []), ("load_samples", []), ("engine_results", []),
        ("downstage_search_caps", [5, 10]), ("safe_downstage_found", False), ("remaining_soft_reasons", ["unknown"])):
        damaged = deepcopy(payload)
        damaged[key] = value
        actions.append(lambda damaged=damaged: WholeShipPropulsionSafetyResult.parse(damaged))
    nested_reasons = deepcopy(payload)
    nested_reasons["remaining_soft_reasons"] = [["structure_limit"]]
    actions.append(lambda: WholeShipPropulsionSafetyResult.parse(nested_reasons))
    forged_hash = deepcopy(payload)
    forged_hash["input_sha256"] = "0" * 64
    actions.append(lambda: validate_whole_ship_safety_result(forged_hash, **args))
    forged_load = deepcopy(payload)
    forged_load["load_samples"][0]["structure_ratio"] = .2
    actions.append(lambda: validate_whole_ship_safety_result(forged_load, **args))
    # 已提交历史和原命令均须匹配，不能伪造软上限或把已跳闸机重新上推。
    started = next_states(good)
    next_args = arguments(started, good.governors, 2, safe)
    bad_governors = list(good.governors)
    bad_governors[0] = replace(bad_governors[0], command=ChannelPropulsionCommand("translation.forward", "stop", None))
    actions.append(lambda: evaluate_whole_ship_propulsion_safety(**{**next_args, "previous_governors": tuple(bad_governors)}))
    future = list(good.governors)
    future[0] = replace(future[0], safety_ceiling_percent=0, safety_reasons=("structure_limit",),
        safety_limited_since_step=3, safety_revision=1)
    actions.append(lambda: evaluate_whole_ship_propulsion_safety(**{**next_args, "previous_governors": tuple(future)}))
    tripped = replace(initial(name="engine.a", mode="off"), phase="tripped")
    tp = preview_governed_propulsion_time_boundary(tripped, capability(), 0, PropulsionTimeCommand.main_engine("full"))
    actions.append(lambda: WholeShipActuatorBoundary("translation.forward", capability(), tp, PropulsionHardAvailability()))
    actions.append(lambda: WholeShipActuatorBoundary("translation.forward", capability(), tp,
        PropulsionHardAvailability(0, ("power_unavailable",))))
    # 对向请求与旧组未停车反向仍是拒绝门，不冒充互锁。
    opposite = directional_control((ChannelPropulsionCommand("translation.reverse", "full", None),))
    actions.append(lambda: run(started, good.governors, 2, safe, control=opposite))
    limited = run(states({"engine.a": 100}, {"engine.a": "translation.forward"}),
        governors(controls({"engine.a": "translation.forward"})), 0, lambda v: (2, 1), {"engine.a": "translation.forward"})
    wrong_event = limited.event_intents[0].to_dict()
    wrong_event["event"]["kind"] = "engine_safety_limit_released"
    actions.append(lambda: ChannelSafetyEventIntent.parse(wrong_event))
    nested_event = limited.event_intents[0].to_dict()
    nested_event["event"]["reasons"] = [["structure_limit"]]
    actions.append(lambda: ChannelSafetyEventIntent.parse(nested_event))
    c, r, m = fixture()
    motion = dynamics.initialize_tactical_motion_state(m)
    sampler = WholeShipVectorLoadSampler(c, m, motion)
    actions += [lambda: sampler(PropulsionOutputVector((("engine.other", 0),))),
        lambda: WholeShipVectorLoadSampler(c, m, replace(motion, fuel_units=motion.fuel_units - 1)),
        lambda: WholeShipVectorLoadSampler(c, m, replace(motion, yaw_rate_radps=float("nan"))),
        lambda: WholeShipVectorLoadSampler(c, replace(m, structure_points_body_m=m.structure_points_body_m[:-1]), motion),
        lambda: prepare_whole_ship_actuator_boundaries(c, physical_engines(c), controls(), {}, 0)]
    for index, action in enumerate(actions):
        try:
            action()
        except ContractError:
            pass
        else:
            raise AssertionError(f"负例 {index} 没有拒绝")
    return {"strict_negative_cases": len(actions)}


def check_real_scene_input_matrix():
    from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases
    from 高天荒野舰艇推进场景构建器 import build_known_directional_scene
    from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
    from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
    from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
    profile = load_propulsion_safety_profile(ROOT / "舰艇数据/标定/T0推进安全技术替身配置.v1.json")
    scene_hashes, counts, ships, actuators = {}, {}, 0, 0
    for name, old, source in migrated_cases():
        bundle = build_known_directional_scene(ROOT, name, source, old.bindings)
        before = canonical_sha256(bundle)
        profiles = {p.profile_key: p for p in bundle.profiles}
        by_ship = {s.ship_id: s for s in bundle.scene.ships}
        replay_hashes = [[], [], []]
        totals = {"steady": 0, "transition": 0}
        for item in bundle.ships:
            b, ship = item.binding, by_ship[item.binding.ship_id]
            context = compile_actual_propulsion_context(name, b.ship_id, b.snapshot,
                profiles[item.profile_key].catalog, item.actuators)
            runtime = compile_runtime_ship_parameters(b.snapshot, b.sortie, ship.combat_state.instance,
                active_automatic_events=continuous_damage_automatic_events(ship.combat_state.instance))
            model = dynamics.build_tactical_ship_model(runtime, b.snapshot)
            control = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),
                ChannelPropulsionCommand("yaw.counterclockwise", None, 100)))
            hard = {binding.actuator_instance_id: PropulsionHardAvailability() for binding in context.bindings}
            source_engines, previous = ship.propulsion_state.engines, ship.propulsion_state.governors
            for n, label in ((0, "steady"), (2, "transition")):
                # 纯边界探针：只推进引擎和 governor，不推进场景、运动、燃料或武器。
                sampler = WholeShipVectorLoadSampler(context, model, replace(ship.motion_state, fixed_step_index=n))
                boundaries = prepare_whole_ship_actuator_boundaries(context, source_engines, control, hard, n)
                args = dict(profile=profile, previous_governors=previous, controls=control, actuators=boundaries,
                    fixed_step_index=n, load_context_sha256=sampler.source_sha256, load_evaluator=sampler,
                    crew_safety_lock_enabled=runtime.crew_safety_lock_enabled)
                for replay in range(3):
                    with patch.object(dynamics, "_load_metrics", wraps=dynamics._load_metrics) as counter:
                        result = evaluate_whole_ship_propulsion_safety(**args)
                    assert counter.call_count == len(result.load_samples)
                    assert len(result.load_samples) <= (1 if n == 0 else 2)
                    replay_hashes[replay].append(canonical_sha256(result))
                totals[label] += len(result.load_samples)
                source_engines, previous = next_states(result), result.governors
            ships += 1
            actuators += len(source_engines)
        assert replay_hashes[0] == replay_hashes[1] == replay_hashes[2]
        assert canonical_sha256(bundle) == before
        scene_hashes[name] = canonical_sha256(replay_hashes[0])
        counts[name] = totals
    assert ships == 224 and actuators == 1224
    assert counts["target_20.motion_only"] == {"steady": 20, "transition": 40}
    return {"scenes": 12, "ships": ships, "actuators": actuators, "boundaries_per_ship": 2,
        "deterministic_replays": 3, "scene_steps_advanced": 0, "scene_hashes": scene_hashes, "load_calls_per_replay": counts}


def check_schemas_and_isolation():
    started = run(states({}), governors(controls()), 0, lambda v: (.1, 1))
    result = run(next_states(started), started.governors, 2, lambda v: (.1 + .3 * sum(v.values()), 1))
    payloads = (result.current_vector.to_dict(), result.load_samples[0].to_dict(),
        result.event_intents[0].to_dict(), result.to_dict())
    registry = {load_json(p)["$id"]: load_json(p) for p in (ROOT / "舰艇数据/模式").glob("*.schema.json")}
    references = 0
    for name, payload in zip(SCHEMA_NAMES, payloads):
        schema = load_json(ROOT / "舰艇数据/模式" / name)
        assert set(schema["properties"]) == set(schema["required"]) == set(payload)
        assert schema["properties"]["interface"]["const"] == payload["interface"]
        assert schema["additionalProperties"] is False
        def walk(node):
            nonlocal references
            if isinstance(node, dict):
                if "$ref" in node:
                    root, _, pointer = node["$ref"].partition("#")
                    target = registry[root] if root else schema
                    for part in pointer.removeprefix("/").split("/") if pointer else ():
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                    assert isinstance(target, dict)
                    references += 1
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(schema)
    for prefix in ("", "import 高天荒野舰艇统一战术场景; "):
        assertion = "assert not any(k.startswith('benchmarks') or k.endswith('测试') for k in sys.modules)"
        if not prefix:
            assertion += "; assert '高天荒野舰艇统一战术场景' not in sys.modules"
        subprocess.run([sys.executable, "-X", "utf8", "-c", prefix +
            "import sys; import 高天荒野舰艇整舰推进安全判定; import 高天荒野舰艇推进向量载荷; " + assertion],
            cwd=ROOT, check=True)
    # 旧场景自身不会通过任何新模块隐式启用软保护。
    subprocess.run([sys.executable, "-X", "utf8", "-c",
        "import sys; import 高天荒野舰艇统一战术场景; assert '高天荒野舰艇整舰推进安全判定' not in sys.modules; assert '高天荒野舰艇推进向量载荷' not in sys.modules"], cwd=ROOT, check=True)
    return {"schemas": 4, "references_checked": references, "cold_import_orders": 2,
        "production_scene_or_test_dependency": False, "legacy_scene_implicitly_wired": False}


def collect_evidence():
    return {"joint_candidates": check_joint_candidates(), "downstage": check_downstage_and_nonmonotonic(),
        "hysteresis_and_hard": check_hysteresis_and_hard_bounds(), "replay": check_replay(),
        "real_loads": check_real_loads(), "real_availability": check_real_availability(),
        "real_overload_governor": check_real_overload_governor(),
        "negative_cases": check_negative_cases(), "real_scene_inputs": check_real_scene_input_matrix(),
        "contracts_and_isolation": check_schemas_and_isolation()}


def main():
    evidence = collect_evidence()
    report = load_json(REPORT)
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for relative, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / relative) == expected, relative
    print(json.dumps({"status": "PASS", "evidence": evidence}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
