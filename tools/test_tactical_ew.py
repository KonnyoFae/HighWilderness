from dataclasses import replace
import unittest
from math import pi
from backend.high_wilderness_sidecar import missile_flight as mf, persistent_ship as ps, battle_preparation as bp, tactical_settlement as settlement
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_ew import Effect
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.ew_fixture import design,battle,ROOT
from tools.test_missile_flight import missile


class ElectronicWarfareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.designs={k:design(cls.index,k) for k in ('player','enemy')}
        cls.template,cls.scenario,_=RealtimeViewService('backend.ew.tests')._template()

    def battle(self):
        b=battle(self.designs,self.template,self.scenario);b.enemy_fire=False;b.step();return b

    def incoming(self,b,observer=0):
        source=1-observer;ship=b.session.world.ships[source];own=b.session.world.ships[observer]
        pos=(own.motion.position_world_m.x,own.motion.position_world_m.y+(2000 if observer==0 else -2000))
        velocity=(0.,-1000. if observer==0 else 1000.)
        p=missile(ship_id=ship.ship_id,position=pos,previous=pos,velocity=velocity,expires=10000)
        f=replace(p.missile,born_step=b.session.world.fixed_step-2000,heading=-pi/2 if observer==0 else pi/2,
                  launch_point=tuple(own.motion.position_world_m.to_list()),original_target=own.ship_id)
        b.projectiles=(replace(p,missile=f),);b.step();return b.projectiles[0] if b.projectiles else None

    def deploy(self,b,mid='countermeasure.chaff.small'):
        command=dict(epoch=b.session.world.epoch,generation=0,sequence=b.ew.sequence+1,ship_id=b.session.world.ships[0].ship_id,module_id=mid,bearing_deg=90)
        b.ew.submit(command);return command

    def test_all_five_external_modules_compile_no_internal_or_crew_and_preload_no_ammo(self):
        b=self.battle();self.assertEqual(len(b.ew.states),10)
        self.assertFalse(b.inventory.inventories[0]._value['magazines'])
        self.assertEqual(len(b.inventory.inventories[0]._value['weapons']),5)
        for m in self.designs['player'].snapshot.outfit.instances:
            if m.id.startswith('countermeasure.'):
                self.assertFalse(m.internal_cells);self.assertFalse(m.prototype.crew);self.assertTrue(m.top_cells)
        self.assertEqual(bp.restore_design(self.designs['player'].archive(),self.index),self.designs['player'])

    def test_repeated_input_and_failed_tick_do_not_spend_twice(self):
        b=self.battle()
        with self.assertRaises(ps.ContractError):self.deploy(b)
        self.incoming(b);cmd=self.deploy(b);before=b.inventory.inventories[0].snapshot()
        def failure(*args):raise RuntimeError('rollback')
        with self.assertRaises(RuntimeError):b.step(project=failure)
        self.assertEqual(b.inventory.inventories[0].snapshot(),before);self.assertFalse(b.ew.effects)
        self.assertFalse(b.ew.submit(cmd));b.step()
        self.assertEqual(len(b.ew.effects),1);self.assertEqual(b.ew.states[0,cmd['module_id']].shots,1)
        row=next(w for w in b.inventory.inventories[0]._value['weapons'] if w['module_id']==cmd['module_id'])
        self.assertEqual(row['ready_rounds'],0)
        origin=b.ew.effects[0].position
        for _ in range(3):b.step()
        self.assertEqual(b.ew.effects[0].position,origin)
        # The alternate IR channel survives chaff, radar occupancy is released.
        devices=[d for d in b.observation.frame.devices if d['ship_index']==0]
        self.assertTrue(all(d['used']==0 for d in devices if d['channel']=='radar'))
        self.assertTrue(any(d['used']>0 for d in devices if d['channel']=='infrared'))

    def test_ai_deploys_all_working_equipment_once_and_reloads_finite_materials(self):
        b=self.battle();self.incoming(b,observer=1)
        self.assertEqual(len(b.ew.effects),5)
        self.assertTrue(all(s.shots==1 for (n,_),s in b.ew.states.items() if n==1))
        before=b.ew.effect_sequence
        for _ in range(5):b.step()
        self.assertEqual(b.ew.effect_sequence,before)
        self.assertTrue(any(w['reload'] for w in b.inventory.inventories[1]._value['weapons']))

    def test_destroyed_launcher_cannot_deploy_and_regions_expire(self):
        b=self.battle();self.incoming(b);self.deploy(b)
        b.step(device_operations=(DeviceOperation(b.session.world.epoch,b.session.world.ships[0].ship_id,'countermeasure.chaff.small',1,'damage',10000.,b.session.world.fixed_step,'opening'),))
        self.assertFalse(b.ew.effects)
        b.ew.effects=(Effect('ew.expire','a','side.enemy','chaff','upper',(0.,0.),(0.,0.),300.,0.,0,b.session.world.fixed_step+1),)
        b.step();self.assertFalse(b.ew.effects)

    def test_lost_sensors_and_data_link_do_not_read_target_truth(self):
        b=self.battle();world=b.session.world;available=b._availability(world)[1]
        env=b.ew.environment(world,available,b.observation.frame,())
        self.assertTrue(env.links)
        for mid in ('sensor_upper_starboard','infrared.backup','datalink.0'):
            b.observation.submit(dict(epoch=world.epoch,generation=0,sequence=b.observation.sequence+1,ship_id=world.ships[0].ship_id,kind='mode',target=mid,value='off'))
        b.step();env=b.ew.environment(b.session.world,b._availability(b.session.world)[1],b.observation.frame,())
        self.assertFalse(any(side==b._sides[0] for side,_ in env.links))
        self.assertFalse(next(t for t in env.contacts if t.id==world.ships[0].ship_id).emitting)

    def test_decoy_actual_collision_consumes_missile_without_ship_damage(self):
        b=self.battle();p=self.incoming(b)
        origin=(p.position[0],p.position[1]-8)
        b.ew.effects=(Effect('ew.collision',b.session.world.ships[0].ship_id,b._sides[0],'decoy','upper',origin,(0.,0.),0.,8.,0,10000),)
        before=b.damage_state.hits;b.step()
        self.assertFalse(b.projectiles);self.assertFalse(b.ew.effects);self.assertEqual(b.damage_state.hits,before)
        self.assertTrue(any(r.get('decoy_id')=='ew.collision' for r in b.damage_state.expired_flights))

    def test_retarget_command_requires_actual_link_and_commits_with_tick(self):
        b=self.battle();world=b.session.world
        # Destroy enemy countermeasure devices to isolate the link command.
        b.step(device_operations=tuple(DeviceOperation(world.epoch,world.ships[1].ship_id,mid,1,'damage',10000.,world.fixed_step,'opening')
                                       for n,mid in b.ew.states if n==1))
        p=missile(ship_id=world.ships[0].ship_id,position=(0.,1000.),previous=(0.,1000.),velocity=(0.,1000.))
        p=replace(p,missile=replace(p.missile,profile=mf.profiles()['gtw.missile.5c.small.rocket.radar_infrared'],born_step=b.session.world.fixed_step-2000,heading=pi/2,
                                  original_target=None,launch_point=(0.,20000.)))
        b.projectiles=(p,)
        command=dict(epoch=world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=world.ships[0].ship_id,
                     order=dict(kind='retarget',projectile_id=p.id,target_id=world.ships[1].ship_id))
        b.missiles.submit(command)
        def fail(*args):raise RuntimeError('rollback link')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertIsNone(b.projectiles[0].missile.original_target);self.assertTrue(b.missiles.retargets)
        self.assertFalse(b.missiles.submit(command));b.step()
        self.assertEqual(b.projectiles[0].missile.original_target,world.ships[1].ship_id)
        self.assertFalse(b.missiles.retargets);self.assertEqual(b.projectiles[0].expires,p.expires)
        b.observation.submit(dict(epoch=world.epoch,generation=0,sequence=b.observation.sequence+1,ship_id=world.ships[0].ship_id,kind='mode',target='datalink.0',value='off'))
        b.step()
        with self.assertRaises(ps.ContractError):b.missiles.submit(dict(command,sequence=command['sequence']+1))

    def test_settlement_retains_spent_stock_but_clears_effects(self):
        b=self.battle();self.incoming(b);self.deploy(b);b.step();b.withdraw()
        self.assertFalse(b.ew.effects);self.assertFalse(b.projectiles)
        result=settlement.validate_result(settlement.capture(b))
        after=next(s['after'] for s in result['ships'] if s['after']['ship_id']==self.designs['player'].resources.seed.contributions.ship_id)
        bp.validate_record(after,self.designs['player'])
        self.assertEqual(next(w['ready_rounds'] for w in after['state']['weapons'] if w['module_id']=='countermeasure.chaff.small'),0)

    def test_view_reads_live_reload_without_revalidating_entire_ship_per_device(self):
        from unittest.mock import patch
        from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
        b=self.battle();self.incoming(b);self.deploy(b);b.step()
        for _ in range(10):b.step()
        inv=b.inventory.inventories[0];saved=inv.snapshot().to_dict()
        with patch.object(InventorySession,'snapshot',side_effect=AssertionError('UI must not compile full ship resources')):
            shown=b.ew.view()
        for device in shown['devices']:
            row=next(w for w in saved['weapons'] if w['module_id']==device['module_id'])
            self.assertEqual(device['ready'],row['ready_rounds'])
            self.assertEqual(device['reload_remaining_s'],row['reload']['remaining_steps']/60 if row['reload'] else 0)
        self.assertEqual(inv.snapshot().to_dict(),saved)

    def test_all_sixteen_models_depart_vls_with_their_own_seeker_and_durability(self):
        for identity,profile in mf.profiles().items():
            if profile.interceptor:continue
            with self.subTest(model=identity):
                b=battle(self.designs,self.template,self.scenario,model=identity);b.enemy_fire=False;b.step()
                world=b.session.world;mid='weapon_upper_port'
                for order in (dict(kind='target',target_id=world.ships[1].ship_id),dict(kind='fire')):
                    b.missiles.submit(dict(epoch=world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=world.ships[0].ship_id,order=dict(module_id=mid,**order)))
                b.step();self.assertEqual(len(b.missiles.pending),1)
                while b.missiles.pending:b.step()
                p=next(p for p in b.projectiles if p.ship_id==world.ships[0].ship_id)
                self.assertEqual(p.missile.profile,profile);self.assertEqual(p.durability,profile.durability)
                self.assertEqual(p.missile.age,0)
                self.assertIn(p.missile.seeker_state,('tracking','acquiring'))


if __name__=='__main__':unittest.main()
