from pathlib import Path
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene
from tools.ew_fixture import design,record


def create(directory):
    server=SidecarServer('backend.ew.fixture',settlement_dir=directory);service=server.preparation;service.provision()
    for key in ('player','enemy'):
        d=design(server.editor.index,key)
        r=record(d,key,model='gtw.missile.5c.small.turbojet.infrared' if key=='enemy' else 'gtw.missile.5c.small.rocket.radar_infrared',auto=key=='enemy',empty_chaff=key=='player')
        service.store.create_ship(d,r['state']['instance_id'])
        with service.store.connection() as db:service.store._write_ship(db,r)
    v=scene.fresh();v['revision']=1;v['distance_m']=7000
    for side,key in zip(v['sides'],('enemy','player')):
        side['flagship_instance_id']='instance.ew.'+key
        side['ships']=[dict(instance_id='instance.ew.'+key,x_m=0,y_m=0,heading_rad=0)]
    scene.save(service,v,0)


if __name__=='__main__':create(Path(sys.argv[1]))
