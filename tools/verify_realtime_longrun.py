"""E3c real stdio long run. Freeze rules before launch; preserve failed runs."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from queue import Queue
import statistics
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from backend.high_wilderness_sidecar.tactical_scenario import SCENARIO_ID
from backend.high_wilderness_sidecar.tactical_scheduler import INTERFACE as CONTROL_INTERFACE
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand

MIB = 1024*1024
RULES = dict(interface='gaotian.realtime-longrun/e3c-v1alpha1', duration_s=1800,
    poll_s=1/15, slow_poll_s=.5, control_s=2, cycle_s=60, warmup_s=120,
    slow_window_s=[40,45], disconnect_at_s=46, disconnect_sleep_s=2.3,
    pause_at_s=50, mode_at_s=53, pause_hold_s=.25,
    read_p99_ms=100, read_max_ms=500, pause_max_ms=250,
    input_observed_max_ms=750, view_lag_max_steps=16, debt_max_steps=15,
    normal_rate_min=.995, normal_rate_max=1.005,
    memory_max_bytes=256*MIB, memory_growth_bytes=16*MIB, memory_slope_mib_per_min=.25,
    minimum_steady_minutes=10, minimum_longrun_s=1800)


def percentile(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values)*p)-1)] if values else None


def memory_gate(samples, rules):
    bins = defaultdict(list)
    for sample in samples:
        if sample['elapsed_s'] >= rules['warmup_s']:
            bins[int((sample['elapsed_s']-rules['warmup_s'])//60)].append(sample['memory'])
    # Incomplete minute windows cannot establish a steady-state platform.
    windows = [(minute, rows) for minute, rows in sorted(bins.items()) if len(rows) >= 45]
    report = dict(minutes=len(windows), metrics={})
    sufficient = len(windows) >= rules['minimum_steady_minutes']
    passed = sufficient
    for name in ('rss_bytes', 'private_bytes'):
        points = [(minute, statistics.median(r[name] for r in rows)) for minute, rows in windows
            if all(r[name] is not None for r in rows)]
        if len(points) != len(windows) or not points:
            report['metrics'][name] = dict(status='UNAVAILABLE'); passed = False; continue
        xs, ys = zip(*points); avgx, avgy = statistics.mean(xs), statistics.mean(ys)
        denominator = sum((x-avgx)**2 for x in xs)
        slope = sum((x-avgx)*(y-avgy) for x,y in points)/denominator/MIB if denominator else 0
        growth = statistics.median(ys[-5:])-statistics.median(ys[:5])
        peak = max(s['memory'][name] for s in samples if s['memory'][name] is not None)
        ok = sufficient and slope <= rules['memory_slope_mib_per_min'] and growth <= rules['memory_growth_bytes'] and peak <= rules['memory_max_bytes']
        report['metrics'][name] = dict(status='PASS' if ok else 'FAIL' if sufficient else 'INSUFFICIENT_DURATION',
            minute_medians_bytes=points, slope_mib_per_min=slope, median_growth_bytes=growth, peak_bytes=peak)
        passed &= ok
    report['status'] = 'PASS' if passed else 'FAIL' if sufficient else 'INSUFFICIENT_DURATION'
    return report


class Client:
    def __init__(self, out):
        self.stderr = (out/'stderr.log').open('x', encoding='utf-8')
        self.process = subprocess.Popen([sys.executable,'-X','utf8','-m','tools.realtime_longrun_probe','--out',str(out)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.outputs = Queue(maxsize=8)
        self.reader = Thread(target=self.collect, daemon=True); self.reader.start()
        self.sequence = 0; self.bytes_in = self.bytes_out = 0
        self.latencies = defaultdict(list)
        self.log = (out/'requests.jsonl').open('x', encoding='utf-8')
        self.started = monotonic()

    def collect(self):
        try:
            for line in self.process.stdout:
                self.outputs.put((json.loads(line),len(line)))
        finally:
            self.outputs.put((None,0))

    def request(self, method, params):
        self.sequence += 1
        value = dict(interface='gaotian.web-bridge/v1alpha1', kind='request', backend_instance_id='backend.e3clongrun',
            request_id=f'req.{self.sequence}', session_id=None, expected_revision=None, method=method, params=params)
        data = (json.dumps(value,ensure_ascii=False)+'\n').encode('utf-8')
        begin = monotonic(); self.process.stdin.write(data); self.process.stdin.flush(); self.bytes_in += len(data)
        while True:
            response, size = self.outputs.get(timeout=15); self.bytes_out += size
            if response is None: raise RuntimeError('Sidecar ended before response')
            if response.get('kind') == 'event': continue
            assert response['request_id'] == value['request_id'], response
            elapsed = (monotonic()-begin)*1000
            self.latencies[method].append(elapsed)
            self.log.write(json.dumps(dict(elapsed_s=monotonic()-self.started,method=method,ms=elapsed,
                request_bytes=len(data),response_bytes=size,ok=response['ok']))+'\n')
            assert response['ok'], response
            return response['result']

    def close(self):
        try:
            if self.process.poll() is None:
                self.request('system.shutdown',dict(reason='user_exit'))
                assert self.process.wait(timeout=10) == 0
        finally:
            if self.process.poll() is None: self.process.kill(); self.process.wait(timeout=5)
            self.reader.join(timeout=2)
            self.process.stdin.close(); self.process.stdout.close(); self.stderr.close(); self.log.close()


class FlightRun:
    def __init__(self, client, out, rules):
        self.client, self.rules = client, rules
        self.log = (out/'observations.jsonl').open('x', encoding='utf-8')
        self.inputs = (out/'inputs.jsonl').open('x', encoding='utf-8')
        self.acks = []; self.event_ack = self.event_seen = 0
        self.pending = {}; self.resolved = set(); self.observed_ms = []
        self.steps = self.highest = self.max_lag = self.events_bytes = self.view_bytes = 0
        self.normal_s = self.normal_steps = 0; self.previous_normal = None
        self.faults = defaultdict(int); self.initialized = False
        self.started = monotonic()

    def call(self, name, params=None):
        return self.client.request('tactical.realtime.'+name,dict(scene_id=self.scene) if params is None else params)

    def accept(self, value, normal=False):
        now = monotonic(); st = value['status']; step = st['fixed_step']
        assert st['epoch'] == self.scene and step >= self.steps
        assert st['highest_input_sequence'] >= self.highest
        assert value['error'] is None and value['available'], value
        lag = step-value['view']['fixed_step']; assert 0 <= lag <= self.rules['view_lag_max_steps'], lag
        self.max_lag = max(self.max_lag,lag)
        self.steps, self.highest = step, st['highest_input_sequence']
        assert st['debt_quanta'] <= self.rules['debt_max_steps']*1_000_000_000, st
        assert st['pending_inputs']<=64 and st['retained_receipts']<=256 and st['reliable_events']<=128
        if self.initialized: assert value['view']['static'] is None, 'Static geometry resent during cache hit'
        self.initialized = True
        for event in value['events']:
            seq = event['sequence']
            if seq > self.event_seen:
                assert seq == self.event_seen+1, 'Reliable output gap'
                self.event_seen = seq
            self.event_ack = max(self.event_ack,seq)
        for query in value['receipts']:
            seq = query['sequence']
            if query['status'] == 'accepted': continue
            assert query['status'] in ('executed','cancelled'), query
            if seq not in self.resolved:
                sent,target = self.pending.pop(seq)
                if query['status']=='executed':
                    assert query['receipt']['resolved_at_step']==target+1, query
                    self.observed_ms.append((now-sent)*1000)
                self.resolved.add(seq)
                self.inputs.write(json.dumps(dict(sequence=seq,status=query['status'],receipt=query['receipt'],
                    observed_after_ms=(now-sent)*1000))+'\n')
            self.acks.append(seq)
        if normal and self.previous_normal is not None:
            old_time,old_step = self.previous_normal
            self.normal_s += now-old_time; self.normal_steps += step-old_step
        self.previous_normal = (now,step) if normal else None
        self.events_bytes += len(json.dumps(value['events']).encode('utf-8'))
        self.view_bytes += len(json.dumps(value['view']).encode('utf-8'))
        self.log.write(json.dumps(dict(elapsed_s=now-self.started,status=st,view_step=value['view']['fixed_step'],
            normal=normal))+'\n')
        self.state = value
        return value

    def read(self, normal=False):
        value = self.call('read',dict(scene_id=self.scene,known_static_sha256=self.digest,
            ack_inputs=sorted(set(self.acks)),ack_events=self.event_ack))
        self.acks=[]
        return self.accept(value,normal)

    def start(self):
        self.client.request('system.hello',dict(client_name='e3clongrun',client_version='1',
            supported_interfaces=['gaotian.web-bridge/v1alpha1'],required_capabilities=['tactical.realtime.create']))
        self.client.request('tactical.set_mode',dict(mode='tactical'))
        value = self.call('create',dict(scenario_id=SCENARIO_ID))
        self.scene=value['status']['epoch']; self.digest=value['view']['static_sha256']
        self.accept(value); self.accept(self.call('resume'))
        self.started=monotonic()

    def control(self):
        self.read()
        st=self.state['status']; seq=st['highest_input_sequence']+1
        # Alternate full/half propulsion and clockwise/counterclockwise turns.
        channels=[ChannelPropulsionCommand('translation.forward','full' if seq%2 else 'half',None)]
        if seq%3: channels.append(ChannelPropulsionCommand('yaw.clockwise' if seq%3==1 else 'yaw.counterclockwise',None,50))
        value=dict(interface=CONTROL_INTERFACE,epoch=self.scene,generation=st['generation'],sequence=seq,
            ship_id=self.state['direct_ship_id'],target_step=st['fixed_step']+2,control=directional_control(tuple(channels)).to_dict())
        sent=monotonic()
        result=self.call('control',dict(scene_id=self.scene,input=value))
        assert result['status']=='accepted',result
        self.pending[seq]=(sent,value['target_step'])
        self.inputs.write(json.dumps(dict(sequence=seq,submitted=value,elapsed_s=sent-self.started))+'\n')

    def fault(self, kind):
        self.previous_normal=None
        if kind=='disconnect':
            sleep(self.rules['disconnect_sleep_s'])
            value=self.read()
            assert not value['status']['running'] and value['status']['pause_reason']=='disconnected',value['status']
        elif kind=='mode':
            self.client.request('tactical.set_mode',dict(mode='editor')); value=self.read()
            assert not value['status']['running'] and value['status']['pause_reason']=='mode_exit'
        else:
            value=self.accept(self.call('pause'))
            assert not value['status']['running'] and value['status']['pause_reason']=='manual'
        step=value['status']['fixed_step']; sleep(self.rules['pause_hold_s'])
        assert self.read()['status']['fixed_step']==step, 'Paused scene moved'
        if kind=='mode': self.client.request('tactical.set_mode',dict(mode='tactical'))
        self.accept(self.call('resume')); self.faults[kind]+=1

    def run(self):
        self.start(); next_control=next_progress=0; done=set(); cycles=self.rules['cycle_s']
        while monotonic()-self.started < self.rules['duration_s']:
            now=monotonic()-self.started; cycle=int(now//cycles); phase=now%cycles
            for kind,key in (('disconnect','disconnect_at_s'),('pause','pause_at_s'),('mode','mode_at_s')):
                if phase >= self.rules[key] and (cycle,kind) not in done:
                    self.fault(kind); done.add((cycle,kind))
            now=monotonic()-self.started; phase=now%cycles
            slow=self.rules['slow_window_s'][0] <= phase < self.rules['slow_window_s'][1]
            state=self.read(normal=not slow)
            assert state['status']['running'],state['status']
            # Do not leave a future control straddling an intentional disconnect.
            if now >= next_control and not (self.rules['disconnect_at_s']-.8 <= phase <= self.rules['disconnect_at_s']):
                self.control(); next_control=now+self.rules['control_s']
            if now >= next_progress:
                print(json.dumps(dict(progress_s=round(now,1),step=self.steps,inputs=self.highest,events=self.event_seen,faults=self.faults)),flush=True)
                self.log.flush(); self.inputs.flush(); self.client.log.flush(); next_progress=now+30
            sleep(self.rules['slow_poll_s'] if slow else self.rules['poll_s'])
        self.accept(self.call('pause')); self.read(); self.read()
        assert not self.pending and not self.state['receipts'] and not self.state['events']
        result=dict(wall_s=monotonic()-self.started,steps=self.steps,submitted_inputs=self.highest,
            resolved_inputs=len(self.resolved),events=self.event_seen,faults=dict(self.faults),
            observed_input_ms=dict(p99=percentile(self.observed_ms,.99),maximum=max(self.observed_ms,default=0)),
            max_view_lag_steps=self.max_lag,normal_measured_s=self.normal_s,
            normal_simulation_ratio=self.normal_steps/60/self.normal_s,
            logical_view_bytes=self.view_bytes,logical_reliable_bytes=self.events_bytes)
        assert result['observed_input_ms']['maximum']<=self.rules['input_observed_max_ms'], result
        assert self.rules['normal_rate_min'] <= result['normal_simulation_ratio'] <= self.rules['normal_rate_max'], result
        assert all(result['faults'].get(k,0)>0 for k in ('disconnect','pause','mode')),result
        self.call('close'); self.log.close(); self.inputs.close()
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=1800)
    args=parser.parse_args()
    if args.seconds<60: parser.error('At least one complete 60-second fault cycle is required')
    out=args.out.resolve(); out.mkdir(parents=True,exist_ok=False)
    rules=dict(RULES,duration_s=args.seconds)
    baseline.write_json(out/'rules.json',rules); baseline.freeze(out)
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    manifest['build']='E3c real Python sidecar stdio, test-only queue/service observer, unprofiled wall-clock run; excludes Rust/UI/GPU'
    manifest['measurement_rules_sha256']=baseline.digest(out/'rules.json')
    baseline.write_json(out/'manifest.json',manifest)
    result=dict(status='FAIL',scope='Real Python sidecar stdio with test-only bounded observation; no Rust/WebView/GPU long-run coverage',
        full_E3='NOT_PASSED',default_backend_migrated=False)
    client=None; flight=None
    try:
        client=Client(out); flight=FlightRun(client,out,rules); result['flight']=flight.run()
        client.close(); client=None
        samples=[json.loads(line) for line in (out/'probe.jsonl').read_text().splitlines()]
        result['memory']=memory_gate(samples,rules)
        result['probe_samples']=len(samples); result['probe_last']=samples[-1]
        result['latencies_ms']={k:dict(count=len(v),p99=percentile(v,.99),maximum=max(v)) for k,v in flight.client.latencies.items()}
        reads=result['latencies_ms']['tactical.realtime.read']
        assert reads['p99']<=rules['read_p99_ms'] and reads['maximum']<=rules['read_max_ms'],reads
        for method in ('tactical.realtime.pause','tactical.set_mode'):
            assert result['latencies_ms'][method]['maximum']<=rules['pause_max_ms'],result['latencies_ms'][method]
        assert all(q['peak']<=q['capacity'] for row in samples for q in row['queues'])
        assert all(row['view_cache_entries']==1 for row in samples)
        result['wire_bytes']=dict(requests=flight.client.bytes_in,responses=flight.client.bytes_out)
        if args.seconds>=rules['minimum_longrun_s']:
            assert result['memory']['status']=='PASS',result['memory']
            result['status']='PASS'; result['gate']='E3C_30MIN_STDIO_PASS'
        else:
            result['status']='PREFLIGHT_PASS'; result['gate']='LONGRUN_NOT_MEASURED'
    except Exception as error:
        result['error']=repr(error)
    finally:
        if client:
            try: client.close()
            except Exception as error: result['shutdown_error']=repr(error)
        if flight:
            flight.log.close(); flight.inputs.close()
        result['evidence_sha256']={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file() and p.name!='result.json'}
        baseline.write_json(out/'result.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status'] in ('PASS','PREFLIGHT_PASS') else 1


if __name__=='__main__':
    raise SystemExit(main())
