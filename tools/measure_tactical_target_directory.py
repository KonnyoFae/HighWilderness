"""P2a.1d serial three-way timing; process-local references, no production flags."""
import argparse
import ast
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as guns, server
from backend.high_wilderness_sidecar import tactical_point_defense as pd, missile_flight as mf
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics, tactical_drag_prediction as drag
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_roster_detection as prior
from tools.profile_tactical_7b import fingerprint, counts, digest_state
from tools.profile_tactical_7c4 import request
from tools.profile_tactical_7c4b import run as paced_run
from tools.profile_tactical_fire_control import stats
from tools.tactical_7c_oracle import snapshot, digests
from tools.tactical_7b_fixture import ROOT, restore, commands, write

CURRENT = '540c2df4cbcfbe1259bdc11e3c5b858b81a4bdd0'
VARIANTS = ('reference', 'backend', 'full')


@lru_cache(maxsize=1)
def old_view():
    path = Path(guns.__file__).relative_to(ROOT).as_posix()
    raw = subprocess.check_output(['git', 'show', prior.REFERENCE+':'+path], cwd=ROOT)
    owner = next(n for n in ast.parse(raw.decode('utf-8')).body if isinstance(n, ast.ClassDef) and n.name == 'GunneryBattle')
    node = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == 'view')
    scope = dict(guns.__dict__)
    exec(compile(ast.Module(body=[node], type_ignores=[]), prior.REFERENCE+':view', 'exec'), scope)
    return scope['view'], sha256(raw).hexdigest()


@contextmanager
def implementation(variant):
    if variant not in VARIANTS:
        raise ValueError(variant)
    with ExitStack() as stack:
        if variant == 'reference':
            stack.enter_context(prior.implementation(True))
        if variant != 'full':
            stack.enter_context(patch.object(guns.GunneryBattle, 'view', old_view()[0]))
        yield


def provenance():
    value = fingerprint()
    value.update(reference_head=prior.REFERENCE, current_head=CURRENT,
                 reference_methods=prior.references()[1], reference_view_file_sha256=old_view()[1],
                 runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                 files_lf={p: sha256((ROOT/p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in value['files']},
                 variants=dict(reference='352f3e5 detection and view; same roster lifecycle',
                               backend='540c2df detection, 352f3e5 view', full='540c2df detection and directory view'))
    return value


def clear_caches():
    for fn in (pd.trajectory, mf.segment, ballistics.reference_range, drag.bands):
        fn.cache_clear()


def packet(service, cursor=None):
    return service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=cursor))


def measure(source, out, rounds):
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    reports = []
    # Rotate the first runner so each variant occupies each position once.
    orders = (VARIANTS, ('full', 'reference', 'backend'), ('backend', 'full', 'reference'))
    for repeat in range(rounds):
        for variant in orders[repeat % 3]:
            clear_caches()
            with implementation(variant), TemporaryDirectory(prefix='hw-directory-timing-') as temp:
                service = RealtimeViewService('backend.7c.compare', settlement_dir=temp)
                battle, geometry, _ = restore(frozen, service, 'fixed')
                service._attach(battle, geometry)
                cursor = packet(service)['view']['projectile_stream']['sequence']
                times = {k: [] for k in ('step', 'publish', 'read', 'encode')}
                cpu = {k: 0. for k in times}; sizes = []; metadata_sizes = []; tape = []
                for n in range(len(expected)):
                    commands(battle, frozen)
                    start = perf_counter(); cpu_start = process_time()
                    service.scheduler._stepper()
                    cpu['step'] += process_time()-cpu_start; times['step'].append(perf_counter()-start)
                    service.scheduler._committed = battle.session.world
                    digest = digest_state(battle)
                    assert digest == expected[n], (variant, n+1)
                    tape.append(digest)
                    if n % 4:
                        continue
                    for label, fn in (('publish', service.publish), ('read', lambda: packet(service, cursor))):
                        start = perf_counter(); cpu_start = process_time(); result = fn()
                        cpu[label] += process_time()-cpu_start; times[label].append(perf_counter()-start)
                        if label == 'read':
                            response = result
                    start = perf_counter(); cpu_start = process_time()
                    wire = server.realtime_response(request(), response, wire=True)
                    cpu['encode'] += process_time()-cpu_start; times['encode'].append(perf_counter()-start)
                    sizes.append(len(wire))
                    summary = response['view']['gunnery'].get('target_directory')
                    metadata_sizes.append(len(json.dumps(summary, ensure_ascii=False, separators=(',', ':')).encode()) if summary else 0)
                    cursor = response['view']['projectile_stream']['sequence']
                report = dict(case=frozen['case'], round=repeat+1, variant=variant, steps=len(tape),
                    timings={k: stats(v) for k, v in times.items()}, samples_s=times, cpu_s=cpu,
                    service_wall_s=sum(sum(v) for v in times.values()), service_cpu_s=sum(cpu.values()),
                    wire_bytes=dict(mean=sum(sizes)/len(sizes), maximum=max(sizes)),
                    directory_bytes=dict(mean=sum(metadata_sizes)/len(metadata_sizes), maximum=max(metadata_sizes)),
                    fixture_sha256=canonical_sha256(frozen), authority_tape_sha256=canonical_sha256(tape),
                    expanded_final=digests(snapshot(battle)), load=counts(battle), physics_caches_cleared=True,
                    scope='Serial offline calls, no profiler, counters or recording. Digest checks outside timers. Not wall-paced realtime.')
                write(out/f'{repeat+1}-{variant}.json', report); reports.append(report)
                print(json.dumps(dict(case=frozen['case'], round=repeat+1, variant=variant,
                    step=report['timings']['step'], service_wall_s=report['service_wall_s'])), flush=True)
    assert all(r['expanded_final'] == reports[0]['expanded_final'] for r in reports)
    write(out/'measurements.json', reports)


def capture(source, out):
    """Target-only immutable inputs for UI replay; kept separate from timed runs."""
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    frames = []; micro = []
    with TemporaryDirectory(prefix='hw-directory-capture-') as temp:
        service = RealtimeViewService('backend.7c.compare', settlement_dir=temp)
        b, geometry, _ = restore(frozen, service, 'fixed'); service._attach(b, geometry)
        for n in range(901):
            if n:
                commands(b, frozen); service.scheduler._stepper(); service.scheduler._committed = b.session.world
                assert digest_state(b) == expected[n-1]
            if n % 12:
                continue
            view = b.view(); observation = view['observation']; directory = view['target_directory']
            frames.append(dict(step=n, directory=directory, ship=observation['ships'][0], memorySeconds=observation['memory_s'],
                               names=dict(b.target_roster.names)))
            # Repeated same-state projection and encoding diagnose marginal costs.
            samples = []; encoding = []
            for _ in range(20):
                clock = perf_counter(); value = b.target_roster.view(b._sides[b._direct_index], observation)
                samples.append(perf_counter()-clock)
                clock = perf_counter(); json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
                encoding.append(perf_counter()-clock)
            micro.append(dict(step=n, active=len(directory['entries']), remembered=len(directory['remembered']),
                              projection=stats(samples), json_encode=stats(encoding)))
    write(out/'frames.json', frames); write(out/'micro.json', micro)
    print(json.dumps(dict(case=frozen['case'], captured_frames=len(frames))), flush=True)


def paced(source, out):
    for variant in ('backend', 'full'):
        for mode in ('fixed', 'adaptive'):
            clear_caches()
            with implementation(variant):
                paced_run(source, out, variant+'-'+mode, SpeedPolicy(mode=mode), verify=False, steps=900, wall_limit=300)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('measure', 'capture', 'paced', 'live'))
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--variant', choices=VARIANTS, default='full'); parser.add_argument('--fixed', action='store_true')
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('Choose a fresh evidence directory')
    args.out.mkdir(parents=True)
    write(args.out/'joint-source.json', provenance())
    if args.mode == 'live':
        from tools.tactical_7c4b_live import serve
        with implementation(args.variant):
            return serve(args.source, args.out, 'fixed' if args.fixed else 'adaptive')
    if args.mode == 'measure':
        if args.rounds < 3:
            raise ValueError('At least three rounds required')
        measure(args.source, args.out, args.rounds)
    elif args.mode == 'capture':
        capture(args.source, args.out)
    else:
        paced(args.source, args.out)


if __name__ == '__main__':
    raise SystemExit(main())
