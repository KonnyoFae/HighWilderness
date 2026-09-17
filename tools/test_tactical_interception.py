"""Actual swept projectile collision and temporal ordering."""
from dataclasses import replace
import unittest

from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical_interception import resolve
from backend.high_wilderness_sidecar.tactical_interception import properties
from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_settlement as settlement
from tools import test_tactical_ballistics as ballistic, test_tactical_targeting as groups


def shell(id,side,position,velocity,**kw):
    return Projectile(id,side,'gun',position,position,velocity,100,height_layer='upper',**kw)


class CollisionTests(unittest.TestCase):
    def pair(self,hp=3):
        return (shell(1,'enemy',(50.,0.),(-5000.,0.),durability=hp,maximum_durability=hp,collision_radius_m=.06),
                shell(2,'own',(0.,0.),(2000.,0.),interception_damage=1,collision_radius_m=.015))

    def run_pair(self,pair,deadlines=None):
        return resolve(pair,{'own':'blue','enemy':'red'},deadlines or {},1)

    def test_high_speed_hit_consumes_round_preserves_target_performance(self):
        pair=self.pair();updated,removed,events=self.run_pair(pair)
        self.assertEqual(removed,{2});self.assertEqual(updated[1].durability,2)
        self.assertEqual(replace(updated[1],durability=3),pair[0])
        self.assertFalse(events[0]['intercepted'])

    def test_three_hits_destroy_and_later_round_survives(self):
        target,shot=self.pair()
        rounds=tuple(replace(shot,id=2+i,position=(-i,0.),previous=(-i,0.)) for i in range(4))
        updated,removed,events=self.run_pair((target,*rounds))
        self.assertEqual(removed,{1,2,3,4});self.assertEqual(len(events),3)
        self.assertTrue(events[-1]['intercepted']);self.assertEqual(updated[1].durability,0)

    def test_crossing_paths_at_different_times_do_not_hit(self):
        target,shot=self.pair()
        shot=replace(shot,position=(0.,2.),velocity=(0.,-100.))
        self.assertFalse(self.run_pair((target,shot))[1])

    def test_no_proximity_friendly_or_other_layer_contact(self):
        target,shot=self.pair()
        for changed in (replace(shot,position=(0.,.2)),replace(shot,ship_id='enemy'),replace(shot,height_layer='cloud')):
            self.assertFalse(self.run_pair((target,changed))[1])

    def test_ship_impact_or_expiry_prevents_late_interception(self):
        pair=self.pair()
        self.assertFalse(self.run_pair(pair,{1:.1})[1])
        self.assertFalse(self.run_pair(pair,{2:.1})[1])
        self.assertFalse(self.run_pair((replace(pair[0],expires=0),pair[1]))[1])
        self.assertTrue(self.run_pair((replace(pair[0],expires=1),pair[1]))[1])

    def test_no_durability_target_cannot_be_intercepted(self):
        target,shot=self.pair()
        self.assertFalse(self.run_pair((replace(target,durability=None),shot))[1])

    def test_expiry_exact_endpoint_wins_but_ordinary_endpoint_can_hit(self):
        target= shell(1,'enemy',(100.,0.),(0.,0.),durability=1,collision_radius_m=.05)
        shot=shell(2,'own',(0.,0.),(6000.,0.),interception_damage=1,collision_radius_m=.015)
        # Radius makes contact before the deadline, not at the point centre.
        self.assertTrue(self.run_pair((replace(target,expires=1),shot))[1])
        late=replace(target,position=(101.,0.))
        self.assertFalse(self.run_pair((replace(late,expires=1),shot))[1])


class PointDefenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ballistic.BallisticsTests.setUpClass();cls.f=ballistic.BallisticsTests()
        groups.TargetingTests.setUpClass();cls.g=groups.TargetingTests()

    def battle(self,multi=False):
        b=self.g.battle(multi=True) if multi else self.f.battle(30)
        b.states=tuple(replace(s,point_defense=b.point_defense.capable[i],target_policy='hold',target=None)
            for i,s in enumerate(b.states))
        for i in range(len(b.session.world.ships)):
            if b._sides[i]!=b._sides[0]:self.g.move(b,i,10000,10000)
        return b

    def incoming(self,b,caliber=75,distance=1200,speed=1000,x=0,layer='upper',target_index=0):
        ship=b.session.world.ships[target_index];px,py=ship.motion.position_world_m.to_list()
        profile=self.f.profile(caliber)
        identity=max(b._projectile_sequence,1000)+1;b._projectile_sequence=identity
        enemy=next(s.ship_id for i,s in enumerate(b.session.world.ships) if b._sides[i]!=b._sides[0])
        p=Projectile(identity,enemy,'incoming',(px+x,py+distance),(px+x,py+distance),(0.,-speed),
            b.session.world.fixed_step+1000,None,layer,(f'projectile.3a.{caliber}mm.ordinary',1),profile,**properties(profile))
        b.projectiles+=p,
        return p

    def test_launched_calibers_have_finite_durability_and_small_rounds_do_not(self):
        for caliber,hp in ((30,None),(50,None),(75,3),(120,6)):
            b=self.f.battle(caliber);p=self.f.shoot(b)
            self.assertEqual(p.durability,hp);self.assertEqual(p.maximum_durability,hp)

    def test_real_interception_preserves_ship_and_consumes_actual_rounds(self):
        b=self.battle();p=self.incoming(b)
        initial=b.inventory.inventories[0]._value['weapons'][0]['ready_rounds']
        for _ in range(150):
            b.step()
            if b.point_defense.kills:break
        self.assertEqual(b.point_defense.kills,1)
        self.assertEqual([e['durability_after'] for e in b.point_defense.recent],[2,1,0])
        self.assertFalse(any(h['ship_id']==b.session._direct for h in b.damage_state.recent))
        self.assertNotIn(p.id,{p.id for p in b.projectiles})
        self.assertEqual(b.inventory.inventories[0]._value['weapons'][0]['ready_rounds'],initial-b.states[0].shots)
        b.withdraw();result=settlement.validate_result(settlement.capture(b))
        self.assertEqual(result['ships'][0]['after']['state']['weapons'][0]['ready_rounds'],initial-b.states[0].shots)

    def test_small_round_near_pass_and_other_layer_are_not_auto_targets(self):
        for kwargs in (dict(caliber=50),dict(x=100),dict(layer='cloud')):
            b=self.battle();self.incoming(b,**kwargs)
            for _ in range(5):b.step()
            self.assertEqual(b.states[0].shots,0);self.assertFalse(b.point_defense.threats)

    def test_no_threat_waits_and_group_toggle_is_atomic_and_retryable(self):
        b=self.g.battle();cmd=self.g.send(b,'point_defense',enabled=True)
        self.assertFalse(b.submit(cmd))
        for _ in range(5):b.step()
        self.assertTrue(all(s.point_defense and s.shots==0 for s in self.g.members(b)))
        states,seq=b.states,b.sequence
        with self.assertRaises(ps.ContractError):self.g.send(b,'point_defense',group='group.heavy',enabled=True)
        self.assertEqual((b.states,b.sequence),(states,seq))
        self.g.send(b,'point_defense',enabled=False)
        self.assertTrue(all(not s.point_defense and s.target_policy=='automatic' for s in self.g.members(b)))

    def test_unavailable_radar_blocks_and_drops_contact(self):
        b=self.battle();self.incoming(b);world=b.session.world
        available=[dict(v) for v in b._availability(world)[1]]
        for k in b._radars[0]:available[0][k]='destroyed'
        contacts,threats=b.point_defense.observe(world,available,b.projectiles)
        self.assertFalse(contacts);self.assertFalse(threats)
        assignment,reason=b.point_defense.choose(0,b.states[0],world,available,b.projectiles,contacts,threats,{},set(),b.inventory.inventories[0])
        self.assertIsNone(assignment);self.assertEqual(reason,'defense_sensor_unavailable')

    def test_high_speed_priority_then_nearest_and_only_collision_threats(self):
        b=self.battle();low=self.incoming(b,distance=900,speed=500)
        fast=self.incoming(b,distance=1500,speed=2000)
        self.incoming(b,distance=300,speed=5000,x=100)
        b.step()
        self.assertEqual(b.states[0].interception_target_id,fast.id)
        self.assertEqual(b.states[0].interception_priority,0)
        self.assertIn(low.id,{t['projectile_id'] for t in b.point_defense.threats})

    def test_shared_inflight_commitment_never_preapplies_damage(self):
        b=self.battle(multi=True);p=self.incoming(b,distance=2200,speed=500)
        for _ in range(35):
            b.step();target=next((q for q in b.projectiles if q.id==p.id),None)
            if target is None:break
            committed=sum(q.interception_damage for q in b.projectiles if q.interception_target_id==p.id
                and q.interception_expected_step>=b.session.world.fixed_step)
            self.assertLessEqual(committed,target.durability)
        self.assertTrue(any(s.shots for s in b.states if s.point_defense))
        self.assertTrue(any(s.status=='defense_covered' for s in b.states))

    def test_failed_step_rolls_back_contacts_damage_ammunition_and_events(self):
        b=self.battle();self.incoming(b)
        old=(b.session.world,b.projectiles,b.states,b.inventory.inventories,b.point_defense.contacts,b.point_defense.recent)
        def fail(*args):raise RuntimeError('projection rejected')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual((b.session.world,b.projectiles,b.states,b.inventory.inventories,b.point_defense.contacts,b.point_defense.recent),old)
        b.step();self.assertEqual(b.states[0].shots,1)

    def test_no_radar_or_work_when_defense_is_disabled(self):
        b=self.battle();self.f.send(b,'clear');self.incoming(b)
        for _ in range(6):b.step()
        self.assertFalse(b.point_defense.contacts);self.assertEqual(b.states[0].shots,0)

    def test_ship_impact_precedes_late_intercept_in_same_boundary(self):
        from tools.test_tactical_damage import DamageTests
        for early in (True,False):
            b=self.battle();self.f.send(b,'clear')
            p=DamageTests().shell(b,(-30,0),(30,0))
            p=replace(p,durability=1,maximum_durability=1,collision_radius_m=.05,height_layer='upper')
            from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment
            point,_=flight_segment(p).at(.01 if early else .99)
            q=Projectile(2000,b.session._direct,'counter',point,point,(0.,0.),100,0,'upper',
                collision_radius_m=.015,interception_damage=1)
            b.projectiles=(p,q);b._projectile_sequence=2000;b.step()
            self.assertEqual(b.point_defense.kills,int(early))
            self.assertEqual(b.damage_state.hits,0 if early else 1)

    def test_partial_hit_does_not_weaken_same_step_ship_damage(self):
        from tools.test_tactical_damage import DamageTests
        from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment
        results=[]
        for with_round in (False,True):
            b=self.battle();self.f.send(b,'clear');p=DamageTests().shell(b,(-30,0),(30,0))
            p=replace(p,durability=3,maximum_durability=3,collision_radius_m=.05,height_layer='upper')
            point,_=flight_segment(p).at(.01)
            q=Projectile(2000,b.session._direct,'counter',point,point,(0.,0.),100,0,'upper',
                collision_radius_m=.015,interception_damage=1)
            b.projectiles=(p,q) if with_round else (p,);b._projectile_sequence=2000;b.step()
            results.append(b.damage_state.recent)
            self.assertEqual(b.point_defense.hits,int(with_round))
        self.assertEqual(results[0],results[1])

    def test_own_threat_precedes_closer_ally_threat_below_high_speed(self):
        b=self.battle(multi=True)
        self.g.move(b,0,0,0);self.g.move(b,1,100,0)
        own=self.incoming(b,distance=1500,speed=500)
        self.incoming(b,distance=700,speed=500,target_index=1)
        b.step();state=next(s for g,s in zip(b.guns,b.states) if g.ship_index==0 and g.module_id=='weapon_upper_port')
        self.assertEqual(state.interception_target_id,own.id);self.assertEqual(state.interception_priority,1)

    def test_cross_layer_uses_selected_single_layer_and_reduced_speed(self):
        from math import hypot
        b=self.battle(multi=True);self.g.move(b,0,0,0);self.g.move(b,1,0,100,layer='cloud')
        p=self.incoming(b,distance=1200,speed=500,layer='cloud',target_index=1)
        self.g.send(b,'layer',layer='cloud')
        for _ in range(12):
            b.step()
            rounds=[q for q in b.projectiles if q.ship_id==b.session._direct and q.interception_target_id==p.id]
            if rounds:break
        self.assertTrue(rounds)
        self.assertTrue(all(q.height_layer=='cloud' for q in rounds))
        self.assertAlmostEqual(hypot(*rounds[-1].velocity),rounds[-1].flight_profile.muzzle_speed_mps*.7)
        self.assertEqual(rounds[-1].expires-b.session.world.fixed_step,rounds[-1].flight_profile.lifetime_steps)

    def test_committed_miss_releases_budget_for_another_round(self):
        b=self.battle();p=self.incoming(b,distance=1200,speed=500)
        q=Projectile(2000,b.session._direct,'old_round',(500.,500.),(500.,500.),(1.,0.),100,
            height_layer='upper',interception_damage=3,interception_target_id=p.id,interception_expected_step=2)
        b.projectiles+=q,;b._projectile_sequence=2000
        b.step();self.assertEqual(b.states[0].shots,0);self.assertEqual(b.states[0].status,'defense_covered')
        b.step();b.step();self.assertEqual(b.states[0].shots,1)

    def test_cached_prediction_matches_authoritative_high_speed_drag(self):
        from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment
        from backend.high_wilderness_sidecar.tactical_point_defense import predict
        p=shell(1,'enemy',(100.,300.),(-5000.,-300.),flight_profile=self.f.profile(120))
        point,velocity=predict(p,137/60);current=p
        for _ in range(137):
            position,speed=flight_segment(current).at(1);current=replace(current,position=position,velocity=speed)
        for a,z in zip(point,current.position):self.assertAlmostEqual(a,z,places=7)
        for a,z in zip(velocity,current.velocity):self.assertAlmostEqual(a,z,places=7)

    def test_firing_solution_compensates_moving_muzzle_and_both_drag_paths(self):
        from math import hypot
        from backend.high_wilderness_sidecar.tactical_point_defense import firing_solution,predict
        target=shell(1,'enemy',(0.,800.),(80.,-2000.),flight_profile=self.f.profile(120))
        origin=(-5.,5.);inherited=(120.,-40.);profile=self.f.profile(30)
        aim,time=firing_solution(origin,inherited,target,profile,1.,.4)
        delta=tuple(a-o for a,o in zip(aim,origin));distance=hypot(*delta)
        direction=tuple(v/distance for v in delta)
        muzzle=tuple(o+d*3 for o,d in zip(origin,direction))
        velocity=tuple(v+d*profile.muzzle_speed_mps for v,d in zip(inherited,direction))
        shot=shell(2,'own',muzzle,velocity,flight_profile=profile)
        self.assertLess(hypot(*(a-z for a,z in zip(predict(shot,time)[0],predict(target,time)[0]))),.001)


if __name__=='__main__':unittest.main()
