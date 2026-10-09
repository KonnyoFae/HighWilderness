"""Frozen legal 7b fleets; no live saves, fabricated threats or runtime buffs."""
import argparse
from copy import deepcopy
import json
from math import pi
from pathlib import Path
from time import perf_counter

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, outfits
from backend.high_wilderness_sidecar import aviation_logistics as al, missile_logistics as ml
from backend.high_wilderness_sidecar import tactical_test_scene as scene, tactical_encounter as encounter, tactical_fleet
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.preparation_maintenance import supply_top_up
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
import 高天荒野舰艇人员舱容量 as housing

ROOT=Path(__file__).resolve().parents[1]
CASES={'guns-4v4':(4,4,False,False),'mixed-4v4':(4,4,True,False),
       'mixed-4v9':(4,9,True,False),'mixed-4v9-reversed':(4,9,True,True),
       'guns-9v9':(9,9,False,False),'mixed-9v9':(9,9,True,False)}


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def design(index,role,key,flagship):
    name={'defense':'进阶自动防御测试舰','missile':'电子对抗与复合制导测试舰',
          'e1':'航空设施测试舰','strike':'航空设施测试舰','gun':'阶段F常规有人战舰舾装'}[role]
    # Resolve resource identity rather than assuming a filename for legacy fixtures.
    source=next(ps.clone(s) for d,s in index.resources.values() if d['kind']=='OutfitPlan' and s['name'].startswith(name))
    source['name']=f'7b {key} · {role}';modules=source['modules']
    if flagship:next(m for m in modules if m['id']=='cic')['prototype']=dict(id='gtw.module.scic.advanced',version=1)
    if role in ('gun','e1','strike'):
        next(m for m in modules if m['id']=='fire_control')['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
        next(m for m in modules if m['id']=='sensor_upper_starboard')['prototype']=dict(id='gtw.module.sensor.5d.infrared' if role=='e1' else 'gtw.module.sensor.5d.radar',version=2)
        next(m for m in modules if m['id']=='weapon_upper_port')['prototype']=dict(id='gtw.module.gun.50mm',version=2)
        modules.append(dict(id='datalink.7b',prototype=dict(id='gtw.module.datalink.5d',version=1),placement=dict(kind='hosted',host_instance_id='fire_control')))
    if role=='gun':
        for i,anchor in enumerate(([-2,4],[2,-4])):
            modules.append(dict(id='gun.7b.'+str(i),prototype=dict(id='gtw.module.gun.50mm',version=2),
                placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=anchor,rotation_deg=0)))
    compiled=outfits.document(source,index).compile()
    deployment=dict(id='deployment.7b.v1',version=1,
        crew=[dict(crew_type=k,count=v) for k,v in housing.bounded_crew(compiled.instances,dict(compiled.standard_crew)).items()],
        fuel_units=sum(m.prototype.capability.to_dict()['fuel_capacity_units'] for m in compiled.instances if m.prototype.category=='lift_fuel_tank'),
        height_layer='upper',control_mode='crewed',active_remote_core_instance_id=None)
    return bp.compile_design(source,index,deployment,load_current(ROOT),ship_id='ship.7b.'+key)


def load_record(d,key,role,direction):
    r=bp.new_record(d,'instance.7b.'+key);inv=InventorySession(d.resources,ps.parse_instance(r['state'],d.resources))
    stock={'ammunition':100000,**{'cargo:'+g['id']:10000 for g in inv._definition['goods']}}
    for m in inv._value['magazines']:
        inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_ammunition',target=m['module_id'],quantity=1000)
    for w in inv._definition['weapons']:
        supply_top_up(inv,w['module_id'],next((k for k in w['recipe_ids'] if k.endswith('.ordinary')),w['recipe_ids'][0]),stock)
    for dc in inv._value['damage_controls']:supply_top_up(inv,dc['module_id'],None,stock)
    if role in ('defense','missile'):
        model='gtw.missile.5c.small.interceptor' if role=='defense' else 'gtw.missile.5c.small.rocket.radar_infrared'
        ml.prepare(inv,[dict(module_id='weapon_upper_port',kind='model',model_id=model),dict(module_id='weapon_upper_port',kind='load')],stock)
        ml.apply(inv,dict(module_id='weapon_upper_port',kind='auto_fire',enabled=True))
    launches=[];loads=[]
    if role in ('e1','strike'):
        spec=inv._definition['aviation'];h=next(f['module_id'] for f in spec['facilities'] if f['kind']=='aircraft_hangar');c=next(f['module_id'] for f in spec['facilities'] if f['kind']=='aircraft_catapult')
        models=['e1','e1'] if role=='e1' else ['f1','b1','f1']
        for i,model in enumerate(models):
            al.prepare(inv,[dict(kind='acquire',model_id='gtw.aircraft.'+model),dict(kind='pilots',module_id=h,quantity={'e1':2,'f1':1,'b1':3}[model])],stock)
            key_id=inv._value['aviation']['manifest']['aircraft'][-1]['id']
            payload={'p1':'self_defense','p2':'self_defense'} if model=='e1' else {'p1':'small_interceptor','p2':'small_missile'} if model=='f1' else {'p1':'large_missile','p2':'large_guided_bomb'}
            order=dict(kind='prepare',aircraft_id=key_id,module_id=h,loadout=payload)
            task=dict(kind='observe' if model=='e1' else 'air_patrol' if model=='f1' else 'sea_patrol',layer='upper',point_m=[0.,direction*(1000 if model=='e1' else 6000)])
            al.prepare(inv,[dict(kind='departure_task',aircraft_ids=[key_id],task=task)],stock)
            if i==len(models)-1:al.prepare(inv,[dict(kind='queue',order=order)],stock)
            else:
                al.prepare(inv,[order],stock)
                if i==0:
                    al.prepare(inv,[dict(kind='load',aircraft_id=key_id,module_id=c)],stock)
                    launches.append(dict(step=0,aircraft_id=key_id))
                else:
                    loads.append(dict(step=1,aircraft_id=key_id,module_id=c))
                    launches.append(dict(step=602,aircraft_id=key_id))
    r['state']=inv.snapshot().to_dict();r['state']['revision']=1
    return bp.validate_record(r,d),launches,loads


def freeze(out,case):
    if (out/'fixture.json').exists():raise ValueError('Frozen fixture already exists; choose a new output directory')
    left,right,mixed,reverse=CASES[case]
    server=SidecarServer('backend.7b.freeze',settlement_dir=out/'store');p=server.preparation;p.provision()
    layout=scene.fresh();layout.update(revision=1,distance_mode='automatic')
    items=[];orders=[]
    for side in layout['sides']:
        count=right if side['id']=='enemy' else left;direction=-1 if side['id']=='enemy' else 1
        roles=(['defense','missile','e1','strike','gun','gun','missile','defense','gun'] if mixed else ['gun']*9)[:count]
        for n,role in enumerate(roles):
            key=side['id']+'.'+str(n);d=design(server.editor.index,role,key,n==0);r,launches,loads=load_record(d,key,role,direction)
            p.store.create_ship(d,r['state']['instance_id'])
            with p.store.connection() as db:p.store._write_ship(db,r)
            side['ships'].append(dict(instance_id=r['state']['instance_id'],x_m=0. if n==0 else ((n+1)//2)*700.*(-1 if n%2 else 1),y_m=-direction*(n//3)*700.,heading_rad=0. if direction==1 else pi))
            if n==0:side['flagship_instance_id']=r['state']['instance_id']
            items.append(dict(role=role,side_id='side.'+side['id'],archive=d.archive(),record=r))
            orders.extend(dict(o,ship_id=r['ship_id'],kind=kind) for kind,values in (('launch',launches),('load',loads)) for o in values)
    scene.save(p,layout,0)
    started=perf_counter();packet=scene.packet(p);contact_s=perf_counter()-started
    assert packet['contact_start']['status']=='ready' and all(f['valid'] for f in packet['fleets'])
    auto=scene.encounter(p,1,'encounter.7b.'+case+'.auto',packet['contact_start']['input_sha256'])
    layout.update(revision=2,distance_mode='manual',distance_m=6000.);scene.save(p,layout,1)
    fixed=scene.encounter(p,2,'encounter.7b.'+case+'.fixed')
    if reverse:
        for req in (auto,fixed):req['player_side_id']='side.enemy'
    frozen=dict(interface='gaotian.tactical-benchmark/7b-v1',case=case,ships=items,requests=dict(automatic=auto,fixed=fixed),
        commands=sorted(orders,key=lambda o:(o['step'],o['ship_id'])),fleet_qualification=packet['fleets'],
        entry_measurement=dict(whole_scene_packet_s=contact_s,contact=packet['contact_start']),
        protocol=dict(fixed_steps=900,automatic_steps=300,publish_every_steps=4,initial_layer='upper',
            opponent_aviation='declared scripted launch/load through shared flight/logistics, no opponent AI claim',
            pause_and_end='measurement stops at sample boundary; no claim of a naturally completed battle'))
    write(out/'fixture.json',frozen)
    return frozen


def restore(frozen,service,mode):
    index=ResourceIndex(ROOT)
    req=frozen['requests'][mode];items={r['record']['state']['instance_id']:r for r in frozen['ships']};rows=[]
    for side in req['sides']:
        for member in side['ships']:
            row=items[member['instance_id']];d=bp.restore_design(row['archive'],index)
            rows.append((side,member,d,bp.validate_record(deepcopy(row['record']),d)))
        tactical_fleet.validate([(d,r) for s,_,d,r in rows if s['side_id']==side['side_id']],side['flagship_instance_id'])
    template,scenario,_=service._template();battle,geometry,_=encounter.build(encounter.parse(req),rows,template,scenario)
    return battle,geometry,rows


def commands(b,frozen):
    from backend.high_wilderness_sidecar import aviation_flight as af
    for order in frozen['commands']:
        if order['step']!=b.session.world.fixed_step:continue
        n=next(i for i,s in enumerate(b.session.world.ships) if s.ship_id==order['ship_id'])
        if order['kind']=='load':al.apply(b.inventory.inventories[n],{k:order[k] for k in ('kind','aircraft_id','module_id')})
        else:af.launch(b,b.session.world,b.inventory.inventories,b.aviation.flights,n,[order['aircraft_id']],'group.7b.'+order['ship_id']+'.'+str(order['step']))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('out',type=Path);a.add_argument('--case',choices=CASES,required=True);args=a.parse_args()
    f=freeze(args.out,args.case);print(json.dumps(dict(case=f['case'],ships=len(f['ships']),contact=f['entry_measurement']),ensure_ascii=False))
