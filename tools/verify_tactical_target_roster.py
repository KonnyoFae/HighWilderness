"""P2a.1a comparison against committed production, including public readouts.

The pinned four old methods restore the pre-roster implementation. All other
code is identical. Diagnostics are correctness evidence, not performance data.
"""
import argparse
import ast
from collections import Counter
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as gunnery, tactical_aviation as aviation, realtime_view
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7c4 import PublicOracle
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write

REFERENCE = '05247c17271ad275c428d75d0276c4cc3ad5d482'


def references():
    result = []; hashes = {}
    for module, cls, names in ((gunnery, gunnery.GunneryBattle, ('__init__', 'step', 'withdraw')),
                               (aviation, aviation.AviationRuntime, ('submit',))):
        path = Path(module.__file__).relative_to(ROOT).as_posix()
        source = subprocess.check_output(['git', 'show', REFERENCE + ':' + path], cwd=ROOT)
        hashes[path] = sha256(source).hexdigest()
        owner = next(n for n in ast.parse(source.decode('utf-8')).body if isinstance(n, ast.ClassDef) and n.name == cls.__name__)
        for name in names:
            method = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == name)
            namespace = dict(module.__dict__)
            exec(compile(ast.Module(body=[method], type_ignores=[]), REFERENCE + ':' + path, 'exec'), namespace)
            result.append((cls, name, namespace[name]))
    return result, hashes


def check_roster(b):
    expected = []
    if not b.ending:
        sides = {s.ship_id: b._sides[n] for n, s in enumerate(b.session.world.ships)}
        expected.extend(('ship', s.ship_id, sides[s.ship_id], s.motion.height_layer) for s in b.session.world.ships
                        if s.command.lifecycle.physical_status == 'operational' and s.motion.hull_integrity_fraction > 0)
        expected.extend(('projectile', p.id, sides[p.ship_id], p.height_layer) for p in b.projectiles
                        if p.durability is not None and p.durability > 0 and not p.aircraft_body)
        expected.extend(('aircraft', key, b._sides[f['owner']], f['layer']) for key, f in b.aviation.flights.items() if f['hp'] > 0)
    frame = b.target_roster.frame
    actual = [(r.handle.family, r.handle.target_id, r.side, r.layer) for r in frame.query()]
    assert actual == expected, f'Roster differs at {b.session.world.fixed_step}'
    for side in set(b._sides):
        for layers in (('upper', 'cloud'), ('upper', 'cloud', 'rain'), ('cloud', 'rain')):
            actual = [(r.handle.family, r.handle.target_id, r.side, r.layer)
                      for r in frame.query(exclude_side=side, layers=layers)]
            assert actual == [r for r in expected if r[2] != side and r[3] in layers]


def run(source, out, steps=900):
    if out.exists():raise ValueError('Choose a fresh evidence directory')
    old, hashes = references()
    out.mkdir(parents=True)
    write(out/'source.json', dict(fingerprint(), reference_head=REFERENCE, reference_files=hashes,
                                 verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest()))
    public = PublicOracle(out); counts = Counter()
    original_step = gunnery.GunneryBattle.step
    original_read = realtime_view.RealtimeViewService.read

    @contextmanager
    def implementation(legacy=False, counters=None):
        def step(b, *args, **kw):
            before = b.target_roster.frame
            result = original_step(b, *args, **kw)
            check_roster(b)
            counts['checked_steps'] += 1
            counts['unchanged_frames' if before is b.target_roster.frame else 'changed_frames'] += 1
            return result
        def read(service, *args, **kw):
            packet = original_read(service, *args, **kw)
            public.record(legacy, packet, service)
            return packet
        with ExitStack() as stack:
            if legacy:
                for cls, name, method in old:stack.enter_context(patch.object(cls, name, method))
            else:
                stack.enter_context(patch.object(gunnery.GunneryBattle, 'step', step))
            stack.enter_context(patch.object(realtime_view.RealtimeViewService, 'read', read))
            yield

    with patch.object(baseline, 'implementation', implementation):
        baseline.verify(source, out, steps)
    assert not public.pending
    write(out/'roster.json', dict(counts, scope='Every candidate step checked against actual entities and ordered side/layer queries.'))
    write(out/'publications.json', dict(matched_reads=len(public.tape), sha256=canonical_sha256(public.tape), tape=public.tape))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    parser.add_argument('--steps', type=int, default=900)
    parser.add_argument('--all', action='store_true', help='Verify each frozen case beneath source')
    args = parser.parse_args()
    if args.all:
        if args.out.exists():raise ValueError('Choose a fresh evidence directory')
        cases = sorted(p for p in args.source.iterdir() if (p/'fixed-tape.json').is_file())
        if not cases:raise ValueError('No frozen cases found')
        for case in cases:run(case, args.out/case.name, args.steps)
    else:
        run(args.source, args.out, args.steps)
