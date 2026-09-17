"""Four real finite-stock ships; optional deterministic tank damage for combined QA.

Only the test sidecar injects one tank failure at 20 simulated seconds. Production
flight, EW, interception, descent, player repair orders and settlement are intact.
"""
from pathlib import Path
import argparse
import json
import sys
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_test_scene as scene
from backend.high_wilderness_sidecar import missile_logistics as ml, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.test_battle_preparation import fixture, ROOT
from tools.defense_fixture import design as defense_design, record as defense_record
from tools.ew_fixture import design as ew_design, record as ew_record


def create(directory):
    server=SidecarServer('backend.joint.fixture',settlement_dir=directory);service=server.preparation;service.provision()
    identities={}
    for key in ('player','ally','enemy','escort'):
        if key in ('player','escort'):
            d=defense_design(server.editor.index,key,advanced=key=='player');r=defense_record(d,key)
        else:
            d=ew_design(server.editor.index,key)
            if key=='ally':
                outfit=ps.clone(d.archive()['document']['outfit'])
                outfit['modules']=[m for m in outfit['modules'] if m['id']!='infrared.backup']
                outfit['modules'].extend([
                    dict(id='joint.gun',prototype=dict(id='gtw.module.gun.50mm',version=2),placement=dict(kind='grid',deck_id='deck.1',anchor_half_cell=[-2,4],rotation_deg=0)),
                    dict(id='joint.ammo',prototype=dict(id='gtw.module.fixture.ammunition_magazine',version=2),placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=[-2,12],rotation_deg=0))])
                d=bp.compile_design(outfit,server.editor.index,fixture(server.editor.index)[1],load_current(ROOT),ship_id='ship.ew.ally')
            r=ew_record(d,key,model='gtw.missile.5c.medium.rocket.'+('radar_infrared' if key=='ally' else 'active_radar'),auto=key=='enemy')
        inv=InventorySession(d.resources,ps.parse_instance(r['state'],d.resources))
        # Keep this combined exercise's reserve magazines small: 2,000 ready
        # CIWS rounds remain, without a 1,000-point depot explosion destroying
        # the flagship before the selected ally's repair can be exercised.
        if key in ('player','escort'):
            for magazine in inv._value['magazines']:magazine['quantity']=10
        for dc in inv._value['damage_controls']:dc['quantity_units']=100000
        if key=='ally':
            inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_ammunition',target='joint.ammo',quantity=1000)
            from backend.high_wilderness_sidecar.preparation_maintenance import top_up
            top_up(inv,'joint.gun','recipe.3a.50mm.ordinary')
        r['state']=inv.snapshot().to_dict();bp.validate_record(r,d)
        service.store.create_ship(d,r['state']['instance_id'])
        with service.store.connection() as db:service.store._write_ship(db,r)
        identities[key]=r['state']['instance_id']
    v=scene.fresh();v['revision']=1;v['distance_m']=16000
    for side,keys in zip(v['sides'],(('enemy','escort'),('player','ally'))):
        side['flagship_instance_id']=identities[keys[0]]
        side['ships']=[dict(instance_id=identities[k],x_m=0 if i==0 else 4000,y_m=-14000 if k=='escort' else 0,heading_rad=0) for i,k in enumerate(keys)]
    scene.save(service,v,0)


def serve(directory):
    from time import perf_counter
    from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
    from backend.high_wilderness_sidecar.tactical_scheduler import DomainBatch
    from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        original_step=battle.step
        timing=dict(steps=0,step_seconds=0.,max_step_s=0.,max_gap_s=0.)
        last=[None]
        def measured_step(*args,**kwargs):
            start=perf_counter()
            if last[0] is not None:timing['max_gap_s']=max(timing['max_gap_s'],start-last[0])
            result=original_step(*args,**kwargs);end=perf_counter();last[0]=end
            timing['steps']+=1;timing['step_seconds']+=end-start;timing['max_step_s']=max(timing['max_step_s'],end-start)
            return result
        battle.step=measured_step
        result=original(self,battle,geometry,key)
        def domains(world):
            if world.fixed_step!=1200:return DomainBatch()
            ship=next(s for s in world.ships if s.ship_id=='ship.ew.ally')
            n=next(i for i,s in enumerate(world.ships) if s.ship_id==ship.ship_id)
            module=ship.devices.modules[battle._indices[n]['lift_tank']]
            return DomainBatch(device_operations=(DeviceOperation(world.epoch,ship.ship_id,'lift_tank',module.sequence+1,'damage',module.durability_points,world.fixed_step,'opening'),))
        self.scheduler._domains=domains
        original_tick=self.tick;written=[False]
        def measured_tick():
            original_tick()
            if not written[0] and (battle.ending or self.scheduler.status.pause_reason=='overload'):
                written[0]=True
                (directory.parent/'runtime-timing.json').write_text(json.dumps(dict(timing,status=self.scheduler.status.pause_reason),indent=2),encoding='utf-8')
        self.tick=measured_tick
        return result
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=directory)
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    serve(args.directory) if args.serve else create(args.directory)
