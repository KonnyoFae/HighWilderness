"""Read-only 7c attribution on frozen 7b inputs; profiler time is not a speed benchmark."""
import argparse
import cProfile
from hashlib import sha256
import json
from pathlib import Path
import pstats
from tempfile import TemporaryDirectory

from tools.tactical_7b_fixture import ROOT,restore,commands,write
from tools.profile_tactical_7b import counts,digest_state,fingerprint
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256


def name(key):
    filename,line,fn=key
    try:filename=Path(filename).relative_to(ROOT).as_posix()
    except ValueError:filename=filename.replace('\\','/')
    return dict(file=filename,line=line,function=fn)


def summarize(profile):
    stats=pstats.Stats(profile)
    rows=[]
    for key,(primitive,total,self_s,inclusive_s,callers) in stats.stats.items():
        rows.append(dict(**name(key),primitive_calls=primitive,total_calls=total,self_s=self_s,inclusive_s=inclusive_s,
            # cProfile caller tuples are (total, primitive, self, inclusive),
            # unlike the enclosing pstats row's (primitive, total, ...) order.
            callers=[dict(**name(k),primitive_calls=v[1],total_calls=v[0],self_s=v[2],inclusive_s=v[3])
                for k,v in sorted(callers.items(),key=lambda kv:kv[1][3],reverse=True)[:8]]))
    return dict(total_profiled_s=stats.total_tt,total_calls=stats.total_calls,
        by_self=sorted(rows,key=lambda r:r['self_s'],reverse=True)[:60],
        by_inclusive=sorted(rows,key=lambda r:r['inclusive_s'],reverse=True)[:100])


def run(source,out):
    if out.exists():raise ValueError('Choose a fresh output directory')
    out.mkdir(parents=True)
    frozen=json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected=json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    if len(expected)!=900:raise ValueError('Expected the complete 900-step frozen authority tape')
    windows=[(1,60),(601,660)];step_profiles=[cProfile.Profile() for _ in windows]
    publication=cProfile.Profile();reads=cProfile.Profile();tape=[];boundaries=[]
    provenance=fingerprint()
    provenance['attribution_tool_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    write(out/'source.json',provenance)
    with TemporaryDirectory(prefix='hw-7c-attribution-') as directory:
        service=RealtimeViewService('backend.7c.attribution',settlement_dir=directory)
        b,geometry,_=restore(frozen,service,'fixed');service._attach(b,geometry)
        packet=service.read(service.digest,display=dict(interface=INTERFACE,after_sequence=None))
        cursor=packet['view']['projectile_stream']['sequence']
        for n in range(900):
            step=n+1;active=next((i for i,(start,end) in enumerate(windows) if start<=step<=end),None)
            commands(b,frozen)
            if active is None:service.scheduler._stepper()
            else:step_profiles[active].runcall(service.scheduler._stepper)
            service.scheduler._committed=b.session.world
            state=digest_state(b);assert state==expected[n],f'Authority mismatch at step {step}'
            tape.append(state)
            if n%4==0:
                if active is None:
                    service.publish();packet=service.read(service.digest,display=dict(interface=INTERFACE,after_sequence=cursor))
                else:
                    publication.runcall(service.publish)
                    packet=reads.runcall(service.read,service.digest,display=dict(interface=INTERFACE,after_sequence=cursor))
                cursor=packet['view']['projectile_stream']['sequence']
            if any(step==end for _,end in windows):
                boundaries.append(dict(step=step,load=counts(b)));print(json.dumps(boundaries[-1]),flush=True)
            assert b.ending is None,'Sample ended before the frozen comparison boundary'
        report=dict(case=frozen['case'],fixture_sha256=canonical_sha256(frozen),authority_tape_sha256=canonical_sha256(tape),
            matched_steps=len(tape),scope='cProfile attribution only; inclusive times overlap and profiling changes elapsed time. No production optimization applied.',
            boundaries=boundaries,windows=[dict(first_step=a,last_step=z,profile=summarize(p)) for (a,z),p in zip(windows,step_profiles)],
            publication=summarize(publication),read=summarize(reads))
        write(out/'hotspots.json',report)
        print(json.dumps(dict(case=frozen['case'],matched_steps=len(tape),windows=windows)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    run(a.source,a.out)
