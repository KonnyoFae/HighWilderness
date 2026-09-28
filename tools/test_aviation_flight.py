"""AV2 real battle transactions, sensing, ownership and rollback regressions."""
from copy import deepcopy
from dataclasses import replace
from math import pi, sin, cos
from unittest.mock import patch
import unittest
from backend.high_wilderness_sidecar import aviation_flight as af, aviation_logistics as al, aviation_recovery as ar
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, tactical_encounter as encounter
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools.test_aviation_logistics import carrier


class FlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.designs=[carrier('ship.flight.'+str(i),links=True) for i in range(3)];cls.template,cls.scenario,_=RealtimeViewService('backend.aviation.flight.tests')._template()

    def battle(self,model='f1',allies=1):
        sides=[];rows=[]
        for i in range(allies+1):
            d=self.designs[i];r=bp.new_record(d,'instance.flight.'+str(i));inv=InventorySession(d.resources,ps.parse_instance(r['state'],d.resources))
            if i==0:
                p=inv._definition['aviation'];hangar=next(f['module_id'] for f in p['facilities'] if f['kind']=='aircraft_hangar')
                catapult=next(f['module_id'] for f in p['facilities'] if f['kind']=='aircraft_catapult')
                stock={'cargo:'+g['id']:100000 for g in inv._definition['goods']}
                al.prepare(inv,[dict(kind='acquire',model_id='gtw.aircraft.'+model),dict(kind='pilots',module_id=hangar,quantity={'f1':1,'e1':2,'b1':3}[model])],stock)
                key=inv._value['aviation']['manifest']['aircraft'][0]['id']
                al.prepare(inv,[dict(kind='prepare',aircraft_id=key,module_id=hangar,loadout={'p1':'self_defense','p2':'self_defense'} if model=='e1' else {'p1':'small_bomb'}),dict(kind='load',aircraft_id=key,module_id=catapult)],stock)
                r['state']=inv.snapshot().to_dict()
            side_id='side.player' if i<allies else 'side.enemy'
            side=next((s for s in sides if s['side_id']==side_id),None)
            if side is None:
                side=dict(side_id=side_id,fleet_id='fleet.'+side_id,flagship_instance_id=r['state']['instance_id'],ships=[]);sides.append(side)
            member=dict(instance_id=r['state']['instance_id'],revision=r['state']['revision'],deployment=dict(x_m=i*500.,y_m=0. if i<allies else 30000.,heading_rad=0.))
            side['ships'].append(member);rows.append((side,member,d,r))
        req=dict(interface=encounter.INTERFACE,encounter_id='encounter.aviation',world_id='world.aviation',world_revision=0,player_side_id='side.player',sides=sides,distance_mode='manual',distance_m=30000.)
        # Encounter contract stores manual placement directly; its geometry is the authority.
        req.pop('distance_mode');req.pop('distance_m')
        b=encounter.build(req,rows,self.template,self.scenario)[0];b.enemy_fire=False
        return b,key

    def command(self,b,kind,keys,**kwargs):
        value=dict(epoch=b.session.world.epoch,generation=0,sequence=b.aviation.sequence+1,ship_id=b.session.world.ships[0].ship_id,order=dict(kind=kind,aircraft_ids=keys,**kwargs))
        b.aviation.submit(value);return value

    def test_preloaded_launch_releases_crew_and_retry_is_exact(self):
        b,key=self.battle();inv=b.inventory.inventories[0];before=deepcopy(inv._value);v=self.command(b,'launch',[key])
        self.assertFalse(b.aviation.submit(v));a=af.plane(b.inventory.inventories[0],key)
        self.assertEqual(a['location'],'airborne');self.assertIsNone(a['module_id']);self.assertEqual(len(a['crew']),1)
        self.assertNotIn(key,b.inventory.inventories[0]._value['aviation']['hangar_assignments'])
        self.assertFalse(b.inventory.inventories[0]._value['aviation']['manifest']['personnel'])
        self.assertEqual(before['fuel_units'],b.inventory.inventories[0]._value['fuel_units'])
        b.step();self.assertTrue(b.aviation.view()['flights']);b.inventory.inventories[0].snapshot()

    def test_fixed_step_rollback_does_not_publish_flight_or_inventory(self):
        b,key=self.battle();self.command(b,'launch',[key]);before=deepcopy(b.aviation.flights);values=[deepcopy(i._value) for i in b.inventory.inventories]
        def fail(*args):raise RuntimeError('reject candidate')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.aviation.flights,before);self.assertEqual([i._value for i in b.inventory.inventories],values)

    def test_return_changes_layer_without_tower_then_unloads_once(self):
        b,key=self.battle();self.command(b,'launch',[key]);f=b.aviation.flights[key];f['layer']='cloud';f['return_requested']=True
        inv=b.inventory.inventories[0];tower=next(x['module_id'] for x in inv._definition['aviation']['facilities'] if x['kind']=='aviation_command');inv._health={**inv._health,tower:0.}
        self.assertFalse(b.aviation.command_available(b.session.world,b._sides[0]))
        with self.assertRaises(ps.ContractError):self.command(b,'task',[key],task=dict(kind='observe',layer='rain',point_m=[0.,0.]))
        fleets=tuple(i.fork() for i in b.inventory.inventories);state=deepcopy(b.aviation.flights)
        arrester=next(x['module_id'] for x in inv._definition['aviation']['facilities'] if x['kind']=='aircraft_arrester')
        at,_=af.origin(b,b.session.world,0,arrester)
        for step in range(361):
            if key in state:state[key]['position']=at
            state=af.advance(b,replace(b.session.world,fixed_step=step),fleets,state,(),())
        self.assertFalse(state);a=af.plane(fleets[0],key);self.assertEqual(a['location'],'cargo');self.assertEqual(a['loadout'],{});self.assertEqual(a['crew'],[])
        self.assertEqual(len(fleets[0]._value['aviation']['manifest']['personnel']),1);fleets[0].snapshot()

    def test_recovery_cross_ship_is_conservative_and_atomic(self):
        b,key=self.battle(allies=2);self.command(b,'launch',[key]);invs=b.inventory.inventories
        self.assertTrue(ar.recover(invs,0,1,key));self.assertFalse(invs[0]._value['aviation']['manifest']['aircraft'])
        a=af.plane(invs[1],key);self.assertEqual(a['ship_id'],invs[1]._value['instance_id']);self.assertEqual(a['home_ship_id'],a['ship_id'])
        self.assertEqual(len(invs[1]._value['aviation']['manifest']['personnel']),1)
        for i in invs:i.snapshot()

    def test_ending_returns_without_arresters_and_survives_restart_record(self):
        b,key=self.battle();self.command(b,'launch',[key]);inv=b.inventory.inventories[0]
        for f in inv._definition['aviation']['facilities']:
            if f['kind']=='aircraft_arrester':inv._health={**inv._health,f['module_id']:0.}
        b.withdraw();self.assertFalse(b.aviation.flights);a=af.plane(b.inventory.inventories[0],key);self.assertEqual(a['location'],'cargo')
        saved=b.inventory.inventories[0].snapshot();ps.parse_instance(saved.to_dict(),inv.pack)

    def test_ace_combines_best_per_stat_without_changing_catalog(self):
        crew=[dict(modifiers=dict(speed=1.2,radar_signature=.8)),dict(modifiers=dict(speed=1.3,radar_signature=.9))]
        mods=af.modifiers(crew);self.assertEqual(mods['speed'],1.3);self.assertEqual(mods['radar_signature'],.8);self.assertEqual(mods['agility'],1.)

    def test_departure_task_is_durable_and_needs_no_tower(self):
        b,key=self.battle();task=dict(kind='sea_patrol',layer='rain',point_m=[4200.,1000.])
        self.command(b,'departure_task',[key],task=task)
        inv=b.inventory.inventories[0];saved=inv.snapshot().to_dict();ps.parse_instance(saved,inv.pack)
        self.assertEqual(saved['aviation']['departure_tasks'][key],task)
        with patch.object(b.aviation,'command_available',return_value=False):self.command(b,'launch',[key])
        self.assertEqual(b.aviation.flights[key]['task'],task)

    def test_launch_uses_module_front_and_ship_heading(self):
        b,key=self.battle();world=b.session.world;s=world.ships[0]
        b.session._world=replace(world,ships=(replace(s,motion=replace(s.motion,heading_rad=pi/2)),*world.ships[1:]))
        mid=af.plane(b.inventory.inventories[0],key)['module_id'];rotation=b._modules[0][mid].rotation_deg*pi/180
        self.command(b,'launch',[key]);v=b.aviation.flights[key]['velocity']
        self.assertAlmostEqual(v[0],120*sin(rotation-pi/2));self.assertAlmostEqual(v[1],120*cos(rotation-pi/2))

    def test_no_arrester_waits_then_diverts_and_full_fleet_salvages(self):
        b,key=self.battle(allies=2);self.command(b,'launch',[key]);f=b.aviation.flights[key];f['return_requested']=True
        _,available=b._availability(b.session.world);blocked=deepcopy(available)
        for n,inv in enumerate(b.inventory.inventories):
            for spec in inv._definition['aviation']['facilities']:
                if spec['kind']=='aircraft_arrester':blocked[n][spec['module_id']]='destroyed'
        with patch.object(b,'_availability',return_value=(None,blocked)):
            state=af.advance(b,b.session.world,b.inventory.inventories,b.aviation.flights,(),())
        self.assertEqual(state[key]['status'],'waiting_recovery');self.assertEqual(len(af.plane(b.inventory.inventories[0],key)['crew']),1)
        for spec in b.inventory.inventories[1]._definition['aviation']['facilities']:
            if spec['kind']=='aircraft_arrester':blocked[1][spec['module_id']]=None
        with patch.object(b,'_availability',return_value=(None,blocked)):
            state=af.advance(b,b.session.world,b.inventory.inventories,state,(),())
        self.assertEqual(state[key]['receiver'][0],1)
        before=deepcopy(b.inventory.inventories[0]._value)
        with patch.object(b.inventory.inventories[1],'_capacity',return_value=0):self.assertFalse(ar.recover(b.inventory.inventories,0,1,key))
        self.assertEqual(b.inventory.inventories[0]._value,before)
        with patch.object(af,'candidates',return_value=iter(())):
            state=af.advance(b,b.session.world,b.inventory.inventories,state,(),())
        self.assertFalse(state);m=b.inventory.inventories[0]._value['aviation']['manifest']
        self.assertEqual(m['aircraft'][0]['location'],'salvage');self.assertEqual(m['personnel'][0]['housing'],'salvage')

    def test_single_recall_does_not_change_other_group_member(self):
        b,key=self.battle();self.command(b,'launch',[key]);inv=b.inventory.inventories[0]
        # Prepare a second real uniquely identified aircraft on the now-free catapult.
        p=inv._definition['aviation'];hangar=next(x['module_id'] for x in p['facilities'] if x['kind']=='aircraft_hangar');catapult=next(x['module_id'] for x in p['facilities'] if x['kind']=='aircraft_catapult')
        stock={'cargo:'+g['id']:100000 for g in inv._definition['goods']}
        al.prepare(inv,[dict(kind='acquire',model_id='gtw.aircraft.f1'),dict(kind='pilots',module_id=hangar,quantity=1)],stock)
        second=inv._value['aviation']['manifest']['aircraft'][-1]['id']
        al.prepare(inv,[dict(kind='prepare',aircraft_id=second,module_id=hangar,loadout={}),dict(kind='load',aircraft_id=second,module_id=catapult)],stock)
        self.command(b,'launch',[second]);task=dict(kind='air_patrol',layer='upper',point_m=[0.,1000.])
        self.command(b,'task',[key,second],task=task);group=b.aviation.flights[key]['group_id']
        self.assertEqual(group,b.aviation.flights[second]['group_id'])
        self.command(b,'task',[second],task={**task,'layer':'cloud'});self.assertNotEqual(group,b.aviation.flights[second]['group_id'])
        self.command(b,'return',[key]);self.assertFalse(b.aviation.flights[second]['return_requested'])

    def test_legal_sensing_lost_target_and_layer_change(self):
        from backend.high_wilderness_sidecar.tactical_observation import Target
        b,key=self.battle();self.command(b,'launch',[key]);state=b.aviation.flights
        f=state[key];f['task']=dict(kind='air_patrol',layer='upper',point_m=list(f['position']))
        at=f['position'];t=Target('enemy.plane','aircraft',b._sides[1],(at[0],at[1]+500),(0.,0.),'upper',powered=True,large=False,radar_signature=3.,infrared_signature=4.)
        with patch.object(b.observation,'targets',return_value=(t,)):
            state=af.advance(b,replace(b.session.world,fixed_step=6),b.inventory.inventories,state,(),())
        self.assertEqual(state[key]['target_id'],t.id)
        with patch.object(b.observation,'targets',return_value=(replace(t,layer='cloud'),)):
            state=af.advance(b,replace(b.session.world,fixed_step=12),b.inventory.inventories,state,(),())
        self.assertIsNone(state[key]['target_id']);self.assertEqual(state[key]['layer'],'upper')
        with patch.object(b.observation,'targets',return_value=(replace(t,position=(1000000.,1000000.)),)):
            state=af.advance(b,replace(b.session.world,fixed_step=18),b.inventory.inventories,state,(),())
        self.assertFalse(state[key]['contacts'])

    def test_fleet_loss_keeps_aircraft_and_crew_as_salvage(self):
        b,key=self.battle();self.command(b,'launch',[key]);world=b.session.world
        world=replace(world,ships=tuple(replace(s,wreck=object()) for s in world.ships))
        state=af.finish(b,world,b.inventory.inventories,b.aviation.flights,True)
        self.assertFalse(state);m=b.inventory.inventories[0]._value['aviation']['manifest']
        self.assertEqual(m['aircraft'][0]['location'],'salvage');self.assertEqual(m['personnel'][0]['housing'],'salvage')
        b.inventory.inventories[0].snapshot()

    def test_unassisted_return_physically_reaches_arrester(self):
        b,key=self.battle();self.command(b,'launch',[key]);state=b.aviation.flights;state[key]['position']=(1000.,1000.);state[key]['speed']=260.;state[key]['return_requested']=True
        for step in range(6000):
            state=af.advance(b,replace(b.session.world,fixed_step=step),b.inventory.inventories,state,(),())
            if not state:break
        self.assertFalse(state);self.assertEqual(af.plane(b.inventory.inventories[0],key)['location'],'cargo')

    def test_mother_exit_recovers_before_inventory_freezes(self):
        b,key=self.battle();self.command(b,'launch',[key]);world=b.session.world;s=world.ships[0]
        exited=replace(s,command=replace(s.command,lifecycle=replace(s.command.lifecycle,physical_status='exited')))
        world=replace(world,ships=(exited,*world.ships[1:]))
        state=af.finish(b,world,b.inventory.inventories,b.aviation.flights)
        self.assertFalse(state);self.assertEqual(af.plane(b.inventory.inventories[0],key)['location'],'cargo')

    def test_ending_cross_ship_receipt_passes_full_settlement_validation(self):
        from backend.high_wilderness_sidecar import tactical_disengagement as disengage, tactical_settlement as settlement
        b,key=self.battle(allies=2);self.command(b,'launch',[key]);world=b.session.world
        b.session._world=replace(world,ships=(disengage.loss_wreck(world.ships[0],world.fixed_step,'withdrawal_loss'),*world.ships[1:]))
        b.withdraw();self.assertFalse(b.aviation.flights)
        self.assertFalse(b.inventory.inventories[0]._value['aviation']['manifest']['aircraft'])
        self.assertEqual(af.plane(b.inventory.inventories[1],key)['location'],'cargo')
        settlement.validate_result(settlement.capture(b))

    def test_e1_empty_defense_payload_and_damage_trigger_individual_return(self):
        b,key=self.battle('e1');self.command(b,'launch',[key]);inv=b.inventory.inventories[0]
        w=al.Work(inv);w.plane(key)['loadout']={};w.commit()
        state=af.advance(b,b.session.world,b.inventory.inventories,b.aviation.flights,(),())
        self.assertTrue(state[key]['return_requested']);self.assertEqual(len(af.plane(inv,key)['crew']),2)
        self.assertFalse(inv._value['aviation']['manifest']['personnel'])
        b,key=self.battle();self.command(b,'launch',[key]);b.aviation.flights[key]['hp']-=1
        state=af.advance(b,b.session.world,b.inventory.inventories,b.aviation.flights,(),())
        self.assertTrue(state[key]['return_requested']);self.assertEqual(af.plane(b.inventory.inventories[0],key)['condition'],'damaged')

    def test_aircraft_sensors_respect_signature_chaff_and_receiving_datalink(self):
        from backend.high_wilderness_sidecar.tactical_observation import Target,Frame,can_observe
        from backend.high_wilderness_sidecar.tactical_ew import Effect
        b,key=self.battle('e1');self.command(b,'launch',[key]);f=b.aviation.flights[key];at=f['position']
        model=b.inventory.inventories[0]._definition['aviation']['catalog']['aircraft'][0]
        t=Target('enemy.plane','aircraft',b._sides[1],(at[0],at[1]+5000),(0.,0.),'upper',powered=True,radar_signature=3.,infrared_signature=.001)
        af.sample(b,b.session.world,f,model,(t,),());self.assertIn(t.id,f['contacts'])
        cloud=Effect('chaff.test',b.session.world.ships[1].ship_id,b._sides[1],'chaff','upper',(at[0],at[1]+2500),(0.,0.),300.,1.,0,1000)
        af.sample(b,replace(b.session.world,fixed_step=6),f,model,(t,),(cloud,));self.assertNotIn(t.id,f['contacts'])
        af.sample(b,replace(b.session.world,fixed_step=12),f,model,(t,),())
        _,available=b._availability(b.session.world);frame=Frame({}, {}, (),12)
        shared=af.share(b,b.session.world,available,frame,{key:f});self.assertIn((0,t.id),shared.tracks)
        off=deepcopy(available)
        for mid in b.observation.links[0]:off[0][mid]='destroyed'
        self.assertNotIn((0,t.id),af.share(b,b.session.world,off,frame,{key:f}).tracks)
        spec=dict(channel='radar',range_m=10000.,ship_range_m=10000.,coasting_range_m=10000.,weather=[1.,1.,1.],range_efficiency=1.)
        self.assertFalse(can_observe(spec,at,'upper',t))
        self.assertTrue(can_observe(spec,at,'upper',replace(t,radar_signature=1000.)))


if __name__=='__main__':unittest.main()
