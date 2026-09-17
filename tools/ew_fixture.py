"""Shared real 5f test designs; never touches the user's tactical store."""
from pathlib import Path
import json
from math import pi
from backend.high_wilderness_sidecar import battle_preparation as bp, missile_logistics as ml, tactical_encounter as encounter
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.test_battle_preparation import fixture,ROOT


def design(index,key):
    outfit=json.loads((ROOT/'舰艇数据/舾装方案夹具/电子对抗与复合制导测试舰.v1.json').read_text(encoding='utf-8'))
    outfit['name']='电子对抗测试舰 · '+key
    return bp.compile_design(outfit,index,fixture(index)[1],load_current(ROOT),ship_id='ship.ew.'+key)


def record(d,key,model='gtw.missile.5c.small.rocket.radar_infrared',*,auto=False,empty_chaff=False):
    r=bp.new_record(d,'instance.ew.'+key);inv=InventorySession(d.resources,bp.ps.parse_instance(r['state'],d.resources))
    ml.prepare(inv,[dict(module_id='weapon_upper_port',kind='model',model_id=model),dict(module_id='weapon_upper_port',kind='load')],
               {'cargo:'+g['id']:100000 for g in inv._definition['goods']})
    for good,quantity in [('cargo.engineering_parts',100),('cargo.turbojet_parts',20)]:
        inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_cargo',target=good,quantity=quantity)
    for w in inv._value['weapons']:
        if not w['module_id'].startswith('countermeasure.'):continue
        if empty_chaff and w['module_id']=='countermeasure.chaff.small':continue
        from backend.high_wilderness_sidecar.preparation_maintenance import top_up
        top_up(inv,w['module_id'],inv._weapons[w['module_id']]['recipe_ids'][0])
    if auto:ml.apply(inv,dict(module_id='weapon_upper_port',kind='auto_fire',enabled=True))
    r['state']=inv.snapshot().to_dict();bp.validate_record(r,d);return r


def battle(designs,template,scenario,model=None):
    sides=[];rows=[]
    for i,(key,d) in enumerate(designs.items()):
        r=record(d,key,**(dict(model=model) if model and key=='player' else {}));side=dict(side_id='side.'+key,fleet_id='fleet.'+key,flagship_instance_id=r['state']['instance_id'],ships=[])
        member=dict(instance_id=r['state']['instance_id'],revision=r['state']['revision'],deployment=dict(x_m=0,y_m=i*5000.,heading_rad=0 if i==0 else pi))
        side['ships'].append(member);sides.append(side);rows.append((side,member,d,r))
    req=dict(interface=encounter.INTERFACE,encounter_id='encounter.ew',world_id='world.ew',world_revision=0,player_side_id='side.player',sides=sides)
    return encounter.build(req,rows,template,scenario)[0]
