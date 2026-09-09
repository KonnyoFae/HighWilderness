"""E2.2a scoped power/crew producer replay, mechanism checks and throughput."""
import argparse
import cProfile
from dataclasses import asdict, replace
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import tactical_realtime_baseline as baseline
from tools.verify_simplified_flight_gate import make_case, summary, WARMUP, MEASURED
from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation


def build(*,with_command=False):
    s=sf.build_sample_session(ROOT,with_resources=True,with_command=with_command,allow_test_device_rebuild=True)
    seed=s._seeds[0]
    modules=[]
    for m in seed.resources.modules:
        proto=m.prototype
        if m.id.startswith(('main_engine','thruster')):
            proto=replace(proto,automation=replace(proto.automation,level='manual',automated_functions=()))
        if m.id.startswith('main_engine'):
            proto=replace(proto,power=replace(proto.power,consumer_category='sensors',active_load_kw=150,standby_load_kw=1))
        modules.append(replace(m,prototype=proto))
    # Explicit synthetic manual/electric fixture; never modifies catalog/player designs.
    seed=replace(seed,resources=replace(seed.resources,modules=tuple(modules)))
    return sf.SimplifiedFlightSession((seed,*s._seeds[1:]),s._profile,direct_ship_id='ship.web.blue',allow_test_device_rebuild=True)


def tape(seed,name):
    controls=make_case('maneuvers' if name=='maneuvers' else 'steady')['controls']
    resources,devices=[],[]
    if name=='resource_bursts':
        seq,dseq=0,0
        maximum=next(m.maximum_durability_points for m in seed.devices.modules if m.instance_id=='generator')
        for start in range(WARMUP,WARMUP+MEASURED,300):
            batch=[]
            for offset,phase,kind,target,value in (
                (30,'closing','crew','ordinary',0),(90,'opening','crew','ordinary',10),
                # After the first reset, idle thrusters stay off and no longer
                # require crew; later shortages only trip the commanded mains.
                *((120,'opening','reset',e.instance_id,None) for e in seed.contributions.engines
                    if start==WARMUP or e.category=='main_engine'),
                (150,'closing','mode','generator','off'),(180,'opening','mode','generator','active'),
                *((210,'opening','reset',e.instance_id,None) for e in seed.contributions.engines if e.category=='main_engine')):
                seq+=1
                batch.append(dict(step=start+offset,phase=phase,kind=kind,target=target,value=value,sequence=seq))
            batch.insert(1,dict(batch[0]))
            resources.extend(batch)
            for offset,kind,amount in ((240,'damage',maximum/4),(270,'test_rebuild',0)):
                dseq+=1
                devices.append(dict(step=start+offset,kind=kind,amount=amount,sequence=dseq))
    return dict(name=name,controls=controls,resources=resources,devices=devices)


def frames(s,case):
    result=[[None,[],[]] for _ in range(WARMUP+MEASURED)]
    for v in case['controls']: result[v['step']][0]=v['value']
    for v in case['resources']:
        result[v['step']][1].append(ResourceOperation(s.world.epoch,'ship.web.blue',v['sequence'],v['kind'],v['target'],
            v['value'],v['step']+(v['phase']=='closing'),v['phase']))
    for v in case['devices']:
        result[v['step']][2].append(DeviceOperation(s.world.epoch,'ship.web.blue','generator',v['sequence'],v['kind'],v['amount'],v['step']+1,'closing'))
    return tuple((c,tuple(r),tuple(d)) for c,r,d in result)


def step(s,f):
    return s.step(f[0],resource_operations=f[1],device_operations=f[2])


def verify(case,out,*,factory=build,rebuild=None):
    a,b=factory(),factory()
    assert a._seeds==b._seeds and a.world.ships==b.world.ships
    active=0
    for n,(fa,fb) in enumerate(zip(frames(a,case),frames(b,case))):
        step(a,fa); step(b,fb)
        assert a.world.ships==b.world.ships and a.last_result==b.last_result
        for seed,ship in zip(a._seeds,a.world.ships):
            assert ship.motion.hull_integrity_fraction>0
            assert all(not slot.blocked[5] or slot.engine.actual_output_percent==0 for slot in ship.propulsion.engines)
            for d in range(6):
                assert ship.propulsion.available_units[d]==sum(e.contribution_units[d] for e,slot in zip(seed.contributions.engines,ship.propulsion.engines) if not any(slot.blocked))
        active+=n>=WARMUP and any(a.world.ships[0].propulsion.output_percent_units)
    assert active>=500
    baseline.write_json(out/f"{case['name']}-final.json",dict(ships=[asdict(v) for v in a.world.ships],last=asdict(a.last_result)))
    results=[]
    for repeat in range(3):
        s=factory(); fs=frames(s,case)
        for f in fs[:WARMUP]: step(s,f)
        if rebuild is not None:
            s=rebuild(s); fs=frames(s,case)
        samples=[]
        started=perf_counter()
        for f in fs[WARMUP:]:
            t=perf_counter(); step(s,f); samples.append((perf_counter()-t)*1000)
        wall=perf_counter()-started
        assert s.world.ships==a.world.ships and s.last_result==a.last_result
        result=dict(repeat=repeat+1,**summary(samples,wall),samples_ms=samples)
        results.append(result)
        baseline.write_json(out/f"{case['name']}-timing.json",results)
        assert result['passed']
    s=factory(); fs=frames(s,case)
    for f in fs[:WARMUP]: step(s,f)
    if rebuild is not None:
        s=rebuild(s); fs=frames(s,case)
    profile=cProfile.Profile()
    with profile:
        for f in fs[WARMUP:]: step(s,f)
    counts=dict.fromkeys(('canonical_sha256','aggregate_actuators','compile_runtime_ship_parameters',
        'verify_derived_ship_snapshot_fingerprint','_parse_exact_timing_capability','_allocate_power','_manual_staffing'),0)
    for (file,line,name),(_,calls,*_) in pstats.Stats(profile).stats.items():
        if name in counts: counts[name]+=calls
        if name=='parse':
            assert Path(file).name in ('高天荒野舰艇定向推进控制桥.py','高天荒野舰艇推进通道合同.py')
    assert not any(v for k,v in counts.items() if k not in ('_allocate_power','_manual_staffing'))
    if case['name']=='steady': assert counts['_allocate_power']==counts['_manual_staffing']==0
    baseline.write_json(out/f"{case['name']}-mechanisms.json",counts)
    return dict(case=case['name'],compared_boundaries=4201,active_blue_steps=active,
        worst_mean_ms=max(r['mean_ms'] for r in results),worst_p95_ms=max(r['p95_ms'] for r in results),
        worst_p99_ms=max(r['p99_ms'] for r in results),maximum_ms=max(r['maximum_ms'] for r in results),mechanisms=counts)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--out',type=Path,required=True)
    out=parser.parse_args().out.resolve(); out.mkdir(parents=True,exist_ok=False)
    try:
        sources=baseline.freeze(out)
        m=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        m['build']='E2.2a synthetic manual/electric blue ship + observer: device/power/crew/phase/reset; no actual CIC or combat/UI'
        baseline.write_json(out/'manifest.json',m)
        s=build()
        baseline.write_json(out/'resources.json',[asdict(v) for v in s._seeds])
        cases=[tape(s._seeds[0],name) for name in ('steady','maneuvers','resource_bursts')]
        baseline.write_json(out/'inputs.json',cases)
        results=[]
        for case in cases:
            results.append(verify(case,out)); print(results[-1],flush=True)
        assert sources==baseline.source_inventory()
        baseline.write_json(out/'result.json',dict(status='PASS',stage='E2.2a',full_E2='NOT_PASSED',product_realtime='NOT_PASSED',
            workloads=results,warmup_steps=WARMUP,measured_steps=MEASURED,repeats=3,
            scope='Two ships, synthetic manual/electric blue ship. Includes domain operations, phase allocation, latching/reset and flight. Excludes oracle/profile/setup, actual CIC/remote/lifecycle, repair, combat, UI, saves and long run.',
            evidence_sha256={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out/'failure.json',dict(status='FAIL',error=repr(error))); raise


if __name__=='__main__': main()
