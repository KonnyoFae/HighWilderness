"""Isolated saved damaged fleet for the real 5b browser verification."""
from pathlib import Path
import sys
from tools.test_preparation_maintenance import MaintenanceTests
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene, battle_preparation as bp
from backend.high_wilderness_sidecar.preparation_policy import load_current
from tools.test_battle_preparation import ROOT


def create(directory):
    MaintenanceTests.setUpClass();f=MaintenanceTests()
    server=SidecarServer('backend.fixture.5b',settlement_dir=directory);service=server.preparation;service.provision()
    for key in ('instance.5b','instance.ally','instance.enemy'):
        f.design=bp.compile_design(f.doc,f.index,f.dep,load_current(ROOT),ship_id='ship.'+key)
        service.store.create_ship(f.design,key);r=f.record(key)
        next(m for m in r['state']['modules'] if m['module_id']=='gun.heavy')['durability_points']=39
        next(m for m in r['state']['modules'] if m['module_id']=='generator')['durability_points']=0
        r['state']['damage_controls'][0]['quantity_units']=99500
        r['state']['magazines'][0]['quantity']=0
        with service.store.connection() as db:service.store._write_ship(db,r)
    v=scene.fresh();v['revision']=1
    for side,ids in zip(v['sides'],(('instance.enemy',),('instance.5b','instance.ally'))):
        side['flagship_instance_id']=ids[0]
        side['ships']=[dict(instance_id=k,x_m=i*180,y_m=0,heading_rad=0) for i,k in enumerate(ids)]
    scene.save(service,v,0)
    # Intentionally insufficient for top-ups; browser must show shortage without
    # changing ships, then use the explicit test-supply flow to replenish it.
    with service.store.connection() as db:
        row=db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(service.supply_id,)).fetchone()
        value=service.store._decode(*row);value['supply']['ammunition_resources']=0
        service.store._write_supply(db,value['supply'],value['goods'])


if __name__=='__main__':create(Path(sys.argv[1]))
