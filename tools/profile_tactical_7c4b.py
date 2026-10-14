"""Frozen state equivalence and separate wall-paced speed diagnostics."""
import argparse
from dataclasses import asdict,replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic_ns,perf_counter,sleep

from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService,VIEW_PERIOD_NS
from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
from backend.high_wilderness_sidecar import server
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from tools.tactical_7b_fixture import ROOT,restore,commands,write
from tools.profile_tactical_7b import fingerprint,counts,digest_state
from tools.tactical_7c_oracle import snapshot,digests
from tools.profile_tactical_fire_control import stats
from tools.test_tactical_scheduler import Clock
from tools.profile_tactical_7c4 import request


def provenance():
    value=fingerprint()
    value['tools']={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c4*') if p.is_file()}
    value['policy']=asdict(SpeedPolicy(mode='adaptive'))
    return value


def run(source,out,label,policy,*,verify,steps=900,wall_limit=180):
    frozen=json.loads((source/'fixture.json').read_text(encoding='utf-8'))
    expected=json.loads((source/'fixed-tape.json').read_text(encoding='utf-8'))
    expanded=json.loads((ROOT/f'artifacts/tactical-7c4a-20261013/verify-{frozen["case"]}/expanded-tape.json').read_text(encoding='utf-8'))
    fake=Clock() if verify else None;clock=fake if fake else monotonic_ns
    with TemporaryDirectory(prefix='hw-7c4b-') as temp:
        s=RealtimeViewService('backend.7c.compare',settlement_dir=temp,clock=clock,speed_policy=policy)
        b,geometry,_=restore(frozen,s,'fixed');s._attach(b,geometry);q=s.scheduler
        cursor=s.read(s.digest,display=dict(interface=INTERFACE,after_sequence=None))['view']['projectile_stream']['sequence']
        stepper=q._stepper;step_times=[];tape=[];reads=[];peak_debt=0;rates=[];last_rate=None
        def step(*args,**kwargs):
            commands(b,frozen);start=perf_counter();result=stepper(*args,**kwargs);step_times.append(perf_counter()-start)
            n=b.session.world.fixed_step
            if verify:
                assert digest_state(b)==expected[n-1],f'7b differs: {label}/{n}'
                state=digests(snapshot(b))
                if state!=expanded[n-1]:
                    write(out/f'{label}-mismatch.json',dict(step=n,sections=[k for k in state if state[k]!=expanded[n-1][k]]))
                    raise AssertionError(f'Expanded state differs at {label}/{n}')
                tape.append(state)
                # Synthetic cost changes force slow-down and later recovery;
                # fixed-speed oracles have no artificial CPU stall. This is
                # correctness testing, not comparative performance timing.
                cost=(5 if n<60 else 40 if n<600 else 80 if n<660 else 3) if policy.mode=='adaptive' else 0
                fake.advance(cost*1_000_000)
            return result
        q._stepper=step;q.resume();start=clock();real_start=perf_counter();next_read=start
        while q.status.running and b.session.world.fixed_step<steps and clock()-start<wall_limit*1_000_000_000:
            if steps-b.session.world.fixed_step<q.limits.steps_per_pump:
                q._limits=replace(q.limits,steps_per_pump=steps-b.session.world.fixed_step)
            s.tick()
            if clock()>=next_read and q.status.running:
                token=s.begin_work();read_start=perf_counter()
                packet=s.read(s.digest,display=dict(interface=INTERFACE,after_sequence=cursor))
                server.realtime_response(request(),packet,wire=True)
                if fake:fake.advance(4_000_000)
                s.finish_work(token);reads.append(perf_counter()-read_start)
                cursor=packet['view']['projectile_stream']['sequence'];s.last_read=clock();next_read=clock()+100_000_000
                events=q.read_events(q.world.epoch,after_sequence=q.status.acknowledged_event_sequence,limit=64)
                if events:q.acknowledge_events(q.world.epoch,events[-1].sequence)
            status=q.status;peak_debt=max(peak_debt,status.debt_quanta/1e9)
            if last_rate!=status.speed.effective:
                rates.append(dict(wall_s=(clock()-start)/1e9,step=status.fixed_step,rate=status.speed.effective));last_rate=status.speed.effective
            if fake:fake.advance(2_000_000)
            elif not s.work_pending:sleep(.002)
        status=q.status;elapsed=(clock()-start)/1e9;completed=b.session.world.fixed_step
        if verify:assert completed==steps and status.running, (label,completed,asdict(status))
        report=dict(case=frozen['case'],variant=label,steps=completed,simulated_s=completed/60,wall_s=elapsed,
            runner_wall_s=perf_counter()-real_start,status=asdict(status),peak_debt_steps=peak_debt,
            rate_changes=rates,rate_wall_s={str(rate/1000):ns/1e9 for rate,ns in sorted(q._speed.rate_wall_ns.items())},
            step=stats(step_times),read_and_encode=stats(reads),load=counts(b),error=s.error,
            fixture_sha256=canonical_sha256(frozen),expanded_tape_sha256=canonical_sha256(tape) if verify else None,
            scope='Synthetic-clock equivalence against 7b and 7c.4a; timings are not performance results.' if verify else
                f'Serial wall-paced sidecar service diagnostic, real read/encode cost, no browser or native UI. Stops at {steps} steps, protection or {wall_limit} wall seconds. At most four steps per pump; last pump is capped at remaining sample steps.')
        write(out/f'{label}.json',report)
        if verify:write(out/f'{label}-tape.json',tape)
        q.pause()
        print(json.dumps({k:report[k] for k in ('case','variant','steps','simulated_s','wall_s','peak_debt_steps','error')}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('verify','paced'));p.add_argument('source',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--steps',type=int,default=900);a=p.parse_args()
    if a.out.exists():raise ValueError('Choose a fresh output directory')
    a.out.mkdir(parents=True);write(a.out/'source.json',provenance())
    policies=[('fixed',SpeedPolicy()),('half',SpeedPolicy(fixed_rate=.5)),('adaptive',SpeedPolicy(mode='adaptive'))]
    for label,policy in policies:
        if label=='half' and a.mode=='paced':continue
        run(a.source,a.out,label,policy,verify=a.mode=='verify',steps=a.steps)
