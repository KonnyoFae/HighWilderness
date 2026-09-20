"""F2 versus F3 on the frozen F1/F2 ships; scheduling changes are reported."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.profile_tactical_fire_control import run
from backend.high_wilderness_sidecar.sessions import ResourceIndex


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
        old=run(fixture,index,case,'optimized',240,runtime='f2')
        new=run(fixture,index,case,'optimized',240,runtime='f3')
        changes={}
        for gun in old['shot_events'].keys()|new['shot_events'].keys():
            a=old['shot_events'].get(gun,[]);b=new['shot_events'].get(gun,[])
            changes[gun]=dict(f2=[s['step'] for s in a],f3=[s['step'] for s in b],
                first_shot_delta_steps=b[0]['step']-a[0]['step'] if a and b else None)
        comparison=dict(shots_f2=old['shots'],shots_f3=new['shots'],shot_steps=changes,
            same_inventory=old['inventory']==new['inventory'],
            precise_solves_f2=old['costs']['solution_calls'],precise_solves_f3=new['costs']['solution_calls'])
        for row,name in ((old,'f2'),(new,'f3')):
            row['variant']=name;row.pop('trace');row.pop('inventory')
        rows.append(dict(case=case,f2=old,f3=new,comparison=comparison))
        print(json.dumps(dict(case=case,comparison=comparison,step_f2=old['warm_step'],step_f3=new['warm_step'],
            work_max=new['work_max']),ensure_ascii=False),flush=True)
    followup=run(fixture,index,dict(name='accelerating_followup',speed=100,accelerate=True,stock='saved'),
                 'optimized',250,runtime='f3')
    followup.pop('trace');followup.pop('inventory')
    paths=[ROOT/'backend/high_wilderness_sidecar'/f for f in ('tactical_gunnery.py','tactical_targeting.py','tactical_fire_control.py','tactical_ballistics.py','tactical_drag_prediction.py','tactical_point_defense.py')]
    paths.extend(ROOT/'tools'/f for f in ('profile_tactical_fire_control.py','profile_tactical_fire_control_f3.py','fire_control_f2_reference.py'))
    hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in paths}
    (args.out/'accelerating-followup.json').write_text(json.dumps(dict(followup,source_sha256=hashes),
        ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out/'result.json').write_text(json.dumps(dict(scope='F3 ordinary guns; F4 point-defense prediction unchanged; fixed-step comparisons, not sustained realtime acceptance',
        fixture_sha256=sha256(source.read_bytes()).hexdigest(),python=sys.version,platform=platform.platform(),source_sha256=hashes,
        runs=rows),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
