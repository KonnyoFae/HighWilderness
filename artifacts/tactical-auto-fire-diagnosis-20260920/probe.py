"""Isolated diagnostic, not an acceptance test or a change to game rules."""
import cProfile
from dataclasses import replace
import json
from pathlib import Path
import pstats
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.test_tactical_targeting import TargetingTests
from backend.high_wilderness_sidecar.simplified_flight import directional_control

OUT = Path(__file__).parent
TargetingTests.setUpClass()
fixture = TargetingTests()

def battle(speed, firing, distance=3000):
    b = fixture.battle()
    fixture.move(b, 0, 0, 0)
    fixture.move(b, 1, 0, distance)
    w = b.session.world
    # A declared initial sideways velocity isolates moving firing solutions
    # from engine startup. Target stays stationary, all simulation is real.
    own = w.ships[0]
    b.session._world = replace(w, ships=(replace(own, motion=replace(own.motion,
        velocity_world_mps=replace(own.motion.velocity_world_mps, x=speed, y=0))), w.ships[1]))
    if not firing:
        b.states = tuple(replace(s, target_policy='hold', target=None) for s in b.states)
    for inv in b.inventory.inventories:
        for gun in inv._value['weapons']:
            gun['ready_rounds'] = inv._weapons[gun['module_id']]['ready_capacity']
    return b

def quantiles(values):
    ordered = sorted(values)
    return dict(mean_ms=statistics.mean(values)*1000,
        p50_ms=ordered[len(ordered)//2]*1000,
        p95_ms=ordered[int((len(ordered)-1)*.95)]*1000, max_ms=max(values)*1000)

rows=[]
for distance in (1000, 3000):
    for speed, firing in ((0,False),(80,False),(0,True),(80,True)):
        b=battle(speed,firing,distance)
        for _ in range(60): b.step(directional_control())
        elapsed=[];views=[];peak=0
        for i in range(240):
            start=perf_counter();b.step(directional_control());elapsed.append(perf_counter()-start)
            peak=max(peak,len(b.projectiles))
            if i%4==0:
                start=perf_counter();json.dumps(b.view(),ensure_ascii=False);views.append(perf_counter()-start)
            if b.ending: break
        row=dict(distance_m=distance,initial_speed_mps=speed,auto_fire=firing,
            steps=len(elapsed),shots=sum(s.shots for s in b.states),peak_projectiles=peak,
            final_statuses=[s.status for s in b.states],step=quantiles(elapsed),gunnery_view=quantiles(views))
        rows.append(row);print(json.dumps(row),flush=True)

# Function/call attribution only: profiled timings are NOT wall-time evidence.
b=battle(80,True,3000)
for _ in range(120):b.step(directional_control())
prof=cProfile.Profile();prof.enable()
for _ in range(60):b.step(directional_control())
prof.disable();prof.dump_stats(str(OUT/'automatic-fire.prof'))
stats=pstats.Stats(prof)
functions=[]
for (file,line,name),(primitive,calls,own,total,_) in stats.stats.items():
    functions.append(dict(file=file,line=line,name=name,calls=calls,self_s=own,cumulative_s=total))
functions.sort(key=lambda r:r['cumulative_s'],reverse=True)
(OUT/'result.json').write_text(json.dumps(dict(scope='Two fixture ships, three 30mm guns and one 75mm gun on own ship; enemy fire disabled; 60 warmup + 240 measured fixed steps. Motion cases start with 80m/s sideways velocity, not engine acceleration. No GUI or realtime scheduler. Gunnery view only, not full bridge.',rows=rows,profile_top=functions[:50]),ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(functions[:20]),flush=True)
