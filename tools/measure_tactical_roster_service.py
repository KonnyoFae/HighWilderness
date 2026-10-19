"""Serial real-clock sidecar checks for P2a.1b; no browser or native UI."""
import argparse
from hashlib import sha256
from pathlib import Path

from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
from backend.high_wilderness_sidecar import tactical_point_defense as pd, missile_flight as mf
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics, tactical_drag_prediction as drag
from tools.profile_tactical_roster_detection import implementation, provenance
from tools.profile_tactical_7c4b import run
from tools.tactical_7b_fixture import write


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path); parser.add_argument('out', type=Path)
    args = parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh evidence directory')
    args.out.mkdir(parents=True)
    write(args.out/'source.json', dict(provenance(), runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest()))
    for legacy in (True, False):
        for mode in ('fixed', 'adaptive'):
            for cached in (pd.trajectory, mf.segment, ballistics.reference_range, drag.bands):cached.cache_clear()
            with implementation(legacy):
                run(args.source, args.out, f'{"reference" if legacy else "candidate"}-{mode}',
                    SpeedPolicy(mode=mode), verify=False, steps=900, wall_limit=300)
