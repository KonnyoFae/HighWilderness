"""Frozen-scene F4 comparison plus a real close-defense salvo workload."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
from time import perf_counter
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.profile_tactical_fire_control import run,stats
from tools.point_defense_reference import Plan as ReferencePlan
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar import tactical_point_defense as pd


def compare(old,new):
    missing=added=0;error=0.
    for before,after in zip(old['threat_trace'],new['threat_trace']):
        a={r[:3]:r[3] for r in before};b={r[:3]:r[3] for r in after}
        missing+=len(a.keys()-b.keys());added+=len(b.keys()-a.keys())
        error=max(error,max((abs(a[k]-b[k]) for k in a.keys()&b.keys()),default=0.))
    return dict(same_inventory=old['inventory']==new['inventory'],same_shots=old['shot_events']==new['shot_events'],
        missing_threats=missing,added_threats=added,max_arrival_difference_s=error)


def salvo(legacy,count=12):
    from tools.test_tactical_interception import PointDefenseTests
    PointDefenseTests.setUpClass();fixture=PointDefenseTests();b=fixture.battle()
    for i in range(count):fixture.incoming(b,distance=1000+i*100,speed=900)
    if legacy:b.point_defense.begin=lambda world:ReferencePlan(b.point_defense,world)
    pd.trajectory.cache_clear();durations=[];shots=[];threats=[];events=[];hits=[];computed=rolled=0;cost=dict(observe=0.,choose=0.,calls=0)
    with ExitStack() as stack:
        for name in ('observe','choose'):
            original=getattr(b.point_defense,name)
            def timed(*args,_name=name,_fn=original,**kwargs):
                start=perf_counter();result=_fn(*args,**kwargs);cost[_name]+=perf_counter()-start
                if _name=='choose':cost['calls']+=1
                return result
            stack.enter_context(patch.object(b.point_defense,name,timed))
        for step in range(180):
            before=tuple(s.shots for s in b.states);start=perf_counter();b.step();durations.append(perf_counter()-start)
            shots.extend((step,n) for n,s in enumerate(b.states) if s.shots>before[n])
            events.extend(e for e in b.point_defense.recent if e['step']==b.session.world.fixed_step)
            hits.extend(e for e in b.damage_state.recent if e['step']==b.session.world.fixed_step)
            computed+=b.point_defense.prediction_metrics['computed'];rolled+=b.point_defense.prediction_metrics['rolled']
            threats.append(tuple((r['observer'],r['projectile_id'],r['ship_id'],r['remaining_s']) for r in b.point_defense.threats))
    return dict(step=stats(durations),cost=cost,computed=computed,rolled=rolled,shot_events=shots,
        interceptions=events,ship_hits=hits,intercepted=b.point_defense.kills,
        # The fixture generates a fresh identity for its target ship per build;
        # all actual resource fields remain in the comparison.
        inventory=[{k:v for k,v in i._value.items() if k!='instance_id'} for i in b.inventory.inventories],threat_trace=threats)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'artifacts/tactical-fire-control-f1f2-20260920/fixture.json'
    fixture=json.loads(source.read_text(encoding='utf-8'));index=ResourceIndex(ROOT);rows=[]
    for case in (dict(name='stationary_firing',speed=0,accelerate=False,stock='saved',distance=12000),
                 dict(name='moving_saved',speed=100,accelerate=False,stock='saved'),
                 dict(name='accelerating_saved',speed=100,accelerate=True,stock='saved'),
                 dict(name='moving_loaded',speed=100,accelerate=False,stock='loaded'),
                 dict(name='moving_empty',speed=100,accelerate=False,stock='empty')):
        old=run(fixture,index,case,'optimized',240,runtime='f3',prediction='reference')
        new=run(fixture,index,case,'optimized',240,runtime='f3')
        comparison=compare(old,new)
        for row in (old,new):
            for field in ('trace','inventory','threat_trace'):row.pop(field)
        rows.append(dict(case=case,reference=old,f4=new,comparison=comparison))
        print(json.dumps(dict(case=case['name'],comparison=comparison,old_step=old['warm_step'],new_step=new['warm_step'],
            old_defense=old['defense']['mean_ms'],new_defense=new['defense']['mean_ms'],work=new['prediction_totals']),ensure_ascii=False),flush=True)
    old,new=salvo(True),salvo(False);comparison=compare(old,new)
    comparison.update(same_interceptions=old['interceptions']==new['interceptions'],same_ship_hits=old['ship_hits']==new['ship_hits'])
    for row in (old,new):
        row.pop('inventory');row.pop('threat_trace')
    print(json.dumps(dict(salvo=comparison,reference={k:old[k] for k in ('step','cost','computed')},
        f4={k:new[k] for k in ('step','cost','computed','rolled')}),ensure_ascii=False),flush=True)
    paths=[ROOT/'backend/high_wilderness_sidecar'/name for name in ('tactical_point_defense.py','tactical_defense_prediction.py','tactical_gunnery.py','tactical_ew.py','tactical_ballistics.py','projectile_observation.py')]
    paths.extend(ROOT/'tools'/name for name in ('point_defense_reference.py','profile_tactical_fire_control.py','profile_tactical_defense_prediction.py'))
    report=dict(scope='F4 rolling hull threats; existing F3 ordinary fire control in both; not F5 sustained realtime acceptance',
        python=sys.version,platform=platform.platform(),fixture_sha256=sha256(source.read_bytes()).hexdigest(),
        source_sha256={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in paths},
        runs=rows,salvo=dict(reference=old,f4=new,comparison=comparison))
    (args.out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in (*rows,report['salvo']):
        c=row['comparison']
        assert c['same_inventory'] and c['same_shots'] and not c['missing_threats'],c
        assert c.get('same_interceptions',True) and c.get('same_ship_hits',True),c


if __name__=='__main__':main()
