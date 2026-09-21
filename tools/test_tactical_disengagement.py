"""6c real prepared encounters: closing damage, exits, rolls and durable results."""
import unittest
from dataclasses import replace
from copy import deepcopy
from unittest.mock import patch
from tools.test_tactical_navigation import NavigationTests
from tools import test_tactical_encounter as encounters
from backend.high_wilderness_sidecar import tactical_encounter as encounter, tactical_settlement as settlement
from backend.high_wilderness_sidecar import tactical_escape_policy as survival, persistent_ship as ps
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from 高天荒野舰艇战术机动求解器 import Vec2


class DisengagementTests(unittest.TestCase):
    setUp=NavigationTests.setUp
    close_lease=NavigationTests.close_lease
    call=NavigationTests.call
    prepare=NavigationTests.prepare
    start=NavigationTests.start
    order=NavigationTests.order
    steps=NavigationTests.steps

    def launch(self,value=None):
        value=deepcopy(value or encounters.request())
        value.update(interface=encounter.DISTANCE_INTERFACE,defending_side_id=value['player_side_id'],
            withdrawal_policy=getattr(self,'policy',deepcopy(survival.DEFAULT)))
        return encounters.EncounterTests.launch(self,value)

    def pose(self,n,*,y=None,layer=None,speed=0):
        world=self.b.session.world;s=world.ships[n]
        motion=replace(s.motion,position_world_m=s.motion.position_world_m if y is None else Vec2(0,y),
            height_layer=layer or s.motion.height_layer,velocity_world_mps=Vec2(0,speed))
        self.b.session._world=replace(world,ships=tuple(replace(v,motion=motion) if i==n else v for i,v in enumerate(world.ships)))

    def damage(self,n,module):
        s=self.b.session.world.ships[n];dk=self.b.session._device_kernels[n];i=dk.by_id[module]
        return DeviceOperation(self.b.session.world.epoch,s.ship_id,module,s.devices.modules[i].sequence+1,
            'damage',dk.seed.modules[i].maximum_durability_points,self.b.session.world.fixed_step+1,'closing')

    def result(self):
        return settlement.validate_result(settlement.capture(self.b))

    def test_exact_distance_and_all_members_height_gate(self):
        self.start();self.pose(0,y=30000);self.pose(1,y=0,layer='cloud')
        self.steps(1)
        self.assertIsNone(self.b.ending);self.assertEqual(self.b.disengagement.threshold,50000)
        self.assertEqual(self.b.disengagement.view()['waiting_ship_ids'],[self.escort])
        self.pose(self.n,layer='cloud');self.steps(1)
        self.assertEqual(self.b.ending['reason'],'withdrawal')
        r=self.result();self.assertEqual(len(r['departures']),2)
        self.assertTrue(all(s['after']['state']['service']['status']=='available' for s in r['ships']))
        self.assertEqual(r['fixed_step'],2)

    def test_boundary_exclusive_and_actual_motion_crossing(self):
        self.start();self.pose(0,y=50000);self.pose(1,y=0)
        self.steps(1);self.assertIsNone(self.b.ending)
        self.pose(1,y=0,speed=-60);self.steps(1)
        self.assertEqual(self.b.ending['reason'],'withdrawal');self.result()

    def test_single_exit_is_independent_frozen_and_excluded_from_height_wait(self):
        self.start();self.pose(0,y=30000);self.pose(1,y=0,layer='cloud')
        self.pose(self.n,y=0);v=self.order('detach')
        self.assertFalse(self.nav.submit(v))
        with self.assertRaises(ps.ContractError):self.order('detach',ship=self.flag)
        # Both exceed the new 25km threshold once the detached member is excluded.
        # Keep it within range to prove the rest of the fleet leaves independently.
        self.pose(self.n,y=10000)
        self.steps(1);self.assertIsNone(self.b.ending)
        self.assertEqual(self.b.session.world.ships[1].command.lifecycle.physical_status,'exited')
        self.pose(self.n,y=0);self.steps(1)
        self.assertEqual(self.b.ending['reason'],'withdrawal')
        r=self.result();solo=next(d for d in r['departures'] if d['ship_id']==self.escort)
        self.assertEqual(solo['strategic_control'],'npc')
        self.assertEqual(next(s for s in r['ships'] if s['after']['ship_id']==self.escort)['after']['state']['service']['status'],'withdrawn')

    def test_departed_ship_cannot_be_hit_or_consume_and_cancel_before_exit(self):
        self.start();self.order('detach');self.order('cancel_detach')
        self.assertEqual(self.nav.orders[self.escort].kind,'formation')
        self.order('detach');self.pose(self.n,y=-31000)
        self.steps(1);self.assertIsNone(self.b.ending)
        self.assertEqual(self.b.session.world.ships[self.n].command.lifecycle.physical_status,'exited')
        inv=self.b.inventory.inventories[self.n];before=inv.snapshot().to_dict()
        pose=self.b.session.world.ships[self.n].motion.position_world_m
        from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
        p=Projectile(self.b._projectile_sequence+1,self.b.session.world.ships[0].ship_id,'weapon_upper_port',
            (pose.x-500,pose.y),(pose.x-500,pose.y),(60000,0),self.b.session.world.fixed_step+60)
        self.b._projectile_sequence=p.id;self.b.projectiles+=(p,)
        hits=self.b.damage_state.hits
        self.steps(60)
        self.assertEqual(self.b.damage_state.hits,hits)
        self.assertEqual(before,self.b.inventory.inventories[self.n].snapshot().to_dict())
        self.assertEqual(pose,self.b.session.world.ships[self.n].motion.position_world_m)
        with self.assertRaises(ps.ContractError):self.order('cancel_detach')
        self.b.withdraw();self.result()

    def test_closing_failure_rolls_back_world_inventory_and_departures(self):
        self.start();self.order('detach');self.pose(self.n,y=-31000)
        before=(self.b.session.world,self.b.disengagement.departures,self.b.inventory.inventories,self.nav.orders.copy())
        with self.assertRaisesRegex(RuntimeError,'projection'):
            self.b.step(project=lambda *_: (_ for _ in ()).throw(RuntimeError('projection')))
        self.assertEqual(before,(self.b.session.world,self.b.disengagement.departures,self.b.inventory.inventories,self.nav.orders))
        self.steps(1);self.assertEqual(len(self.b.disengagement.departures),1)

    def test_flagship_closing_damage_wins_over_distance_and_probability_is_durable(self):
        self.policy=dict(survival.DEFAULT,ship_modifiers=[dict(instance_id='instance.custom.2',bonus=.5)])
        self.start();self.pose(0,y=60000)
        cic=self.b.session._command_kernels[1].cic
        self.b.step(device_operations=(self.damage(1,cic),))
        self.assertEqual(self.b.ending['reason'],'defeat')
        r=self.result();self.assertEqual(len(r['escape_outcomes']),1)
        self.assertTrue(r['escape_outcomes'][0]['survived']);self.assertEqual(r['escape_outcomes'][0]['probability'],1)
        self.assertEqual(r['wrecks'][0]['reason'],'cic_destroyed')
        identity=self.live.store.stage(r)
        saved=self.live.store.save(identity)
        self.assertEqual(saved,self.live.store.save(identity))
        self.assertEqual(saved['result'],r)
        self.assertEqual(settlement.SettlementStore(self.live.store.directory).read(identity),saved)
        record=self.live.store.load_ship('instance.custom.2',2)
        self.assertEqual(record['state']['service']['status'],'withdrawn')
        retry=encounters.request()
        retry['sides'][0]['ships'][0]['revision']=2
        retry['sides'][1].update(flagship_instance_id='instance.custom.2',ships=[dict(instance_id='instance.custom.2',revision=2,deployment=dict(x_m=0,y_m=0,heading_rad=0))])
        with self.assertRaises(ps.ContractError): encounter.load(self.live.preparation_store,retry)

    def test_zero_probability_and_simultaneous_flagship_loss(self):
        self.policy=dict(survival.DEFAULT,base_probability=0)
        self.start()
        self.b.step(device_operations=tuple(self.damage(n,self.b.session._command_kernels[n].cic) for n in (0,1)))
        self.assertEqual(self.b.ending['reason'],'draw')
        r=self.result();self.assertFalse(r['escape_outcomes'][0]['survived'])
        self.assertEqual(len(r['wrecks']),3)
        forged=deepcopy(r);forged['escape_outcomes'][0]['survived']=True
        with self.assertRaises(ps.ContractError):settlement.validate_result(forged)

    def test_disabled_escort_can_recover_before_departure_and_abandoned_only_at_end(self):
        self.start()
        seed=self.b.session._seeds[self.n]
        from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
        def mode(engine,value):
            s=self.b.session.world.ships[self.n]
            return ResourceOperation(self.b.session.world.epoch,s.ship_id,s.resources.sequence+1,
                'mode',engine,value,self.b.session.world.fixed_step,'opening')
        for e in seed.contributions.engines:self.b.step(resource_operations=(mode(e.instance_id,'off'),))
        self.assertFalse(self.b.disengagement.mobile(self.b.session.world.ships[self.n]))
        self.assertIsNone(self.b.session.world.ships[self.n].wreck);self.assertIsNone(self.b.ending)
        engine=next(e.instance_id for e in seed.contributions.engines if any(e.contribution_units[:4]))
        self.b.step(resource_operations=(mode(engine,'active'),))
        self.assertTrue(self.b.disengagement.mobile(self.b.session.world.ships[self.n]))
        self.b.step(device_operations=(self.damage(self.n,engine),))
        self.pose(0,y=60000);self.steps(1)
        r=self.result();wreck=next(w for w in r['wrecks'] if w['ship_id']==self.escort)
        self.assertEqual(wreck['reason'],'propulsion_abandoned')

    def test_stranded_ship_remains_repairable_until_detached_member_leaves(self):
        self.prepare(4);r=encounters.request()
        r['sides'][0]['ships'][0]['deployment']['y_m']=60000
        for n in (2,3):
            r['sides'][1]['ships'].append(dict(instance_id=f'instance.custom.{n}',revision=1,
                deployment=dict(x_m=0,y_m=20000 if n==2 else -300,heading_rad=0)))
        self.launch(r);self.b=self.live.gunnery;self.nav=self.b.navigation
        self.flag=self.b.session._direct;self.escort=self.b.session.world.ships[2].ship_id
        self.b.enemy_fire=False
        self.order('detach')
        seed=self.b.session._seeds[3]
        self.b.step(device_operations=tuple(self.damage(3,e.instance_id) for e in seed.contributions.engines))
        self.assertIsNone(self.b.ending)
        self.assertIsNone(self.b.session.world.ships[3].wreck)
        self.pose(2,y=0);self.steps(1)
        self.assertEqual(self.b.ending['reason'],'withdrawal')
        self.assertEqual(self.b.session.world.ships[3].wreck.reason,'propulsion_abandoned')
        self.result()


class SurvivalPolicyTests(unittest.TestCase):
    def test_buff_clamp_and_input_validation(self):
        for base,bonus,expected in ((.5,.8,1),(.5,-.8,0),(.5,.1,.6)):
            p=dict(survival.DEFAULT,base_probability=base,ship_modifiers=[dict(instance_id='instance.a',bonus=bonus)])
            survival.parse(p,{'instance.a'})
            a=survival.resolve(p,'scene.a','instance.a',7)
            self.assertAlmostEqual(a['probability'],expected)
            self.assertEqual(a,survival.resolve(p,'scene.a','instance.a',7))
        for invalid in (-.1,1.1,True,float('nan')):
            with self.assertRaises(ps.ContractError):survival.parse(dict(survival.DEFAULT,base_probability=invalid),set())


if __name__=='__main__': unittest.main()
