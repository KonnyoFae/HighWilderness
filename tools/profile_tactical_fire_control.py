"""Frozen saved-scene F1/F2 comparison; never runs against the user's live battle.

Capture once with --capture-store .local/tactical/settlements.sqlite3. Subsequent
runs restore only fixture.json. Baseline patches are process-local test oracles.
"""
import argparse
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_encounter as encounter
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics, tactical_targeting as targeting
from backend.high_wilderness_sidecar import tactical_point_defense as defense, tactical_drag_prediction as drag
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.fire_control_reference import time_to_distance as reference, UncachedSolutions
from tools.fire_control_f2_reference import Runtime as F2Runtime
from 高天荒野舰艇定向推进控制桥 import directional_control, ChannelPropulsionCommand


def capture(path):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        request = json.loads(db.execute('SELECT payload FROM tactical_encounters ORDER BY rowid DESC LIMIT 1').fetchone()[0])['request']
        rows = []
        for side in request['sides']:
            for member in side['ships']:
                identity = member['instance_id']
                archive = json.loads(db.execute('SELECT payload FROM preparation_designs WHERE id=?',(identity,)).fetchone()[0])
                record = json.loads(db.execute('SELECT payload FROM ships WHERE id=?',(identity,)).fetchone()[0])
                rows.append(dict(side=side,member=member,archive=archive,record=record))
    return dict(request=request,ships=rows)


def stats(values):
    v = sorted(values)
    if not v:return None
    return dict(mean_ms=statistics.mean(v)*1000,p50_ms=v[int((len(v)-1)*.5)]*1000,
                p95_ms=v[int((len(v)-1)*.95)]*1000,p99_ms=v[int((len(v)-1)*.99)]*1000,max_ms=v[-1]*1000)


def build(fixture,index,service,speed,stock,distance=16000):
    rows = []
    for row in fixture['ships']:
        design = bp.restore_design(row['archive'],index)
        record = json.loads(json.dumps(row['record']))
        if stock != 'saved':
            weapons = {w['module_id']:w for w in design.resources.definition()['weapons']}
            for weapon in record['state']['weapons']:
                definition = weapons[weapon['module_id']]
                weapon.update(ready_rounds=definition['ready_capacity'] if stock=='loaded' else 0,
                              recipe_id=weapon['recipe_id'] or definition['recipe_ids'][0],reload=None,cooldown_steps=0)
            for magazine in record['state']['magazines']:
                magazine['quantity'] = 0
        rows.append((row['side'],row['member'],design,bp.validate_record(record,design)))
    template,scenario,_ = service._template()
    battle,geometry,_ = encounter.build(fixture['request'],rows,template,scenario)
    world = battle.session.world; ships = []
    for n,ship in enumerate(world.ships):
        own = n == battle._direct_index
        ships.append(replace(ship,motion=replace(ship.motion,
            position_world_m=replace(ship.motion.position_world_m,x=0,y=0 if own else distance),
            velocity_world_mps=replace(ship.motion.velocity_world_mps,x=0,y=speed if own else 0))))
    battle.session._world = replace(world,ships=tuple(ships))
    return battle,geometry


def run(fixture,index,case,variant,steps,*,runtime='f2',prediction='current'):
    with TemporaryDirectory(prefix='gtw-fire-control-') as directory, ExitStack() as stack:
        # Every variant starts with the same empty process-wide prediction caches.
        ballistics.reference_range.cache_clear();defense.trajectory.cache_clear();drag.bands.cache_clear()
        service = RealtimeViewService('backend.fire-control-benchmark',settlement_dir=directory)
        battle,geometry = build(fixture,index,service,case['speed'],case['stock'],case.get('distance',16000))
        if runtime=='f2':battle.fire_control=F2Runtime()
        if prediction=='reference':
            from tools.point_defense_reference import Plan
            stack.enter_context(patch.object(battle.point_defense,'begin',lambda world:Plan(battle.point_defense,world)))
        service._attach(battle,geometry)
        if variant != 'optimized':stack.enter_context(patch.object(ballistics,'time_to_distance',reference))
        if variant == 'baseline':stack.enter_context(patch.object(targeting,'StepSolutions',UncachedSolutions))
        costs = dict(solution_seconds=0.,solution_calls=0,defense_seconds=0.,inverse_seconds=0.,inverse_calls=0)
        def meter(owner,name,seconds,calls=None):
            original = getattr(owner,name)
            def timed(*args,**kwargs):
                start = perf_counter()
                result = original(*args,**kwargs)
                costs[seconds] += perf_counter()-start
                if calls:costs[calls] += 1
                return result
            stack.enter_context(patch.object(owner,name,timed))
        meter(targeting,'solution','solution_seconds','solution_calls')
        meter(ballistics,'time_to_distance','inverse_seconds','inverse_calls')
        meter(battle.point_defense,'observe','defense_seconds')
        control = directional_control((ChannelPropulsionCommand('translation.forward','full',None),)) if case['accelerate'] else directional_control()
        durations=[]; components=[]; publications=[]; reads=[]; trace=[]; hits=0; requests=0; peak=0;peak_bytes=0
        work_totals={};work_max={};shot_events={};prediction_totals={};prediction_max={};threat_trace=[]
        for n in range(steps):
            before=dict(costs); start=perf_counter()
            shot_counts=tuple(s.shots for s in battle.states)
            service.scheduler._stepper(control)
            service.scheduler._committed=battle.session.world
            durations.append(perf_counter()-start)
            components.append({k:v-before[k] for k,v in costs.items()})
            metrics=battle.fire_control_metrics; hits+=metrics['cache_hits']; requests+=metrics['requests']
            for k,v in battle.point_defense.prediction_metrics.items():
                prediction_totals[k]=prediction_totals.get(k,0)+v;prediction_max[k]=max(prediction_max.get(k,0),v)
            threat_trace.append(tuple((r['observer'],r['projectile_id'],r['ship_id'],r['remaining_s']) for r in battle.point_defense.threats))
            for k,v in metrics.items():
                if k=='step':continue
                work_totals[k]=work_totals.get(k,0)+v;work_max[k]=max(work_max.get(k,0),v)
            for i,(gun,state,count) in enumerate(zip(battle.guns,battle.states,shot_counts)):
                if state.shots>count:shot_events.setdefault(str(i),[]).append(dict(step=n+1,angle=state.angle,target=state.target))
            peak=max(peak,len(battle.projectiles))
            trace.append([(s.target,s.status,s.shots,s.quality,s.angle) for s in battle.states])
            if n%4==0:
                start=perf_counter();service.publish();publications.append(perf_counter()-start)
                start=perf_counter();packet=service.read(service.digest);reads.append(perf_counter()-start)
                peak_bytes=max(peak_bytes,len(json.dumps(packet,ensure_ascii=False).encode('utf-8')))
            if battle.ending:break
        # Timings use all steps and a separately reported warmed tail; no dropped
        # physical steps and no altered debt threshold. This is not a realtime run.
        return dict(case=case,variant=variant,steps=len(durations),guns=len(battle.guns),
            guns_per_ship=[sum(g.ship_index==i for g in battle.guns) for i in range(len(battle.session.world.ships))],
            shots=sum(s.shots for s in battle.states),peak_projectiles=peak,peak_response_bytes=peak_bytes,
            step=stats(durations),warm_step=stats(durations[30:]),
            aim=stats([v['solution_seconds'] for v in components]),
            inverse=stats([v['inverse_seconds'] for v in components]),
            defense=stats([v['defense_seconds'] for v in components]),publish=stats(publications),read=stats(reads),
            costs=costs,requests=requests,cache_hits=hits,solution_age_steps=work_max.get('solution_age_steps',0),
            work_totals=work_totals,work_max=work_max,shot_events=shot_events,
            prediction_totals=prediction_totals,prediction_max=prediction_max,threat_trace=threat_trace,
            trace=trace,inventory=[inv._value for inv in battle.inventory.inventories])


def compare(reference,current):
    mismatches=[]; angle=0.
    for step,(old,new) in enumerate(zip(reference['trace'],current['trace']),1):
        for gun,(a,b) in enumerate(zip(old,new)):
            if a[:4]!=b[:4]:mismatches.append(dict(step=step,gun=gun,before=a[:4],after=b[:4]))
            angle=max(angle,abs(a[4]-b[4]))
    return dict(decision_mismatches=len(mismatches),examples=mismatches[:8],maximum_angle_difference_rad=angle,
                same_inventory=reference['inventory']==current['inventory'],same_step_count=reference['steps']==current['steps'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--capture-store',type=Path);parser.add_argument('--steps',type=int,default=240)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    fixture_path=args.out/'fixture.json'
    if args.capture_store:
        if fixture_path.exists():raise ValueError('Frozen fixture already exists; use a new output directory')
        fixture_path.write_text(json.dumps(capture(args.capture_store),ensure_ascii=False,indent=2),encoding='utf-8')
    fixture=json.loads(fixture_path.read_text(encoding='utf-8'));index=ResourceIndex(ROOT)
    cases=[dict(name='stationary_saved',speed=0,accelerate=False,stock='saved'),
           dict(name='stationary_firing',speed=0,accelerate=False,stock='saved',distance=12000),
           dict(name='moving_saved',speed=100,accelerate=False,stock='saved'),
           dict(name='accelerating_saved',speed=100,accelerate=True,stock='saved'),
           dict(name='moving_loaded',speed=100,accelerate=False,stock='loaded'),
           dict(name='moving_empty',speed=100,accelerate=False,stock='empty')]
    rows=[]
    for case in cases:
        baseline=run(fixture,index,case,'baseline',args.steps)
        variants=('cache_only','optimized') if case['name']=='moving_saved' else ('optimized',)
        for variant in variants:
            row=run(fixture,index,case,variant,args.steps);row['comparison']=compare(baseline,row)
            row.pop('trace');row.pop('inventory');rows.append(row)
            print(json.dumps({k:v for k,v in row.items() if k in ('case','variant','warm_step','aim','defense','comparison')},ensure_ascii=False),flush=True)
        baseline.pop('trace');baseline.pop('inventory');rows.append(baseline)
    paths=[ROOT/'backend/high_wilderness_sidecar'/f for f in ('tactical_gunnery.py','tactical_targeting.py','tactical_ballistics.py','tactical_drag_prediction.py','tactical_point_defense.py')]
    report=dict(scope='F1/F2, frozen two-ship 16 km scene plus 12 km stationary firing; same decision frequency; not final F or fleet-scale acceptance',
                fixture_sha256=sha256(fixture_path.read_bytes()).hexdigest(),python=sys.version,platform=platform.platform(),
                source_sha256={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in paths},runs=rows)
    (args.out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        check = row.get('comparison')
        if check and (check['decision_mismatches'] or not check['same_inventory'] or not check['same_step_count']
                      or check['maximum_angle_difference_rad'] > 1e-6):
            raise AssertionError(f"Fire-control comparison failed: {row['case']['name']} / {row['variant']}")


if __name__=='__main__':main()
