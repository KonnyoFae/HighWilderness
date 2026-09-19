"""Read-only saved designs; all battle state lives in an isolated directory."""
import cProfile
from dataclasses import replace
import json
from pathlib import Path
import pstats
import sqlite3
import statistics
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).parent
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_encounter as encounter
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from 高天荒野舰艇定向推进控制桥 import directional_control, ChannelPropulsionCommand

index=ResourceIndex(ROOT)
with sqlite3.connect('file:'+str(ROOT/'.local/tactical/settlements.sqlite3').replace('\\','/')+'?mode=ro',uri=True) as db:
    association=json.loads(db.execute('SELECT payload FROM tactical_encounters ORDER BY rowid DESC LIMIT 1').fetchone()[0])
    request=association['request'];ships=[]
    for side in request['sides']:
        for member in side['ships']:
            identity=member['instance_id']
            archive=json.loads(db.execute('SELECT payload FROM preparation_designs WHERE id=?',(identity,)).fetchone()[0])
            record=json.loads(db.execute('SELECT payload FROM ships WHERE id=?',(identity,)).fetchone()[0])
            design=bp.restore_design(archive,index)
            ships.append((side,member,design,bp.validate_record(record,design)))
service=RealtimeViewService('backend.diagnosis',settlement_dir=OUT/'isolated-store')
template,scenario,_=service._template()

def build(distance,speed,fire,accelerate=False):
    b,geom,_=encounter.build(request,ships,template,scenario)
    world=b.session.world;changed=[]
    for n,s in enumerate(world.ships):
        own=n==b._direct_index
        m=replace(s.motion,position_world_m=replace(s.motion.position_world_m,x=0,y=0 if own else distance),
            velocity_world_mps=replace(s.motion.velocity_world_mps,x=0,y=speed if own else 0))
        changed.append(replace(s,motion=m))
    b.session._world=replace(world,ships=tuple(changed))
    if not fire:b.states=tuple(replace(s,target_policy='hold',target=None) for s in b.states)
    return b,geom

def stats(times):
    v=sorted(times)
    return dict(mean_ms=statistics.mean(v)*1000,p95_ms=v[int((len(v)-1)*.95)]*1000,max_ms=max(v)*1000)

if __name__ == "__main__":
    rows=[]
    for distance,speed,fire,accelerate in [(20000,0,False,False),(20000,100,False,False),(20000,100,False,True),
            (16000,0,True,False),(16000,100,True,False),(12000,0,True,False),(12000,100,True,False),
            (8000,0,True,False),(8000,100,True,False),(8000,100,True,True)]:
        b,geom=build(distance,speed,fire)
        service._attach(b,geom)
        control=directional_control((ChannelPropulsionCommand('translation.forward','full',None),)) if accelerate else directional_control()
        def step():
            service.scheduler._stepper(control)
            service.scheduler._committed=b.session.world
        for _ in range(30):step()
        times=[];publications=[];reads=[];peak=0
        for i in range(120):
            start=perf_counter();step();times.append(perf_counter()-start)
            peak=max(peak,len(b.projectiles))
            if i%4==0:
                start=perf_counter();service.publish();publications.append(perf_counter()-start)
                start=perf_counter();service.read(service.digest);reads.append(perf_counter()-start)
            if b.ending:break
        row=dict(distance_m=distance,initial_speed_mps=speed,auto_fire=fire,accelerating=accelerate,
            shots=sum(s.shots for s in b.states),peak_projectiles=peak,step=stats(times),publish=stats(publications),read=stats(reads),
            weapons=[dict(ship=b.session.world.ships[g.ship_index].ship_id,id=g.module_id,status=s.status,shots=s.shots) for g,s in zip(b.guns,b.states)])
        rows.append(row);print(json.dumps({k:v for k,v in row.items() if k!='weapons'}),flush=True)

    b,geom=build(12000,100,True)
    for _ in range(30):b.step(directional_control())
    prof=cProfile.Profile();prof.enable()
    for _ in range(60):b.step(directional_control())
    prof.disable();prof.dump_stats(str(OUT/'saved-scene.prof'))
    functions=[]
    for (file,line,name),(primitive,calls,own,total,_) in pstats.Stats(prof).stats.items():
        functions.append(dict(file=file,line=line,name=name,calls=calls,self_s=own,cumulative_s=total))
    functions.sort(key=lambda r:r['cumulative_s'],reverse=True)
    (OUT/'saved-scene-result.json').write_text(json.dumps(dict(scope='Read-only latest local saved encounter designs and inventories. Isolated battle with controlled separation and starting speed. 30 warmup +120 measured steps (2s); publication/read every4steps. No actual browser/host queue; this is cost isolation, not reproduction of the live pause.',rows=rows,profile_top=functions[:60]),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(functions[:20]),flush=True)
