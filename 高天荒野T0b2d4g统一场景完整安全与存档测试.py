"""d4.7：完整安全统一场景、资源重建、故障/互锁及存档续跑证据。"""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
from unittest.mock import patch

from benchmarks.t0.scenario import controls_for_step
from 高天荒野T0b2d2a推进资源与控制桥测试 import migrated_cases, PROFILE_PATH
from 高天荒野T0b2d2b4场景接线与新黄金测试 import step
from 高天荒野舰艇完整受控推进场景 import (
    build_known_fully_governed_scene, save_fully_governed_scene, load_fully_governed_scene_save,
    validate_fully_governed_scene_step_payload,
)
from 高天荒野舰艇完整受控推进场景合同 import SceneHardFaultCommandBatch
from 高天荒野舰艇完整受控推进场景版本 import FULL_SCENE_INTERFACE_ID, FULL_STEP_INTERFACE_ID
from 高天荒野舰艇受控推进硬故障适配器 import GovernedPropulsionHardFaultCommand
from 高天荒野舰艇定向推进控制桥 import directional_control, migrate_known_t0_control_to_directional
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇统一战术场景 import TacticalSceneState, TacticalSceneExitDirective, advance_tactical_scene_step
from 高天荒野舰艇战术机动求解器 import Vec2
import 高天荒野舰艇受控推进完整安全适配器 as full_adapter


ROOT = Path(__file__).resolve().parent
REPORT = ROOT/'舰艇数据/报告/阶段T0b2d4g统一场景完整安全与存档接口.v1.json'


def refused(action, code=None):
    try:
        action()
    except ContractError as error:
        if code:
            assert error.code == code, str(error)
        return
    raise AssertionError('非法输入必须以 ContractError 拒绝')


@lru_cache(maxsize=1)
def source_cases():
    return migrated_cases()


@lru_cache(maxsize=12)
def case(name='functional_6.motion_only'):
    _, old, source = next(item for item in source_cases() if item[0] == name)
    session = build_known_fully_governed_scene(ROOT, name, source, old.bindings, load_propulsion_safety_profile(PROFILE_PATH))
    return old, source, session


def control(channel='translation.forward', notch='full', *, overg=False):
    return directional_control((ChannelPropulsionCommand(channel, notch, None),), overg_requested=overg)


def change_ship(scene, ship_id, change):
    return replace(scene, ships=tuple(change(s) if s.ship_id == ship_id else s for s in scene.ships))


def fuel(scene, ship_id, value):
    def change(ship):
        instance = ship.combat_state.instance
        return replace(ship, motion_state=replace(ship.motion_state, fuel_units=value),
            combat_state=replace(ship.combat_state, instance=replace(instance,
                operational_state=replace(instance.operational_state, fuel_units=value))))
    return change_ship(scene, ship_id, change)


def command_batch(scene, commands):
    return SceneHardFaultCommandBatch(canonical_sha256(scene), scene.fixed_step_index, tuple(sorted(commands.items())))


def reload_scene(old, source, session, scene):
    saved = save_fully_governed_scene(scene, session.propulsion_context)
    restored = load_fully_governed_scene_save(json.loads(json.dumps(saved)), root=ROOT,
        scene_id=session.propulsion_context.execution.scene_id, source_scene=source, source_bindings=old.bindings,
        safety_profile=session.propulsion_context.safety_profile)
    assert restored.scene == scene
    assert all(a.runtime_cache is not b.runtime_cache for a, b in zip(session.bindings, restored.bindings))
    return restored


def check_named_matrix():
    trace = []
    ships = engines = governors = 0
    for name, _, _ in source_cases():
        old, source, initial = case(name)
        assert initial.scene.to_dict()['interface'] == FULL_SCENE_INTERFACE_ID
        commands = {s: migrate_known_t0_control_to_directional(c) for s, c in controls_for_step(old, initial.scene).items()}
        with patch.object(full_adapter, 'evaluate_whole_ship_propulsion_safety', wraps=full_adapter.evaluate_whole_ship_propulsion_safety) as counter:
            result = step(old, initial, initial.scene, commands)
        payload = result.to_dict()
        validate_fully_governed_scene_step_payload(payload, initial.scene, result, initial.propulsion_context)
        assert payload['interface'] == FULL_STEP_INTERFACE_ID
        assert counter.call_count == len(initial.scene.ships)
        assert len(result.fully_governed_openings) == len(result.fully_governed_closings) == len(initial.scene.ships)
        loaded = reload_scene(old, source, initial, result.resulting_scene)
        resumed = step(old, loaded, loaded.scene)
        continuous = step(old, initial, result.resulting_scene)
        assert resumed.to_dict() == continuous.to_dict()
        ships += len(initial.scene.ships)
        engines += sum(len(s.propulsion_state.engines) for s in initial.scene.ships)
        governors += sum(len(s.propulsion_state.governors) for s in initial.scene.ships)
        trace.append((name, canonical_sha256(payload), canonical_sha256(resumed.to_dict())))
    return {'scenes': len(trace), 'ships': ships, 'engines': engines, 'governors': governors,
            'steps_per_scene': 2, 'reload_boundary': 1, 'trace_sha256': canonical_sha256(trace), 'closing_once_per_active_ship': True}


def check_fault_reset_emergency():
    old, source, session = case()
    target = session.scene.ships[0].ship_id
    failed = fuel(session.scene, target, 0.0)
    original_hash = canonical_sha256(failed)
    trip = step(old, session, failed, {target: control(overg=True)})
    assert canonical_sha256(failed) == original_hash
    tripped = next(s for s in trip.resulting_scene.ships if s.ship_id == target)
    assert all(e.phase == 'tripped' for e in tripped.propulsion_state.engines)
    assert dict(trip.fully_governed_openings)[target].hard_fault_events
    assert next(r for r in trip.ship_results if r.ship_id == target).diagnostics.base.diagnostic.fuel_units_consumed == 0
    validate_fully_governed_scene_step_payload(trip.to_dict(), failed, trip, session.propulsion_context)
    loaded = reload_scene(old, source, session, trip.resulting_scene)
    assert step(old, loaded, loaded.scene).to_dict() == step(old, session, trip.resulting_scene).to_dict()
    ids = tuple(e.actuator_instance_id for e in tripped.propulsion_state.engines)
    reset = GovernedPropulsionHardFaultCommand(ids)
    invalid = command_batch(trip.resulting_scene, {target: reset})
    before_rejected = canonical_sha256(trip.resulting_scene)
    refused(lambda: step(old, session, trip.resulting_scene, propulsion_hard_fault_commands=invalid))
    assert canonical_sha256(trip.resulting_scene) == before_rejected
    recovered = fuel(trip.resulting_scene, target, session.scene.ships[0].motion_state.fuel_units)
    # 补回燃料不会自动清除跳闸锁存。
    latched = step(old, session, recovered)
    assert all(e.phase == 'tripped' for e in latched.resulting_scene.ships[0].propulsion_state.engines)
    batch = command_batch(recovered, {target: reset})
    result = step(old, session, recovered, propulsion_hard_fault_commands=batch)
    opening = dict(result.fully_governed_openings)[target]
    assert all(e.actual_output_percent == 0 and e.target_output_percent == 0 for e in opening.state.engines)
    assert any(r.action == 'reset' for r in opening.hard_fault_opening.hard_fault_results)
    validate_fully_governed_scene_step_payload(result.to_dict(), recovered, result, session.propulsion_context)
    after_reset = reload_scene(old, source, session, result.resulting_scene)
    next_reset = step(old, after_reset, after_reset.scene)
    assert not any(r.action == 'reset' for r in dict(next_reset.fully_governed_openings)[target].hard_fault_opening.hard_fault_results)
    refused(lambda: step(old, after_reset, after_reset.scene, propulsion_hard_fault_commands=batch), 'full_scene.command_source')
    emergency = command_batch(session.scene, {target: GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')})
    cut = step(old, session, session.scene, {target: control()}, propulsion_hard_fault_commands=emergency)
    cut_opening = dict(cut.fully_governed_openings)[target]
    assert all(e.actual_output_percent == 0 and e.target_output_percent == 0 for e in cut_opening.state.engines)
    assert all(d.action == 'emergency_cut_hold' for d in cut_opening.direction_interlock.decisions)
    assert cut.resulting_scene.ships[0].propulsion_control == control()
    cut_loaded = reload_scene(old, source, session, cut.resulting_scene)
    resumed = step(old, cut_loaded, cut_loaded.scene)
    assert dict(resumed.fully_governed_openings)[target].hard_fault_opening.command == GovernedPropulsionHardFaultCommand()
    assert resumed.to_dict() == step(old, session, cut.resulting_scene).to_dict()
    return {'trip_with_overg': True, 'fuel_restore_preserves_latch': True, 'failed_reset_atomic': True,
            'reset_opening_zero': True, 'reset_not_reconsumed': True, 'emergency_one_shot': True,
            'reload_checkpoints': ['tripped', 'reset', 'emergency'], 'trace_sha256': canonical_sha256([trip.to_dict(), result.to_dict(), cut.to_dict()])}


def check_reversal_reload():
    old, source, initial = case()
    target = initial.scene.ships[0].ship_id
    # Yaw 双向喷口无需冷启动，短序列也覆盖实际升降阶与反向精确归零放行。
    def yaw(channel):
        return directional_control((ChannelPropulsionCommand(channel, None, 25),))
    first, second = 'yaw.counterclockwise', 'yaw.clockwise'
    session, scene = initial, initial.scene
    trace, checkpoints = [], {}
    blocking_steps, release_steps = [], []
    bindings = initial.propulsion_context.ship(target).aggregation_context.bindings
    channels = {b.actuator_instance_id: b.command_channels[0] for b in bindings}
    for n in range(52):
        requested = {target: yaw(first if n < 18 else second)} if n in (0, 18) else None
        result = step(old, session, scene, requested)
        opening = dict(result.fully_governed_openings)[target]
        if n >= 18:
            decisions = {d.command_channel: d for d in opening.direction_interlock.decisions}
            old_output = sum(e.actual_output_percent for e in opening.hard_fault_opening.state.engines if channels[e.actuator_instance_id] == first)
            if old_output > 0:
                assert decisions[second].action == 'blocked_until_opposing_zero'
                assert all(e.actual_output_percent == 0 for e in opening.state.engines if channels[e.actuator_instance_id] == second)
                blocking_steps.append(n)
            else:
                assert decisions[second].action == 'pass_through'
                release_steps.append(n)
        trace.append(canonical_sha256(result.to_dict()))
        scene = result.resulting_scene
        if scene.fixed_step_index in (18, 20, 27):
            checkpoints[scene.fixed_step_index] = save_fully_governed_scene(scene, initial.propulsion_context)
        if n in (0, 18, 26, 51):
            validate_fully_governed_scene_step_payload(result.to_dict(), session.scene if n == 0 else previous, result, session.propulsion_context)
        previous = scene
    assert blocking_steps and release_steps and min(release_steps) > min(blocking_steps)
    final = scene.ships[0]
    assert any(e.actual_output_percent > 0 for e in final.propulsion_state.engines if channels[e.actuator_instance_id] == second)
    for boundary, saved in checkpoints.items():
        restored = load_fully_governed_scene_save(saved, root=ROOT, scene_id='functional_6.motion_only',
            source_scene=source, source_bindings=old.bindings, safety_profile=initial.propulsion_context.safety_profile)
        resumed = restored.scene
        for n in range(boundary, 52):
            result = step(old, restored, resumed, {target: yaw(second)} if n == 18 else None)
            assert canonical_sha256(result.to_dict()) == trace[n], (boundary, n)
            resumed = result.resulting_scene
    return {'steps': len(trace), 'reload_boundaries': sorted(checkpoints), 'first_blocked_step': min(blocking_steps),
            'first_released_step': min(release_steps), 'trace_sha256': canonical_sha256(trace)}


def check_lifecycle_and_rejections():
    old, source, session = case()
    target = session.scene.ships[0].ship_id
    exited = step(old, session, session.scene, exit_directives=(TacticalSceneExitDirective(target, 0.0, 'scripted_transfer'),))
    validate_fully_governed_scene_step_payload(exited.to_dict(), session.scene, exited, session.propulsion_context)
    assert target not in dict(exited.fully_governed_openings)
    loaded = reload_scene(old, source, session, exited.resulting_scene)
    frozen = step(old, loaded, loaded.scene)
    assert frozen.resulting_scene.ships[0].propulsion_state == exited.resulting_scene.ships[0].propulsion_state
    closing_exit = step(old, session, session.scene, exit_directives=(TacticalSceneExitDirective(target, 1/60, 'scripted_transfer'),))
    validate_fully_governed_scene_step_payload(closing_exit.to_dict(), session.scene, closing_exit, session.propulsion_context)
    assert target in dict(closing_exit.fully_governed_closings)
    batch = command_batch(session.scene, {target: GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')})
    refused(lambda: step(old, session, session.scene, propulsion_hard_fault_commands=batch,
        exit_directives=(TacticalSceneExitDirective(target, 0.0, 'scripted_transfer'),)), 'full_scene.command_exited')
    refused(lambda: step(old, session, session.scene, propulsion_hard_fault_commands={}), 'full_scene.command_type')
    refused(lambda: step(old, session, session.scene, propulsion_hard_fault_commands=replace(batch, source_scene_sha256='f'*64)), 'full_scene.command_source')
    refused(lambda: advance_tactical_scene_step(old.initial_scene, (), None, None, None,
        propulsion_hard_fault_commands=batch), 'full_scene.legacy_commands')
    saved = save_fully_governed_scene(session.scene, session.propulsion_context)
    load_args = dict(root=ROOT, scene_id='functional_6.motion_only', source_scene=source, source_bindings=old.bindings,
                     safety_profile=session.propulsion_context.safety_profile)
    count = 0
    def reject_save(change):
        nonlocal count
        bad = deepcopy(saved)
        change(bad['scene'])
        bad['scene_sha256'] = canonical_sha256(bad['scene'])
        refused(lambda: load_fully_governed_scene_save(bad, **load_args))
        count += 1
    reject_save(lambda s: s['propulsion_execution'].update(resource_bundle_sha256='f'*64))
    reject_save(lambda s: s['ships'][0].update(derived_snapshot_sha256='f'*64))
    reject_save(lambda s: s['ships'][0]['propulsion_state']['engines'].pop())
    reject_save(lambda s: s['ships'][0]['propulsion_state']['engines'][0].update(actuator_instance_id='unknown.engine'))
    reject_save(lambda s: s['ships'][0]['propulsion_state']['governors'][0].update(last_evaluated_step_index=10))
    reject_save(lambda s: s.update(propulsion_safety_profile_sha256='f'*64))
    yaw = directional_control((ChannelPropulsionCommand('yaw.counterclockwise', None, 25),))
    responding = step(old, session, session.scene, {target: yaw}).resulting_scene
    bad_schedule = save_fully_governed_scene(responding, session.propulsion_context)
    scheduled = next(e for e in bad_schedule['scene']['ships'][0]['propulsion_state']['engines'] if e['next_transition_step'] is not None)
    scheduled['next_transition_step'] += 1
    bad_schedule['scene_sha256'] = canonical_sha256(bad_schedule['scene'])
    refused(lambda: load_fully_governed_scene_save(bad_schedule, **load_args), 'propulsion_time.committed_schedule')
    count += 1
    # 无主机反向能力时，自动制动仍报告缺失通道，不改用转向喷口。
    moving = change_ship(session.scene, target, lambda s: replace(s, motion_state=replace(s.motion_state, velocity_world_mps=Vec2(0.0, 3.0))))
    brake = step(old, session, moving, {target: directional_control(automatic_brake=True)})
    validate_fully_governed_scene_step_payload(brake.to_dict(), moving, brake, session.propulsion_context)
    def disable_cic(ship):
        instance = ship.combat_state.instance
        return replace(ship, combat_state=replace(ship.combat_state, instance=replace(instance,
            module_states=tuple(replace(m, operating_mode='standby') if m.instance_id == 'cic' else m for m in instance.module_states))))
    uncommanded = change_ship(session.scene, target, disable_cic)
    refused(lambda: step(old, session, uncommanded, {target: yaw}), 'tactical_scene.command_unavailable')
    suppressed = step(old, session, uncommanded)
    row = next(r for r in suppressed.ship_results if r.ship_id == target)
    assert row.propulsion_delivery_status == 'suppressed_uncommanded'
    assert row.diagnostics.base.diagnostic.fuel_units_consumed == 0
    assert row.diagnostics.base.diagnostic.active_force_body_n.length == 0
    validate_fully_governed_scene_step_payload(suppressed.to_dict(), uncommanded, suppressed, session.propulsion_context)
    return {'opening_exit_frozen': True, 'closing_exit_committed_once': True, 'invalid_save_cases': count,
            'automatic_brake_supported': True, 'uncommanded_zero_delivery': True, 'command_negative_cases': 5}


def check_closing_damage_and_fault_delay():
    from 高天荒野舰艇战术弹丸世界 import ProjectileState
    old, source, session = case()
    target, shooter = session.scene.ships[:2]
    shot = ProjectileState('projectile.d4g.near_hull', shooter.ship_id, 'weapon_upper_port',
        'gtw.munition.fixture.76mm.standard', target.ship_id, 0, 0.0, 0,
        (-4.9, target.motion_state.position_world_m.y - 80.0), (0.0, 1000.0), 0.0)
    impact_scene = replace(session.scene, projectile_world=replace(session.scene.projectile_world, projectiles=(shot,)))
    impact_scene = TacticalSceneState.parse(impact_scene.to_dict())
    result = step(old, session, impact_scene)
    assert result.impact_events
    closing = dict(result.fully_governed_closings)[target.ship_id]
    opening = dict(result.fully_governed_openings)[target.ship_id]
    assert closing.final_runtime_sha256 != opening.hard_fault_opening.projection.runtime_parameters_sha256
    assert 'main_engine_port' in result.impact_events[0].damaged_module_instance_ids
    validate_fully_governed_scene_step_payload(result.to_dict(), impact_scene, result, session.propulsion_context)
    # 在场景真实区间结算接口注入确定的船壳摧毁，用于检查交付/最终采样分离。
    # 保留原积分先发生，随后统一场景处理最终生命周期并执行收边界。
    import 高天荒野舰艇统一战术场景 as scene_module
    original_advance_world = scene_module.advance_projectile_world
    def destroy_during_interval(*args, **kwargs):
        outcome = original_advance_world(*args, **kwargs)
        updated_targets = []
        for item in outcome.resulting_targets:
            if item.ship_id == target.ship_id:
                item = replace(item, combat_state=replace(item.combat_state,
                    instance=replace(item.combat_state.instance, current_hull_integrity_fraction=0.0)))
            updated_targets.append(item)
        return replace(outcome, resulting_targets=tuple(updated_targets))
    yaw = directional_control((ChannelPropulsionCommand('yaw.counterclockwise', None, 25),))
    running = step(old, session, session.scene, {target.ship_id: yaw}).resulting_scene
    for _ in range(6):
        running = step(old, session, running).resulting_scene
    with patch.object(scene_module, 'advance_projectile_world', side_effect=destroy_during_interval):
        damaged = step(old, session, running)
    row = next(r for r in damaged.ship_results if r.ship_id == target.ship_id)
    closed = dict(damaged.fully_governed_closings)[target.ship_id]
    opened = dict(damaged.fully_governed_openings)[target.ship_id]
    assert row.propulsion_delivery_status == 'delivered' and closed.propulsion_delivery_status == 'suppressed_falling'
    assert row.diagnostics.base.diagnostic.fuel_units_consumed > 0
    assert any(e.actual_output_percent > 0 for e in opened.state.engines)
    assert not opened.hard_fault_events
    validate_fully_governed_scene_step_payload(damaged.to_dict(), running, damaged, session.propulsion_context)
    next_result = step(old, session, damaged.resulting_scene)
    next_opening = dict(next_result.fully_governed_openings)[target.ship_id]
    assert all(e.phase == 'tripped' for e in next_opening.state.engines)
    assert next_opening.hard_fault_events
    loaded = reload_scene(old, source, session, damaged.resulting_scene)
    assert step(old, loaded, loaded.scene).to_dict() == next_result.to_dict()
    return {'real_projectile_impact': True, 'closing_uses_post_impact_runtime': True,
            'interval_delivery_preserved_after_destruction': True, 'hard_fault_sampled_next_opening': True,
            'post_damage_reload_replay': True, 'trace_sha256': canonical_sha256([result.to_dict(), damaged.to_dict(), next_result.to_dict()])}


def collect_evidence():
    return {'named_matrix': check_named_matrix(), 'fault_reset_emergency': check_fault_reset_emergency(),
            'reversal_reload': check_reversal_reload(), 'lifecycle_rejections': check_lifecycle_and_rejections(),
            'closing_damage_fault_delay': check_closing_damage_and_fault_delay()}


def main():
    evidence = collect_evidence()
    report = load_json(REPORT)
    assert report['status'] == 'PASS' and report['evidence'] == evidence
    from benchmarks.t0.metadata import file_sha256
    for path, expected in report['implementation_hashes'].items():
        assert file_sha256(ROOT/path) == expected, path
    print(json.dumps({'status': 'PASS', 'evidence': evidence}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
