"""5g real prepared designs with finite ammunition, independent of user saves."""
from math import pi
import json
from backend.high_wilderness_sidecar import battle_preparation as bp, missile_logistics as ml, tactical_encounter as encounter
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.test_battle_preparation import fixture,ROOT


def design(index,key,advanced=False,vls=False):
    name='进阶自动防御测试舰' if advanced else '基础自动拦截测试舰'
    outfit=json.loads((ROOT/f'舰艇数据/舾装方案夹具/{name}.v1.json').read_text(encoding='utf-8'))
    if vls:
        launcher=next(m for m in outfit['modules'] if m['id']=='weapon_upper_port')
        launcher['prototype']=dict(id='gtw.module.launcher.5c.vls',version=1)
        launcher['placement']['deck_id']='deck.0'
    return bp.compile_design(outfit,index,fixture(index)[1],load_current(ROOT),ship_id='ship.defense.'+key)


def record(d,key,*,auto=True):
    r=bp.new_record(d,'instance.defense.'+key);inv=InventorySession(d.resources,bp.ps.parse_instance(r['state'],d.resources))
    ml.prepare(inv,[dict(module_id='weapon_upper_port',kind='model',model_id='gtw.missile.5c.small.interceptor'),dict(module_id='weapon_upper_port',kind='load')],{'cargo:'+g['id']:100000 for g in inv._definition['goods']})
    inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_ammunition',target='defense.magazine',quantity=1000)
    from backend.high_wilderness_sidecar.preparation_maintenance import top_up
    top_up(inv,'defense.ciws','recipe.3a.30mm.ordinary')
    ml.apply(inv,dict(module_id='weapon_upper_port',kind='auto_fire',enabled=auto))
    r['state']=inv.snapshot().to_dict();bp.validate_record(r,d);return r


def battle(designs,template,scenario,allies=1):
    sides=[];rows=[]
    for i,(key,d) in enumerate(designs.items()):
        side_id='side.player' if i<allies else 'side.enemy';r=record(d,key)
        side=next((s for s in sides if s['side_id']==side_id),None)
        if side is None:
            side=dict(side_id=side_id,fleet_id='fleet.'+side_id,flagship_instance_id=r['state']['instance_id'],ships=[]);sides.append(side)
        member=dict(instance_id=r['state']['instance_id'],revision=r['state']['revision'],deployment=dict(x_m=0.,y_m=0. if i<allies else 30000.,heading_rad=0 if i<allies else pi))
        side['ships'].append(member);rows.append((side,member,d,r))
    req=dict(interface=encounter.INTERFACE,encounter_id='encounter.defense',world_id='world.defense',world_revision=0,player_side_id='side.player',sides=sides)
    return encounter.build(req,rows,template,scenario)[0]
