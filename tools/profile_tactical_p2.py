"""P2 attribution only: observe existing decisions; never change battle policy.

Diagnostic counters are separate from uninstrumented timings. Both replay the
original 7b inputs; diagnostics additionally match every P1c expanded snapshot.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from dataclasses import fields
from hashlib import sha256
import json
from math import sin, cos
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from unittest.mock import patch

from tools.tactical_7b_fixture import ROOT, restore, commands, write
from tools.profile_tactical_7b import counts, digest_state, fingerprint
from tools.profile_tactical_fire_control import stats
from tools.tactical_7c_oracle import snapshot, digests
from tools.profile_tactical_7c4 import request
from backend.high_wilderness_sidecar import tactical_defense_prediction as dp
from backend.high_wilderness_sidecar import tactical_point_defense as pd
from backend.high_wilderness_sidecar import projectile_observation as observed
from backend.high_wilderness_sidecar import tactical_missile_defense as md
from backend.high_wilderness_sidecar import aviation_recovery as recovery
from backend.high_wilderness_sidecar import aviation_flight as af, aviation_combat as ac
from backend.high_wilderness_sidecar import missile_flight as mf, tactical_ballistics as ballistics
from backend.high_wilderness_sidecar import tactical_drag_prediction as drag, server
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

WINDOWS = ((1, 60), (301, 360), (601, 660), (841, 900))


class Diagnostics:
    def __init__(self):
        self.totals = defaultdict(lambda: defaultdict(Counter))
        self.step = 0
        self.receiver_ids = set()

    def add(self, group, key, n=1):
        self.totals['all'][group][key] += n
        for a, z in WINDOWS:
            if a <= self.step <= z:
                self.totals[f'{a}-{z}'][group][key] += n

    @contextmanager
    def install(self, b):
        original = dp.Plan.collisions
        anchor = dp.Plan.path_anchor
        motion = dp.same_motion
        receive = recovery.can_receive
        environment = b.ew.environment
        replace = md._ReservationInputs.replace

        def collisions(plan, observer, projectile, stamp=None):
            p = observed.sample(projectile)
            stamp = plan.world.fixed_step if stamp is None else stamp
            side = plan.battle._sides[observer]
            key = side, stamp, p
            previous = plan.previous.get(key)
            before = dict(plan.metrics)
            result = original(plan, observer, projectile, stamp)
            # Read the branch actually taken instead of reproducing its decision.
            branch = next((name for name in ('shared', 'rolled', 'computed')
                           if plan.metrics[name] > before[name]), 'expired')
            caller = sys._getframe(2).f_code.co_name
            self.add('forecast', branch)
            self.add('forecast_caller', caller + ':' + branch)
            if branch == 'computed':
                if previous is None:
                    old = [(s, q) for (team, s, q) in plan.previous if team == side and q.id == p.id]
                    reason = 'changed_key' if old else 'identity_absent_in_previous_step'
                    self.add('recompute', reason)
                    if old:
                        old_stamp, old_p = max(old, key=lambda row: row[0])
                        if old_stamp != stamp:self.add('changed_key_fields', 'stamp')
                        for field in fields(p):
                            if getattr(old_p, field.name) != getattr(p, field.name):
                                self.add('changed_key_fields', field.name)
                else:
                    self.add('recompute', 'exact_key_present')
                    if plan.metrics['invalidated_motion'] > before['invalidated_motion']:
                        self.add('exact_key_recompute_flags', 'ship_motion')
                    if plan.world.fixed_step >= previous.due:
                        self.add('exact_key_recompute_flags', 'due')
                    if any(t <= plan.world.fixed_step / 60 for _, t in previous.collisions):
                        self.add('exact_key_recompute_flags', 'passed_contact')
            return result

        def path_anchor(plan, side, p, stamp):
            old = plan.anchors.get((side, p.id))
            before = plan.metrics['path_reused']
            result = anchor(plan, side, p, stamp)
            if plan.metrics['path_reused'] > before:reason = 'reused'
            elif old is None:reason = 'no_previous_anchor'
            elif p.flight_profile is None or not p.flight_profile.drag:reason = 'not_drag_path'
            elif stamp < old[1]:reason = 'stamp_before_anchor'
            elif p.flight_profile != old[0].flight_profile:reason = 'flight_profile_changed'
            elif (p.expires, p.height_layer, p.altitude_m, p.vertical_velocity_mps) != (
                    old[0].expires, old[0].height_layer, old[0].altitude_m, old[0].vertical_velocity_mps):
                reason = 'expiry_or_vertical_changed'
            else:reason = 'measured_kinematics_changed'
            self.add('path_anchor', reason)
            return result

        def same_motion(old, current, seconds):
            result = motion(old, current, seconds)
            if not result:
                reasons = set()
                if len(old) != len(current):reasons.add('fleet_membership')
                for a, z in zip(old, current):
                    if a[0] != z[0]:reasons.add('fleet_membership');continue
                    for index, label in ((2, 'velocity'), (4, 'yaw_rate'), (5, 'layer'), (6, 'module_alive')):
                        if a[index] != z[index]:reasons.add(label)
                    if any(abs(x + v * seconds - y) >= 1e-7 for x, v, y in zip(a[1], a[2], z[1])):
                        reasons.add('position_not_linear')
                    if abs(sin(a[3] + a[4] * seconds - z[3])) >= 1e-10 or cos(a[3] + a[4] * seconds - z[3]) <= 0:
                        reasons.add('heading_not_linear')
                for reason in reasons:self.add('motion_flags', reason)
            return result

        def can_receive(source, receiver, aircraft_id):
            result = receive(source, receiver, aircraft_id)
            self.add('reception', 'calls')
            self.add('reception', 'accepted' if result else 'rejected')
            identity = receiver._value['instance_id']
            if identity not in self.receiver_ids:
                self.receiver_ids.add(identity)
                self.add('reception', 'distinct_receivers_per_step')
            # Repeated ship identity is only an upper bound; state may mutate.
            return result

        def build_environment(world, available, frame, effects, projectiles=(), **kw):
            result = environment(world, available, frame, effects, projectiles, **kw)
            self.add('environment', 'builds')
            eligible = sum(s.command.lifecycle.physical_status == 'operational' and s.motion.hull_integrity_fraction > 0
                and any(available[n][mid] is None for mid in b.observation.links[n]) for n, s in enumerate(world.ships))
            self.add('environment', 'track_loop_visits', eligible * len(frame.tracks))
            self.add('environment', 'links_built', len(result.links))
            self.add('environment', 'contacts_built', len(result.contacts))
            return result

        def reservations(inputs, index, projectile):
            old = inputs.targets.get(index)
            cached = set(inputs.covered)
            result = replace(inputs, index, projectile)
            new = inputs.targets.get(index)
            self.add('reservations', 'replace_calls')
            self.add('reservations', 'same_target' if old is not None and old == new else 'changed_or_unassigned')
            self.add('reservations', 'cached_targets_invalidated', len(cached - set(inputs.covered)))
            return result

        with ExitStack() as stack:
            for owner, name, fn in ((dp.Plan, 'collisions', collisions), (dp.Plan, 'path_anchor', path_anchor),
                    (dp, 'same_motion', same_motion), (recovery, 'can_receive', can_receive),
                    (b.ew, 'environment', build_environment), (md._ReservationInputs, 'replace', reservations)):
                stack.enter_context(patch.object(owner, name, fn))
            for owner in (af, ac):
                fn = owner.deepcopy
                label = owner.__name__.rsplit('.', 1)[-1]
                def copy(value, _fn=fn, _label=label, **kw):
                    self.add('deepcopy_top_level', _label)
                    return _fn(value, **kw)
                stack.enter_context(patch.object(owner, 'deepcopy', copy))
            yield


def run(mode, source, out):
    if out.exists():raise ValueError('Choose a fresh output directory')
    out.mkdir(parents=True)
    frozen = json.loads((source / 'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source / 'fixed-tape.json').read_text(encoding='utf-8'))
    expanded_path = ROOT / 'artifacts/tactical-p1c-20261014' / ('verify-' + frozen['case']) / 'expanded-tape.json'
    expanded = json.loads(expanded_path.read_text(encoding='utf-8'))
    assert len(expected) == len(expanded) == 900
    provenance = fingerprint()
    provenance['tools'] = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__), ROOT / 'tools/tactical_7c_oracle.py', ROOT / 'tools/profile_tactical_fire_control.py',
        ROOT / 'tools/profile_tactical_7c4.py')}
    provenance['expanded_reference_sha256'] = sha256(expanded_path.read_bytes()).hexdigest()
    write(out / 'source.json', provenance)
    for cached in (pd.trajectory, mf.segment, ballistics.reference_range, drag.bands):cached.cache_clear()
    diagnostic = Diagnostics()
    durations = []; cpus = []; publication = []; reads = []; service_cpu = 0.
    loads = []; caches = []; tape = []
    with TemporaryDirectory(prefix='hw-p2-attribution-') as directory:
        service = RealtimeViewService('backend.p2.attribution', settlement_dir=directory)
        b, geometry, _ = restore(frozen, service, 'fixed')
        service._attach(b, geometry)
        packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=None))
        cursor = packet['view']['projectile_stream']['sequence']
        with ExitStack() as stack:
            if mode == 'diagnostic':stack.enter_context(diagnostic.install(b))
            for n in range(900):
                step = n + 1
                diagnostic.step = step; diagnostic.receiver_ids.clear()
                commands(b, frozen)
                clock = perf_counter(); cpu = process_time()
                service.scheduler._stepper()
                cpus.append(process_time() - cpu); durations.append(perf_counter() - clock)
                service_cpu += cpus[-1]
                service.scheduler._committed = b.session.world
                state = digest_state(b)
                assert state == expected[n], f'7b authority mismatch at {step}'
                tape.append(state)
                if mode == 'diagnostic':
                    assert digests(snapshot(b)) == expanded[n], f'Expanded authority mismatch at {step}'
                    for key, value in b.point_defense.prediction_metrics.items():
                        if key not in ('max_age_steps', 'entries'):diagnostic.add('committed_prediction_metrics', key, value)
                if n % 4 == 0:
                    clock = perf_counter(); cpu = process_time(); service.publish()
                    service_cpu += process_time() - cpu; publication.append(perf_counter() - clock)
                    clock = perf_counter(); cpu = process_time()
                    packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=cursor))
                    server.realtime_response(request(), packet, wire=True)
                    service_cpu += process_time() - cpu; reads.append(perf_counter() - clock)
                    cursor = packet['view']['projectile_stream']['sequence']
                if step % 150 == 0 or any(step in (a - 1, z) for a, z in WINDOWS):
                    loads.append(dict(step=step, load=counts(b)))
                    caches.append(dict(step=step, trajectory=pd.trajectory.cache_info()._asdict(),
                                       segment=mf.segment.cache_info()._asdict()))
                    print(json.dumps(dict(case=frozen['case'], mode=mode, step=step)), flush=True)
                assert b.ending is None
        final = digests(snapshot(b))
        assert final == expanded[-1]
        report = dict(case=frozen['case'], mode=mode, steps=900, fixture_sha256=canonical_sha256(frozen),
            authority_tape_sha256=canonical_sha256(tape), expanded_final=final, final_load=counts(b),
            loads=loads, caches=caches, physics_caches_cleared=True)
        if mode == 'diagnostic':
            for bucket, groups in diagnostic.totals.items():
                metrics = groups['committed_prediction_metrics']; decisions = groups['forecast']
                assert metrics['requests'] == sum(decisions[k] for k in ('shared', 'rolled', 'computed')), bucket
                for key in ('shared', 'rolled', 'computed'):assert metrics[key] == decisions[key], (bucket, key)
                assert sum(groups['path_anchor'].values()) == metrics['computed'], bucket
                assert sum(groups['recompute'].values()) == metrics['computed'], bucket
            report.update(counters=diagnostic.totals, expanded_matched_steps=900,
                scope='Read-only diagnostic wrappers; no performance timings. Flags are non-exclusive; changed key compares latest same-identity prior sample. Receiver repeats are not proven safe cache hits.')
        else:
            report.update(step=stats(durations), step_cpu_s=sum(cpus), publish=stats(publication), read_encode=stats(reads),
                service_cpu_s=service_cpu, service_wall_s=sum(durations) + sum(publication) + sum(reads),
                windows=[dict(first_step=a, last_step=z, step=stats(durations[a-1:z]), cpu_s=sum(cpus[a-1:z])) for a, z in WINDOWS],
                scope='Uninstrumented current-version offline baseline; read includes response encoding. No profiler or diagnostic patches. Not paced service, UI acceptance, or a before/after optimization comparison.')
            write(out / 'step-samples.json', [dict(step=n+1, wall_s=v, cpu_s=cpus[n]) for n, v in enumerate(durations)])
        write(out / 'report.json', report)
        print(json.dumps({k: report[k] for k in ('case', 'mode', 'steps', 'final_load')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('diagnostic', 'timing'))
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    args = parser.parse_args()
    run(args.mode, args.source, args.out)
