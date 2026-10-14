"""Supplemental pure-gun/chaff inputs; original 7b fixtures remain unchanged."""
from copy import deepcopy
import json
from math import pi
from pathlib import Path

from backend.high_wilderness_sidecar import battle_preparation as bp, outfits
from backend.high_wilderness_sidecar import tactical_encounter as encounter, tactical_test_scene as scene
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.preparation_maintenance import supply_top_up
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.test_battle_preparation import fixture, ROOT
import 高天荒野舰艇人员舱容量 as housing

CHAFF = 'countermeasure.chaff.small'
RADAR = 'sensor_upper_starboard'
IR = 'infrared.backup'


def design(index, key):
    source = json.loads((ROOT/'舰艇数据/舾装方案夹具/电子对抗与复合制导测试舰.v1.json').read_text(encoding='utf-8'))
    ordinary = {m['id']: m for m in fixture(index)[0]['outfit']['modules']}
    source['name'] = '纯炮箔条专项 · '+key
    for i, module in enumerate(source['modules']):
        if module['id'] in ('weapon_upper_port', 'ammunition_magazine'):
            source['modules'][i] = deepcopy(ordinary[module['id']])
            if module['id'] == 'weapon_upper_port':
                source['modules'][i]['prototype'] = dict(id='gtw.module.gun.50mm', version=2)
        if module['id'] == 'cic' and key == 'player':
            module['prototype'] = dict(id='gtw.module.scic.basic', version=1)
    compiled = outfits.document(source, index).compile()
    deployment = dict(id='deployment.chaff.p1a', version=1,
        crew=[dict(crew_type=k, count=v) for k,v in housing.bounded_crew(compiled.instances, dict(compiled.standard_crew)).items()],
        fuel_units=sum(m.prototype.capability.to_dict()['fuel_capacity_units'] for m in compiled.instances if m.prototype.category=='lift_fuel_tank'),
        height_layer='upper', control_mode='crewed', active_remote_core_instance_id=None)
    return bp.compile_design(source, index, deployment, load_current(ROOT), ship_id='ship.chaff.'+key)


def record(d, key):
    r = bp.new_record(d, 'instance.chaff.'+key)
    inv = InventorySession(d.resources, bp.ps.parse_instance(r['state'], d.resources))
    stock = {'ammunition': 10000, **{'cargo:'+g['id']: 100 for g in inv._definition['goods']}}
    for m in inv._value['magazines']:
        inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='load_ammunition', target=m['module_id'], quantity=500)
    for w in inv._definition['weapons']:
        supply_top_up(inv, w['module_id'], next((k for k in w['recipe_ids'] if k.endswith('.ordinary')), w['recipe_ids'][0]), stock)
    # No spare engineering parts: spent chaff cannot silently be replenished.
    r['state'] = inv.snapshot().to_dict()
    return bp.validate_record(r, d)


def build(designs, template, scenario, *, ally=False):
    sides = []; rows = []
    for side_key, keys in (('player', ['player','ally'] if ally else ['player']), ('enemy', ['enemy'])):
        side = dict(side_id='side.'+side_key, fleet_id='fleet.chaff.'+side_key,
                    flagship_instance_id='instance.chaff.'+side_key, ships=[])
        for key in keys:
            d=designs[key]; r=record(d,key)
            member=dict(instance_id=r['state']['instance_id'], revision=r['state']['revision'],
                deployment=dict(x_m=1200. if key=='ally' else 0., y_m=5000. if key=='enemy' else 0., heading_rad=pi if key=='enemy' else 0.))
            side['ships'].append(member); rows.append((side,member,d,r))
        sides.append(side)
    req=dict(interface=encounter.INTERFACE, encounter_id='encounter.chaff', world_id='world.chaff', world_revision=0,
             player_side_id='side.player', sides=sides)
    return encounter.build(req,rows,template,scenario)


def battle(designs, template, scenario, *, ally=False):
    return build(designs,template,scenario,ally=ally)[0]


def create(directory):
    from backend.high_wilderness_sidecar.server import SidecarServer
    directory=Path(directory)
    if (directory/'fixture.json').exists():
        raise ValueError('Choose a fresh output directory; frozen inputs must not be replaced')
    server=SidecarServer('backend.chaff.fixture', settlement_dir=directory/'store')
    service=server.preparation; service.provision(); inputs=[]
    for key in ('player','enemy'):
        d=design(server.editor.index,key); r=record(d,key)
        next(m for m in r['state']['modules'] if m['module_id']==IR)['operating_mode']='off'
        bp.validate_record(r,d)
        service.store.create_ship(d,r['state']['instance_id'])
        with service.store.connection() as db: service.store._write_ship(db,r)
        inputs.append(dict(archive=d.archive(),record=r))
    v=scene.fresh(); v.update(revision=1,distance_mode='manual',distance_m=5000.)
    for side in v['sides']:
        key=side['id']; side['flagship_instance_id']='instance.chaff.'+key
        side['ships']=[dict(instance_id='instance.chaff.'+key,x_m=0.,y_m=0.,heading_rad=pi if key=='enemy' else 0.)]
    scene.save(service,v,0)
    (directory/'fixture.json').write_text(json.dumps(dict(scene=v,ships=inputs,
        scope='Pure gun fire with manual chaff; no missiles or aircraft; finite ammunition and no replacement chaff material.'),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    import sys
    create(sys.argv[1])
