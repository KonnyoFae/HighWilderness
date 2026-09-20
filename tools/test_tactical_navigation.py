"""Real encounter navigation, permissions, rollback and turning balance."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from tools import test_tactical_encounter as encounters
from tools.test_battle_preparation import fixture,ROOT
from tools.test_tactical_scheduler import Clock
from backend.high_wilderness_sidecar import battle_preparation as bp,persistent_ship as ps,outfit_documents
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.preparation_ammunition_upgrade import upgrade
from backend.high_wilderness_sidecar.tactical_navigation import Order
from backend.high_wilderness_sidecar.tactical_observation import Track,Target
from tools.upgrade_maneuver_preparations import policy_upgrade
from 高天荒野舰艇战术机动求解器 import Vec2
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand

class NavigationTests(unittest.TestCase):
    setUp=encounters.EncounterTests.setUp
    close_lease=encounters.EncounterTests.close_lease
    call=encounters.EncounterTests.call
    prepare=encounters.EncounterTests.prepare
    launch=encounters.EncounterTests.launch

    def start(self):
        self.prepare(3);r=encounters.request()
        r['sides'][0]['ships'][0]['deployment']['y_m']=20000
        r['sides'][1]['ships'].append(dict(instance_id='instance.custom.2',revision=1,deployment=dict(x_m=200,y_m=-300,heading_rad=0)))
        self.launch(r);self.b=self.live.gunnery;self.nav=self.b.navigation
        self.flag=self.b.session._direct
        self.escort=next(k for k in self.nav.members[self.flag] if k!=self.flag)
        self.n=next(i for i,s in enumerate(self.b.session.world.ships) if s.ship_id==self.escort)
        self.b.enemy_fire=False
        for i,state in enumerate(self.b.states):
            self.b.states=tuple(replace(s,target_policy='hold') if j==i else s for j,s in enumerate(self.b.states))

    def order(self,kind,arguments=None,ship=None):
        v=dict(epoch=self.b.session.world.epoch,generation=0,sequence=self.nav.sequence+1,ship_id=ship or self.escort,kind=kind,arguments=arguments or {})
        self.nav.submit(v);return v

    def steps(self,n):
        for _ in range(n):self.b.step()

    def test_real_actuators_move_hold_return_and_independent_height(self):
        self.start();ship=self.b.session.world.ships[self.n];p=ship.motion.position_world_m
        self.assertFalse(ship.authority_allowed)
        v=self.order('move',dict(point_m=[p.x,p.y+500],append=False,speed_mps=30,heading_deg=0))
        self.assertFalse(self.nav.submit(v))
        self.order('move',dict(point_m=[p.x+100,p.y+800],append=True,speed_mps=30,heading_deg=None))
        self.b.session.set_height_target(self.escort,'cloud')
        self.steps(420);m=self.b.session.world.ships[self.n].motion
        self.assertGreater(m.position_world_m.y,p.y+1);self.assertGreater(m.velocity_world_mps.y,0)
        self.assertEqual(len(self.nav.orders[self.escort].points),2)
        self.order('hold');self.assertIsNotNone(self.b.session.world.ships[self.n].height_navigation.target_layer)
        self.steps(1);self.order('return');self.steps(1)
        self.assertEqual(self.nav.orders[self.escort].kind,'formation')
        self.assertFalse(self.b.session.world.ships[self.n].authority_allowed)
        self.assertEqual(self.b.session.world.ships[1].motion.position_world_m,Vec2(0,-300))

    def test_route_completion_and_transaction_rollback(self):
        self.start();p=self.b.session.world.ships[self.n].motion.position_world_m
        self.order('move',dict(point_m=p.to_list(),append=False,speed_mps=30,heading_deg=None))
        before=(self.nav.orders.copy(),self.nav.controls.copy(),self.b.session.world)
        with patch.object(self.b.inventory,'step',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):self.b.step()
        self.assertEqual((self.nav.orders,self.nav.controls,self.b.session.world),before)
        self.steps(1);self.assertEqual(self.nav.orders[self.escort].kind,'formation')
        published=self.nav.view();self.assertEqual([s['ship_id'] for s in published['ships']],[self.escort])
        with self.assertRaises(ps.ContractError):self.order('hold',ship=self.b.session.world.ships[0].ship_id)
        with self.assertRaises(ps.ContractError):self.order('move',dict(point_m=[0,0],append=False,speed_mps=30,heading_deg=None),ship=self.flag)

    def test_attack_uses_observations_and_returns_when_lost(self):
        self.start();enemy=self.b.session.world.ships[0].ship_id
        with self.assertRaises(ps.ContractError):self.order('attack',dict(target_id=enemy,distance_m=3000,speed_mps=100))
        t=Track(Target(enemy,'ship',self.b._sides[0],(0,10000),(0,10),'upper'),0,())
        self.b.observation.frame=replace(self.b.observation.frame,tracks={(self.n,enemy):t})
        self.order('attack',dict(target_id=enemy,distance_m=3000,speed_mps=100))
        plan=self.nav.plan(self.b.session.world)
        states=self.nav.gun_states(plan[0],plan[4]);own=[s for g,s in zip(self.b.guns,states) if g.ship_index==self.n]
        self.assertTrue(own);self.assertTrue(all(s.target==(0,None) and s.mode=='auto' for s in own))
        self.b.states=tuple(states);self.order('hold')
        self.assertTrue(all(not s.navigation_override for s in self.nav.gun_states(self.nav.orders,{})))
        self.order('attack',dict(target_id=enemy,distance_m=3000,speed_mps=100))
        self.b.observation.frame=replace(self.b.observation.frame,tracks={})
        plan=self.nav.plan(self.b.session.world);self.assertEqual(plan[0][self.escort].kind,'formation')

    def test_retreat_minimum_excludes_disabled_and_manual_helm_cancels(self):
        self.start();self.order('withdraw')
        with patch.object(self.nav,'sustainable_speed',side_effect=lambda n,s,c:0 if s.ship_id==self.escort else 80):
            plan=self.nav.plan(self.b.session.world)
            self.assertEqual(plan[3][self.flag],80)
        self.steps(1);self.assertIn(self.flag,self.nav.withdrawals)
        self.assertGreater(self.nav.retreat_speeds[self.flag],0)
        self.assertLess(self.b.session.world.ships[1].motion.velocity_world_mps.length,self.nav.retreat_speeds[self.flag])
        control=directional_control([ChannelPropulsionCommand('translation.forward','quarter',None)])
        self.b.step(control=control);self.assertNotIn(self.flag,self.nav.withdrawals)
        self.order('withdraw');self.steps(6);self.order('cancel_withdraw');self.steps(1)
        self.assertEqual(self.b.session.world.ships[1].control,directional_control())

    def test_rpc_pause_generation_and_enemy_observation_boundary(self):
        self.start();q=self.live.scheduler
        def submit(value):return self.live.dispatch(dict(method='tactical.realtime.navigation',params=dict(scene_id=q.world.epoch,input=value),session_id=None,expected_revision=None),mode='tactical')
        value=dict(epoch=q.world.epoch,generation=q.status.generation,sequence=1,ship_id=self.escort,kind='hold',arguments={})
        with self.assertRaises(ValueError):submit(value)
        q.resume();value['generation']=q.status.generation;submit(value)
        q.pause();self.assertEqual(submit(value)['view']['navigation']['command_sequence'],1)
        with self.assertRaises(ValueError):submit({**value,'sequence':2})
        self.b.enemy_fire=True;enemy=self.b.session.world.ships[0].ship_id
        world=self.b.session.world;target=world.ships[1].ship_id
        frame=replace(self.b.observation.frame,tracks={(0,target):Track(Target(target,'ship',self.b._sides[1],(0,-900),(0,0),'upper'),0,())})
        self.b.observation.frame=frame
        plan=self.nav.plan(world);self.assertEqual(plan[0][enemy].target,target)
        # Moving unseen truth cannot change the AI command derived from the sample.
        changed=replace(world,ships=(world.ships[0],replace(world.ships[1],motion=replace(world.ships[1].motion,position_world_m=Vec2(10000,10000))),world.ships[2]))
        self.assertEqual(self.nav.plan(changed)[1][enemy],plan[1][enemy])

class NavigationMissileTests(unittest.TestCase):
    def test_attack_does_not_enable_launchers_and_does_not_rewrite_manual_assignment(self):
        from tools import test_missile_flight as f
        f.CombatTests.setUpClass();fixture=f.CombatTests()
        b=fixture.battle('vls');world=b.session.world
        own,enemy=world.ships[0].ship_id,world.ships[-1].ship_id
        fixture.order(b,'point',point_m=fixture.point(b))
        original=b.missiles.states[0,f.LAUNCHER]
        def plan():
            inventories=tuple(i.fork() for i in b.inventory.inventories)
            return b.missiles.plan(world,inventories,b._availability(world)[1],[],0,b.observation.frame,None,
                navigation_orders={own:Order('attack',target=enemy,status='attacking')})
        result=plan();self.assertEqual(result[0][0,f.LAUNCHER].shots,0);self.assertFalse(result[1])
        fixture.order(b,'auto_fire',enabled=True)
        result=plan();self.assertEqual(result[0][0,f.LAUNCHER].shots,1)
        self.assertEqual(result[1][0].projectile.aimed_ship_id,enemy)
        self.assertEqual(result[0][0,f.LAUNCHER].point,original.point)
        self.assertIsNone(result[0][0,f.LAUNCHER].target)

class TurningBalanceTests(unittest.TestCase):
    def test_tenfold_only_thrusters_and_archive_replay(self):
        index=ResourceIndex(ROOT);old_index=outfit_documents.catalog_generations(index)[1]
        doc,dep,policy=fixture(old_index)
        old=bp.compile_design(doc,old_index,dep,policy,ship_id='ship.turning')
        new=bp.compile_design(doc,index,dep,policy_upgrade(ps.clone(policy)),ship_id='ship.turning')
        self.assertEqual(bp.restore_design(old.archive(),index),old)
        self.assertEqual(bp.restore_design(new.archive(),index),new)
        for a,b in zip(old.resources.seed.contributions.engines,new.resources.seed.contributions.engines):
            factor=10 if a.category=='maneuver_thruster' else 1
            self.assertEqual(b.contribution_units,tuple(v*factor for v in a.contribution_units))
        self.assertEqual(old.resources.seed.contributions.design_mass_kg,new.resources.seed.contributions.design_mass_kg)
        with TemporaryDirectory() as directory:
            store=PreparationStore(directory,index);record=store.create_ship(old,'instance.turning')
            from backend.high_wilderness_sidecar.preparation_maneuver_upgrade import apply_idle
            with store.connection() as db:db.execute('INSERT INTO battle_instance_claims VALUES (?,?)',('instance.turning','battle.test'))
            self.assertEqual(apply_idle(store,ROOT)['deferred'],['instance.turning'])
            self.assertFalse((Path(directory)/'maneuver-upgrades').exists())
            with store.connection() as db:db.execute('DELETE FROM battle_instance_claims WHERE instance_id=?',('instance.turning',))
            backup=Path(directory)/'backup.json'
            report=upgrade(store,ROOT,apply=True,backup_path=backup,policy_upgrade=policy_upgrade)
            self.assertTrue(report['applied']);self.assertTrue(backup.exists())
            with store.connection() as db:
                row=db.execute('SELECT payload,digest FROM ships WHERE id=?',('instance.turning',)).fetchone()
                saved=store._decode(*row)
            self.assertEqual({k:v for k,v in saved['state'].items() if k not in ('revision','resources_sha256')},
                             {k:v for k,v in record['state'].items() if k not in ('revision','resources_sha256')})
            self.assertFalse(upgrade(store,ROOT,apply=True,backup_path=backup,policy_upgrade=policy_upgrade)['applied'])

if __name__=='__main__':unittest.main()
