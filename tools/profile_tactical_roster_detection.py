"""P2a.1b committed reference, exact comparisons and uninstrumented pairs."""
import argparse
import ast
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as guns, tactical_observation as obs
from backend.high_wilderness_sidecar import aviation_flight as flight, aviation_ew as ew, tactical_detection as detection
from backend.high_wilderness_sidecar import realtime_view, server, tactical_target_roster as roster
from backend.high_wilderness_sidecar import tactical_contact_start as contact
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import fingerprint
from tools.profile_tactical_7c4 import PublicOracle, request
from tools.tactical_7b_fixture import ROOT, write

REFERENCE = '352f3e5f5f8bca499a6b77cb27332057f2bd3be3'
PUBLIC = None
WIRE_TIMING = False


@lru_cache(maxsize=1)
def references():
    methods = []; hashes = {}
    for module, owner, names in ((guns, guns.GunneryBattle, ('step',)),
                                 (obs, obs.ObservationRuntime, ('plan',)),
                                 (flight, flight, ('advance', 'sample')), (ew, ew, ('refresh',)),
                                 (contact, contact.ContactState, ('__init__',))):
        path = Path(module.__file__).relative_to(ROOT).as_posix()
        source = subprocess.check_output(['git', 'show', REFERENCE+':'+path], cwd=ROOT)
        hashes[path] = sha256(source).hexdigest()
        tree = ast.parse(source.decode('utf-8'))
        body = tree.body if owner is module else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner.__name__).body
        namespace = dict(module.__dict__)
        for name in names:
            node = next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(compile(ast.Module(body=[node], type_ignores=[]), REFERENCE+':'+path, 'exec'), namespace)
            methods.append((owner, name, namespace[name]))
    return methods, hashes


@contextmanager
def implementation(legacy=False, counters=None):
    methods, _ = references()
    original_read = realtime_view.RealtimeViewService.read
    def measured(name, fn):
        def call(*a, **kw):
            counters[name] += 1
            return fn(*a, **kw)
        return call
    def read(service, *a, **kw):
        packet = original_read(service, *a, **kw)
        if PUBLIC is not None:PUBLIC.record(legacy, packet, service)
        if WIRE_TIMING:server.realtime_response(request(), packet, wire=True)
        return packet
    with ExitStack() as stack:
        if legacy:
            for owner, name, fn in methods:stack.enter_context(patch.object(owner, name, fn))
        if counters is not None:
            # Count real construction, including reference function globals;
            # these wrappers are absent in all performance measurements.
            construct = measured('target_samples', obs.Target)
            for module in (obs, flight, detection):stack.enter_context(patch.object(module, 'Target', construct))
            for _, _, fn in methods:stack.enter_context(patch.dict(fn.__globals__, Target=construct))
            for owner, name, label in ((detection.TargetStage, '__init__', 'stages'),
                                       (detection.Candidate, '__init__', 'resolved_candidates'),
                                       (detection.TargetStage, 'threat', 'threat_evaluations'),
                                       (roster.Plan, '_sync', 'metadata_family_checks'),
                                       (obs.ObservationRuntime, 'visible', 'ship_visibility_checks')):
                stack.enter_context(patch.object(owner, name, measured(label, getattr(owner, name))))
            original_targets = obs.ObservationRuntime.targets
            def old_targets(runtime, world, projectiles, missiles=()):
                projectiles = tuple(projectiles)
                counters['full_target_builds'] += 1
                counters['threat_evaluations'] += sum(p.durability is not None and p.durability > 0 for p in projectiles)
                return original_targets(runtime, world, projectiles, missiles)
            stack.enter_context(patch.object(obs.ObservationRuntime, 'targets', old_targets))
            query = detection.TargetStage.query
            def queried(stage, side, layer, **kw):
                counters['query_input_rows'] += sum(len(f.rows) for f in stage.access.frame.families) + len(stage.extras)
                for value in query(stage, side, layer, **kw):
                    counters['query_output_rows'] += 1
                    yield value
            stack.enter_context(patch.object(detection.TargetStage, 'query', queried))
        if PUBLIC is not None or WIRE_TIMING:stack.enter_context(patch.object(realtime_view.RealtimeViewService, 'read', read))
        yield


def provenance():
    data = fingerprint()
    data.update(reference_head=REFERENCE, reference_files=references()[1],
                verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                files_lf={p: sha256((ROOT/p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in data['files']},
                scope='P2a.1a committed production vs roster-based detection; raw and LF-normalized source hashes retained.')
    return data


def run(mode, source, out, steps, pairs):
    global PUBLIC, WIRE_TIMING
    if out.exists():raise ValueError('Choose a fresh evidence directory')
    out.mkdir(parents=True); write(out/'source.json', provenance())
    with patch.object(baseline, 'implementation', implementation):
        if mode == 'verify':
            PUBLIC = PublicOracle(out)
            try:
                baseline.verify(source, out, steps)
                assert not PUBLIC.pending
                write(out/'publications.json', dict(matched_reads=len(PUBLIC.tape), sha256=canonical_sha256(PUBLIC.tape), tape=PUBLIC.tape))
            finally:PUBLIC = None
        elif mode == 'verify-chaff':
            from tools import profile_tactical_p1b as prior
            with patch.object(prior, 'implementation', implementation):prior.verify_chaff(source, out)
        elif mode == 'measure':
            WIRE_TIMING = True
            try:baseline.measure(source, out, pairs)
            finally:WIRE_TIMING = False
        else:baseline.interleaved(source, out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('verify', 'verify-chaff', 'measure', 'interleaved'))
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    parser.add_argument('--steps', type=int, default=900); parser.add_argument('--pairs', type=int, default=3)
    parser.add_argument('--all', action='store_true')
    args = parser.parse_args()
    if args.pairs < 1:raise ValueError('At least one pair required')
    if args.all:
        if args.out.exists():raise ValueError('Choose a fresh evidence directory')
        cases = sorted(p for p in args.source.iterdir() if (p/'fixed-tape.json').is_file())
        if not cases:raise ValueError('No frozen cases found')
        for case in cases:run(args.mode, case, args.out/case.name, args.steps, args.pairs)
    else:run(args.mode, args.source, args.out, args.steps, args.pairs)
