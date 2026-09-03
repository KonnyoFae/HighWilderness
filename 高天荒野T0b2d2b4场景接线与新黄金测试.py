"""d2b.4：显式场景版本、真实首尾边界、存档连续性与独立新黄金。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from math import isclose
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases, PROFILE_PATH, test_existing_authority_isolation
from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import controls_for_step, launch_directives_for_step, guidance_inputs_for_step, advance_scenario_step
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇实际推进场景 import (
    build_known_actual_scene, save_actual_scene, load_actual_scene_save, validate_actual_scene_step_payload,
)
from 高天荒野舰艇推进固定步接线 import ACTUAL_SCENE_INTERFACE_ID, ActualPropulsionBoundaryRecord
from 高天荒野舰艇定向推进控制桥 import directional_control, migrate_known_t0_control_to_directional
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇统一战术场景 import (
    advance_tactical_scene_step, prepare_tactical_scene_bindings, TacticalSceneState,
    BINDING_VALIDATION_TRUSTED, TacticalSceneExitDirective,
)
from 高天荒野舰艇战术机动求解器 import Vec2, TacticalControlInput
from 高天荒野舰艇战术弹丸世界 import ProjectileState
from 高天荒野舰艇场景推进结果 import BoundaryScenePropulsionEvent
import 高天荒野舰艇统一战术场景 as scene_module

ROOT = Path(__file__).resolve().parent
GOLDEN_PATH = ROOT / "contracts/web_bridge/t0-actual-propulsion-step-golden.v1.json"
REPORT_PATH = ROOT / "舰艇数据/报告/阶段T0b2d2b4场景接线与独立新黄金接口.v1.json"


@lru_cache(maxsize=12)
def case(name):
    name, old, source = next(x for x in migrated_cases() if x[0] == name)
    session = build_known_actual_scene(ROOT, name, source, old.bindings, load_propulsion_safety_profile(PROFILE_PATH))
    return old, source, session


def step(old, session, scene, controls=None, **extra):
    directives = launch_directives_for_step(old, scene)
    return advance_tactical_scene_step(scene, session.bindings, old.timing_catalog,
        old.projectile_catalog, old.material_registry, propulsion_context=session.propulsion_context,
        propulsion_controls=controls, guidance_catalog=old.guidance_catalog,
        guidance_inputs=guidance_inputs_for_step(old, scene, directives), launch_directives=directives,
        continuous_damage_profile=old.continuous_damage_profile if old.load_stage == "scripted_damage_and_recompile" else None,
        binding_validation_mode=BINDING_VALIDATION_TRUSTED, **extra)


def close(a, b):
    assert isclose(a, b, rel_tol=1e-10, abs_tol=1e-9), (a, b)


def rejected(action, code=None):
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
    else:
        raise AssertionError("非法输入未拒绝")


def check_first_step():
    old, source, session = case("functional_6.motion_only")
    assert session.scene.to_dict()["interface"] == ACTUAL_SCENE_INTERFACE_ID
    control = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
    with patch.object(scene_module, "integrate_tactical_step", side_effect=AssertionError("legacy integrator called")):
        result = advance_tactical_scene_step(session.scene, session.bindings,
            old.timing_catalog, old.projectile_catalog, old.material_registry,
            propulsion_context=session.propulsion_context,
            propulsion_controls={s.ship_id: control for s in session.scene.ships})
    assert result.resulting_scene.fixed_step_index == 1
    assert result.resulting_scene.propulsion_safety_profile == session.scene.propulsion_safety_profile
    assert result.propulsion_boundaries  # ready -> running 的接令变化也必须可审计。
    assert all(r.diagnostics.active_force_body_n.length == 0 for r in result.ship_results)
    assert all(r.diagnostics.soft_governor_status == "unwired" for r in result.ship_results)
    validate_actual_scene_step_payload(result.to_dict(), session.scene, result, session.propulsion_context)
    assert result.to_dict()["propulsion_events"] == []  # ready 接令不是冷启动事件。
    assert all(r.before.phase == "ready" and r.after.phase == "running" for r in result.propulsion_boundaries)
    assert all(ActualPropulsionBoundaryRecord.parse(r.to_dict()) == r for r in result.propulsion_boundaries)
    return result


def check_long_sequence():
    name = "functional_6.motion_only"
    old, source, initial = case(name)
    controlled = initial.scene.ships[0].ship_id
    ships = []
    for ship in initial.scene.ships:
        engines = tuple(replace(e, phase="off", ready_at_fixed_step=None)
            if ship.ship_id == controlled and e.actuator_category == "main_engine" else e
            for e in ship.propulsion_state.engines)
        ships.append(replace(ship, propulsion_state=replace(ship.propulsion_state, engines=engines)))
    cold = replace(initial.scene, ships=tuple(ships))
    requests = {0: "full", 130: "half", 170: "stop", 210: "full", 280: "quarter"}
    traces, sample_boundaries = [], {}
    for repetition in range(3):
        scene = cold
        session = replace(initial, bindings=prepare_tactical_scene_bindings(scene, initial.bindings))
        trace = []
        seen_events = set()
        for n in range(331):
            controls = None if n not in requests else {controlled: directional_control((
                ChannelPropulsionCommand("translation.forward", requests[n], None),))}
            before = scene
            result = step(old, session, scene, controls)
            scene = result.resulting_scene
            sample = scene.ships[0]
            engine = next(e for e in sample.propulsion_state.engines if e.actuator_category == "main_engine")
            diagnostic = result.ship_results[0].diagnostics
            payload = result.to_dict()
            for event in payload["propulsion_events"]:
                parsed = BoundaryScenePropulsionEvent.parse(event)
                key = canonical_sha256(event)
                assert key not in seen_events
                seen_events.add(key)
                assert parsed.event.fixed_step_index in (n, n + 1)
            trace.append({"scene": canonical_sha256(scene), "result": canonical_sha256(payload)})
            if n + 1 in (1, 59, 60, 61, 62, 119, 120, 121, 160, 200, 270, 325, 331):
                point = {"phase": engine.phase, "actual": engine.actual_output_percent,
                    "force_y_n": diagnostic.active_force_body_n.y,
                    "fuel": sample.motion_state.fuel_units, "next_transition": engine.next_transition_step}
                if repetition == 0:
                    sample_boundaries[str(n + 1)] = point
                else:
                    assert sample_boundaries[str(n + 1)] == point
                validate_actual_scene_step_payload(payload, before, result, session.propulsion_context)
                assert TacticalSceneState.parse(scene.to_dict()).to_dict() == scene.to_dict()
            # 第二次在启动刚完成时重载，第三次在停车后重载；重建所有静态/runtime 缓存。
            reload_at = 61 if repetition == 1 else 200 if repetition == 2 else None
            if n + 1 == reload_at:
                saved = save_actual_scene(scene, session.propulsion_context)
                session = load_actual_scene_save(saved, root=ROOT, scene_id=name, source_scene=source,
                    source_bindings=old.bindings, safety_profile=session.propulsion_context.safety_profile)
                assert session.scene == scene
                assert all(b.runtime_cache is not previous.runtime_cache for b, previous in zip(session.bindings, initial.bindings))
                scene = session.scene
        traces.append(canonical_sha256(trace))
    assert len(set(traces)) == 1
    assert sample_boundaries["59"]["phase"] == "starting"
    assert sample_boundaries["60"]["phase"] == "running"
    assert sample_boundaries["61"]["actual"] == 0
    assert sample_boundaries["62"]["actual"] == 2 and sample_boundaries["62"]["force_y_n"] == 0
    assert sample_boundaries["120"]["actual"] == 100 and sample_boundaries["120"]["force_y_n"] < 200000
    close(sample_boundaries["121"]["force_y_n"], 200000)
    assert sample_boundaries["160"]["actual"] == 50
    assert sample_boundaries["200"]["phase"] == "ready" and sample_boundaries["200"]["actual"] == 0
    assert sample_boundaries["270"]["actual"] == 100  # warm ready 恢复不重复冷启动。
    assert sample_boundaries["325"]["actual"] == 25
    return {"steps_per_replay": 331, "replays": 3, "reload_boundaries": [61, 200],
            "trace_sha256": traces[0], "boundary_samples": sample_boundaries}


def replace_instance(scene, ship_id, transform):
    ships = []
    for ship in scene.ships:
        if ship.ship_id == ship_id:
            instance = transform(ship.combat_state.instance)
            ship = replace(ship, combat_state=replace(ship.combat_state, instance=instance),
                motion_state=replace(ship.motion_state, fuel_units=instance.operational_state.fuel_units,
                    hull_integrity_fraction=instance.current_hull_integrity_fraction))
        ships.append(ship)
    return replace(scene, ships=tuple(ships))


def check_overlap_and_delivery():
    old, _, session = case("functional_6.guided_projectiles")
    target = session.scene.ships[0].ship_id
    scene = replace_instance(session.scene, target, lambda instance: replace(instance,
        weapon_timeline_state=replace(instance.weapon_timeline_state,
            sequences=tuple(replace(q, next_event_time_s=2 / 60) for q in instance.weapon_timeline_state.sequences))))
    controls = {target: directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))}
    first = step(old, session, scene, controls)
    second = step(old, session, first.resulting_scene)
    third = step(old, session, second.resulting_scene)
    assert any(w.ship_id == target and w.event.action_kind == "fire" and w.event.status == "resolved" and w.event.tactical_time_s == 2 / 60 for w in second.weapon_events)
    assert any(r.ship_id == target and r.boundary_phase == "closing" and r.after.actual_output_percent == 2 for r in second.propulsion_boundaries)
    assert second.ship_results[0].diagnostics.active_force_body_n.y == 0
    assert third.ship_results[0].diagnostics.active_force_body_n.y == 4000
    assert third.ship_results[0].diagnostics.request.runtime_parameters_sha256 == second.ship_results[0].resulting_runtime.source_sha256
    assert not any(w.ship_id == target and w.event.action_kind == "fire" for w in third.weapon_events)
    assert not any(r.boundary_phase == "opening" for r in third.propulsion_boundaries)

    old, _, session = case("functional_6.motion_only")
    target = session.scene.ships[0].ship_id
    # 单个主机冷启动，另一侧 warm ready，形成真实异步实际阶段。
    cold = session.scene.ships[0]
    cold = replace(cold, propulsion_state=replace(cold.propulsion_state, engines=tuple(
        replace(e, phase="off", ready_at_fixed_step=None) if e.actuator_instance_id == "main_engine_starboard" else e
        for e in cold.propulsion_state.engines)))
    scene = replace(session.scene, ships=(cold,) + session.scene.ships[1:])
    for n in range(60):
        scene = step(old, session, scene, controls if n == 0 else None).resulting_scene
    limited = replace_instance(scene, target, lambda i: replace(i,
        operational_state=replace(i.operational_state, fuel_units=1 / 120)))
    partial = step(old, session, limited)
    d = partial.ship_results[0].diagnostics
    close(d.fuel_delivery_fraction, .5)
    close(d.active_force_body_n.y, 50000)
    close(d.active_torque_n_m, -250000)
    assert partial.resulting_scene.ships[0].motion_state.fuel_units == 0
    empty = step(old, session, partial.resulting_scene)
    assert empty.ship_results[0].diagnostics.active_force_body_n.length == 0
    assert empty.ship_results[0].diagnostics.fuel_units_consumed == 0
    assert any(e.actual_output_percent == 100 for e in empty.resulting_scene.ships[0].propulsion_state.engines)
    assert not any(e["event"]["kind"] == "engine_tripped" for e in empty.to_dict()["propulsion_events"])
    destroyed = replace_instance(scene, target, lambda i: replace(i, module_states=tuple(
        replace(m, current_durability_points=0) if m.instance_id == "main_engine_port" else m for m in i.module_states)))
    damaged = step(old, session, destroyed)
    rows = damaged.ship_results[0].propulsion_aggregation.contributions
    assert len(rows) == 6 and not next(r for r in rows if r.actuator_instance_id == "main_engine_port").runtime_available
    assert damaged.ship_results[0].diagnostics.active_force_body_n.length == 0
    falling = replace_instance(scene, target, lambda i: replace(i, current_hull_integrity_fraction=0))
    fallen = step(old, session, falling)
    assert fallen.ship_results[0].propulsion_delivery_status == "suppressed_falling"
    assert fallen.ship_results[0].diagnostics.active_force_body_n.length == 0
    assert fallen.ship_results[0].diagnostics.fuel_units_consumed == 0
    exited = step(old, session, scene, exit_directives=(TacticalSceneExitDirective(target, scene.tactical_time_s, "scripted_transfer"),))
    assert exited.ship_results[0].diagnostics is None
    assert exited.ship_results[0].propulsion_delivery_status == "suppressed_exited"
    next_exit = step(old, session, exited.resulting_scene)
    assert next_exit.resulting_scene.ships[0].motion_state.fuel_units == exited.resulting_scene.ships[0].motion_state.fuel_units
    # 模式随存档持久化，但方向每步重新由速度计算；缺失侧向/反向主机不借用 yaw。
    moving = replace(session.scene, ships=(replace(session.scene.ships[0],
        motion_state=replace(session.scene.ships[0].motion_state, velocity_world_mps=Vec2(3, 10))),) + session.scene.ships[1:])
    braking = step(old, session, moving, {target: directional_control(automatic_brake=True)})
    assert braking.resulting_scene.ships[0].propulsion_control.automatic_brake
    assert braking.ship_results[0].missing_propulsion_channels == ("translation.reverse", "translation.left")
    assert braking.ship_results[0].diagnostics.active_force_body_n.length == 0
    assert braking.ship_results[0].diagnostics.drag_force_world_n.length > 0
    assert step(old, session, braking.resulting_scene).resulting_scene.ships[0].propulsion_control.automatic_brake
    return {"weapon_and_stage_closing_boundary": 2, "partial_fuel_fraction": d.fuel_delivery_fraction,
        "partial_force_y_n": d.active_force_body_n.y, "partial_torque_n_m": d.active_torque_n_m,
        "destroyed_actuator_identity_preserved": True, "empty_fuel_keeps_time_state": True,
        "falling_and_exited_delivery_suppressed_without_hard_trip": True,
        "automatic_brake_missing_channels": list(braking.ship_results[0].missing_propulsion_channels)}


def check_negative_boundaries():
    old, source, session = case("functional_6.motion_only")
    count = 0
    def reject(action, code=None):
        nonlocal count
        rejected(action, code)
        count += 1
    def direct(scene, **kwargs):
        return advance_tactical_scene_step(scene, session.bindings, old.timing_catalog,
            old.projectile_catalog, old.material_registry, **kwargs)
    for scene in (source, session.resource_bundle.scene):
        reject(lambda: direct(scene), "tactical_scene.propulsion_unwired")
    reject(lambda: direct(session.scene), "actual_scene.context_required")
    reject(lambda: direct(old.initial_scene, propulsion_context=session.propulsion_context), "actual_scene.unexpected_input")
    reject(lambda: direct(session.scene, propulsion_context=session.propulsion_context, controls={}), "actual_scene.legacy_control")
    reject(lambda: step(old, session, session.scene, []), "actual_scene.control_map")
    reject(lambda: step(old, session, session.scene, {"ship.unknown": directional_control()}))
    ship_id = session.scene.ships[0].ship_id
    reject(lambda: step(old, session, session.scene, {ship_id: TacticalControlInput()}), "actual_scene.control_type")
    wrong_context = replace(session.propulsion_context, execution=replace(session.propulsion_context.execution, resource_bundle_sha256="0" * 64))
    reject(lambda: direct(session.scene, propulsion_context=wrong_context), "actual_scene.execution_lineage")
    limited_context = replace(session.propulsion_context, ships=session.propulsion_context.ships[:-1])
    reject(lambda: direct(session.scene, propulsion_context=limited_context), "actual_scene.ship_set")
    for key, value in (("interface", "gaotian.tactical-scene-timeline/v4alpha1"), ("policy", "unknown"), ("extra", 0)):
        payload = session.scene.to_dict()
        payload[key] = value
        reject(lambda: TacticalSceneState.parse(payload))
    payload = session.scene.to_dict()
    payload.pop("propulsion_execution")
    reject(lambda: TacticalSceneState.parse(payload))
    payload = session.scene.to_dict()
    payload["ships"][0].pop("propulsion_control")
    reject(lambda: TacticalSceneState.parse(payload))
    wrong_engine = replace(session.scene.ships[0].propulsion_state.engines[0], phase="running",
        commanded_notch="full", target_output_percent=100, next_transition_step=2,
        response_started_at_fixed_step=0, response_start_output_percent=0)
    wrong_ship = replace(session.scene.ships[0], propulsion_state=replace(session.scene.ships[0].propulsion_state,
        engines=(wrong_engine,) + session.scene.ships[0].propulsion_state.engines[1:]))
    reject(lambda: step(old, session, replace(session.scene, ships=(wrong_ship,) + session.scene.ships[1:])))
    left = directional_control((ChannelPropulsionCommand("yaw.counterclockwise", None, 100),))
    right = directional_control((ChannelPropulsionCommand("yaw.clockwise", None, 100),))
    left_step = step(old, session, session.scene, {ship_id: left})
    reject(lambda: step(old, session, left_step.resulting_scene, {ship_id: right}), "propulsion_control.direction_switch_unwired")
    left_live = step(old, session, left_step.resulting_scene)
    stopping = step(old, session, left_live.resulting_scene, {ship_id: directional_control()})
    # 上步 closing 已提交到 2%，停车后反向仍被实际输出门阻止。
    assert any(e.actual_output_percent == 2 for e in stopping.resulting_scene.ships[0].propulsion_state.engines)
    reject(lambda: step(old, session, stopping.resulting_scene, {ship_id: right}), "propulsion_control.direction_switch_unwired")
    first = check_first_step()
    expected = first.to_dict()
    for mutate in (
        lambda p: p.update(source_fixed_step_index=True),
        lambda p: p.update(source_scene_sha256="0" * 64),
        lambda p: p.update(interface="gaotian.tactical-scene-step-resolution/v3alpha1"),
        lambda p: p["propulsion_boundaries"].pop(),
        lambda p: p["propulsion_boundaries"].append(deepcopy(p["propulsion_boundaries"][0])),
        lambda p: p["propulsion_boundaries"][0].update(boundary_phase="closing"),
        lambda p: p["propulsion_boundaries"][0].update(ship_id="ship.wrong"),
        lambda p: p["propulsion_boundaries"][0]["after"].update(next_transition_step=7),
        lambda p: p["propulsion_boundaries"][0]["command"].update(target_output_percent=True),
        lambda p: p.update(weapon_events=[{}]),
    ):
        changed = deepcopy(expected)
        mutate(changed)
        reject(lambda: validate_actual_scene_step_payload(changed, session.scene, first, session.propulsion_context))
    saved = save_actual_scene(first.resulting_scene, session.propulsion_context)
    def load(value):
        return load_actual_scene_save(value, root=ROOT, scene_id=session.resource_bundle.scene_id,
            source_scene=source, source_bindings=old.bindings, safety_profile=session.propulsion_context.safety_profile)
    for mutate in (
        lambda p: p.update(interface="unknown"), lambda p: p.update(extra={}),
        lambda p: p.update(scene_sha256="0" * 64),
    ):
        changed = deepcopy(saved)
        mutate(changed)
        reject(lambda: load(changed))
    for mutate in (
        lambda p: p["propulsion_execution"].update(resource_bundle_sha256="0" * 64),
        lambda p: p.update(propulsion_safety_profile_sha256="0" * 64),
        lambda p: p["ships"][0].update(derived_snapshot_sha256="0" * 64),
        lambda p: p["ships"][0]["propulsion_state"]["engines"][0].update(next_transition_step=9),
        lambda p: p["ships"][0]["propulsion_state"]["engines"][0].update(response_started_at_fixed_step=1),
        lambda p: p["ships"][0]["propulsion_state"]["engines"][0].update(next_transition_step=1),
        lambda p: p["ships"][0]["propulsion_control"].update(overg_requested=1),
        lambda p: p["ships"][0]["propulsion_state"]["governors"][0].update(last_evaluated_step_index=0),
    ):
        changed = deepcopy(saved)
        mutate(changed["scene"])
        changed["scene_sha256"] = canonical_sha256(changed["scene"])
        reject(lambda: load(changed))
    return count


def check_impact_runtime_feedback():
    old, source, session = case("functional_6.motion_only")
    target, shooter = session.scene.ships[:2]
    scene = session.scene
    control = {target.ship_id: directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))}
    for n in range(60):
        scene = step(old, session, scene, control if n == 0 else None).resulting_scene
    shot = ProjectileState("projectile.d2b4.near_hull", shooter.ship_id, "weapon_upper_port",
        "gtw.munition.fixture.76mm.standard", target.ship_id, 0, scene.tactical_time_s, 0,
        (-4.9, scene.ships[0].motion_state.position_world_m.y - 80), (0, 1000), 0)
    scene = replace(scene, projectile_world=replace(scene.projectile_world, projectiles=(shot,)))
    result = None
    for _ in range(20):
        result = step(old, session, scene)
        scene = result.resulting_scene
        if result.impact_events:
            break
    assert result.impact_events
    impact = result.impact_events[0]
    assert impact.target_ship_id == target.ship_id and "main_engine_port" in impact.damaged_module_instance_ids
    before_port = next(r for r in result.ship_results[0].propulsion_aggregation.contributions if r.actuator_instance_id == "main_engine_port")
    after = step(old, session, scene)
    after_port = next(r for r in after.ship_results[0].propulsion_aggregation.contributions if r.actuator_instance_id == "main_engine_port")
    assert after_port.runtime_thrust_n < before_port.runtime_thrust_n
    # 命中后的实例/局部装甲/推进排程一起重载，续跑仍与不中断执行一致。
    restored = load_actual_scene_save(save_actual_scene(scene, session.propulsion_context), root=ROOT,
        scene_id=session.resource_bundle.scene_id, source_scene=source, source_bindings=old.bindings,
        safety_profile=session.propulsion_context.safety_profile)
    resumed = step(old, restored, restored.scene)
    assert resumed.resulting_scene == after.resulting_scene and resumed.to_dict() == after.to_dict()
    return {"impact_step": scene.fixed_step_index, "impact": impact.to_dict(),
        "port_runtime_thrust_before_n": before_port.runtime_thrust_n,
        "port_runtime_thrust_after_n": after_port.runtime_thrust_n,
        "post_impact_save_reload_equal": True}


def collect_matrix():
    cases, differences = {}, {}
    total_ship_steps = total_actuator_samples = 0
    for name, _, _ in migrated_cases():
        old, _, initial = case(name)
        source_hash = canonical_sha256(initial.resource_bundle)
        replays = []
        for repetition in range(3):
            scene = initial.scene
            session = replace(initial, bindings=prepare_tactical_scene_bindings(scene, initial.bindings))
            snapshots, results, inputs, counts = [], [], [], Counter()
            first, last = None, None
            for n in range(12):
                control = {ship: migrate_known_t0_control_to_directional(c) for ship, c in controls_for_step(old, scene).items()}
                inputs.append({key: value.to_dict() for key, value in sorted(control.items())})
                before = scene
                result = step(old, session, scene, control)
                scene = result.resulting_scene
                first = result if first is None else first
                last = result
                payload = result.to_dict()
                snapshots.append(canonical_sha256(scene))
                results.append(canonical_sha256(payload))
                assert scene.propulsion_execution == initial.scene.propulsion_execution
                assert TacticalSceneState.parse(scene.to_dict()).to_dict() == scene.to_dict()
                for key, value in payload.items():
                    if key.endswith("_events") or key == "spawned_projectiles":
                        counts[key] += len(value)
                if repetition == 0:
                    total_ship_steps += len(scene.ships)
                    total_actuator_samples += sum(len(s.propulsion_state.engines) for s in scene.ships)
                if n in (0, 1, 11):
                    validate_actual_scene_step_payload(payload, before, result, session.propulsion_context)
            replays.append({"initial_scene_sha256": canonical_sha256(initial.scene),
                "resource_bundle_sha256": source_hash, "steps": 12,
                "input_stream_sha256": canonical_sha256(inputs), "per_step_scene_hashes": snapshots,
                "per_step_result_hashes": results, "resulting_scene_sha256": canonical_sha256(scene),
                "event_counts": dict(sorted(counts.items()))})
        assert replays[0] == replays[1] == replays[2]
        assert canonical_sha256(initial.resource_bundle) == source_hash
        cases[name] = replays[0]
        old_scene = old.initial_scene
        old_first = None
        for n in range(12):
            old_result = advance_scenario_step(old, old_scene)
            old_first = old_result if old_first is None else old_first
            old_scene = old_result.resulting_scene
        new_ship, old_ship = scene.ships[0], old_scene.ships[0]
        differences[name] = {
            "legacy_initial_scene_sha256": canonical_sha256(old.initial_scene),
            "new_initial_scene_sha256": canonical_sha256(initial.scene),
            "legacy_first_force_body_n": old_first.ship_results[0].diagnostics.active_force_body_n.to_list(),
            "new_first_force_body_n": first.ship_results[0].diagnostics.active_force_body_n.to_list(),
            "new_step_12_force_body_n": last.ship_results[0].diagnostics.active_force_body_n.to_list(),
            "first_ship_position_delta_m": (new_ship.motion_state.position_world_m - old_ship.motion_state.position_world_m).to_list(),
            "legacy_total_fuel_consumed": sum(a.motion_state.fuel_units - b.motion_state.fuel_units for a,b in zip(old.initial_scene.ships, old_scene.ships)),
            "new_total_fuel_consumed": sum(a.motion_state.fuel_units - b.motion_state.fuel_units for a,b in zip(initial.scene.ships, scene.ships)),
        }
    return {"interface": "gaotian.t0-actual-propulsion-step-golden/v1", "scene_interface": ACTUAL_SCENE_INTERFACE_ID,
            "replays": 3, "cases": cases}, {"scenes": 12, "ships": 224, "actuators": 1224,
            "steps_per_scene_per_replay": 12, "ship_steps_per_replay": total_ship_steps,
            "actuator_samples_per_replay": total_actuator_samples, "differences": differences}


def check_schemas_and_imports():
    schemas = [json.loads((ROOT / "舰艇数据/模式" / name).read_text(encoding="utf-8")) for name in (
        "高天荒野舰艇统一战术场景状态契约.v5alpha1.schema.json",
        "高天荒野舰艇实际推进边界记录契约.v1alpha1.schema.json",
        "高天荒野舰艇场景单步推进结果契约.v4alpha1.schema.json",
        "高天荒野舰艇实际推进场景存档契约.v1alpha1.schema.json")]
    registry = {}
    for path in (ROOT / "舰艇数据/模式").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry[schema["$id"]] = schema
    first = check_first_step()
    _, _, session = case("functional_6.motion_only")
    samples = (first.resulting_scene.to_dict(), first.propulsion_boundaries[0].to_dict(),
        first.to_dict(), save_actual_scene(first.resulting_scene, session.propulsion_context))
    for schema, payload in zip(schemas, samples):
        assert set(schema["required"]) <= set(payload) <= set(schema["properties"])
        assert schema["properties"]["interface"]["const"] == payload["interface"]
        def walk(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    root, _, pointer = node["$ref"].partition("#")
                    target = registry[root] if root else schema
                    for part in pointer.removeprefix("/").split("/") if pointer else ():
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                    assert isinstance(target, dict)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(schema)
    for prefix in ("", "import 高天荒野舰艇统一战术场景; "):
        subprocess.run([sys.executable, "-X", "utf8", "-c", prefix +
            "import sys; import 高天荒野舰艇实际推进场景; assert not any(k.startswith('benchmarks.') or k.endswith('测试') for k in sys.modules)"], cwd=ROOT, check=True)
    return {"schema_shapes_and_refs": 4, "cold_import_orders": 2}


def collect_evidence():
    check_first_step()
    boundaries = check_long_sequence()
    overlap = check_overlap_and_delivery()
    negative_cases = check_negative_boundaries()
    golden, matrix = collect_matrix()
    return golden, {"long_sequence": boundaries, "overlap_and_delivery": overlap,
        "negative_cases": negative_cases, "matrix": matrix,
        "impact_runtime_feedback": check_impact_runtime_feedback(),
        "contracts": check_schemas_and_imports(), "legacy_isolation": test_existing_authority_isolation(),
        "official_performance_runs_executed": 0}


def main():
    golden, evidence = collect_evidence()
    assert json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) == golden
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["status"] == "PASS" and report["evidence"] == evidence
    for relative, expected in report["implementation_hashes"].items():
        assert file_sha256(ROOT / relative) == expected, relative
    print(json.dumps({"status": "PASS", "golden_cases": len(golden["cases"]), "evidence": evidence}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
