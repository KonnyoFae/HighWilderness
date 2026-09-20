"""F5 bounded real-time combat acceptance, separate from E3c's 30-minute gate."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
from time import monotonic, sleep

from tools.verify_realtime_longrun import Client, FlightRun, RULES as BASE_RULES, percentile
from backend.high_wilderness_sidecar.tactical_scheduler import INTERFACE
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand

ROOT=Path(__file__).resolve().parents[1]


class CombatRun(FlightRun):
    def control(self):
        self.read(); st=self.state['status']; seq=st['highest_input_sequence']+1
        channels=() if self.rules['case']=='ciws' else (
            ChannelPropulsionCommand('translation.forward','full' if seq%2 else 'half',None),)
        value=dict(interface=INTERFACE,epoch=self.scene,generation=st['generation'],sequence=seq,
            ship_id=self.state['direct_ship_id'],target_step=st['fixed_step']+6,
            control=directional_control(channels).to_dict())
        sent=monotonic(); result=self.call('control',dict(scene_id=self.scene,input=value))
        assert result['status']=='accepted',result
        self.pending[seq]=(sent,value['target_step'])
        self.inputs.write(json.dumps(dict(sequence=seq,submitted=value))+'\n')

    def run(self):
        self.start(); next_control=next_progress=0; paused=False
        while monotonic()-self.started < self.rules['duration_s']:
            now=monotonic()-self.started
            if now>=self.rules['duration_s']/2 and not paused:
                self.fault('pause'); paused=True
            value=self.read(normal=True)
            assert value['status']['running'],value['status']
            if now>=next_control:
                self.control(); next_control=now+2
            if now>=next_progress:
                print(json.dumps(dict(case=self.rules['case'],elapsed_s=round(now,1),step=self.steps)),flush=True)
                self.log.flush(); self.inputs.flush(); self.client.log.flush(); next_progress=now+30
            sleep(self.rules['poll_s'])
        self.accept(self.call('pause')); self.read(); self.read()
        assert not self.pending and not self.state['receipts'] and not self.state['events']
        ratio=self.normal_steps/60/self.normal_s
        assert self.rules['normal_rate_min']<=ratio<=self.rules['normal_rate_max'],ratio
        maximum=max(self.observed_ms,default=0)
        assert maximum<=self.rules['input_observed_max_ms'],maximum
        return dict(wall_s=monotonic()-self.started,steps=self.steps,normal_simulation_ratio=ratio,
            normal_measured_s=self.normal_s,submitted_inputs=self.highest,resolved_inputs=len(self.resolved),
            maximum_input_observed_ms=maximum,max_view_lag_steps=self.max_lag,pause_resume_verified=paused)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--case',choices=('saved','ciws'),required=True)
    parser.add_argument('--seconds',type=float,default=120)
    args=parser.parse_args()
    if args.seconds<10: parser.error('At least 10 seconds for a smoke test')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    keys=('poll_s','read_p99_ms','read_max_ms','pause_max_ms','input_observed_max_ms','view_lag_max_steps',
          'debt_max_steps','normal_rate_min','normal_rate_max','pause_hold_s')
    rules={k:BASE_RULES[k] for k in keys};rules.update(case=args.case,duration_s=args.seconds,acceptance_minimum_s=120)
    (out/'rules.json').write_text(json.dumps(rules,indent=2),encoding='utf-8')
    paths=[*Path('backend/high_wilderness_sidecar').glob('*.py'),Path('tools/fire_control_acceptance_probe.py'),
           Path('tools/verify_fire_control_realtime.py'),Path('tools/verify_realtime_longrun.py'),
           Path('tools/realtime_longrun_probe.py'),Path('tools/profile_tactical_fire_control.py'),
           Path('artifacts/tactical-fire-control-f1f2-20260920/fixture.json')]
    result=dict(status='FAIL',scope='F5 120-second real Python stdio/scheduler combat; separate browser evidence; no maximum fleet or 30-minute claim',
        python=sys.version,platform=platform.platform(),source_sha256={str(p):sha256(p.read_bytes()).hexdigest() for p in paths})
    client=flight=None
    try:
        client=Client(out,probe_module='tools.fire_control_acceptance_probe',probe_args=('--case',args.case))
        flight=CombatRun(client,out,rules);result['flight']=flight.run()
        client.close();client=None
        result['runtime']=json.loads((out/'runtime.json').read_text(encoding='utf-8'))
        samples=[json.loads(line) for line in (out/'probe.jsonl').read_text().splitlines()]
        result['probe_samples']=len(samples);result['probe_last']=samples[-1]
        result['peak_memory_bytes']={k:max(s['memory'][k] for s in samples if s['memory'][k] is not None)
                                     for k in ('rss_bytes','private_bytes') if any(s['memory'][k] is not None for s in samples)}
        result['latencies_ms']={k:dict(count=len(v),p99=percentile(v,.99),maximum=max(v)) for k,v in flight.client.latencies.items()}
        reads=result['latencies_ms']['tactical.realtime.read'];pause=result['latencies_ms']['tactical.realtime.pause']
        assert reads['p99']<=rules['read_p99_ms'] and reads['maximum']<=rules['read_max_ms'],reads
        assert pause['maximum']<=rules['pause_max_ms'],pause
        assert all(q['peak']<=q['capacity'] for s in samples for q in s['queues'])
        assert all(s['status']['pause_reason']!='overload' for s in samples)
        assert result['runtime']['shots']>0,result['runtime']
        if args.case=='ciws':
            assert result['runtime']['intercepted']==result['runtime']['incoming_waves']*12,result['runtime']
        result['status']='PASS' if args.seconds>=rules['acceptance_minimum_s'] else 'SMOKE_PASS'
    except Exception as error:
        result['error']=repr(error)
    finally:
        if client:
            try:client.close()
            except Exception as error:result['shutdown_error']=repr(error)
        if flight:flight.log.close();flight.inputs.close()
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k in ('status','error','flight','latencies_ms')},ensure_ascii=False),flush=True)
    return 0 if result['status'] in ('PASS','SMOKE_PASS') else 1


if __name__=='__main__':raise SystemExit(main())
