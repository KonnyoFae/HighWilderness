"""7c.0/1a exact authority comparison and uninstrumented paired timing.

Uses frozen 7b inputs. Never writes into the source directory or an existing
output directory. Verification/counters and performance timing are separate.
"""
import argparse
import ast
from collections import Counter
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
from unittest.mock import patch

from backend.high_wilderness_sidecar import missile_guidance as mg, aviation_flight as af, tactical_ew as ew
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools import tactical_7c_reference as reference
from tools.tactical_7c_oracle import snapshot, digests, difference
from tools.tactical_7b_fixture import ROOT, restore, commands, write
from tools.profile_tactical_7b import counts, digest_state, fingerprint
from tools.profile_tactical_fire_control import stats


@contextmanager
def implementation(legacy=False, counters=None):
    """Patch all three consumers, including the two imported function aliases."""
    blocked = reference.blocked_channels if legacy else mg.blocked_channels
    geometry = reference.segment_circle if legacy else mg.segment_circle
    phase = ['direct']
    def segment(*args):
        counters[phase[0]+'.geometry_calls'] += 1
        hit = geometry(*args)
        counters[phase[0]+'.geometry_hits'] += int(hit)
        return hit
    def measured(label):
        def call(areas, origin, layer, target, target_layer, **kwargs):
            previous = phase[0]; phase[0] = label
            counters[label+'.calls'] += 1
            for area in areas:
                counters[label+'.input_regions.'+area.kind] += 1
                if area.layer not in (layer, target_layer):
                    counters[label+'.irrelevant_layer'] += 1
                elif area.kind == 'electronic' and (kwargs.get('observer_side') is None or area.side == kwargs['observer_side']
                    or kwargs.get('seeker') == 'anti_radiation' and kwargs.get('target_jamming')):
                    counters[label+'.irrelevant_electronic'] += 1
            try: return blocked(areas, origin, layer, target, target_layer, **kwargs)
            finally: phase[0] = previous
        return call
    with ExitStack() as stack:
        for owner in (mg, reference):
            stack.enter_context(patch.object(owner, 'segment_circle', segment if counters is not None else geometry))
        for owner, label in ((mg, 'guidance'), (af, 'aircraft'), (ew, 'ship')):
            stack.enter_context(patch.object(owner, 'blocked_channels', measured(label) if counters is not None else blocked))
        yield


def attach(frozen, directory, legacy):
    with implementation(legacy):
        service = RealtimeViewService('backend.7c.compare', settlement_dir=directory)
        battle, geometry, _ = restore(frozen, service, 'fixed')
        service._attach(battle, geometry)
        packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=None))
    return service, battle, packet['view']['projectile_stream']['sequence']


def verify(source, out, steps):
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    if not 1 <= steps <= len(expected): raise ValueError('Step count exceeds frozen tape')
    tape = []; counters = [Counter(), Counter()]; late = [Counter(), Counter()]
    with TemporaryDirectory(prefix='hw-7c-oracle-') as directory:
        runs = [attach(frozen, str(Path(directory)/str(n)), n == 0) for n in range(2)]
        cursors = [r[2] for r in runs]
        if error := difference(snapshot(runs[0][1]), snapshot(runs[1][1])):
            write(out/'mismatch.json', dict(step=0, difference=error)); raise AssertionError(error)
        for n in range(steps):
            states = []
            for variant, (service, b, _) in enumerate(runs):
                measured = Counter()
                with implementation(variant == 0, measured):
                    commands(b, frozen); service.scheduler._stepper(); service.scheduler._committed = b.session.world
                    if n % 4 == 0:
                        service.publish()
                        packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=cursors[variant]))
                        cursors[variant] = packet['view']['projectile_stream']['sequence']
                counters[variant].update(measured)
                if 600 <= n < 660: late[variant].update(measured)
                assert digest_state(b) == expected[n], f'7b digest mismatch at {n+1}, variant {variant}'
                assert b.ending is None, 'Frozen short battle ended unexpectedly'
                states.append(snapshot(b))
            if error := difference(*states):
                write(out/'mismatch.json', dict(step=n+1, difference=error, reference=states[0], candidate=states[1]))
                raise AssertionError(f'Authority mismatch at {n+1}: {error["path"]}')
            tape.append(digests(states[0]))
            if (n+1) % 150 == 0: print(json.dumps(dict(case=frozen['case'], matched_steps=n+1)), flush=True)
        report = dict(case=frozen['case'], steps=steps, sections=list(tape[-1]),
            fixture_sha256=canonical_sha256(frozen), expanded_tape_sha256=canonical_sha256(tape),
            final_load=counts(runs[1][1]), counters=dict(reference=dict(counters[0]), candidate=dict(counters[1])),
            late_601_660=dict(reference=dict(late[0]), candidate=dict(late[1])),
            scope='Exact authority comparison and diagnostic counts; no performance timings. Epochs normalized; derived caches and wall clocks excluded.')
        write(out/'expanded-tape.json', tape); write(out/'verification.json', report)
        print(json.dumps(dict(case=report['case'], matched_steps=steps, sections=len(report['sections']))), flush=True)


def measure(source, out, pairs):
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    reports = []
    for pair in range(pairs):
        # Alternate order; every run restores a fresh battle from identical input.
        for legacy in ((True, False) if pair % 2 == 0 else (False, True)):
            # Do not let the second variant inherit trajectories/ranges populated
            # by the first one. These caches are not part of the battle object.
            from backend.high_wilderness_sidecar import tactical_point_defense as pd, missile_flight as mf
            from backend.high_wilderness_sidecar import tactical_ballistics as ballistics, tactical_drag_prediction as drag
            for cached in (pd.trajectory, mf.segment, ballistics.reference_range, drag.bands): cached.cache_clear()
            with TemporaryDirectory(prefix='hw-7c-timing-') as directory:
                service, b, cursor = attach(frozen, directory, legacy)
                sample = []; publish = []; read = []; cpu = 0.; tape = []
                with implementation(legacy):
                    for n in range(len(expected)):
                        commands(b, frozen); clock = perf_counter(); cpu_clock = process_time()
                        service.scheduler._stepper()
                        cpu += process_time()-cpu_clock; sample.append(perf_counter()-clock)
                        service.scheduler._committed = b.session.world
                        state = digest_state(b); assert state == expected[n], f'7b mismatch at {n+1}'
                        tape.append(state)
                        if n % 4 == 0:
                            clock = perf_counter(); cpu_clock = process_time(); service.publish()
                            cpu += process_time()-cpu_clock; publish.append(perf_counter()-clock)
                            clock = perf_counter(); cpu_clock = process_time()
                            packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=cursor))
                            cpu += process_time()-cpu_clock; read.append(perf_counter()-clock)
                            cursor = packet['view']['projectile_stream']['sequence']
                report = dict(case=frozen['case'], pair=pair+1, variant='reference' if legacy else 'candidate', steps=len(sample),
                    step=stats(sample), publish=stats(publish), read=stats(read), service_cpu_s=cpu,
                    service_wall_s=sum(sample)+sum(publish)+sum(read), final_load=counts(b),
                    fixture_sha256=canonical_sha256(frozen), authority_tape_sha256=canonical_sha256(tape),
                    expanded_final=digests(snapshot(b)), physics_caches_cleared=True,
                    scope='Uninstrumented offline service calls; no profiler/diagnostic counters. Not wall-clock paced real-time acceptance.')
                write(out/f'{pair+1}-{report["variant"]}.json', report); reports.append(report)
                print(json.dumps({k: report[k] for k in ('case', 'pair', 'variant', 'step', 'service_cpu_s', 'service_wall_s')}), flush=True)
    for row in reports[1:]: assert row['expanded_final'] == reports[0]['expanded_final'], 'Final expanded state differs'
    write(out/'measurements.json', reports)


def interleaved(source, out):
    """Diagnostic control for drift: swap first runner on every fixed step."""
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    samples = [[], []]; publications = [[], []]; reads = [[], []]
    with TemporaryDirectory(prefix='hw-7c-interleaved-') as directory:
        runs = [attach(frozen, str(Path(directory)/str(n)), n == 0) for n in range(2)]
        cursors = [row[2] for row in runs]
        for n in range(len(expected)):
            for variant in ((0, 1) if n % 2 == 0 else (1, 0)):
                service, b, _ = runs[variant]
                with implementation(variant == 0):
                    commands(b, frozen); clock = perf_counter()
                    service.scheduler._stepper(); samples[variant].append(perf_counter()-clock)
                    service.scheduler._committed = b.session.world
                    assert digest_state(b) == expected[n], f'7b mismatch at {n+1}, variant {variant}'
                    if n % 4 == 0:
                        clock = perf_counter(); service.publish(); publications[variant].append(perf_counter()-clock)
                        clock = perf_counter()
                        packet = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=cursors[variant]))
                        reads[variant].append(perf_counter()-clock); cursors[variant] = packet['view']['projectile_stream']['sequence']
        assert difference(snapshot(runs[0][1]), snapshot(runs[1][1])) is None
        report = dict(case=frozen['case'], steps=len(expected), fixture_sha256=canonical_sha256(frozen),
            variants={name: dict(step=stats(samples[n]), publish=stats(publications[n]), read=stats(reads[n])) for n, name in enumerate(('reference', 'candidate'))},
            scope='Supplementary interleaved-step timing for drift control. Two live battles share process caches; do not merge with independent-run distributions.')
        write(out/'interleaved.json', report); print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('mode', choices=('verify', 'measure', 'interleaved'))
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    parser.add_argument('--steps', type=int, default=900); parser.add_argument('--pairs', type=int, default=3)
    args = parser.parse_args()
    if args.out.exists(): raise ValueError('Choose a fresh output directory')
    if args.pairs < 1: raise ValueError('At least one measurement pair is required')
    args.out.mkdir(parents=True)
    provenance = fingerprint()
    reference_head = '79db27e563d9901f5ef14716ce16e9cc5d831cac'
    original = subprocess.check_output(['git', 'show', reference_head+':backend/high_wilderness_sidecar/missile_guidance.py'], cwd=ROOT).decode('utf-8')
    def functions(text):
        return {node.name: ast.dump(node) for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name in ('segment_circle', 'blocked_channels')}
    assert functions(original) == functions(Path(reference.__file__).read_text(encoding='utf-8')), 'Frozen reference differs from original source'
    provenance.update(reference_head=reference_head, reference_functions_ast_verified=True)
    provenance['tools_7c'] = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*.py')}
    write(args.out/'source.json', provenance)
    if args.mode == 'verify': verify(args.source, args.out, args.steps)
    elif args.mode == 'measure': measure(args.source, args.out, args.pairs)
    else: interleaved(args.source, args.out)


if __name__ == '__main__': main()
