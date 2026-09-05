"""d4.6：完整受控场景、命令、诊断、迁移及旧版隔离的可执行证据。"""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256, load_json
from 高天荒野舰艇完整受控推进场景版本 import *
from 高天荒野舰艇完整受控推进场景合同 import (
    FULL_EVENT_INTERFACE_ID, FULL_STEP_REQUIRED_KEYS, BASE_EVENT_KEYS,
    FullyGovernedSceneSave, FullyGovernedStepDiagnostics, SceneHardFaultCommandBatch,
    migrate_d3_scene_save, validate_d3_scene_save_migration, validate_fully_governed_scene,
    validate_fully_governed_scene_step_contract, validate_fully_governed_scene_step_sources,
    serialize_fully_governed_events,
)
from 高天荒野舰艇受控推进场景合同 import GovernedSceneSave, GovernedActualTacticalStepDiagnostics, validate_governed_scene_step_contract
from 高天荒野舰艇受控推进场景版本 import GovernedPropulsionExecutionPolicy
from 高天荒野舰艇受控推进完整安全适配器 import (
    commit_fully_governed_propulsion_opening, integrate_fully_governed_propulsion_interval,
    evaluate_fully_governed_propulsion_closing,
)
from 高天荒野舰艇受控推进硬故障适配器 import GovernedPropulsionHardFaultCommand
from 高天荒野舰艇统一战术场景 import TacticalSceneState, advance_tactical_scene_step
from 高天荒野舰艇推进通道合同 import TRANSLATION_CHANNELS, YAW_CHANNELS
from 高天荒野T0b2d3e统一场景受控推进与存档测试 import governed_cases, governed_case, step as d3_step
from 高天荒野T0b2d4e完整受控推进适配器测试 import (
    basic_initialized_fixture, fixture, physical_engines, propulsion_state, with_clock,
    state_with_outputs, translation_control, yaw_control, PROFILE,
)
from 高天荒野舰艇定向推进控制桥 import directional_control
import 高天荒野舰艇战术机动求解器 as dynamics


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / '舰艇数据/报告/阶段T0b2d4f场景存档与迁移合同接口.v1.json'
SCHEMAS = (
    '高天荒野舰艇受控推进治理标记契约.v2alpha1.schema.json',
    '高天荒野舰艇统一战术场景状态契约.v7alpha1.schema.json',
    '高天荒野舰艇受控实际推进场景存档契约.v2alpha1.schema.json',
    '高天荒野舰艇场景硬故障命令批次契约.v1alpha1.schema.json',
    '高天荒野舰艇受控推进存档迁移凭证契约.v1alpha1.schema.json',
    '高天荒野舰艇实际推进积分诊断契约.v3alpha1.schema.json',
    '高天荒野舰艇完整受控推进场景边界事件契约.v1alpha1.schema.json',
    '高天荒野舰艇场景单步推进结果契约.v6alpha1.schema.json',
)


def refused(action, code=None):
    try:
        action()
    except ContractError as error:
        if code is not None:
            assert error.code == code, str(error)
        return
    raise AssertionError('非法输入必须通过 ContractError 拒绝')


def mutate(value, path, replacement):
    result = deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return result


@lru_cache(maxsize=1)
def migrated_matrix():
    rows = []
    for name, old, _, session in governed_cases():
        source = GovernedSceneSave(session.scene.to_dict()).to_dict()
        target, receipt = migrate_d3_scene_save(source, session.propulsion_context, expected_source_save_sha256=canonical_sha256(source))
        rows.append((name, old, session, source, target, receipt))
    return tuple(rows)


def check_scene_save_and_migration():
    trace = []
    ships = engines = governors = 0
    for name, old, session, source, target, receipt in migrated_matrix():
        saved = target.to_dict()
        validate_d3_scene_save_migration(receipt, source, saved, session.propulsion_context)
        assert FullyGovernedSceneSave.parse(json.loads(json.dumps(saved))).to_dict() == saved
        assert TacticalSceneState.parse(saved['scene']).to_dict() == saved['scene']
        assert GovernedSceneSave(session.scene.to_dict()).to_dict() == source
        refused(lambda: GovernedSceneSave.parse(saved))
        refused(lambda: FullyGovernedSceneSave.parse(source))
        parsed = TacticalSceneState.parse(target.scene)
        refused(lambda: advance_tactical_scene_step(parsed, (), None, None, None), 'full_scene.context_required')
        # 已提交内容只有治理身份改变；运动、燃料、弹丸和软安全历史逐字保留。
        assert target.scene['ships'] == source['scene']['ships']
        assert target.scene['projectile_world'] == source['scene']['projectile_world']
        assert target.scene['fixed_step_index'] == source['scene']['fixed_step_index']
        for _ in range(2):
            again, again_receipt = migrate_d3_scene_save(source, session.propulsion_context, expected_source_save_sha256=canonical_sha256(source))
            assert again.to_dict() == saved and again_receipt == receipt
        ships += len(parsed.ships)
        engines += sum(len(ship.propulsion_state.engines) for ship in parsed.ships)
        governors += sum(len(ship.propulsion_state.governors) for ship in parsed.ships)
        trace.append((name, receipt))
    # 有效 d4 跳闸必须往返，d3 不能通过换标记接纳。
    _, _, session, source, target, _ = migrated_matrix()[0]
    scene = TacticalSceneState.parse(target.scene)
    ship = scene.ships[0]
    tripped = replace(ship.propulsion_state.engines[0], phase='tripped', target_output_percent=0,
                      actual_output_percent=0, ready_at_fixed_step=None, next_transition_step=None,
                      response_started_at_fixed_step=None, response_start_output_percent=None)
    updated = replace(ship, propulsion_state=replace(ship.propulsion_state, engines=(tripped, *ship.propulsion_state.engines[1:])))
    scene = replace(scene, ships=(updated, *scene.ships[1:]))
    saved = FullyGovernedSceneSave(scene.to_dict()).to_dict()
    assert FullyGovernedSceneSave.parse(saved).to_dict() == saved
    refused(lambda: TacticalSceneState.parse(replace(scene, propulsion_governance=GovernedPropulsionExecutionPolicy()).to_dict()))
    return {'scenes': len(trace), 'ships': ships, 'engines': engines, 'governors': governors,
            'replays': 3, 'migration_trace_sha256': canonical_sha256(trace), 'tripped_d4_round_trip': True,
            'old_version_gates': len(trace) * 3 + 1}


@lru_cache(maxsize=None)
def boundary_sample(mode='normal', delivery='delivered'):
    context, runtime, model, motion, source_control, initialized = basic_initialized_fixture()
    source = initialized.state
    requested = translation_control(TRANSLATION_CHANNELS[0], 'dead_slow')
    command = GovernedPropulsionHardFaultCommand()
    if mode in {'emergency', 'interlock'}:
        source_control = translation_control(TRANSLATION_CHANNELS[0], 'half')
        source = with_clock(state_with_outputs(context, {TRANSLATION_CHANNELS[0]: ('running', 50, 50)}, source_control))
        requested = translation_control(TRANSLATION_CHANNELS[1], 'full')
        if mode == 'emergency':
            command = GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')
    if mode == 'trip':
        context, runtime, model = fixture(mutate=lambda instance: replace(instance, operational_state=replace(instance.operational_state, fuel_units=0.0)))
        motion = dynamics.initialize_tactical_motion_state(model)
        source_control = directional_control()
        source = propulsion_state(context, physical_engines(context), source_control, 0)
    if mode == 'reset':
        first = replace(source.engines[0], phase='tripped', target_output_percent=0, actual_output_percent=0,
                        ready_at_fixed_step=None, next_transition_step=None, response_started_at_fixed_step=None,
                        response_start_output_percent=None)
        source = replace(source, engines=(first, *source.engines[1:]))
        command = GovernedPropulsionHardFaultCommand((first.actuator_instance_id,))
    opening = commit_fully_governed_propulsion_opening(context, runtime, source, source_control, requested, command, fixed_step_index=0)
    interval = integrate_fully_governed_propulsion_interval(context, runtime, model, motion, opening, propulsion_delivery_status=delivery)
    _, final_runtime, final_model = fixture(mutate=lambda instance: replace(instance,
        current_hull_integrity_fraction=interval.resulting_motion.hull_integrity_fraction,
        operational_state=replace(instance.operational_state, fuel_units=interval.resulting_motion.fuel_units)))
    profile = PROFILE if mode != 'soft' else replace(PROFILE, id='gtw.safety.d4f.strict',
        structure_engage_ratio=0.0001, structure_release_ratio=0.00005, crew_engage_g=0.0001, crew_release_g=0.00005)
    closing = evaluate_fully_governed_propulsion_closing(context, opening, profile, final_runtime, final_model, interval.resulting_motion,
        fixed_step_index=1, propulsion_delivery_status=delivery, crew_safety_lock_enabled=final_runtime.crew_safety_lock_enabled)
    diagnostic = FullyGovernedStepDiagnostics.from_interval(interval, opening)
    commands = () if command == GovernedPropulsionHardFaultCommand() else ((context.ship_id, command),)
    payload = {key: [] for key in BASE_EVENT_KEYS}
    payload.update(interface=FULL_STEP_INTERFACE_ID, policy=FULL_STEP_POLICY_ID,
        source_scene_sha256='0'*64, resulting_scene_sha256='1'*64, source_fixed_step_index=0, resulting_fixed_step_index=1,
        propulsion_governance=FullyGovernedPropulsionExecutionPolicy().to_dict(),
        hard_fault_commands=SceneHardFaultCommandBatch('0'*64, 0, commands).to_dict(),
        propulsion_opening_records=[{'ship_id': context.ship_id, 'opening': opening.to_dict()}],
        propulsion_closing_records=[{'ship_id': context.ship_id, 'closing': closing.to_dict()}],
        propulsion_boundary_events=serialize_fully_governed_events(((context.ship_id, opening),), ((context.ship_id, closing),)),
        ship_results=[{'ship_id': context.ship_id, 'resulting_runtime_parameters_sha256': final_runtime.source_sha256,
            'diagnostics': diagnostic.to_dict(), 'propulsion_aggregation': interval.aggregation.to_dict(),
            'propulsion_delivery_status': delivery, 'missing_propulsion_channels': []}])
    return payload


def check_step_evidence():
    hashes = []
    for mode, delivery in (('normal', 'delivered'), ('trip', 'delivered'), ('reset', 'delivered'),
                           ('emergency', 'delivered'), ('interlock', 'delivered'),
                           ('interlock', 'suppressed_falling'), ('normal', 'suppressed_uncommanded'), ('soft', 'delivered')):
        sample = boundary_sample(mode, delivery)
        validate_fully_governed_scene_step_contract(sample)
        for _ in range(3):
            reloaded = json.loads(json.dumps(sample))
            validate_fully_governed_scene_step_contract(reloaded)
            assert canonical_sha256(reloaded) == canonical_sha256(sample)
        refused(lambda: validate_governed_scene_step_contract(sample))
        refused(lambda: GovernedActualTacticalStepDiagnostics.parse(sample['ship_results'][0]['diagnostics']))
        hashes.append(canonical_sha256(sample))
    # 多舰序列中所有硬事件先于时间事件，不按事件名重排造成因果倒置。
    from 高天荒野舰艇受控推进完整安全适配器 import FullyGovernedPropulsionOpening, FullyGovernedPropulsionClosing
    trip = boundary_sample('trip')
    normal = boundary_sample('interlock')
    opening_trip = FullyGovernedPropulsionOpening.parse(trip['propulsion_opening_records'][0]['opening'])
    opening_normal = FullyGovernedPropulsionOpening.parse(normal['propulsion_opening_records'][0]['opening'])
    closing_trip = FullyGovernedPropulsionClosing.parse(trip['propulsion_closing_records'][0]['closing'])
    closing_normal = FullyGovernedPropulsionClosing.parse(normal['propulsion_closing_records'][0]['closing'])
    events = serialize_fully_governed_events((('ship.z', opening_trip), ('ship.a', opening_normal)),
                                           (('ship.z', closing_trip), ('ship.a', closing_normal)))
    opening_stages = [r['event_stage'] for r in events if r['boundary_phase'] == 'opening']
    assert 'hard' in opening_stages and 'time' in opening_stages
    assert opening_stages == sorted(opening_stages, key={'hard': 0, 'time': 1}.__getitem__)
    assert len({canonical_sha256(event) for event in events}) == len(events)
    return {'cases': len(hashes), 'round_trips_per_case': 3, 'trace_sha256': canonical_sha256(hashes),
            'multi_ship_causal_order': True}


def check_negative_contracts():
    count = 0
    def reject(parser, value):
        nonlocal count
        refused(lambda: parser(value))
        count += 1
    _, _, session, source, target, receipt = migrated_matrix()[0]
    saved = target.to_dict()
    normal = boundary_sample()
    trip = boundary_sample('trip')
    parsers = [(FullyGovernedPropulsionExecutionPolicy.parse, saved['scene']['propulsion_governance']),
               (FullyGovernedSceneSave.parse, saved), (SceneHardFaultCommandBatch.parse, normal['hard_fault_commands']),
               (FullyGovernedStepDiagnostics.parse, normal['ship_results'][0]['diagnostics']),
               (validate_fully_governed_scene_step_contract, normal)]
    for parser, sample in parsers:
        for key in sample:
            missing = deepcopy(sample)
            del missing[key]
            reject(parser, missing)
        reject(parser, {**sample, 'unexpected': 1})
        reject(parser, [])
    for path, replacement in [(['interface'], 'gaotian.governed-propulsion-scene-save/v1alpha1'),
                              (['boundary_phase'], 'opening'), (['scene_sha256'], 'f'*64),
                              (['scene'], {}), (['scene', 'pending_reset'], [])]:
        bad = mutate(saved, path, replacement)
        if path[0] == 'scene': bad['scene_sha256'] = canonical_sha256(bad['scene'])
        reject(FullyGovernedSceneSave.parse, bad)
    for path, replacement in [(['resulting_fixed_step_index'], 2), (['source_fixed_step_index'], True),
        (['propulsion_governance', 'hard_fault_status'], 'unwired'),
        (['hard_fault_commands', 'source_scene_sha256'], 'a'*64),
        (['propulsion_closing_records'], []), (['propulsion_opening_records'], normal['propulsion_opening_records'] * 2),
        (['ship_results'], normal['ship_results'] * 2),
        (['ship_results', 0, 'propulsion_delivery_status'], 'suppressed_exited'),
        (['ship_results', 0, 'missing_propulsion_channels'], ['unknown']),
        (['ship_results', 0, 'diagnostics', 'source_opening_sha256'], 'b'*64),
        (['ship_results', 0, 'diagnostics', 'hard_fact_projection_sha256'], 'b'*64),
        (['ship_results', 0, 'diagnostics', 'direction_interlock_sha256'], 'b'*64),
        (['ship_results', 0, 'diagnostics', 'source_governors_sha256'], 'b'*64),
        (['ship_results', 0, 'diagnostics', 'crew_g'], float('nan')),
        (['ship_results', 0, 'propulsion_aggregation', 'ship_id'], 'ship.unknown'),
        (['ship_results', 0, 'propulsion_aggregation', 'contributions'], []),
        (['ship_results', 0, 'propulsion_aggregation', 'contributions', 0, 'command_channel'], 'yaw.clockwise'),
        (['ship_results', 0, 'propulsion_aggregation', 'contributions', 0, 'actual_output_percent'], True),
        (['ship_results', 0, 'propulsion_aggregation', 'contributions', 0, 'force_body_n'], [1.0, 0.0])]:
        reject(validate_fully_governed_scene_step_contract, mutate(normal, path, replacement))
    for events in ([], list(reversed(trip['propulsion_boundary_events'])), trip['propulsion_boundary_events'] * 2):
        reject(validate_fully_governed_scene_step_contract, {**trip, 'propulsion_boundary_events': events})
    reject(validate_fully_governed_scene_step_contract, mutate(trip, ['propulsion_boundary_events', 0, 'local_sequence'], False))
    reject(validate_fully_governed_scene_step_contract, mutate(trip, ['propulsion_boundary_events', 0, 'fixed_step_index'], False))
    emergency = boundary_sample('emergency')
    reject(validate_fully_governed_scene_step_contract, {**emergency, 'hard_fault_commands': normal['hard_fault_commands']})
    ship_id = saved['scene']['ships'][0]['ship_id']
    command = GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')
    batch = SceneHardFaultCommandBatch(saved['scene_sha256'], 0, ((ship_id, command),))
    batch.validate_scene(saved['scene'])
    for bad in (replace(batch, fixed_step_index=1), replace(batch, source_scene_sha256='e'*64),
                replace(batch, commands=(('ship.unknown', command),)),
                replace(batch, commands=((ship_id, GovernedPropulsionHardFaultCommand(('unknown_engine',))),))):
        refused(lambda: bad.validate_scene(saved['scene']))
        count += 1
    reject(SceneHardFaultCommandBatch.parse, {**batch.to_dict(), 'commands': batch.to_dict()['commands'] * 2})
    reject(SceneHardFaultCommandBatch.parse, mutate(batch.to_dict(), ['fixed_step_index'], True))
    reject(SceneHardFaultCommandBatch.parse, mutate(batch.to_dict(), ['commands', 0, 'command', 'reset_actuator_instance_ids'], ['unknown_engine']))
    def migrate(value):
        return migrate_d3_scene_save(value, session.propulsion_context, expected_source_save_sha256=canonical_sha256(value))
    refused(lambda: migrate_d3_scene_save(source, session.propulsion_context, expected_source_save_sha256='f'*64))
    count += 1
    for key in ('pending_reset', 'hard_fault_commands', 'direction_transition'):
        bad = deepcopy(source)
        bad['scene'][key] = []
        bad['scene_sha256'] = canonical_sha256(bad['scene'])
        reject(migrate, bad)
    for replacement in ('starting', 'stopping', 'tripped'):
        bad = deepcopy(source)
        bad['scene']['ships'][0]['propulsion_state']['engines'][0]['phase'] = replacement
        bad['scene_sha256'] = canonical_sha256(bad['scene'])
        reject(migrate, bad)
    for key in receipt:
        bad = deepcopy(receipt)
        bad[key] = 'f'*64 if 'sha256' in key else ('wrong' if key != 'fixed_step_index' else True)
        reject(lambda r: validate_d3_scene_save_migration(r, source, saved, session.propulsion_context), bad)
    return {'strict_negative_cases': count}


def check_scene_source_links(*, with_exit=False):
    # 合同夹具复用旧场景的非推进字段；d4 记录完全来自逐舰无场景适配器。
    # 这里没有开放或调用 v7 统一场景执行路径。
    from 高天荒野舰艇持续毁伤 import continuous_damage_automatic_events
    _, old, _, session = governed_case('functional_6.motion_only')
    if with_exit:
        from 高天荒野舰艇统一战术场景 import TacticalSceneExitDirective
        exit_result = d3_step(old, session, session.scene, exit_directives=(
            TacticalSceneExitDirective(session.scene.ships[0].ship_id, 0.0, 'scripted_transfer'),))
        session = replace(session, scene=exit_result.resulting_scene)
    n = session.scene.fixed_step_index
    old_result = d3_step(old, session, session.scene)
    before = replace(session.scene, propulsion_governance=FullyGovernedPropulsionExecutionPolicy())
    after = replace(old_result.resulting_scene, propulsion_governance=FullyGovernedPropulsionExecutionPolicy())
    bindings = {binding.ship_id: binding for binding in session.bindings}
    target_ships = {ship.ship_id: ship for ship in after.ships}
    old_rows = {row.ship_id: row for row in old_result.ship_results}
    openings, closings, results, updated_ships = [], [], [], []
    for ship in before.ships:
        binding = bindings[ship.ship_id]
        context = session.propulsion_context.ship(ship.ship_id).aggregation_context
        if ship.lifecycle_state.physical_status == 'exited':
            target = target_ships[ship.ship_id]
            updated_ships.append(target)
            result = old_rows[ship.ship_id]
            results.append({'ship_id': ship.ship_id, 'resulting_runtime_parameters_sha256': result.resulting_runtime.source_sha256,
                'diagnostics': None, 'propulsion_aggregation': None, 'propulsion_delivery_status': 'suppressed_exited',
                'missing_propulsion_channels': []})
            continue
        def runtime_for(item):
            instance = item.combat_state.instance
            events = tuple(sorted(set(binding.active_automatic_events) | set(continuous_damage_automatic_events(instance))))
            return binding.runtime_cache.resolve(binding.snapshot, binding.sortie, instance, active_automatic_events=events).runtime
        runtime = runtime_for(ship)
        model = dynamics.build_tactical_ship_model(runtime, binding.snapshot)
        target = target_ships[ship.ship_id]
        final_runtime = runtime_for(target)
        final_model = dynamics.build_tactical_ship_model(final_runtime, binding.snapshot)
        opening = commit_fully_governed_propulsion_opening(context, runtime, ship.propulsion_state,
            ship.propulsion_control, ship.propulsion_control, GovernedPropulsionHardFaultCommand(), fixed_step_index=n)
        status = old_rows[ship.ship_id].propulsion_delivery_status
        interval = integrate_fully_governed_propulsion_interval(context, runtime, model, ship.motion_state, opening,
            propulsion_delivery_status=status)
        assert interval.resulting_motion == target.motion_state
        closing = evaluate_fully_governed_propulsion_closing(context, opening, session.propulsion_context.safety_profile,
            final_runtime, final_model, target.motion_state, fixed_step_index=n+1, propulsion_delivery_status=status,
            crew_safety_lock_enabled=final_runtime.crew_safety_lock_enabled)
        updated_ships.append(replace(target, propulsion_state=closing.state, propulsion_control=opening.requested_control))
        openings.append((ship.ship_id, opening))
        closings.append((ship.ship_id, closing))
        results.append({'ship_id': ship.ship_id, 'resulting_runtime_parameters_sha256': final_runtime.source_sha256,
            'diagnostics': FullyGovernedStepDiagnostics.from_interval(interval, opening).to_dict(),
            'propulsion_aggregation': interval.aggregation.to_dict(), 'propulsion_delivery_status': status,
            'missing_propulsion_channels': []})
    after = replace(after, ships=tuple(updated_ships))
    payload = {key: old_result.to_dict()[key] for key in BASE_EVENT_KEYS}
    payload.update(interface=FULL_STEP_INTERFACE_ID, policy=FULL_STEP_POLICY_ID,
        source_scene_sha256=canonical_sha256(before), resulting_scene_sha256=canonical_sha256(after),
        source_fixed_step_index=n, resulting_fixed_step_index=n+1,
        propulsion_governance=FullyGovernedPropulsionExecutionPolicy().to_dict(),
        hard_fault_commands=SceneHardFaultCommandBatch(canonical_sha256(before), n).to_dict(),
        propulsion_opening_records=[{'ship_id': s, 'opening': o.to_dict()} for s, o in openings],
        propulsion_closing_records=[{'ship_id': s, 'closing': c.to_dict()} for s, c in closings],
        propulsion_boundary_events=serialize_fully_governed_events(openings, closings), ship_results=results)
    validate_fully_governed_scene_step_sources(payload, before.to_dict(), after.to_dict())
    if with_exit:
        frozen = before.ships[0]
        command = GovernedPropulsionHardFaultCommand(emergency_cut_cause='operator_requested')
        refused(lambda: SceneHardFaultCommandBatch(canonical_sha256(before), n, ((frozen.ship_id, command),)).validate_scene(before.to_dict()),
                'full_scene.command_exited')
        assert FullyGovernedSceneSave.parse(FullyGovernedSceneSave(after.to_dict()).to_dict()).scene == after.to_dict()
        return {'linked_ships': len(results), 'active_ships': len(openings), 'frozen_ships': 1,
                'payload_sha256': canonical_sha256(payload), 'exited_command_rejected': True}
    stale = mutate(payload, ['source_scene_sha256'], 'f'*64)
    stale['hard_fault_commands']['source_scene_sha256'] = 'f'*64
    refused(lambda: validate_fully_governed_scene_step_sources(stale, before.to_dict(), after.to_dict()), 'full_scene.step_scene_chain')
    bad_source = mutate(before.to_dict(), ['ships', 0, 'motion_state', 'fuel_units'], -1)
    refused(lambda: validate_fully_governed_scene_step_sources(payload, bad_source, after.to_dict()))
    # 使用真实 d3 改令得到合法启动中状态，验证迁移策略自身的拒绝门。
    target_id = session.scene.ships[0].ship_id
    starting = d3_step(old, session, session.scene, propulsion_controls={target_id: translation_control(TRANSLATION_CHANNELS[0], 'full')})
    starting_save = GovernedSceneSave(starting.resulting_scene.to_dict()).to_dict()
    refused(lambda: migrate_d3_scene_save(starting_save, session.propulsion_context,
        expected_source_save_sha256=canonical_sha256(starting_save)), 'full_scene.migration_transition')
    return {'linked_ships': len(results), 'payload_sha256': canonical_sha256(payload),
            'strict_source_negative_cases': 3, 'd4_scene_execution_used': False}


def check_schemas_and_imports():
    schemas = {doc['$id']: doc for doc in (load_json(path) for path in (ROOT/'舰艇数据/模式').glob('*.schema.json'))}
    references = []
    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value:
                reference = value['$ref']
                base, _, fragment = reference.partition('#')
                assert base in schemas, reference
                resolved = schemas[base]
                for part in fragment.strip('/').split('/') if fragment else ():
                    resolved = resolved[part.replace('~1', '/').replace('~0', '~')]
                references.append(reference)
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    for name in SCHEMAS:
        schema = load_json(ROOT/'舰艇数据/模式'/name)
        assert schema['additionalProperties'] is False
        assert set(schema['required']) <= set(schema['properties'])
        walk(schema)
    for names in (('高天荒野舰艇统一战术场景', '高天荒野舰艇完整受控推进场景合同'),
                  ('高天荒野舰艇完整受控推进场景合同', '高天荒野舰艇统一战术场景')):
        run = subprocess.run([sys.executable, '-X', 'utf8', '-c', '; '.join(f'import {name}' for name in names)],
                             cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        assert run.returncode == 0, run.stderr
    run = subprocess.run([sys.executable, '-X', 'utf8', '-c',
        "import sys; import 高天荒野舰艇统一战术场景; "
        "assert '高天荒野舰艇完整受控推进场景合同' not in sys.modules; "
        "assert '高天荒野舰艇受控推进完整安全适配器' not in sys.modules"],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == 0, run.stderr
    return {'schemas': len(SCHEMAS), 'references_checked': len(references), 'cold_import_orders': 2,
            'legacy_scene_does_not_import_d4_algorithms': True}


def collect_evidence():
    return {'scene_save_migration': check_scene_save_and_migration(), 'step_evidence': check_step_evidence(),
            'negative_contracts': check_negative_contracts(), 'scene_source_links': check_scene_source_links(),
            'exited_scene_source_links': check_scene_source_links(with_exit=True),
            'schemas_and_imports': check_schemas_and_imports()}


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
