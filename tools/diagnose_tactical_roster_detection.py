"""Separate late-window attribution; profiler time is not benchmark time."""
import argparse
import cProfile
from hashlib import sha256
import json
from pathlib import Path
import pstats
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_point_defense as pd, missile_flight as mf
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics, tactical_drag_prediction as drag
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import digest_state
from tools.profile_tactical_roster_detection import implementation, provenance
from tools.tactical_7b_fixture import commands, write


def run(source, out):
    if out.exists():raise ValueError('Choose a fresh evidence directory')
    out.mkdir(parents=True)
    write(out/'source.json', dict(provenance(), runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest()))
    frozen = json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected = json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    for legacy in (True, False):
        for cached in (pd.trajectory, mf.segment, ballistics.reference_range, drag.bands):cached.cache_clear()
        profiler = cProfile.Profile()
        with TemporaryDirectory(prefix='hw-roster-profile-') as temp, patch.object(baseline, 'implementation', implementation):
            service, battle, cursor = baseline.attach(frozen, temp, legacy)
            with implementation(legacy):
                for n in range(660):
                    commands(battle, frozen)
                    if n >= 600:profiler.enable()
                    service.scheduler._stepper()
                    if n >= 600:profiler.disable()
                    service.scheduler._committed = battle.session.world
                    assert digest_state(battle) == expected[n]
                    if n % 4 == 0:
                        service.publish()
                        packet = service.read(service.digest, display=dict(interface=baseline.INTERFACE, after_sequence=cursor))
                        cursor = packet['view']['projectile_stream']['sequence']
        stats = pstats.Stats(profiler)
        rows = [dict(file=file, line=line, function=name, primitive_calls=cc, calls=nc, self_s=tt, cumulative_s=ct)
                for (file, line, name), (cc, nc, tt, ct, _) in stats.stats.items()]
        focus = [r for r in rows if any(name in r['file'] for name in ('tactical_detection.py', 'tactical_target_roster.py',
                  'tactical_observation.py', 'aviation_flight.py'))]
        name = 'reference' if legacy else 'candidate'
        write(out/f'{name}.json', dict(case=frozen['case'], variant=name, steps=[601, 660], verified_steps=660,
              total_profiled_s=stats.total_tt, top=sorted(rows, key=lambda r: r['self_s'], reverse=True)[:45],
              focus=sorted(focus, key=lambda r: r['cumulative_s'], reverse=True),
              scope='Step-only cProfile attribution after 600 warm steps. Cumulative times overlap; not comparable to uninstrumented benchmark distributions.'))
        print(json.dumps(dict(case=frozen['case'], variant=name, profiled_steps=60, total_profiled_s=stats.total_tt)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    args = parser.parse_args(); run(args.source, args.out)
