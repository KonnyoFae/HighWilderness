"""7a entry-only 9v9 calculation; not full-battle/7b performance acceptance."""
from dataclasses import replace
from pathlib import Path
from time import perf_counter
import json
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_contact_start as contact, tactical_fleet
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.simplified_flight import build_sample_session
from tools.test_tactical_observation import document


def main():
    root=Path(__file__).resolve().parents[1];index=ResourceIndex(root);policy=load_current(root)
    profile=build_sample_session(root,with_command=True)._profile
    rows=[];doc,dependencies=document(index,'infrared')
    next(m for m in doc['outfit']['modules'] if m['id']=='cic')['prototype']=dict(id='gtw.module.scic.advanced',version=1)
    next(c for c in dependencies['crew'] if c['crew_type']=='officer')['count']=4
    started=perf_counter()
    for side in ('enemy','player'):
        fleet=dict(side_id='side.'+side,fleet_id='fleet.contact.'+side,flagship_instance_id=f'instance.{side}.0')
        for n in range(9):
            design=bp.compile_design(doc,index,dependencies,policy,ship_id=f'ship.{side}.{n}')
            record=bp.new_record(design,f'instance.{side}.{n}')
            member=dict(instance_id=record['state']['instance_id'],revision=record['state']['revision'],deployment=dict(
                x_m=n*150.,y_m=(n%3)*100.,heading_rad=3.141592653589793 if side=='enemy' else 0.))
            rows.append((fleet,member,design,record))
        tactical_fleet.validate([(d,r) for s,_,d,r in rows if s['side_id']==fleet['side_id']],fleet['flagship_instance_id'])
    compile_s=perf_counter()-started
    started=perf_counter();state=contact.ContactState(rows,profile);build_s=perf_counter()-started
    reports=[]
    for layer in ('upper','cloud','rain'):
        state.session._world=replace(state.session.world,ships=tuple(replace(s,motion=replace(s.motion,height_layer=layer)) for s in state.session.world.ships))
        samples=[];results=[]
        for _ in range(3):
            started=perf_counter();result=contact.solve(state,'ship.player.0');samples.append(perf_counter()-started);results.append(result)
        assert all(r==results[0] for r in results) and result['status']=='ready'
        assert result['distance_m']<=result['threshold_m']
        reports.append(dict(layer=layer,seconds=samples,result=result))
    out=root/'artifacts/tactical-7a-20260921/contact-9v9.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    report=dict(scope='Entry-only legal 9v9 with infrared, finite saved state; excludes sustained battle/render performance',
        ships=len(rows),compile_s=compile_s,state_build_s=build_s,cases=reports)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
