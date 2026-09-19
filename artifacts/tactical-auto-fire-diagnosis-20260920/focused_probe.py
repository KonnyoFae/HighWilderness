"""Real scheduler reproduction and isolated counterfactual cost checks."""
from dataclasses import replace
import json
from time import perf_counter,sleep
import saved_scene_probe as fixture
from backend.high_wilderness_sidecar import tactical_targeting as targeting

original_solution=targeting.solution

rows=[]
for variant in ('normal','empty_guns_hold'):
    b,geom=fixture.build(16000,100,True)
    if variant=='empty_guns_hold':
        # Diagnostic only: skip aiming on empty guns in this isolated battle.
        # Production behavior is unchanged. Keep defense observation intact.
        b.states=tuple(replace(s,target_policy='hold',target=None) if
            next(w for w in b.inventory.inventories[g.ship_index]._value['weapons'] if w['module_id']==g.module_id)['ready_rounds']==0
            else s for g,s in zip(b.guns,b.states))
    service=fixture.service;service._attach(b,geom);times=[];components=[]
    costs=dict(aim_seconds=0.,aim_calls=0,defense_seconds=0.,defense_calls=0)
    def solution(*args,**kwargs):
        begin=perf_counter();result=original_solution(*args,**kwargs)
        costs['aim_seconds']+=perf_counter()-begin;costs['aim_calls']+=1
        return result
    targeting.solution=solution
    original_observe=b.point_defense.observe
    def observe(*args,**kwargs):
        begin=perf_counter();result=original_observe(*args,**kwargs)
        costs['defense_seconds']+=perf_counter()-begin;costs['defense_calls']+=1
        return result
    b.point_defense.observe=observe
    original=service.scheduler._stepper
    def step(*args,**kwargs):
        before=dict(costs)
        start=perf_counter();result=original(*args,**kwargs);times.append(perf_counter()-start)
        components.append({k:v-before[k] for k,v in costs.items()})
        return result
    service.scheduler._stepper=step
    service.scheduler.resume()
    start=perf_counter();last_read=start;peak=0
    while service.running and perf_counter()-start<5:
        service.tick();peak=max(peak,len(b.projectiles))
        now=perf_counter()
        if now-last_read>=1/15:
            service.last_read=service.clock();service.read(service.digest);last_read=now
        if not service.work_pending:sleep(.002)
    row=dict(variant=variant,wall_seconds=perf_counter()-start,step_count=len(times),step=fixture.stats(times),
        reason=service.scheduler.status.pause_reason,running=service.running,debt_steps=service.scheduler.status.debt_quanta/1e9,
        shots=sum(s.shots for s in b.states),peak_projectiles=peak,components_total=costs,
        last30=dict(step=fixture.stats(times[-30:]),aim=fixture.stats([r['aim_seconds'] for r in components[-30:]]),
            defense=fixture.stats([r['defense_seconds'] for r in components[-30:]])))
    print(json.dumps(row),flush=True);rows.append(row)
    if service.running:service.pause('diagnostic_complete')
(fixture.OUT/'focused-result.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
