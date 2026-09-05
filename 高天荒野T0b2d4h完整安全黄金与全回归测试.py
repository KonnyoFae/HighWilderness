"""d4.8：独立完整安全黄金、三次场景重放、混合长序列与迁移续跑。"""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

from benchmarks.t0.metadata import file_sha256
from benchmarks.t0.scenario import controls_for_step
from 高天荒野T0b2d4g统一场景完整安全与存档测试 import (
    case, source_cases, step, change_ship, fuel, command_batch, reload_scene,
)
from 高天荒野舰艇完整受控推进场景 import (
    load_fully_governed_scene_save, validate_fully_governed_scene_step_payload,
)
from 高天荒野舰艇完整受控推进场景合同 import migrate_d3_scene_save, validate_d3_scene_save_migration
from 高天荒野舰艇受控推进场景 import build_known_governed_scene, save_governed_scene
from 高天荒野舰艇受控推进硬故障适配器 import GovernedPropulsionHardFaultCommand
from 高天荒野舰艇定向推进控制桥 import directional_control, migrate_known_t0_control_to_directional
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇统一战术场景 import TacticalSceneState, TacticalSceneExitDirective, prepare_tactical_scene_bindings
from 高天荒野舰艇数据契约 import canonical_sha256, load_json


ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT/'contracts/web_bridge/t0-fully-governed-propulsion-step-golden.v1.json'
REPORT = ROOT/'舰艇数据/报告/阶段T0b2d4h完整安全黄金与全回归接口.v1.json'
VALIDATION = ROOT/'artifacts/t0-local/d4h-last-validation.json'
LEGACY_GOLDENS = {
    't0-authority-step-golden.v1.json': 'e7e5cf3dd494e5d8390f2aeabfbb4d2e6f4426562901879dbc7268ee68b5f095',
    't0-actual-propulsion-step-golden.v1.json': '6636afd396df0dd1ec17906f4b8fbe0d23d1a88e49d47dae64d1872f259d78ed',
    't0-governed-propulsion-step-golden.v1.json': 'eb253fa94e691505564071d00716104f19443d1ef7f76719d8a49c719f725882',
}
LONG_STEPS = 401
RELOAD_PLANS = ((), (92, 112, 118, 212, 272, 277), (111, 117, 211, 267, 276, 337))


def progress(message):
    print(message, file=sys.stderr, flush=True)


def motion_payload(scene):
    return tuple({'ship_id': ship.ship_id, 'motion_state': ship.motion_state.to_dict()} for ship in scene.ships)


def event_counts(payload, counts):
    for name, values in payload.items():
        if name.endswith('_events') or name == 'spawned_projectiles':
            counts[name] += len(values)


def check_legacy_files():
    for name, expected in LEGACY_GOLDENS.items():
        assert file_sha256(ROOT/'contracts/web_bridge'/name) == expected, name
    return dict(LEGACY_GOLDENS)


def replay_named(name):
    old, _, initial = case(name)
    scene = initial.scene
    session = replace(initial, bindings=prepare_tactical_scene_bindings(scene, initial.bindings))
    trace, inputs = [], []
    counts, boundary_counts = Counter(), Counter()
    openings = closings = 0
    for n in range(12):
        controls = {ship_id: migrate_known_t0_control_to_directional(control)
                    for ship_id, control in controls_for_step(old, scene).items()}
        inputs.append({s: c.to_dict() for s, c in sorted(controls.items())})
        source = scene
        result = step(old, session, source, controls)
        scene = result.resulting_scene
        payload = result.to_dict()
        if n in (0, 11):
            validate_fully_governed_scene_step_payload(payload, source, result, session.propulsion_context)
        active = sum(s.lifecycle_state.physical_status != 'exited' for s in source.ships)
        assert len(result.fully_governed_openings) == len(result.fully_governed_closings) == active
        assert all(g.last_evaluated_step_index == n+1 for s in scene.ships
                   if s.lifecycle_state.physical_status != 'exited' for g in s.propulsion_state.governors)
        openings += active
        closings += active
        event_counts(payload, counts)
        for event in payload['propulsion_boundary_events']:
            boundary_counts[event['event_stage']] += 1
        trace.append({'result_sha256': canonical_sha256(payload), 'scene_sha256': canonical_sha256(scene)})
    return {
        'steps': 12, 'ships': len(scene.ships), 'opening_records': openings, 'closing_records': closings,
        'initial_scene_sha256': canonical_sha256(initial.scene), 'resulting_scene_sha256': canonical_sha256(scene),
        'input_stream_sha256': canonical_sha256(inputs), 'resource_bundle_sha256': canonical_sha256(initial.resource_bundle),
        'step_trace_sha256': canonical_sha256(trace), 'final_motion_sha256': canonical_sha256(motion_payload(scene)),
        'event_counts': dict(sorted(counts.items())), 'boundary_event_counts': dict(sorted(boundary_counts.items())),
    }


def collect_matrix(replays):
    old_golden = load_json(ROOT/'contracts/web_bridge/t0-governed-propulsion-step-golden.v1.json')
    cases = {}
    for name, _, _ in source_cases():
        rows = []
        for replay in range(replays):
            progress(f'd4h matrix {name}: replay {replay+1}/{replays}')
            rows.append(replay_named(name))
        assert all(row == rows[0] for row in rows)
        row, old = rows[0], old_golden['cases'][name]
        assert row['final_motion_sha256'] == old['final_motion_sha256'], name
        assert row['resource_bundle_sha256'] == old['resource_bundle_sha256'], name
        assert row['input_stream_sha256'] == old['input_stream_sha256'], name
        assert row['initial_scene_sha256'] != old['initial_scene_sha256']
        assert row['resulting_scene_sha256'] != old['resulting_scene_sha256']
        assert row['boundary_event_counts'].get('hard', 0) == row['boundary_event_counts'].get('soft', 0) == 0
        assert row['boundary_event_counts'].get('time', 0) == old['event_counts']['propulsion_events']
        for key, count in old['event_counts'].items():
            if key not in ('propulsion_events', 'propulsion_safety_events'):
                assert row['event_counts'].get(key, 0) == count, (name, key)
        cases[name] = row
    return cases


def long_initial():
    old, source, initial = case()
    target = initial.scene.ships[0].ship_id
    # 用真实退出指令建立五艘冻结舰；保留完整资源集合，长序列集中验证一艘活动舰。
    setup = step(old, initial, initial.scene, exit_directives=tuple(
        TacticalSceneExitDirective(s.ship_id, 0.0, 'scripted_transfer') for s in initial.scene.ships[1:]))
    scene = setup.resulting_scene
    def limit(ship):
        governors = tuple(replace(g, safety_ceiling_percent=25, safety_reasons=('crew_limit',),
            safety_limited_since_step=1, release_candidate_since_step=None, safety_revision=1)
            if g.command_channel == 'translation.forward' else g for g in ship.propulsion_state.governors)
        return replace(ship, propulsion_state=replace(ship.propulsion_state, governors=governors))
    scene = TacticalSceneState.parse(change_ship(scene, target, limit).to_dict())
    return old, source, initial, scene, canonical_sha256(setup.to_dict())


def mixed_control(main='full', yaw='yaw.counterclockwise', overg=False):
    return directional_control((ChannelPropulsionCommand('translation.forward', main, None),
        ChannelPropulsionCommand(yaw, None, 25)), overg_requested=overg)


def replay_long(reload_at):
    old, source, initial, scene, setup_sha = long_initial()
    initial_hash = canonical_sha256(scene)
    session = replace(initial, scene=scene, bindings=prepare_tactical_scene_bindings(scene, initial.bindings))
    target = scene.ships[0].ship_id
    channels = {b.actuator_instance_id: b.command_channels[0]
                for b in initial.propulsion_context.ship(target).aggregation_context.bindings}
    ids = tuple(e.actuator_instance_id for e in scene.ships[0].propulsion_state.engines)
    commands = {
        0: mixed_control(), 2: mixed_control(overg=True),
        90: mixed_control(yaw='yaw.clockwise', overg=True),
        212: mixed_control(main='half'), 248: mixed_control(main='stop'),
        266: mixed_control(main='stop', yaw='yaw.clockwise'),
        280: mixed_control(main='quarter', yaw='yaw.clockwise'),
    }
    trace, input_trace, samples = [], [], {}
    counts, actions = Counter(), Counter()
    blocked_steps, released_steps = [], []
    reset_steps, trip_steps, emergency_steps = [], [], []
    fuel_total = 0.0
    initial_frozen = {s.ship_id: (s.propulsion_state, s.propulsion_control) for s in scene.ships[1:]}
    checkpoints = set(RELOAD_PLANS[1]) | set(RELOAD_PLANS[2])
    for n in range(LONG_STEPS):
        if n % 80 == 0:
            progress(f'd4h long reload={list(reload_at)}: {n}/{LONG_STEPS}')
        boundary = scene.fixed_step_index
        if boundary in reload_at:
            session = reload_scene(old, source, session, scene)
            scene = session.scene
        resource_edit = None
        if n in (110, 270):
            resource_edit = 0.0
        elif n in (114, 273):
            resource_edit = 800.0
        if resource_edit is not None:
            scene = fuel(scene, target, resource_edit)
        hard = None
        if n in (116, 275):
            hard = GovernedPropulsionHardFaultCommand(ids)
        elif n == 210:
            hard = GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')
        batch = command_batch(scene, {target: hard}) if hard is not None else None
        requested = {target: commands[n]} if n in commands else None
        input_trace.append({'source_step': boundary, 'fuel_units': resource_edit,
            'control': commands[n].to_dict() if n in commands else None,
            'hard_command': hard.to_dict() if hard else None})
        before = scene
        result = step(old, session, before, requested, propulsion_hard_fault_commands=batch)
        opening = dict(result.fully_governed_openings)[target]
        assert len(result.fully_governed_openings) == len(result.fully_governed_closings) == 1
        hard_actions = [r.action for r in opening.hard_fault_opening.hard_fault_results]
        if 'trip' in hard_actions:
            trip_steps.append(n)
        if 'reset' in hard_actions:
            reset_steps.append(n)
        if n in (110, 114, 270, 273):
            assert all(e.phase == 'tripped' for e in opening.state.engines)
        if n in (116, 275, 210):
            assert all(e.actual_output_percent == e.target_output_percent == 0 for e in opening.state.engines)
        if n == 210:
            assert any(e.actual_output_percent > 0 for e in before.ships[0].propulsion_state.engines)
            assert all(d.action == 'emergency_cut_hold' for d in opening.direction_interlock.decisions)
            emergency_steps.append(n)
        if hard is None:
            assert opening.hard_fault_opening.command == GovernedPropulsionHardFaultCommand()
        for decision in opening.direction_interlock.decisions:
            actions[decision.action] += 1
        if 90 <= n < 110 or 266 <= n < 270:
            decisions = {d.command_channel: d for d in opening.direction_interlock.decisions}
            old_output = sum(e.actual_output_percent for e in opening.hard_fault_opening.state.engines
                             if channels[e.actuator_instance_id] == 'yaw.counterclockwise')
            if old_output > 0:
                assert decisions['yaw.clockwise'].action == 'blocked_until_opposing_zero'
                assert all(e.actual_output_percent == 0 for e in opening.state.engines
                           if channels[e.actuator_instance_id] == 'yaw.clockwise')
                blocked_steps.append(n)
            else:
                assert decisions['yaw.clockwise'].action == 'pass_through'
                released_steps.append(n)
        scene = result.resulting_scene
        for ship in scene.ships[1:]:
            assert (ship.propulsion_state, ship.propulsion_control) == initial_frozen[ship.ship_id]
            assert ship.motion_state.fixed_step_index == scene.fixed_step_index
        payload = result.to_dict()
        if boundary in checkpoints or n in (0, 90, 110, 114, 116, 210, 266, 270, 273, 275, 400):
            validate_fully_governed_scene_step_payload(payload, before, result, session.propulsion_context)
        fuel_total += result.ship_results[0].diagnostics.base.diagnostic.fuel_units_consumed
        event_counts(payload, counts)
        trace.append({'scene_sha256': canonical_sha256(scene), 'result_sha256': canonical_sha256(payload)})
        if boundary in checkpoints or n in (0, 2, 89, 90, 110, 114, 116, 209, 210, 266, 270, 273, 275, 400):
            ship = scene.ships[0]
            samples[str(scene.fixed_step_index)] = {
                'engines': {e.actuator_instance_id: {'phase': e.phase, 'actual': e.actual_output_percent,
                    'target': e.target_output_percent} for e in ship.propulsion_state.engines},
                'fuel_units': ship.motion_state.fuel_units,
                'governors_sha256': canonical_sha256([g.to_dict() for g in ship.propulsion_state.governors]),
            }
    assert trip_steps == [110, 270] and reset_steps == [116, 275] and emergency_steps == [210]
    assert blocked_steps and released_steps and min(released_steps) > min(blocked_steps)
    assert fuel_total > 0
    assert any(e.actual_output_percent == 25 for e in scene.ships[0].propulsion_state.engines if e.actuator_category == 'main_engine')
    governor = next(g for g in scene.ships[0].propulsion_state.governors if g.command_channel == 'translation.forward')
    assert governor.safety_ceiling_percent == 100 and governor.safety_revision >= 2
    return {'steps': LONG_STEPS, 'initial_boundary': 1, 'final_boundary': scene.fixed_step_index,
        'active_ships': 1, 'frozen_ships': 5, 'setup_sha256': setup_sha, 'initial_scene_sha256': initial_hash,
        'final_scene_sha256': canonical_sha256(scene), 'trace_sha256': canonical_sha256(trace),
        'input_stream_sha256': canonical_sha256(input_trace), 'samples': samples,
        'trip_input_steps': trip_steps, 'reset_input_steps': reset_steps, 'emergency_input_steps': emergency_steps,
        'first_blocked_input_step': min(blocked_steps), 'first_released_input_step': min(released_steps),
        'event_counts': dict(sorted(counts.items())), 'interlock_action_counts': dict(sorted(actions.items())),
        'actual_fuel_units_consumed': fuel_total, 'final_soft_ceiling_percent': governor.safety_ceiling_percent}


def check_migration_resume():
    old, source, initial = case()
    legacy = build_known_governed_scene(ROOT, initial.propulsion_context.execution.scene_id, source,
        old.bindings, initial.propulsion_context.safety_profile)
    saved = save_governed_scene(legacy.scene, legacy.propulsion_context)
    migrated, receipt = migrate_d3_scene_save(saved, legacy.propulsion_context, expected_source_save_sha256=canonical_sha256(saved))
    migrated = migrated.to_dict()
    validate_d3_scene_save_migration(receipt, saved, migrated, legacy.propulsion_context)
    loaded = load_fully_governed_scene_save(migrated, root=ROOT,
        scene_id=initial.propulsion_context.execution.scene_id, source_scene=source,
        source_bindings=old.bindings, safety_profile=initial.propulsion_context.safety_profile)
    assert loaded.scene == initial.scene
    resumed = step(old, loaded, loaded.scene, {loaded.scene.ships[0].ship_id: mixed_control()})
    direct = step(old, initial, initial.scene, {initial.scene.ships[0].ship_id: mixed_control()})
    assert resumed.to_dict() == direct.to_dict()
    validate_fully_governed_scene_step_payload(resumed.to_dict(), loaded.scene, resumed, loaded.propulsion_context)
    return {'loaded_ships': len(loaded.scene.ships), 'receipt_sha256': canonical_sha256(receipt),
        'resumed_result_sha256': canonical_sha256(resumed.to_dict()), 'matches_named_v7': True}


def collect(replays=3):
    legacy = check_legacy_files()
    cases = collect_matrix(replays)
    longs = [replay_long(plan) for plan in RELOAD_PLANS[:replays]]
    assert all(value == longs[0] for value in longs)
    golden = {'interface': 'gaotian.t0-fully-governed-propulsion-step-golden/v1',
        'scene_interface': 'gaotian.tactical-scene-timeline/v7alpha1',
        'step_interface': 'gaotian.tactical-scene-step-resolution/v6alpha1',
        'cases': cases, 'mixed_long_sequence': longs[0], 'migration_resume': check_migration_resume()}
    evidence = {'scenes': len(cases), 'steps_per_scene': 12, 'replays': replays,
        'ships': sum(row['ships'] for row in cases.values()),
        'ship_steps_per_replay': sum(row['closing_records'] for row in cases.values()),
        'opening_records_per_replay': sum(row['opening_records'] for row in cases.values()),
        'closing_records_per_replay': sum(row['closing_records'] for row in cases.values()),
        'motion_fuel_resource_and_native_events_match_d3_cases': len(cases),
        'long_steps_per_replay': LONG_STEPS, 'long_replays': len(longs),
        'long_reload_boundaries': [list(plan) for plan in RELOAD_PLANS[:replays]],
        'legacy_golden_hashes': legacy, 'official_performance_runs_executed': 0}
    return golden, evidence


def main():
    if '--emit-matrix-baseline' in sys.argv:
        print(json.dumps({'cases': collect_matrix(1), 'migration_resume': check_migration_resume()}, ensure_ascii=False, indent=2))
        return
    if '--emit-long' in sys.argv:
        print(json.dumps(replay_long(()), ensure_ascii=False, indent=2))
        return
    if '--emit-baseline' in sys.argv:
        golden, evidence = collect(replays=1)
        print(json.dumps({'golden': golden, 'baseline_evidence': evidence}, ensure_ascii=False, indent=2))
        return
    golden, evidence = collect()
    assert load_json(GOLDEN) == golden
    report = load_json(REPORT)
    for name, expected in report['implementation_hashes'].items():
        assert file_sha256(ROOT/name) == expected, name
    validation = {'status': 'PASS', 'evidence': evidence, 'golden_sha256': file_sha256(GOLDEN),
        'implementation_hashes': report['implementation_hashes']}
    VALIDATION.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION.write_text(json.dumps(validation, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
