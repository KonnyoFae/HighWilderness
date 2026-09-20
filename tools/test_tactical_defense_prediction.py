"""F4 rolling forecast: precision, information boundary and transaction tests."""
from dataclasses import replace
from math import cos, sin
from random import Random
import unittest

from backend.high_wilderness_sidecar import projectile_observation as observed
from backend.high_wilderness_sidecar import tactical_defense_prediction as prediction
from backend.high_wilderness_sidecar.tactical_point_defense import predict
from tools.point_defense_reference import predict_collisions as reference, Plan as ReferencePlan
from tools import test_tactical_interception as interception, test_missile_defense as missile_defense


class PredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        interception.PointDefenseTests.setUpClass();cls.fixture=interception.PointDefenseTests()
        missile_defense.MissileDefenseTests.setUpClass();cls.missiles=missile_defense.MissileDefenseTests()

    def scene(self,**kwargs):
        b=self.fixture.battle();p=self.fixture.incoming(b,**kwargs)
        return b,replace(p,id=12),b.session.world

    def advance_world(self,w,steps):
        seconds=steps/60
        return replace(w,fixed_step=w.fixed_step+steps,ships=tuple(replace(s,motion=replace(s.motion,
            position_world_m=replace(s.motion.position_world_m,x=s.motion.position_world_m.x+s.motion.velocity_world_mps.x*seconds,
                y=s.motion.position_world_m.y+s.motion.velocity_world_mps.y*seconds),
            heading_rad=s.motion.heading_rad+s.motion.yaw_rate_radps*seconds)) for s in w.ships))

    def test_rolls_countdown_shares_equal_samples_and_prunes_unused_entries(self):
        b,p,w=self.scene(distance=10000,speed=1000)
        first=prediction.Plan(b,w,{})
        hit=first.collisions(0,p,0);self.assertTrue(hit)
        self.assertEqual(first.collisions(0,p,0),hit);self.assertEqual(first.metrics['computed'],1)
        self.assertEqual(first.metrics['shared'],1)
        next_world=self.advance_world(w,1);second=prediction.Plan(b,next_world,first.values)
        new=second.collisions(0,p,0)
        self.assertEqual(second.metrics['computed'],0);self.assertEqual(second.metrics['rolled'],1)
        self.assertAlmostEqual(new[0][1],hit[0][1]-1/60)
        empty=prediction.Plan(b,self.advance_world(w,2),second.values)
        b.point_defense.commit({},(),(),prediction=empty)
        self.assertEqual(b.point_defense.forecasts,{})

    def test_motion_layer_and_exposed_module_destruction_invalidate_immediately(self):
        b,p,w=self.scene(distance=10000,speed=1000);plan=prediction.Plan(b,w,{})
        self.assertTrue(plan.collisions(0,p,0));next_world=self.advance_world(w,1);ship=next_world.ships[0]
        for change in (
            replace(ship,motion=replace(ship.motion,velocity_world_mps=replace(ship.motion.velocity_world_mps,x=500))),
            replace(ship,motion=replace(ship.motion,yaw_rate_radps=.1)),
            replace(ship,motion=replace(ship.motion,height_layer='cloud')),
            replace(ship,devices=replace(ship.devices,modules=(replace(ship.devices.modules[0],durability_points=0),*ship.devices.modules[1:]))),
        ):
            world=replace(next_world,ships=(change,*next_world.ships[1:]))
            current=prediction.Plan(b,world,plan.values);current.collisions(0,p,0)
            self.assertEqual(current.metrics['computed'],1);self.assertEqual(current.metrics['invalidated_motion'],1)
        # Constant measured translation and rotation are safe to roll.
        turning=replace(w.ships[0],motion=replace(w.ships[0].motion,yaw_rate_radps=.03,
            velocity_world_mps=replace(w.ships[0].motion.velocity_world_mps,x=2,y=3)))
        world=replace(w,ships=(turning,*w.ships[1:]));first=prediction.Plan(b,world,{})
        first.collisions(0,p,0);second=prediction.Plan(b,self.advance_world(world,1),first.values)
        second.collisions(0,p,0);self.assertEqual(second.metrics['rolled'],1)

    def test_new_observation_refreshes_and_hidden_live_motion_never_enters_cache(self):
        b,p,w=self.scene(distance=1200,speed=1000);available=b._availability(w)[1]
        frame=b.observation.plan(w,available,(p,));first=prediction.Plan(b,w,{})
        contacts,threats=b.point_defense.observe(w,available,(p,),frame,prediction=first)
        self.assertTrue(threats)
        hidden=replace(p,position=(9999,9999),velocity=(1000,1000),height_layer='rain')
        second=prediction.Plan(b,w,{})
        self.assertEqual(b.point_defense.observe(w,available,(hidden,),frame,prediction=second),(contacts,threats))
        # A newly legal sample of the maneuver does invalidate the old path.
        frame=b.observation.plan(self.advance_world(w,1),available,(hidden,))
        third=prediction.Plan(b,self.advance_world(w,1),first.values)
        self.assertFalse(b.point_defense.observe(third.world,available,(hidden,),frame,prediction=third)[1])
        self.assertFalse(third.collisions(0,replace(observed.sample(p),velocity=(1000,0)),1))
        self.assertEqual(third.metrics['computed'],1)
        # Loss and destruction prune even a cached positive on this step.
        lost=replace(frame,tracks={});fourth=prediction.Plan(b,w,first.values)
        self.assertFalse(b.point_defense.observe(w,available,(p,),lost,prediction=fourth)[1])
        self.assertFalse(fourth.values)
        self.assertFalse(b.point_defense.observe(w,available,(),frame,prediction=fourth)[1])

    def test_guard_horizon_does_not_hide_newly_entering_threat(self):
        b,p,w=self.scene(distance=15150,speed=500)
        p=replace(p,id=11,expires=10000,flight_profile=replace(p.flight_profile,drag=False))
        first=prediction.Plan(b,w,{})
        self.assertFalse(first.collisions(0,p,0))
        self.assertTrue(next(iter(first.values.values())).collisions)
        later=self.advance_world(w,10);second=prediction.Plan(b,later,first.values)
        hit=second.collisions(0,p,0);self.assertTrue(hit)
        self.assertEqual(second.metrics['rolled'],1)
        old=reference(b.point_defense,0,observed.extrapolate(p,10/60),later)
        self.assertAlmostEqual(hit[0][1],old[0][1],places=7)

    def test_near_threat_refreshes_faster_and_deadline_is_not_extended(self):
        for distance,period in ((1200,prediction.NEAR_STEPS),(10000,prediction.FAR_STEPS)):
            b,p,w=self.scene(distance=distance,speed=1000);first=prediction.Plan(b,w,{})
            first.collisions(0,p,0);value=next(iter(first.values.values()))
            self.assertLessEqual(value.due-w.fixed_step,period)
            refreshed=prediction.Plan(b,self.advance_world(w,value.due),first.values)
            refreshed.collisions(0,p,0);self.assertEqual(refreshed.metrics['computed'],1)
        expired=replace(p,expires=1);late=self.advance_world(w,2)
        self.assertFalse(prediction.Plan(b,late,first.values).collisions(0,expired,0))

    def test_conservative_filter_and_rolling_paths_match_reference(self):
        b,p,w=self.scene();rng=Random(4401);hits=0;rejected=0
        for i in range(240):
            speed=rng.uniform(200,5000);heading=rng.uniform(-3.14,3.14);time=rng.uniform(.2,12)
            profile=self.fixture.f.profile(rng.choice((75,120)))
            projectile=replace(p,flight_profile=profile,velocity=(speed*sin(heading),speed*cos(heading)),expires=1800)
            motion=replace(w.ships[0].motion,heading_rad=rng.uniform(-3.14,3.14),yaw_rate_radps=rng.choice((0.,.03,-.07)),
                velocity_world_mps=replace(w.ships[0].motion.velocity_world_mps,x=rng.uniform(-50,50),y=rng.uniform(-50,50)))
            world=replace(w,ships=(replace(w.ships[0],motion=motion),*w.ships[1:]))
            delta=tuple(z-a for z,a in zip(predict(projectile,time)[0],projectile.position))
            position=tuple(a+v*time-d for a,v,d in zip(motion.position_world_m.to_list(),motion.velocity_world_mps.to_list(),delta))
            if i%3==0:position=(position[0]+(50000 if i%6==0 else 1000),position[1]+500)
            projectile=replace(projectile,position=position,previous=position)
            age=rng.randrange(6);world=self.advance_world(world,age)
            old=reference(b.point_defense,0,observed.extrapolate(projectile,age/60),world)
            plan=prediction.Plan(b,world,{})
            actual=plan.collisions(0,projectile,0);rejected+=plan.metrics['broad_rejected']
            self.assertEqual(bool(old),bool(actual),(i,old,actual))
            if old:
                hits+=1;self.assertEqual(old[0][0],actual[0][0]);self.assertLess(abs(old[0][1]-actual[0][1]),.0001)
        self.assertGreater(hits,100);self.assertGreater(rejected,10)

    def test_altitude_crossings_use_sample_and_ship_current_layer(self):
        b,p,w=self.scene(distance=1500,speed=1000)
        w=replace(w,ships=(replace(w.ships[0],motion=replace(w.ships[0].motion,height_layer='cloud')),*w.ships[1:]))
        measured=replace(observed.sample(p),altitude_m=5500,vertical_velocity_mps=-500)
        first=prediction.Plan(b,w,{})
        self.assertTrue(first.collisions(0,measured,0))
        later=self.advance_world(w,1);second=prediction.Plan(b,later,first.values)
        self.assertTrue(second.collisions(0,measured,0));self.assertEqual(second.metrics['rolled'],1)
        stopped=replace(measured,vertical_velocity_mps=0)
        self.assertFalse(second.collisions(0,stopped,1));self.assertEqual(second.metrics['computed'],1)

    def test_failed_step_preserves_forecasts_and_metrics(self):
        b,p,w=self.scene();b.projectiles=(p,);b.step()
        previous=b.point_defense.forecasts;metrics=dict(b.point_defense.prediction_metrics)
        with self.assertRaises(RuntimeError):
            b.step(project=lambda *args:(_ for _ in ()).throw(RuntimeError('rejected')))
        self.assertIs(b.point_defense.forecasts,previous)
        self.assertEqual(b.point_defense.prediction_metrics,metrics)
        b.step();self.assertIsNot(b.point_defense.forecasts,previous)

    def test_fresh_drag_measurements_share_only_matching_numerical_path(self):
        from backend.high_wilderness_sidecar.tactical_ballistics import advance_projectile
        b,p,w=self.scene(distance=12000,speed=1800)
        plan=prediction.Plan(b,w,{})
        plan.collisions(0,p,0)
        anchor=next(iter(plan.values.values())).anchor
        for step in range(1,61):
            p=advance_projectile(p)
            if step%6:continue
            current=self.advance_world(w,step)
            updated=prediction.Plan(b,current,plan.values)
            actual=updated.collisions(0,p,step)
            old=reference(b.point_defense,0,p,current)
            self.assertEqual(bool(actual),bool(old))
            if old:self.assertLess(abs(actual[0][1]-old[0][1]),.0001)
            self.assertEqual(updated.metrics['path_reused'],1)
            self.assertIs(next(iter(updated.values.values())).anchor,anchor)
            plan=updated
        for changed in (replace(p,velocity=(1000.,0.)),replace(p,position=(100.,500.)),
                        replace(p,height_layer='cloud'),replace(p,expires=p.expires+1),
                        replace(p,flight_profile=replace(p.flight_profile,form_factor=2.))):
            updated=prediction.Plan(b,current,plan.values);updated.collisions(0,changed,step)
            self.assertEqual(updated.metrics['path_reused'],0)
            self.assertEqual(next(iter(updated.values.values())).anchor,observed.sample(changed))

    def test_path_anchor_does_not_cross_observer_sides_or_survive_track_removal(self):
        b,p,w=self.scene();first=prediction.Plan(b,w,{})
        first.collisions(0,p,0);new=observed.extrapolate(p,.1)
        side=b._sides[0];other=next(s for s in b._sides if s!=side)
        current=prediction.Plan(b,self.advance_world(w,6),first.values)
        self.assertEqual(current.path_anchor(other,new,6),(new,6))
        empty=prediction.Plan(b,self.advance_world(w,6),{})
        self.assertEqual(empty.path_anchor(side,new,6),(new,6))

    def test_incoming_salvo_keeps_actual_hits_shot_cadence_and_inventory(self):
        results=[]
        for legacy in (True,False):
            b=self.fixture.battle()
            for distance in (900,1400,1900):self.fixture.incoming(b,distance=distance,speed=1000)
            if legacy:b.point_defense.begin=lambda world:ReferencePlan(b.point_defense,world)
            shots=[]
            for step in range(180):
                before=tuple(s.shots for s in b.states);b.step()
                shots.extend((step,n) for n,s in enumerate(b.states) if s.shots>before[n])
            results.append((shots,b.point_defense.recent,b.inventory.inventories[0]._value,b.damage_state.recent))
        self.assertEqual(results[0],results[1]);self.assertGreater(len(results[0][0]),3)

    def test_shared_friendly_track_reuses_forecast_but_lost_link_stops_permission(self):
        b=self.missiles.battle(allies=True);p=self.missiles.incoming(b,distance=12000)
        w=b.session.world;available=b._availability(w)[1];frame=b.observation.plan(w,available,(p,))
        plan=prediction.Plan(b,w,{})
        contacts,threats=b.point_defense.observe(w,available,(p,),frame,prediction=plan)
        self.assertTrue(threats);self.assertGreater(plan.metrics['shared'],0)
        self.assertIn((0,p.id),contacts);self.assertIn((1,p.id),contacts)
        # Remove all valid sources for just the receiving ship.
        frame=replace(frame,tracks={k:v for k,v in frame.tracks.items() if k[0]!=0})
        after=prediction.Plan(b,w,plan.values)
        contacts,_=b.point_defense.observe(w,available,(p,),frame,prediction=after)
        self.assertNotIn((0,p.id),contacts);self.assertIn((1,p.id),contacts)


if __name__=='__main__':unittest.main()
