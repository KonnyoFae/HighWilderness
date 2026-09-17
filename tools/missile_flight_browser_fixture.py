"""Real 5e combat fleet, isolated from player data."""
from pathlib import Path
import sys
from tools.test_missile_logistics import document,ROOT,LAUNCHER
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_test_scene as scene,missile_logistics as ml
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.preparation_policy import load_current


def create(directory, *, arcs=False):
    server=SidecarServer('backend.fixture.5e',settlement_dir=directory)
    service=server.preparation;service.provision()
    for key,launcher,name in [('player','gtw.module.launcher.5c.vls','VLS 导弹旗舰'),
                               ('partner','gtw.module.launcher.5c.small.turbojet.infrared','涡喷红外协同舰'),
                               ('enemy','gtw.module.launcher.5c.small.rocket.active_radar','敌方靶舰')]:
        doc,dep=document(server.editor.index,launcher=launcher);doc['outfit']['name']=name
        for m in doc['outfit']['modules']:
            if key=='player' and m['id']==LAUNCHER:m['placement']['deck_id']='deck.0'
            if m['id']=='sensor_upper_starboard':m['prototype']=dict(id='gtw.module.sensor.5d.radar',version=1)
            if m['id']=='fire_control':m['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
        if arcs and key=='player':
            for identity,deck,anchor,rotation in [('arc.blocked','deck.0',[0,-4],90),('arc.clear','deck.1',[2,-4],0)]:
                doc['outfit']['modules'].append(dict(id=identity,prototype=dict(id='gtw.module.launcher.5c.small.rocket.active_radar',version=1),
                    placement=dict(kind='grid',deck_id=deck,anchor_half_cell=anchor,rotation_deg=rotation)))
        design=bp.compile_design(doc,server.editor.index,dep,load_current(ROOT),ship_id='ship.missile-flight.'+key)
        record=service.store.create_ship(design,'instance.missile-flight.'+key)
        inv=InventorySession(design.resources,bp.ps.parse_instance(record['state'],design.resources))
        ml.prepare(inv,[dict(module_id=LAUNCHER,kind='load')],{'cargo:'+g['id']:100000 for g in inv._definition['goods']})
        record['state']=inv.snapshot().to_dict();bp.validate_record(record,design)
        with service.store.connection() as db:service.store._write_ship(db,record)
    v=scene.fresh();v['revision']=1;v['distance_m']=5000
    for side,keys in zip(v['sides'],(('enemy',),('player','partner'))):
        side['flagship_instance_id']='instance.missile-flight.'+keys[0]
        side['ships']=[dict(instance_id='instance.missile-flight.'+key,x_m=i*150,y_m=0,heading_rad=0) for i,key in enumerate(keys)]
    scene.save(service,v,0)


if __name__=='__main__':create(Path(sys.argv[1]),arcs='--arcs' in sys.argv[2:])
