"""Real ended battle and new rescue formation in an isolated AV5 browser store."""
from pathlib import Path
import sys
from tools.test_aviation_salvage import SalvageTests
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import tactical_test_scene as scene


def create(directory):
    SalvageTests.setUpClass();fixture=SalvageTests()
    service=SidecarServer('backend.fixture.salvage',settlement_dir=directory).preparation
    fixture.store=service.store;fixture.service=service
    _,result=fixture.ending(all_lost=True);service.store.save(result['settlement_id'])
    for d,key in zip(fixture.designs[1:],('instance.rescue','instance.next.enemy')):service.store.create_ship(d,key)
    service.provision()
    with service.store.connection() as db:v=scene.read(db,service.store)
    v.update(revision=2,distance_mode='manual',distance_m=30000)
    for side,key in zip(v['sides'],('instance.next.enemy','instance.rescue')):
        side.update(flagship_instance_id=key,ships=[dict(instance_id=key,x_m=0,y_m=0,heading_rad=0)])
    scene.save(service,v,1)


if __name__=='__main__':create(Path(sys.argv[1]))
