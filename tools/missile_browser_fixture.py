"""Isolated two-sided 5c acceptance fleet; never edits player saves."""
from pathlib import Path
import sys
from tools.test_missile_logistics import document, ROOT
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_test_scene as scene
from backend.high_wilderness_sidecar.preparation_policy import load_current


def create(directory):
    server=SidecarServer('backend.fixture.5c',settlement_dir=directory)
    service=server.preparation;service.provision()
    for key in ('instance.missile.player','instance.missile.enemy'):
        doc,dep=document(server.editor.index)
        doc['outfit']['name']='导弹后勤测试舰 · '+('我方' if key.endswith('player') else '敌方')
        design=bp.compile_design(doc,server.editor.index,dep,load_current(ROOT),ship_id='ship.'+key)
        record=service.store.create_ship(design,key)
        # Reserve raw material aboard so battle assembly can be exercised after
        # preparation; stocking is a fixture input, never a runtime free refill.
        record['state']['cargo']=[dict(good_id=g['id'],quantity=30 if g['id'] in
                                 ('cargo.rocket_parts','cargo.radar_parts','cargo.high_explosive') else 5)
                                 for g in design.resources.definition()['goods']]
        bp.validate_record(record,design)
        bp.ps.validate_initial_loading(bp.ps.parse_instance(record['state'],design.resources),design.resources)
        with service.store.connection() as db:service.store._write_ship(db,record)
    v=scene.fresh();v['revision']=1
    for side,key in zip(v['sides'],('instance.missile.enemy','instance.missile.player')):
        side['flagship_instance_id']=key
        side['ships']=[dict(instance_id=key,x_m=0,y_m=0,heading_rad=0)]
    scene.save(service,v,0)


if __name__=='__main__':create(Path(sys.argv[1]))
