"""E2.3 internal committed-boundary checkpoint. No product save or legacy import.

Only authoritative state crosses JSON. Compiled resources are supplied by the
caller and bound in full; caches, totals, schedules and prior step receipts are
rebuilt or omitted. A load creates a new epoch, never replaces a live session.
"""
from dataclasses import asdict, replace
import json
from math import isfinite
from threading import get_ident

from 高天荒野舰艇数据契约 import ContractError, RuntimePowerPolicyInput, canonical_sha256
from 高天荒野舰艇推进状态合同 import EngineRuntimeState, ENGINE_RUNTIME_STATE_INTERFACE_ID, PropulsionGovernorState
from 高天荒野舰艇统一战术场景 import TacticalShipLifecycleState
from . import simplified_flight as sf
from .tactical_devices import DeviceState, ModuleState, DeviceOperation
from .tactical_resources_runtime import ResourceState, ResourceOperation, REASONS as RESOURCE_REASONS
from .tactical_command_runtime import CommandState
from . import tactical_layers as height
from . import tactical_descent as descent

INTERFACE = 'gaotian.tactical-checkpoint/e2.3-v3alpha1'
POLICY = 'fixed-six-directions/no-balance/no-propulsion-fuel/rescuable-descent-v1'


def need(ok, detail):
    if not ok:
        raise ContractError('tactical_checkpoint.invalid', '$', detail)


def obj(value, keys):
    need(type(value) is dict and set(value) == set(keys.split()), 'Missing or unknown checkpoint fields')
    return value


def array(value, length=None):
    need(type(value) is list and (length is None or len(value) == length), 'Invalid checkpoint array')
    return value


def integer(value, maximum=None):
    need(type(value) is int and value >= 0 and (maximum is None or value <= maximum), 'Invalid counter or boundary')
    return value


def number(value):
    need(type(value) in (int, float) and isfinite(value), 'Expected finite number')
    return value


def boolean(value):
    need(type(value) is bool, 'Expected boolean')
    return value


def past(value, step):
    return None if value is None else integer(value, step)


def _pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def _signature(value):
    if value is None:
        return None
    return [v.to_dict() if isinstance(v, RuntimePowerPolicyInput) else v for v in value]


def _binding(session):
    return canonical_sha256(dict(policy=POLICY, seeds=[asdict(s) for s in session._seeds],
        safety=asdict(session._profile), direct_ship_id=session._direct,
        allow_test_device_rebuild=session._allow_test_device_rebuild))


def _ship(ship):
    p, d, r, c = ship.propulsion, ship.devices, ship.resources, ship.command
    return dict(ship_id=ship.ship_id, motion=ship.motion.to_dict(), control=ship.control.to_dict(),
        height_navigation=asdict(ship.height_navigation),
        descent=None if ship.descent is None else asdict(ship.descent),
        wreck=None if ship.wreck is None else asdict(ship.wreck),
        authority_allowed=ship.authority_allowed, authority_version=ship.authority_version,
        propulsion=dict(engines=[dict(engine=s.engine.to_dict(), blocked=list(s.blocked), versions=list(s.versions)) for s in p.engines],
            targets=list(p.targets), governors=[asdict(g) for g in p.governors], phase_revision=p.phase_revision),
        devices=dict(revision=d.revision, modules=[dict(durability_points=m.durability_points, sequence=m.sequence,
            last_operation=_signature(m.last_operation)) for m in d.modules]),
        resources=dict(modes=list(r.modes), crew=[list(v) for v in r.crew], policy=r.policy.to_dict(),
            sequence=r.sequence, last_operation=_signature(r.last_operation), input_revision=r.input_revision,
            revision=r.revision, latched=list(r.latched)),
        command=dict(lifecycle=c.lifecycle.to_dict(), fleet_phase=c.fleet_phase, loss_reason=c.loss_reason,
            loss_step=c.loss_step, revision=c.revision))


def dumps(session):
    """Export only outside step callbacks on the session's owner thread."""
    need(get_ident() == session._owner and not session._executing, 'Checkpoint requires idle owner boundary')
    need(all(s.devices is not None and s.resources is not None and s.command is not None for s in session.world.ships),
        'Checkpoint requires device/resource/command domains for every ship')
    return json.dumps(dict(interface=INTERFACE, policy=POLICY, resources_sha256=_binding(session),
        fixed_step=session.world.fixed_step, ships=[_ship(s) for s in session.world.ships]),
        ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _motion(value, seed, step):
    obj(value, 'position_world_m velocity_world_mps heading_rad yaw_rate_radps height_layer layer_transition hull_integrity_fraction fuel_units fixed_step_index')
    need(integer(value['fixed_step_index']) == step, 'Motion boundary mismatch')
    need(type(value['height_layer']) is str and value['height_layer'] in height.LAYERS, 'Unsupported height layer')
    transition = None
    if value['layer_transition'] is not None:
        v = obj(value['layer_transition'], 'source_layer target_layer elapsed_s duration_s progress')
        need(v['source_layer'] == value['height_layer'] and type(v['target_layer']) is str and v['target_layer'] in height.LAYERS,
            'Invalid layer transition endpoints')
        need(abs(height.LAYERS.index(v['source_layer']) - height.LAYERS.index(v['target_layer'])) == 1, 'Non-adjacent layer transition')
        elapsed, duration = number(v['elapsed_s']), number(v['duration_s'])
        need(duration > 0 and 0 <= elapsed < duration and abs(number(v['progress']) - elapsed / duration) < 1e-10, 'Invalid layer progress')
        transition = sf.dynamics.LayerTransitionState(v['source_layer'], v['target_layer'], elapsed, duration)
    need(number(value['fuel_units']) == seed.motion.fuel_units, 'Tactical propulsion fuel must remain unchanged')
    def vector(v):
        return sf.dynamics.Vec2(*(number(n) for n in array(v, 2)))
    motion = sf.dynamics.TacticalMotionState(vector(value['position_world_m']), vector(value['velocity_world_mps']),
        number(value['heading_rad']), number(value['yaw_rate_radps']), value['height_layer'], transition,
        number(value['hull_integrity_fraction']), value['fuel_units'], step)
    sf.validate_motion(motion)
    return motion


def _propulsion(value, kernel, step):
    obj(value, 'engines targets governors phase_revision')
    slots, schedule = [], []
    available, outputs = [0]*6, [0]*6
    for i, (raw, design) in enumerate(zip(array(value['engines'], len(kernel.resources.engines)), kernel.resources.engines)):
        obj(raw, 'engine blocked versions')
        e = EngineRuntimeState.parse(raw['engine'], '$.engine')
        need(e.interface_id == ENGINE_RUNTIME_STATE_INTERFACE_ID and (e.actuator_instance_id, e.actuator_category) ==
            (design.instance_id, design.category), 'Engine resource or interface mismatch')
        blocked = tuple(boolean(v) for v in array(raw['blocked'], len(sf.REASONS)))
        versions = tuple(integer(v) for v in array(raw['versions'], len(sf.REASONS)))
        need((e.phase == 'tripped') == blocked[5], 'Trip latch/engine mismatch')
        need(not any(blocked) or e.phase in ('off', 'tripped'), 'Blocked engine still active')
        if e.phase != 'starting':
            past(e.ready_at_fixed_step, step)
        else:
            need(step < e.ready_at_fixed_step <= step + design.startup_steps, 'Invalid startup deadline')
        past(e.response_started_at_fixed_step, step)
        if e.response_started_at_fixed_step is not None:
            need(e.ready_at_fixed_step <= e.response_started_at_fixed_step, 'Response before ready')
            # Reconstruct the exact current response from its saved anchor.
            anchor = replace(e, actual_output_percent=e.response_start_output_percent,
                target_output_percent=e.response_start_output_percent, next_transition_step=None,
                response_started_at_fixed_step=None, response_start_output_percent=None,
                phase='ready' if e.response_start_output_percent == 0 else 'running')
            expected, _ = sf._apply_command(anchor, kernel.timings[i], e.response_started_at_fixed_step,
                e.commanded_notch, e.target_output_percent)
            while expected.next_transition_step is not None and expected.next_transition_step <= step:
                expected, _ = sf._commit_due_transition(expected, kernel.timings[i], expected.next_transition_step)
            need(expected == e, 'Response output/deadline does not match compiled timing')
        if e.next_transition_step is not None:
            need(e.next_transition_step > step, 'Unprocessed engine deadline')
            schedule.append((e.next_transition_step, i))
        slots.append(sf.EngineSlot(e, blocked, versions))
        for d, units in enumerate(design.contribution_units):
            available[d] += units if not any(blocked) else 0
            outputs[d] += units * e.actual_output_percent
    targets = tuple(integer(v, 100) for v in array(value['targets'], 6))
    need(all(v in sf.THRUST_OUTPUT_STAGES_PERCENT for v in targets), 'Invalid direction targets')
    for slot, direction in zip(slots, kernel.channels):
        if not any(slot.blocked):
            need(slot.engine.target_output_percent == (0 if direction is None else targets[direction]),
                'Engine target disagrees with direction target')
    governors = []
    for raw in array(value['governors'], 6):
        obj(raw, 'ceiling reasons limited_since release_since')
        reasons = tuple(array(raw['reasons']))
        need(all(type(v) is str for v in reasons), 'Invalid safety reasons')
        g = sf.Governor(integer(raw['ceiling'], 100), reasons, past(raw['limited_since'], step), past(raw['release_since'], step))
        PropulsionGovernorState('forward', 'stop', g.ceiling, g.reasons, g.limited_since, g.release_since, step, 0)
        governors.append(g)
    need(all(v <= g.ceiling for v, g in zip(targets, governors)), 'Target exceeds safety ceiling')
    return sf.PropulsionState(tuple(slots), tuple(available), tuple(outputs), tuple(sorted(schedule)),
        targets, tuple(governors), integer(value['phase_revision']))


def _devices(value, dk, step, allow_rebuild, lift_tanks=()):
    obj(value, 'modules revision')
    modules = []
    for raw, design, initial in zip(array(value['modules'], len(dk.seed.modules)), dk.seed.modules, dk.seed.initial_durability_points):
        obj(raw, 'durability_points sequence last_operation')
        hp, seq = number(raw['durability_points']), integer(raw['sequence'])
        need(0 <= hp <= design.maximum_durability_points, 'Durability outside design limits')
        signature = raw['last_operation']
        if seq == 0:
            need(signature is None and hp == initial, 'Unrecorded durability change')
        else:
            signature = tuple(array(signature, 5))
            need(integer(signature[0]) == seq, 'Device receipt sequence mismatch')
            n = integer(signature[3], step)
            need(signature[4] in ('opening', 'closing') and (n < step or signature[4] == 'closing'), 'Uncommitted device receipt')
            op = DeviceOperation('', '', design.instance_id,
                seq, signature[1], signature[2], n, signature[4])
            dk.validate_operation(op, epoch='', ship_id='', step=n-(op.phase=='closing'), allow_rebuild=allow_rebuild,
                allow_repair=True, allow_lift_repair=design.instance_id in lift_tanks)
            if op.kind == 'test_rebuild':
                need(hp == design.maximum_durability_points, 'Rebuild receipt disagrees with durability')
        modules.append(ModuleState(hp, seq, signature))
    revision = integer(value['revision'])
    total = sum(m.sequence for m in modules)
    need((revision == 0) == (total == 0) and revision <= total, 'Invalid device revision')
    return DeviceState(tuple(modules), revision)


def _resources(value, rk, devices, propulsion, step):
    obj(value, 'modes crew policy sequence last_operation input_revision revision latched')
    modes = tuple(array(value['modes'], len(rk.modules)))
    crew = tuple(tuple(array(v, 2)) for v in array(value['crew']))
    rk._validate_modes(modes); rk._validate_crew(crew)
    policy = RuntimePowerPolicyInput.parse(value['policy'], '$.resources.policy')
    seq = integer(value['sequence'])
    signature = value['last_operation']
    if seq == 0:
        need(signature is None and (modes, crew, policy) == (rk.seed.modes, rk.seed.crew, rk.seed.policy), 'Unrecorded resource change')
    else:
        signature = list(array(signature, 6))
        need(integer(signature[0]) == seq, 'Resource receipt sequence mismatch')
        n = integer(signature[4], step)
        need(signature[5] in ('opening', 'closing') and (n < step or signature[5] == 'closing'), 'Uncommitted resource receipt')
        if signature[1] == 'policy':
            signature[3] = RuntimePowerPolicyInput.parse(signature[3], '$.last_operation.policy')
        # Validate the last request with the existing domain contract; never replay
        # it against the loaded state or increment its stored sequence.
        probe = replace(rk.initial(), sequence=seq-1, modes=modes, crew=crew, policy=policy)
        op = ResourceOperation('', '', seq, *signature[1:])
        applied, _, _ = rk.operations(probe, (op,), epoch='', ship_id='', step=n, phase=op.phase, can_reset=True)
        need((applied.modes, applied.crew, applied.policy) == (modes, crew, policy), 'Resource receipt contradicts current inputs')
        signature = tuple(signature)
    need(integer(value['input_revision']) == seq, 'Resource input revision mismatch')
    revision = integer(value['revision'])
    need(revision > 0, 'Missing resource evaluation')
    latched = tuple(boolean(v) for v in array(value['latched'], len(rk.engines)))
    state = ResourceState(modes, crew, policy, seq, signature, seq, revision=revision, latched=latched)
    rebuilt, _ = rk.resolve(state, devices, propulsion)
    need(rebuilt.latched == latched, 'Unsettled resource trip state')
    return replace(rebuilt, revision=revision)


def _command(value, ck, devices, resources, motion, seed, step, wreck):
    obj(value, 'lifecycle fleet_phase loss_reason loss_step revision')
    lifecycle = TacticalShipLifecycleState.parse(value['lifecycle'], '$.lifecycle')
    integer(lifecycle.last_transition_step_index, step)
    need(set(lifecycle.failure_causes) <= {'cic_destroyed', 'hull_structure_collapsed', 'insufficient_lift',
        'cic_control_unavailable', 'remote_control_lost'}, 'Unsupported lifecycle failure source')
    if lifecycle.physical_status == 'falling':
        need(bool(set(lifecycle.failure_causes) & {'cic_destroyed', 'hull_structure_collapsed', 'insufficient_lift'}), 'Falling without terminal cause')
    if lifecycle.physical_status == 'exited':
        need(lifecycle.exit_reason in ('scripted_transfer', 'fell_below_scene') and
            lifecycle.exit_tactical_time_s == lifecycle.last_transition_step_index/60, 'Unsupported exit or time mismatch')
        if lifecycle.exit_reason == 'fell_below_scene':
            need(bool(set(lifecycle.failure_causes) & {'cic_destroyed', 'hull_structure_collapsed', 'insufficient_lift'}), 'Fall exit without cause')
    phase, loss, loss_step = value['fleet_phase'], value['loss_reason'], past(value['loss_step'], step)
    if ck.direct:
        need(phase in ('active', 'command_defeat_withdrawal'), 'Unsupported fleet phase')
        if phase == 'active':
            need(loss is None and loss_step is None, 'Active flagship has loss record')
        else:
            need(loss in ('direct_ship_falling', 'direct_ship_exited', 'direct_control_link_lost') and loss_step is not None,
                'Missing command loss record')
            need(loss_step <= lifecycle.last_transition_step_index, 'Loss after latest lifecycle transition')
    else:
        need(phase == 'unassigned' and loss is None and loss_step is None, 'Observer cannot acquire flagship authority')
    revision = integer(value['revision'])
    need(revision > 0, 'Missing command evaluation')
    state = CommandState(lifecycle, phase, loss, loss_step, revision)
    rebuilt = ck.resolve(state, devices, resources, motion, mass=height.dry_mass(seed.contributions), step=step,
        lift_crashed=wreck is not None and wreck.reason=='insufficient_lift')
    need((rebuilt.lifecycle, rebuilt.fleet_phase, rebuilt.loss_reason, rebuilt.loss_step) ==
        (lifecycle, phase, loss, loss_step), 'Command state contradicts current domain state')
    return replace(rebuilt, revision=revision)


def loads(payload, seeds, safety_profile, *, direct_ship_id, allow_test_device_rebuild=False):
    """Strict rebuild into a new owner/session/epoch; last_result starts as None.

    Receipts belong to the previous delivery, not future simulation state. The
    next step produces exactly the same receipt as uninterrupted continuation.
    """
    try:
        need(type(payload) is str and len(payload) <= 16*1024*1024, 'Invalid or oversized checkpoint JSON')
        value = json.loads(payload, object_pairs_hook=_pairs, parse_constant=lambda _: need(False, 'Non-finite JSON'))
        obj(value, 'interface policy resources_sha256 fixed_step ships')
        need(value['interface'] == INTERFACE and value['policy'] == POLICY, 'Unsupported checkpoint policy/version')
        session = sf.SimplifiedFlightSession(seeds, safety_profile, direct_ship_id=direct_ship_id,
            allow_test_device_rebuild=allow_test_device_rebuild)
        need(all(k is not None for k in session._command_kernels), 'Checkpoint requires all three domains')
        need(value['resources_sha256'] == _binding(session), 'Compiled resource binding mismatch')
        step = integer(value['fixed_step'])
        raws = array(value['ships'], len(session._seeds))
        if step == 0:
            # Orders can be accepted before the first tick. All other initial
            # facts must still match the entry, including baseline lift.
            initial = json.loads(json.dumps(value))
            for raw in initial['ships']:
                raw['height_navigation']['target_layer'] = None
                raw['motion']['layer_transition'] = None
            need(initial == json.loads(dumps(session)), 'Fresh checkpoint must match prepared scene')
        ships = []
        for raw, seed, kernel, dk, rk, ck in zip(raws, session._seeds, session._kernels,
                session._device_kernels, session._resource_kernels, session._command_kernels):
            obj(raw, 'ship_id motion control authority_allowed authority_version propulsion devices resources command height_navigation descent wreck')
            need(raw['ship_id'] == seed.contributions.ship_id, 'Ship ordering/identity mismatch')
            motion = _motion(raw['motion'], seed, step)
            propulsion = _propulsion(raw['propulsion'], kernel, step)
            devices = _devices(raw['devices'], dk, step, allow_test_device_rebuild, dict(ck.lift))
            resources = _resources(raw['resources'], rk, devices, propulsion, step)
            wreck = None
            if raw['wreck'] is not None:
                w = obj(raw['wreck'], 'fixed_step height_layer position_m reason')
                wreck = descent.Wreck(integer(w['fixed_step'],step),w['height_layer'],tuple(number(x) for x in array(w['position_m'],2)),w['reason'])
                need(wreck.reason in ('insufficient_lift','cic_destroyed','hull_structure_collapsed') and
                    wreck.height_layer==motion.height_layer and wreck.position_m==tuple(motion.position_world_m.to_list()) and
                    motion.velocity_world_mps==sf.dynamics.Vec2(0.,0.) and motion.yaw_rate_radps==0., 'Invalid wreck position or cause')
                need(wreck.reason!='insufficient_lift' or wreck.height_layer=='rain', 'Lift crash before rain deadline')
            command = _command(raw['command'], ck, devices, resources, motion, seed, step, wreck)
            allowed, version = boolean(raw['authority_allowed']), integer(raw['authority_version'], command.revision)
            need(allowed == command.allowed and (version == command.revision or version == 0 and command.revision == 1),
                'Authority disagrees with command domain')
            control = sf.DirectionalPropulsionControlInput.parse(raw['control'])
            need(allowed or control == sf.directional_control(), 'Lost authority retains player command')
            need(allowed or not any(propulsion.targets), 'Lost authority retains propulsion targets')
            for i, slot in enumerate(propulsion.engines):
                need(slot.blocked[:2] == dk.reasons(devices, i), 'Durability and engine blockers disagree')
                for reason, active in zip(RESOURCE_REASONS, resources.facts[i]):
                    need(slot.blocked[sf.REASONS.index(reason)] == active, 'Resource and engine blockers disagree')
                cut = command.suppress or ck.direct and not command.allowed
                need(slot.blocked[8] == cut, 'Command and engine blockers disagree')
                need(not slot.blocked[9] or motion.hull_integrity_fraction <= 0, 'Spurious hull collapse blocker')
                for reason, v in zip(sf.REASONS, slot.versions):
                    limit = devices.revision if reason in ('actuator_destroyed', 'host_destroyed') else resources.revision if reason in RESOURCE_REASONS else command.revision if reason == 'command_unavailable' else None
                    need(limit is None or v <= limit, 'Availability version exceeds its producer')
            nav = obj(raw['height_navigation'], 'dry_mass_kg initial_lift_force_n base_duration_s target_layer')
            baseline = next(s.height_navigation for s in session.world.ships if s.ship_id == raw['ship_id'])
            for key in ('dry_mass_kg', 'initial_lift_force_n', 'base_duration_s'):
                need(nav[key] is None if getattr(baseline,key) is None else number(nav[key]) == getattr(baseline,key), 'Altered entry height baseline')
            target = nav['target_layer']
            need(target is None or type(target) is str and target in height.LAYERS and target != motion.height_layer, 'Invalid height order')
            ship = sf.FlightShip(raw['ship_id'], motion, propulsion, control, allowed, version, devices, resources, command,
                replace(baseline, target_layer=target), wreck=wreck)
            if raw['descent'] is not None:
                d = obj(raw['descent'], 'source_layer progress duration_s paused')
                state = descent.Descent(d['source_layer'],number(d['progress']),number(d['duration_s']),boolean(d['paused']))
                need(wreck is None and target is None and command.lifecycle.physical_status=='operational' and
                    state.source_layer==motion.height_layer and 0<=state.progress<1 and state.duration_s>0, 'Invalid descent state')
                seconds = descent.duration(baseline.dry_mass_kg,command.lift_force_n)
                need(command.lift_force_n<=baseline.dry_mass_kg*height.STANDARD_GRAVITY_MPS2 and
                    (state.paused and seconds is None or not state.paused and state.duration_s==seconds), 'Descent contradicts lift')
                ship = replace(ship, descent=state)
            if step>0 and command.lifecycle.physical_status=='operational':
                need(descent.reconcile(ship)==ship, 'Missing or unsettled descent')
            need(wreck is None or wreck.reason in command.lifecycle.failure_causes, 'Wreck lacks a terminal cause')
            need((target is None) == (motion.layer_transition is None), 'Height order/segment disagreement')
            if target is not None:
                segment = motion.layer_transition
                need(height.unavailable_reason(ship) is None and
                    (height.LAYERS.index(segment.target_layer) - height.LAYERS.index(motion.height_layer)) *
                    (height.LAYERS.index(target) - height.LAYERS.index(motion.height_layer)) > 0, 'Invalid active height direction')
                need(segment.duration_s == height.duration(ship.height_navigation,command.lift_force_n), 'Unsettled height debuff')
            ships.append(ship)
        session._world = sf.FlightWorld(session.world.epoch, step, tuple(ships))
        return session
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError) as error:
        raise ContractError('tactical_checkpoint.invalid', '$', str(error)) from error
