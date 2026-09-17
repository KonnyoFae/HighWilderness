"""Finite four-ship 5i fixture; no combat or storage behavior is replaced."""
from pathlib import Path
import sys
from tools.joint_combat_fixture import create as create_joint
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import persistent_ship as ps, missile_logistics as ml, missile_resources as mr
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar import tactical_test_scene as scene

ALLY = 'instance.ew.ally'
LAUNCHER = 'weapon_upper_port'
MAG = 'ammunition_magazine'


def create(directory, *, layered=False):
    create_joint(directory)
    server = SidecarServer('backend.continuity.fixture', settlement_dir=directory)
    service = server.preparation
    with service.store.connection() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM ships')]
        rows = service.store._inputs(db, ids, service.supply_id)[0]
        for design, record in rows:
            inv = InventorySession(design.resources, ps.parse_instance(record['state'], design.resources))
            for row in inv._value.get('missiles', {}).get('launchers', []): row['auto_fire'] = False
            if record['state']['instance_id'] == ALLY:
                model = inv._value['missiles']['launchers'][0]['model_id']
                stock = {'cargo:' + g['id']: 10000 for g in inv._definition['goods']}
                ml.prepare(inv, [dict(module_id=LAUNCHER, kind='unload'),
                    dict(module_id=MAG, kind='model', model_id=model),
                    dict(module_id=MAG, kind='assemble', quantity=5),
                    dict(module_id=LAUNCHER, kind='load')], stock)
                for good, amount in mr.recipe(inv._definition['missiles'], model, 'blast').items():
                    inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='load_cargo', target=good, quantity=amount*5)
            record['state'] = inv.snapshot().to_dict()
            service.store._write_ship(db, record)
    packet = scene.packet(service); value = packet['scene']; previous = value['revision']
    value['revision'] += 1; value['distance_m'] = 16000 if layered else 50000
    for side in value['sides']:
        for ship in side['ships']: ship['y_m'] = 0
    scene.save(service, value, previous)


def serve(directory):
    from dataclasses import replace
    from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        w=battle.session.world
        battle.session._world=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if s.ship_id=='ship.ew.enemy' else s for s in w.ships))
        # A declared stationary cloud target isolates save ownership from EW.
        battle.ew.states={k:s for k,s in battle.ew.states.items() if battle._sides[k[0]]==battle._sides[battle._direct_index]}
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    raise SystemExit(SidecarServer('backend.e3bbrowser',settlement_dir=directory).serve(sys.stdin.buffer,sys.stdout.buffer))


if __name__ == '__main__':
    directory=Path(sys.argv[1])
    serve(directory) if '--serve' in sys.argv[2:] else create(directory,layered='--layered' in sys.argv[2:])
