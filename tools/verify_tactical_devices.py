"""E2.1 frozen domain-producer evidence; no actual projectile or repair simulation."""
import argparse
import cProfile
from dataclasses import asdict
import gzip
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import tactical_realtime_baseline as baseline
from tools.verify_simplified_flight_gate import summary, make_case, WARMUP, MEASURED
from tools.verify_simplified_flight import observe
from backend.high_wilderness_sidecar import simplified_flight as flight
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation, EPS


def build():
    return flight.build_sample_session(ROOT, with_devices=True, allow_test_device_rebuild=True)


def inputs(session, name):
    case = make_case(name)
    case['availability'] = []
    case['scope'] = 'E2.1 two technical ships, one controlled; durability producer enabled, other domains pending'
    operations, sequences = [], {}
    if name == 'damage_bursts':
        for start in range(WARMUP, WARMUP + MEASURED, 300):
            for offset, phase, kind, scale in ((30, 'closing', 'damage', .25),
                    (90, 'closing', 'damage', 1), (150, 'opening', 'test_rebuild', 0)):
                batch = []
                for seed in session._seeds:
                    modules = {m.instance_id: m for m in seed.devices.modules}
                    for engine in seed.contributions.engines:
                        key = (seed.contributions.ship_id, engine.instance_id)
                        sequences[key] = sequences.get(key, 0) + 1
                        batch.append(dict(step=start+offset, phase=phase, ship_id=key[0], module_id=key[1],
                            sequence=sequences[key], kind=kind, amount=modules[key[1]].maximum_durability_points*scale))
                operations.extend(batch)
                operations.append(dict(batch[0]))
    case['device_operations'] = operations
    return case


def frames(session, case):
    result = [[None, []] for _ in range(WARMUP + MEASURED)]
    for c in case['controls']:
        result[c['step']][0] = c['value']
    for op in case['device_operations']:
        result[op['step']][1].append(DeviceOperation(session.world.epoch, op['ship_id'], op['module_id'],
            op['sequence'], op['kind'], op['amount'], op['step']+(op['phase']=='closing'), op['phase']))
    return tuple((c, tuple(ops)) for c, ops in result)


def ledger(session, health, seen, operations):
    for op in operations:
        key = (op.ship_id, op.module_id)
        if seen.get(key) == op.sequence:
            continue
        seen[key] = op.sequence
        initial, hp = health[key]
        health[key] = (initial, max(0, hp-op.amount) if op.kind == 'damage' else initial)
    for seed, ship in zip(session._seeds, session.world.ships):
        design = {m.instance_id:m for m in seed.devices.modules}
        for m, state in zip(seed.devices.modules, ship.devices.modules):
            assert state.durability_points == health[(ship.ship_id,m.instance_id)][1]
            assert state.sequence == seen.get((ship.ship_id,m.instance_id),0)
        totals, outputs = [0]*6, [0]*6
        for engine, slot in zip(seed.contributions.engines, ship.propulsion.engines):
            dead = health[(ship.ship_id,engine.instance_id)][1] <= EPS
            host_dead, host = False, design[engine.instance_id].host_instance_id
            while host is not None:
                host_dead |= health[(ship.ship_id,host)][1] <= EPS
                host = design[host].host_instance_id
            assert slot.blocked[:2] == (dead,host_dead)
            assert not any(slot.blocked[2:]), 'Unexpected non-device failure in workload'
            if dead or host_dead:
                assert slot.engine.actual_output_percent == 0 and slot.engine.next_transition_step is None
            for d,v in enumerate(engine.contribution_units):
                totals[d] += v * (not (dead or host_dead))
                outputs[d] += v * slot.engine.actual_output_percent
        assert ship.propulsion.available_units == tuple(totals)
        assert ship.propulsion.output_percent_units == tuple(outputs)
        assert ship.motion.hull_integrity_fraction > 0
        assert len(ship.propulsion.schedule) <= len(seed.contributions.engines)


def run_case(case, out):
    a,b = build(),build()
    assert a._seeds == b._seeds
    assert a.world.ships == b.world.ships
    health = {(s.contributions.ship_id,m.instance_id):(m.maximum_durability_points,hp)
        for s in a._seeds for m,hp in zip(s.devices.modules,s.devices.initial_durability_points)}
    seen,active = {},0
    ledger(a,health,seen,())
    with gzip.open(out/f"{case['name']}-checkpoints.jsonl.gz",'wt',encoding='utf-8') as f:
        f.write(json.dumps(observe(a),ensure_ascii=False,allow_nan=False)+'\n')
        for n,((ca,oa),(cb,ob)) in enumerate(zip(frames(a,case),frames(b,case))):
            a.step(ca,device_operations=oa)
            b.step(cb,device_operations=ob)
            assert a.world.ships == b.world.ships and a.last_result == b.last_result
            ledger(a,health,seen,oa)
            active += n>=WARMUP and any(a.world.ships[0].propulsion.output_percent_units)
            if (n+1)%60==0:
                f.write(json.dumps(observe(a),ensure_ascii=False,allow_nan=False)+'\n')
    assert active >= 500
    baseline.write_json(out/f"{case['name']}-resources.json",[asdict(s) for s in a._seeds])
    runs=[]
    for repeat in range(3):
        started=perf_counter()
        s=build()
        creation=perf_counter()-started
        tape=frames(s,case)
        for c,ops in tape[:WARMUP]: s.step(c,device_operations=ops)
        samples,event_samples=[],[]
        started=perf_counter()
        for c,ops in tape[WARMUP:]:
            t=perf_counter()
            s.step(c,device_operations=ops)
            elapsed=(perf_counter()-t)*1000
            samples.append(elapsed)
            if ops: event_samples.append(elapsed)
        wall=perf_counter()-started
        assert s.world.ships==a.world.ships and s.last_result==a.last_result
        result=dict(case=case['name'],repeat=repeat+1,creation_s=creation,**summary(samples,wall),samples_ms=samples,
            event_step_count=len(event_samples),event_mean_ms=sum(event_samples)/len(event_samples) if event_samples else None,
            event_max_ms=max(event_samples,default=None))
        runs.append(result)
        baseline.write_json(out/f"{case['name']}-timing.json",runs)
        assert result['passed'], 'Scoped E2.1 throughput failed'
    s=build()
    tape=frames(s,case)
    for c,ops in tape[:WARMUP]: s.step(c,device_operations=ops)
    profile=cProfile.Profile()
    with profile:
        for c,ops in tape[WARMUP:]: s.step(c,device_operations=ops)
    forbidden={name:0 for name in ('canonical_sha256','aggregate_actuators','compile_runtime_ship_parameters',
        'verify_derived_ship_snapshot_fingerprint','_parse_exact_timing_capability')}
    calls=[]
    for (file,line,name),(_,count,own,cumulative,_) in pstats.Stats(profile).stats.items():
        if name in forbidden: forbidden[name]+=count
        if name=='parse':
            assert Path(file).name in ('高天荒野舰艇定向推进控制桥.py','高天荒野舰艇推进通道合同.py')
        if Path(file).name=='tactical_devices.py':
            calls.append(dict(name=name,line=line,calls=count,own_s=own,cumulative_s=cumulative))
    assert not any(forbidden.values())
    if case['name']!='damage_bursts':
        assert not any(c['name']=='reasons' for c in calls)
    baseline.write_json(out/f"{case['name']}-mechanisms.json",dict(forbidden_calls=forbidden,device_calls=calls))
    return dict(case=case['name'],compared_boundaries=4201,active_blue_steps=active,
        operation_count=len(case['device_operations']),worst_mean_ms=max(r['mean_ms'] for r in runs),
        worst_p95_ms=max(r['p95_ms'] for r in runs),worst_p99_ms=max(r['p99_ms'] for r in runs),
        maximum_ms=max(r['maximum_ms'] for r in runs))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    out=parser.parse_args().out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    try:
        sources=baseline.freeze(out)
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        manifest['build']='E2.1 device durability producer + simplified flight; other domains, combat/UI excluded'
        baseline.write_json(out/'manifest.json',manifest)
        sample=build()
        cases=[inputs(sample,name) for name in ('steady','maneuvers','damage_bursts')]
        baseline.write_json(out/'inputs.json',cases)
        results=[]
        for case in cases:
            results.append(run_case(case,out))
            print(results[-1],flush=True)
        assert sources==baseline.source_inventory(), 'Source changed during run'
        baseline.write_json(out/'result.json',dict(status='PASS',stage='E2.1',full_E2='NOT_PASSED',product_realtime='NOT_PASSED',
            workloads=results,warmup_steps=WARMUP,measured_steps=MEASURED,repeats=3,
            scope='Includes operation validation, durability writes, dependency events and flight. Excludes tape construction, oracle, profile, actual projectile/repair, power/crew/command producers, save/UI/long run.',
            evidence_sha256={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out/'failure.json',dict(status='FAIL',error=repr(error)))
        raise


if __name__=='__main__': main()
