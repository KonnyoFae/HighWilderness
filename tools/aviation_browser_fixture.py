"""Empty carriers in a disposable store for real AV1 UI acceptance."""
from pathlib import Path
import sys
import os
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene


def create(directory):
    service=SidecarServer('backend.fixture.aviation',settlement_dir=directory).preparation
    source=next(s for s in service.library()['sources'] if '航空设施' in s['name'])
    for key in ('instance.aviation.player','instance.aviation.enemy'):
        service.import_ship(dict(instance_id=key,source=dict(kind='resource',value=source['key'])))
    service.provision()
    v=scene.fresh();v.update(revision=1,distance_mode='manual',distance_m=2000 if os.environ.get('HW_AVIATION_COMBAT')=='1' else 30000)
    for side,key in zip(v['sides'],('instance.aviation.enemy','instance.aviation.player')):
        side['flagship_instance_id']=key
        side['ships']=[dict(instance_id=key,x_m=0,y_m=0,heading_rad=0)]
    scene.save(service,v,0)


if __name__=='__main__':create(Path(sys.argv[1]))
