"""F3 scheduling, fire cadence, observation safety and bounded reprojection."""
from dataclasses import replace
from math import atan2, hypot, pi
from random import Random
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_fire_control as fc, tactical_targeting as targeting
from backend.high_wilderness_sidecar import tactical_gunnery as tg
from tools import test_tactical_gunnery as guns
from tools.fire_control_f2_reference import Runtime as F2Runtime
from tools.test_tactical_fire_control_optimization import profiles, physical_distance


class SchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):guns.GunneryTests.setUpClass();cls.f=guns.GunneryTests()

    def battle(self,*,fast=False,drag=False,**config):
        if fast:
            zero=dict(direction_error_mdeg=0,position_error_mm=0,velocity_error_mmps=0)
            config.update(rounds=200,cooldown_steps=1,target_velocity_mps=[0,0],fire_control=dict(normal=zero,degraded=zero))
        b=self.f.battle(**config)
        if drag:
            profile=next(p for p in profiles() if p.caliber_mm==75)
            b._gun_flights=tuple({r:profile for r in rows} for rows in b._gun_flights)
        self.f.send(b)
        return b

    def test_loaded_high_rate_gun_keeps_first_shot_and_every_cooldown(self):
        for drag in (False,True):
            with self.subTest(drag=drag):
                current,reference=self.battle(fast=True,drag=drag),self.battle(fast=True,drag=drag)
                reference.fire_control=F2Runtime();solves=0;reuses=0;ages=[];shots=[];old_shots=[]
                for step in range(180):
                    before=current.states[0].shots;old=reference.states[0].shots
                    current.step();reference.step()
                    if current.states[0].shots>before:shots.append(step)
                    if reference.states[0].shots>old:old_shots.append(step)
                    solves+=current.fire_control_metrics['solves'];reuses+=current.fire_control_metrics['reused_solutions']
                    ages.append(current.fire_control_metrics['solution_age_steps'])
                self.assertEqual(shots,old_shots)
                self.assertGreater(len(shots),100)
                self.assertLess(solves,40);self.assertGreater(reuses,100)
                self.assertLess(max(ages),fc.LEASE_STEPS)
                self.assertEqual([i._value for i in current.inventory.inventories],[i._value for i in reference.inventory.inventories])

    def test_empty_and_reloading_guns_track_without_expensive_solves(self):
        for reload in (False,True):
            b=self.battle();inv=b.inventory.inventories[0]
            inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='discharge',target=guns.GUN,quantity=1,cooldown_steps=1)
            if reload:inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='start_reload',target=guns.GUN,recipe_id=tg.RECIPE)
            else:
                for magazine in inv._value['magazines']:magazine['quantity']=0
            aims=[];angles=[]
            for _ in range(55):
                b.step();self.assertEqual(b.fire_control_metrics['solves'],0)
                aims.append(b.states[0].aim_point);angles.append(b.states[0].angle)
            self.assertTrue(all(a is not None for a in aims));self.assertNotEqual(aims[0],aims[-1])
            self.assertNotEqual(angles[0],angles[-1]);self.assertEqual(b.states[0].shots,0)
            if reload:
                for _ in range(100):
                    b.step()
                    if b.states[0].shots:break
                self.assertEqual(b.states[0].shots,1)
                self.assertEqual(b.session.world.fixed_step,120)

    def test_lost_source_invalidates_on_same_step_without_reading_hidden_motion(self):
        b=self.battle(fast=True,visual_range_m=100)
        for _ in range(8):b.step()
        self.assertIsNotNone(b.fire_control.work[0].shot);before=b.states[0].shots
        b.step(device_operations=(self.f.damage(b,'sensor_upper_starboard'),))
        self.assertEqual(b.states[0].shots,before);self.assertIsNone(b.states[0].aim_point)
        self.assertIsNone(b.fire_control.work[0].shot)
        world=b.session.world;target=world.ships[1]
        b.session._world=replace(world,ships=(world.ships[0],replace(target,motion=replace(target.motion,
            velocity_world_mps=replace(target.motion.velocity_world_mps,x=500,y=-200)))))
        for _ in range(20):b.step()
        self.assertEqual(b.states[0].shots,before);self.assertIsNone(b.states[0].aim_point)

    def test_configuration_change_and_step_failure_do_not_reuse_old_permission(self):
        b=self.battle(fast=True)
        b.step();before=dict(b.fire_control.work);metrics=dict(b.fire_control_metrics);world=b.session.world
        with self.assertRaises(RuntimeError):
            b.step(project=lambda *args:(_ for _ in ()).throw(RuntimeError('projection failed')))
        self.assertEqual(b.fire_control.work,before);self.assertEqual(b.fire_control_metrics,metrics)
        self.assertIs(b.session.world,world)
        self.f.send(b,'target',ship_id='ship.web.red',module_id='cic');b.step()
        self.assertEqual(b.fire_control_metrics['invalidated'],1)
        self.assertEqual(b.fire_control.work[0].shot.signature[0],(1,'cic'))
        self.f.send(b,'layer',layer='cloud');shots=b.states[0].shots;b.step()
        self.assertEqual(b.states[0].shots,shots);self.assertIsNone(b.fire_control.work[0].shot)
        self.f.send(b,'clear');b.step();self.assertIsNone(b.fire_control.work[0].solve_since)

    def synthetic(self,count=65):
        # Scheduling harness only: distinct indexed muzzles share one valid
        # inventory source; no physical discharge or fake persisted modules.
        b=self.battle(fast=True,drag=True);base=b.guns[0];state=b.states[0]
        b.guns=tuple(replace(base,anchor=(i*.3,0)) for i in range(count))
        b.states=tuple(state for _ in b.guns);b._gun_flights=tuple(b._gun_flights[0] for _ in b.guns)
        return b

    def test_bounded_fair_queue_uses_latest_inputs_and_staggers_refresh(self):
        traces=[]
        for _ in range(2):
            b=self.synthetic();history=[];solved=set();maxwait=0
            contact=tg.Contact(0,(0,1500),(10,0),0,0,'normal',0)
            for step in range(1,37):
                world=replace(b.session.world,fixed_step=step)
                if step==2:contact=replace(contact,step=step,position=(100,1500))
                plan=b.fire_control.begin(step);solutions=targeting.StepSolutions(step)
                plan.prepare(b,world,b._availability(world)[1],b.inventory.inventories,b.states,{(0,1):contact},b.observation.frame,solutions)
                metrics=plan.metrics(solutions)
                self.assertLessEqual(metrics['solves'],metrics['solve_budget'])
                self.assertLessEqual(metrics['solve_queue'],len(b.guns))
                ids=tuple(i for i,w in plan.work.items() if w.shot and w.shot.step==step)
                for i in ids:
                    solved.add(i)
                    self.assertEqual(plan.work[i].shot.contact,contact)
                history.append(ids);maxwait=max(maxwait,metrics['max_solve_wait_steps'])
                b.fire_control.commit(plan)
            self.assertEqual(len(solved),65);self.assertLessEqual(maxwait,11)
            self.assertTrue(all(len(ids)<65 for ids in history))
            traces.append(history)
        self.assertEqual(traces[0],traces[1])

    def test_search_budget_includes_candidate_scans_and_keeps_valid_targets(self):
        b=self.synthetic();b.states=tuple(replace(s,target=None,target_policy='automatic') for s in b.states)
        contacted=set()
        for step in range(1,16):
            world=replace(b.session.world,fixed_step=step);plan=b.fire_control.begin(step)
            with patch.object(fc,'candidate',wraps=fc.candidate) as candidate:
                states,_=plan.acquire(b,world,b._availability(world)[1],b.inventory.inventories,b.observation.frame,targeting.StepSolutions(step))
            retained=sum(s.target is not None for s in b.states)
            self.assertLessEqual(candidate.call_count,retained+plan.counters['search_budget'])
            b.states=states;b.fire_control.commit(plan)
            contacted.update(i for i,s in enumerate(states) if s.target)
        self.assertEqual(len(contacted),65)
        self.assertEqual(plan.counters['searches'],0)

    def test_conservative_filter_keeps_actual_legal_solutions(self):
        b=self.battle(drag=True);rng=Random(93);count=0;profile=next(iter(b._gun_flights[0].values()))
        for _ in range(700):
            gun=replace(b.guns[0],minimum=-2.5,maximum=2.5,blocked=((60.,90.),(230.,270.)),rotation=rng.uniform(-pi,pi),maximum_range=16000)
            ship=replace(b.session.world.ships[0],motion=replace(b.session.world.ships[0].motion,heading_rad=rng.uniform(-pi,pi)))
            contact=tg.Contact(0,(rng.uniform(-9000,9000),rng.uniform(-9000,9000)),
                (rng.uniform(-150,150),rng.uniform(-150,150)),0,0,'normal',rng.uniform(-.04,.04))
            origin=(0.,0.);inherited=(rng.uniform(-150,150),rng.uniform(-150,150));ratio=rng.choice((.7,1.))
            state=replace(b.states[0],target=(1,None))
            aim=targeting.solution(b,state,contact,0,origin,inherited,profile,ratio)
            if aim is None:continue
            local=tg.rotate(aim,-ship.motion.heading_rad);desired=tg.wrap(atan2(local[0],local[1])-gun.rotation+contact.bearing_error)
            maximum=min(gun.maximum_range,fc.ballistics.reference_range(profile,ratio))
            if gun.minimum<=desired<=gun.maximum and gun.minimum_range<=hypot(*aim)<=maximum and not b._hull_blocked(gun,desired):
                self.assertTrue(fc.possible(b,gun,ship,contact,origin,inherited,profile,ratio,0));count+=1
        self.assertGreater(count,100)

    def test_reprojected_permission_meets_physical_error_limit_after_motion(self):
        rng=Random(17);count=0
        for profile in profiles():
            for _ in range(20):
                old=(rng.uniform(-1000,1000),rng.uniform(500,9000));velocity=(rng.uniform(-100,100),rng.uniform(-100,100))
                result=fc.ballistics.intercept((0,0),(40,10),old,velocity,profile,.7)
                if result is None:continue
                position=(old[0]+rng.uniform(-3,3),old[1]+rng.uniform(-3,3));origin=(4.,1.);inherited=(45.,12.)
                aim,time=fc.reproject(result[1],position,velocity,origin,inherited,profile,.7)
                if aim is None:continue
                delta=tuple(a-o for a,o in zip(aim,origin));length=hypot(*delta)
                v=tuple(i+d/length*profile.muzzle_speed_mps*.7 for i,d in zip(inherited,delta));speed=hypot(*v)
                distance=physical_distance(profile,speed,time)
                miss=hypot(*(o+x/speed*distance-p-u*time for o,x,p,u in zip(origin,v,position,velocity)))
                self.assertLess(miss,.15);count+=1
        self.assertGreater(count,50)

    def test_new_recipe_clears_search_rejection_and_far_targets_gain_no_new_permission(self):
        b=self.battle(drag=True)
        b.states=tuple(replace(s,target=None,target_policy='automatic') for s in b.states)
        gun=b.guns[0];world=b.session.world;origin,inherited=fc.geometry(gun,world.ships[0])
        profile=b._gun_flights[0][b.states[0].reload_recipe_id]
        maximum=min(gun.maximum_range,fc.ballistics.reference_range(profile))
        contact=tg.Contact(0,(origin[0],origin[1]+maximum+10),(0,-100),0,0,'normal',0)
        self.assertTrue(fc.possible(b,gun,world.ships[0],contact,origin,inherited,profile,1,0))
        self.assertIsNone(fc.candidate(b,world,b.observation.frame,{(0,1):contact},gun,fc.Work(),
            world.ships[0].motion.height_layer,origin,inherited,profile,1,0,1))
        # A newly selected/configured round must not inherit an earlier blocked
        # search result, even while the old retry timer has not expired.
        b.fire_control.work={0:fc.Work(search_due=100,rejected=(1,100),search_config=('old recipe',))}
        contact=replace(contact,position=(origin[0],origin[1]+500),velocity=(0,0))
        with patch.object(targeting,'search_contacts',return_value={(0,1):contact}):
            plan=b.fire_control.begin(1)
            states,_=plan.acquire(b,replace(world,fixed_step=1),b._availability(world)[1],b.inventory.inventories,
                b.observation.frame,targeting.StepSolutions(1))
        self.assertEqual(states[0].target,(1,None));self.assertIsNone(plan.work[0].rejected)

    def test_moving_target_high_rate_does_not_turn_every_round_into_full_solve(self):
        b=self.battle(fast=True,drag=True);reference=self.battle(fast=True,drag=True)
        reference.fire_control=F2Runtime()
        for battle in (b,reference):
            world=battle.session.world;own,target=world.ships
            battle.session._world=replace(world,ships=(replace(own,motion=replace(own.motion,
                velocity_world_mps=replace(own.motion.velocity_world_mps,x=50,y=10))),
                replace(target,motion=replace(target.motion,
                    velocity_world_mps=replace(target.motion.velocity_world_mps,x=40,y=-10)))))
        events=[];old_events=[];solves=0
        for step in range(150):
            before=b.states[0].shots;old=reference.states[0].shots;b.step();reference.step()
            if b.states[0].shots>before:events.append(step)
            if reference.states[0].shots>old:old_events.append(step)
            solves+=b.fire_control_metrics['solves']
        self.assertEqual(events,old_events);self.assertGreater(len(events),75)
        self.assertLess(solves,len(events)//2)

    def test_cancelled_search_is_removed_and_waiting_tracking_drops_expired_lead(self):
        b=self.battle()
        b.fire_control.work[0]=fc.Work(search_since=0,search_due=100)
        plan=b.fire_control.begin(1)
        plan.acquire(b,replace(b.session.world,fixed_step=1),b._availability(b.session.world)[1],
            b.inventory.inventories,b.observation.frame,targeting.StepSolutions(1))
        self.assertIsNone(plan.work[0].search_since)
        for _ in range(50):
            b.step()
            if b.states[0].shots:break
        self.assertEqual(b.states[0].shots,1)
        work=b.fire_control.work[0]
        b.fire_control.work[0]=replace(work,shot=replace(work.shot,step=-100,time=100000))
        b.step();origin,_=fc.geometry(b.guns[0],b.session.world.ships[0])
        self.assertEqual(b.fire_control_metrics['solves'],0)
        self.assertLess(hypot(*(a-o for a,o in zip(b.states[0].aim_point,origin))),2000)


if __name__=='__main__':unittest.main()
