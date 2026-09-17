from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene
from tools.defense_fixture import design,record
from tools.ew_fixture import design as enemy_design,record as enemy_record


def create(directory):
    server=SidecarServer('backend.defense.fixture',settlement_dir=directory);service=server.preparation;service.provision()
    for key in ('player','enemy'):
        if key=='player':d=design(server.editor.index,key,True);r=record(d,key)
        else:
            d=enemy_design(server.editor.index,key)
            r=enemy_record(d,key,model='gtw.missile.5c.medium.rocket.active_radar',auto=True)
        service.store.create_ship(d,r['state']['instance_id'])
        with service.store.connection() as db:service.store._write_ship(db,r)
    v=scene.fresh();v['revision']=1;v['distance_m']=16000
    for side,key in zip(v['sides'],('enemy','player')):
        identity=('instance.defense.' if key=='player' else 'instance.ew.')+key
        side['flagship_instance_id']=identity
        side['ships']=[dict(instance_id=identity,x_m=0,y_m=0,heading_rad=0)]
    scene.save(service,v,0)


if __name__=='__main__':create(Path(sys.argv[1]))
