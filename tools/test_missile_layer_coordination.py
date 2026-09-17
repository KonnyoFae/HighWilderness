"""5j.3 observed cross-layer guidance, threat prediction and finite commitments."""
from dataclasses import replace
from math import pi
import unittest
from backend.high_wilderness_sidecar import missile_guidance as mg, missile_maneuver as mm
from backend.high_wilderness_sidecar import projectile_observation as ob, tactical_defense as defense
from backend.high_wilderness_sidecar.tactical_missile_defense import reservation
from backend.high_wilderness_sidecar.tactical_observation import tracking_cost
from tools.test_missile_maneuver import body
from tools.test_missile_defense import MissileDefenseTests as Fixtures


class LayerCoordinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixtures.setUpClass();cls.fixture=Fixtures()

    def test_cross_layer_link_guides_before_seeker_activation_without_lock_or_reset(self):
        p=body(800,layer='cloud');f=p.missile
        f=replace(f,profile=replace(f.profile,datalink=True),original_target='old',seeker_state='midcourse',
                  failed_climb_targets=('old',),launch_point=(70000.,0.))
        info=mg.Measurement('new',(65000.,0.),(0.,0.),1200,'upper','relay',(0.,0.))
        changed,aim=mg.update(f,p,1200,'blue',mg.Environment(links=(('blue',info),),retargets=((p.id,'new'),)))
        controlled=mm.control(changed,800.,aim,p.position,p.height_layer)
        self.assertEqual(controlled.goal_layer,'upper');self.assertIsNone(controlled.target_id)
        self.assertEqual(controlled.seeker_state,'midcourse')
        for name in ('born_step','age','ratio','altitude_m','vertical_velocity_mps','failed_climb_targets'):
            self.assertEqual(getattr(f,name),getattr(controlled,name),name)
        lost,aim=mg.update(replace(controlled,profile=replace(controlled.profile,lost_behavior='memory_search')),p,1201,'blue',mg.Environment())
        self.assertEqual(lost.seeker_state,'memory');self.assertEqual(lost.last_sample,info)
        self.assertEqual(mm.control(lost,800,aim,p.position,p.height_layer).goal_layer,'upper')
        back=replace(info,id='old',position=(8000.,0.))
        changed,aim=mg.update(controlled,p,1200,'blue',mg.Environment(links=(('blue',back),),retargets=((p.id,'old'),)))
        self.assertEqual(mm.control(changed,800,aim,p.position,p.height_layer).maneuver_reason,'climb_failed_for_target')
        for link in (replace(info,step=0),replace(info,sender_position=(200000.,0.))):
            unchanged,_=mg.update(f,p,1200,'blue',mg.Environment(links=(('blue',link),),retargets=((p.id,'new'),)))
            self.assertEqual(unchanged.original_target,'old');self.assertIsNone(unchanged.link_sample)

    def test_forecast_contains_no_enemy_intent_and_crosses_only_at_anchors(self):
        p=body(1500,layer='upper',pitch=-pi/6,z=5500)
        changed=replace(p,missile=replace(p.missile,goal_layer='rain',original_target='hidden',return_layer='cloud',
                                       failed_climb_targets=('secret',),maneuver_state='returning'))
        self.assertEqual(ob.sample(p),ob.sample(changed));self.assertFalse(hasattr(ob.sample(p),'missile'))
        self.assertEqual(ob.layer_at(p,.6),'upper');self.assertEqual(ob.layer_at(p,1),'cloud')
        self.assertEqual(ob.layer_at(p,8),'rain')
        self.assertEqual(ob.extrapolate(p,1).height_layer,'cloud')

    def test_linked_other_layer_assignment_is_not_stolen_by_nearby_current_layer_target(self):
        for interceptor in (False,True):
            p=body(1000,layer='upper');f=replace(p.missile,profile=replace(p.missile.profile,datalink=True,interceptor=interceptor),
                original_target='old',seeker_state='search')
            kind='projectile' if interceptor else 'ship'
            other=mg.Contact('other','red',(500.,0.),(0.,0.),'upper',kind=kind,durability=6.)
            assigned=replace(other,id='assigned',layer='cloud',position=(1000.,0.))
            link=mg.Measurement('assigned',assigned.position,assigned.velocity,1200,'cloud','relay')
            env=mg.Environment(contacts=(other,assigned),links=(('blue',link),),retargets=((p.id,'assigned'),))
            changed,aim=mg.update(f,p,1200,'blue',env)
            self.assertIsNone(changed.target_id);self.assertEqual(changed.seeker_state,'datalink')
            self.assertEqual(mm.control(changed,1000,aim,p.position,p.height_layer).goal_layer,'cloud')
            lost,_=mg.update(changed,p,1201,'blue',mg.Environment(contacts=(other,)))
            self.assertEqual(lost.target_id,'other')
            arrived,_=mg.update(changed,replace(p,height_layer='cloud'),1201,'blue',replace(env,retargets=()))
            self.assertEqual(arrived.target_id,'assigned')

    def test_threat_prediction_uses_measured_altitude_and_not_ship_change_orders(self):
        b=self.fixture.battle();w=b.session.world;s=w.ships[0]
        w=replace(w,ships=(replace(s,motion=replace(s.motion,height_layer='cloud')),*w.ships[1:]))
        p=self.fixture.incoming(b)
        measured=replace(ob.sample(p),altitude_m=5500.,vertical_velocity_mps=-500.)
        hit=b.point_defense.predict_collisions(0,measured,w)
        self.assertEqual(hit[0][0],s.ship_id)
        self.assertFalse(b.point_defense.predict_collisions(0,replace(measured,vertical_velocity_mps=0.),w))
        self.assertFalse(b.point_defense.predict_collisions(0,replace(measured,altitude_m=9500.,vertical_velocity_mps=-100.),w))
        # Crossing into cloud before passing the hull removes the upper-layer threat.
        self.assertFalse(b.point_defense.predict_collisions(0,measured,b.session.world))

    def test_tracking_and_high_speed_priority_use_total_speed(self):
        b=self.fixture.battle();p=self.fixture.incoming(b,speed=800)
        t=next(t for t in b.observation.targets(b.session.world,(p,)) if t.id==p.id)
        t=replace(t,payload=replace(t.payload,vertical_velocity_mps=800.))
        self.assertEqual(tracking_cost(t),4)
        self.assertEqual(tracking_cost(replace(t,payload=replace(t.payload,vertical_velocity_mps=0.))),1)

    def test_commitment_releases_failed_or_exhausted_pursuit_but_keeps_reachable_crossing(self):
        p=body(1600,layer='cloud');step=1200
        profile=replace(p.missile.profile,interceptor=True)
        target=mg.Measurement(42,(2000.,0.),(0.,0.),step,'upper')
        f=replace(p.missile,profile=profile,last_sample=target,target_id=42,original_target=42,seeker_state='tracking')
        p=replace(p,missile=f,expires=step+1200,interception_damage=6.)
        self.assertEqual(reservation(p,step).interception_target_id,42)
        failed=replace(p,missile=replace(f,failed_climb_targets=(42,)))
        self.assertIsNone(reservation(failed,step).interception_target_id)
        self.assertIsNone(reservation(replace(p,velocity=(100.,0.)),step).interception_target_id)
        self.assertIsNone(reservation(replace(p,expires=step+1),step).interception_target_id)
        same=replace(failed,missile=replace(failed.missile,last_sample=replace(target,layer='cloud')))
        self.assertEqual(reservation(same,step).interception_target_id,42)
        down=replace(failed,missile=replace(failed.missile,last_sample=replace(target,layer='rain')))
        self.assertEqual(reservation(down,step).interception_target_id,42)

    def test_ciws_shot_no_longer_covers_target_that_will_leave_its_layer(self):
        b=self.fixture.battle();p=self.fixture.incoming(b)
        w=b.session.world;available=b._availability(w)[1]
        frame=b.observation.plan(w,available,(p,));track=frame.tracks[0,p.id]
        moving=replace(track,target=replace(track.target,payload=replace(track.target.payload,altitude_m=5100.,vertical_velocity_mps=-500.)))
        frame=replace(frame,tracks={**frame.tracks,(0,p.id):moving})
        q=replace(p,id=999,ship_id=w.ships[0].ship_id,interception_target_id=p.id,interception_damage=1.,interception_expected_step=w.fixed_step+60)
        self.assertFalse(defense.commitments(b,0,p.id,(q,),w,available,frame=frame))
        self.assertEqual(len(defense.commitments(b,0,p.id,(replace(q,height_layer='cloud'),),w,available,frame=frame)),1)

    def test_ciws_aims_at_predicted_contact_layer_instead_of_current_target_layer(self):
        b=self.fixture.battle();w=b.session.world;own=w.ships[0]
        w=replace(w,ships=(replace(own,motion=replace(own.motion,height_layer='cloud')),*w.ships[1:]))
        p=self.fixture.incoming(b,distance=1000,speed=1000,hp=3)
        available=b._availability(w)[1];frame=b.observation.plan(w,available,(p,));track=frame.tracks[0,p.id]
        measured=replace(track.target.payload,altitude_m=5050.,vertical_velocity_mps=-500.)
        frame=replace(frame,tracks={**frame.tracks,(0,p.id):replace(track,target=replace(track.target,payload=measured))})
        contacts,threats=b.point_defense.observe(w,available,(p,),frame)
        i=next(i for i,g in enumerate(b.guns) if g.ship_index==0 and b.point_defense.capable[i])
        def choose(state):return b.point_defense.choose(i,state,w,available,(p,),contacts,threats,{},set(),b.inventory.inventories[0],frame)
        choice,_=choose(replace(b.states[i],attack_layer='cloud'))
        self.assertIsNotNone(choice);self.assertEqual(choice['projectile_id'],p.id)
        self.assertIsNone(choose(replace(b.states[i],attack_layer='upper'))[0])

    def test_lost_observation_stops_cross_layer_defense_and_shared_sample_can_replace_local(self):
        b=self.fixture.battle(allies=True);w=b.session.world
        ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if n<2 else s for n,s in enumerate(w.ships))
        b.session._world=replace(w,ships=ships);w=b.session.world
        p=self.fixture.incoming(b,distance=12000)
        p=replace(p,missile=replace(body(1500).missile,altitude_m=5500.,vertical_velocity_mps=-500.))
        available=b._availability(w)[1];frame=b.observation.plan(w,available,(p,))
        contacts,threats=b.point_defense.observe(w,available,(p,),frame)
        self.assertTrue(threats);self.assertEqual(threats[0]['impact_layer'],'cloud')
        self.fixture.mode(b,'sensor_upper_starboard',n=0);b.projectiles=();b.step()
        w=b.session.world;available=b._availability(w)[1];frame=b.observation.plan(w,available,(p,))
        self.assertTrue(b.observation.defense_sources(0,p.id,w,available,frame)[0])
        self.assertTrue(b.point_defense.observe(w,available,(p,),frame)[1])
        self.fixture.mode(b,'sensor_upper_starboard',n=1);b.step()
        w=b.session.world;available=b._availability(w)[1];frame=b.observation.plan(w,available,(p,))
        self.assertFalse(b.point_defense.observe(w,available,(p,),frame)[1])

    def test_command_accepts_observed_other_layer_and_still_rejects_lost_target(self):
        from backend.high_wilderness_sidecar import persistent_ship as ps
        b=self.fixture.battle();target=self.fixture.incoming(b,layer='cloud');b.step()
        p=body(1500,layer='upper');p=replace(p,id=777,ship_id=b.session.world.ships[0].ship_id,
            missile=replace(p.missile,profile=replace(p.missile.profile,interceptor=True,datalink=True)))
        b.projectiles+=p,
        command=dict(epoch=b.session.world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=p.ship_id,
                     order=dict(kind='retarget',projectile_id=p.id,target_id=target.id))
        self.assertTrue(b.missiles.submit(command));self.assertFalse(b.missiles.submit(command))
        track=b.observation.frame.tracks[0,target.id]
        b.observation.frame=replace(b.observation.frame,tracks={(0,target.id):replace(track,valid=False,sources=())})
        with self.assertRaises(ps.ContractError):b.missiles.submit({**command,'sequence':b.missiles.sequence+1})

    def test_actual_interceptor_follows_layer_change_without_linked_duplicate_salvo(self):
        from math import atan2
        from backend.high_wilderness_sidecar import missile_flight as mf
        b=self.fixture.battle(allies=True);w=b.session.world;own=w.ships[0]
        b.session._world=replace(w,ships=tuple(replace(s,motion=replace(s.motion,height_layer='cloud')) if n<2 else s for n,s in enumerate(w.ships)))
        p=self.fixture.incoming(b,distance=16000,speed=1100)
        profile=mf.profiles()['gtw.missile.5c.medium.rocket.active_radar']
        f=mf.Flight(profile,'blast',w.fixed_step-1200,-pi/2,(0.,0.),(0.,0.),own.ship_id,age=1200,phase='coast',
            seeker_state='tracking',target_id=own.ship_id,ever_locked=True,
            last_sample=mg.Measurement(own.ship_id,(0.,0.),(0.,0.),w.fixed_step,'cloud'),
            altitude_m=5100.,vertical_velocity_mps=-300.,pitch_rad=atan2(-300,1100))
        p=replace(p,missile=f,expires=w.fixed_step+profile.lifetime()-1200,flight_profile=profile.ballistics(1.));b.projectiles=(p,)
        layers=set();deadline=None;identity=None
        for _ in range(650):
            b.step()
            shots=sum(s.shots for (n,_),s in b.missiles.states.items() if n<2)
            self.assertEqual(shots,1)
            for q in b.projectiles:
                if q.missile and q.missile.profile.interceptor:
                    identity=identity or q.id;deadline=deadline or q.expires
                    self.assertEqual(q.id,identity);self.assertEqual(q.expires,deadline)
                    layers.add(q.height_layer)
            if b.point_defense.recent:break
        self.assertEqual(layers,{'upper','cloud'})
        event=b.point_defense.recent[-1]
        self.assertEqual(event['projectile_id'],p.id);self.assertEqual(event['round_id'],identity)
        self.assertTrue(event['intercepted']);self.assertEqual(event['height_layer'],'cloud')


if __name__=='__main__':unittest.main()
