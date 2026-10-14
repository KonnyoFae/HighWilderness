"""Same-input paired collision timing to isolate whole-service timing drift.

Both methods run at each real battle boundary in alternating order. Only the
candidate result is committed, after exact comparison. Timings are component
diagnostics with shared process caches, not whole-service acceptance.
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from tools import profile_tactical_p1c as comparison
from tools.profile_tactical_fire_control import stats
from tools.profile_tactical_7b import digest_state,counts
from tools.tactical_7b_fixture import write,commands
from tools.tactical_7c_oracle import digests,snapshot


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    comparison.validate_reference();args.out.mkdir(parents=True);write(args.out/'source.json',comparison.provenance())
    frozen=json.loads((args.source/'fixture.json').read_text(encoding='utf-8'))
    expected=json.loads((args.source/'fixed-tape.json').read_text(encoding='utf-8'))
    samples=[];calls=0
    with TemporaryDirectory(prefix='hw-p1c-collision-') as directory:
        with patch.object(comparison.baseline,'implementation',comparison.implementation):
            _,b,_=comparison.baseline.attach(frozen,directory,False)
        candidate=b.damage.advance;reference=comparison.reference_globals['advance']
        def timed(before,world,projectiles,state,decoys=(),aircraft=()):
            nonlocal calls
            calls+=1;results={};times={}
            for legacy in ((True,False) if calls%2 else (False,True)):
                start=perf_counter()
                value=reference(b.damage,before,world,projectiles,state,decoys,aircraft) if legacy else candidate(before,world,projectiles,state,decoys,aircraft)
                times['reference' if legacy else 'candidate']=perf_counter()-start;results[legacy]=value
            assert results[True]==results[False],f'Collision outputs differ at {world.fixed_step}'
            if projectiles:samples.append(dict(step=world.fixed_step,projectiles=len(projectiles),reference_first=calls%2==1,**times))
            return results[False]
        with patch.object(b.damage,'advance',timed):
            for n in range(len(expected)):
                commands(b,frozen);b.step();assert digest_state(b)==expected[n],f'7b mismatch at {n+1}'
                if (n+1)%150==0:print(json.dumps(dict(collision_matched_steps=n+1)),flush=True)
        report=dict(case=frozen['case'],steps=len(expected),matched_calls=calls,nonempty_calls=len(samples),final_load=counts(b),
            expanded_final=digests(snapshot(b)),variants={name:stats([r[name] for r in samples]) for name in ('reference','candidate')},
            scope='Same-input collision component diagnostic, alternating method order, shared process caches. Exact outputs compared at every boundary; excludes the rest of the simulation, publication and transport.')
        write(args.out/'samples.json',samples);write(args.out/'collision.json',report)
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
