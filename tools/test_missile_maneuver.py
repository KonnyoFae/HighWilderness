"""5j.2 continuous altitude, information boundaries and swept layer contacts."""
from dataclasses import replace
from math import cos,sin,pi,hypot,atan2
from types import SimpleNamespace
import unittest
from backend.high_wilderness_sidecar import missile_flight as mf,missile_guidance as mg,missile_maneuver as mm
from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment,advance_projectile,layer_at
from backend.high_wilderness_sidecar.tactical_interception import contact_fraction,resolve
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from tools.test_missile_flight import missile


def body(speed=800.,layer='cloud',pitch=0.,z=None,**kwargs):
    p=missile(speed,height_layer=layer,velocity=(speed*cos(pitch),0.))
    f=replace(p.missile,altitude_m=mm.altitude(layer) if z is None else z,pitch_rad=pitch,
              vertical_velocity_mps=speed*sin(pitch),settled_layer=layer if z is None else None,**kwargs)
    return replace(p,missile=f)


def controlled(p,target_layer,point=None,identity='enemy'):
    f=replace(p.missile,seeker_state='tracking',last_sample=mg.Measurement(identity,point or (p.position[0]+1000,p.position[1]),(0.,0.),0,target_layer))
    return replace(p,missile=mm.control(f,hypot(*p.velocity,f.vertical_velocity_mps),point or (p.position[0]+1000,p.position[1]),p.position,p.height_layer))


class ManeuverTests(unittest.TestCase):
    def test_gravity_energy_and_engine_cap_are_separate(self):
        for pitch in (-pi/6,pi/6):
            p=body(900,pitch=pitch,acceleration=0.,pitch_rate=0.)
            path=flight_segment(p);after=advance_projectile(p,path)
            energy=lambda q:.5*hypot(*q.velocity,q.missile.vertical_velocity_mps)**2+mm.G*q.missile.altitude_m
            self.assertAlmostEqual(energy(p),energy(after),places=6)
            self.assertEqual(hypot(*after.velocity,after.missile.vertical_velocity_mps)>900,pitch<0)
        p=body(1800,pitch=-pi/6)
        f=replace(p.missile,age=p.missile.profile.boost_steps)
        f=controlled(replace(p,missile=f),'rain').missile
        self.assertLessEqual(f.acceleration,0.)
        q=advance_projectile(replace(p,missile=replace(f,pitch_rate=0.,angular_rate=0.)))
        self.assertGreater(hypot(*q.velocity,q.missile.vertical_velocity_mps),1800)

    def test_shared_overload_low_speed_and_boost(self):
        p=body(700);f=p.missile
        q=controlled(p,'upper',(100,1000))
        turn=hypot(q.missile.angular_rate*cos(f.pitch_rad),q.missile.pitch_rate)*700
        self.assertAlmostEqual(turn,f.profile.max_g*mm.G)
        self.assertAlmostEqual(q.missile.acceleration,-turn)
        boosted=controlled(replace(p,missile=replace(f,age=0)),'upper',(0,1000))
        self.assertEqual((boosted.missile.pitch_rate,boosted.missile.angular_rate),(0.,0.))
        stopped=controlled(body(0),'upper',(0,1000))
        self.assertEqual((stopped.missile.pitch_rate,stopped.missile.angular_rate),(0.,0.))
        low=controlled(body(10),'upper',(0,1000))
        self.assertLess(hypot(low.missile.pitch_rate,low.missile.angular_rate),hypot(q.missile.pitch_rate,q.missile.angular_rate))

    def test_real_adjacent_travel_and_replanning_preserve_state_and_deadline(self):
        for start,target,speed in (('cloud','upper',1600),('upper','rain',900)):
            p=body(speed,layer=start);deadline=p.expires;seen=[start]
            for _ in range(6000):
                p=advance_projectile(controlled(p,target))
                if p.height_layer!=seen[-1]:seen.append(p.height_layer)
                if p.height_layer==target:break
            self.assertEqual(p.height_layer,target)
            self.assertEqual(seen,['upper','cloud','rain'] if start=='upper' else ['cloud','upper'])
            self.assertEqual(p.expires,deadline)
        q=body(900,layer='cloud',pitch=pi/6,z=6500)
        changed=controlled(q,'rain')
        self.assertEqual(changed.missile.altitude_m,6500)
        self.assertEqual(changed.velocity,q.velocity)
        self.assertEqual(changed.missile.pitch_rad,pi/6)
        self.assertLess(changed.missile.pitch_rate,0)

    def test_failure_is_continuous_ballistic_return_and_same_target_ban(self):
        p=body(50.01,pitch=pi/6,z=5010,maneuver_target_id='enemy')
        path=flight_segment(p)
        self.assertIsNotNone(path.failure)
        before=path.state(path.failure-1e-8);after=path.state(path.failure+1e-8)
        self.assertLess(hypot(*(a-b for a,b in zip(before[0],after[0]))),1e-5)
        self.assertLess(hypot(*(a-b for a,b in zip(before[1],after[1]))),1e-5)
        p=advance_projectile(p,path)
        self.assertEqual(p.missile.return_layer,'cloud')
        self.assertEqual(p.missile.failed_climb_targets,('enemy',))
        self.assertEqual(p.height_layer,'cloud')
        deadline=p.expires
        for _ in range(1000):
            p=advance_projectile(controlled(p,'upper'))
            self.assertEqual(p.height_layer,'cloud')
            if p.missile.return_layer is None:break
        self.assertIsNone(p.missile.return_layer)
        self.assertLessEqual(p.missile.altitude_m,5000)
        self.assertEqual(p.expires,deadline)
        retry=controlled(p,'upper')
        self.assertEqual(retry.missile.maneuver_reason,'climb_failed_for_target')
        other=controlled(p,'upper',identity='other')
        self.assertEqual(other.missile.goal_layer,'upper')
        back=controlled(other,'upper')
        self.assertEqual(back.missile.maneuver_reason,'climb_failed_for_target')
        self.assertEqual(controlled(p,'rain').missile.goal_layer,'rain')
        self.assertEqual(controlled(p,'cloud').missile.goal_layer,'cloud')

    def test_failure_uses_total_speed_and_model_threshold(self):
        p=body(80,pitch=pi/3,z=6000,maneuver_target_id='enemy')
        self.assertLess(hypot(*p.velocity),50)
        self.assertIsNone(flight_segment(p).failure)
        q=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,minimum_climb_speed_mps=90)))
        self.assertEqual(flight_segment(q).failure,0.)

    def test_return_can_turn_horizontally_but_cannot_resume_climbing(self):
        p=body(49.,pitch=.1,z=5100,return_layer='cloud',failed_climb_targets=('enemy',))
        p=controlled(p,'upper',(0.,1000.))
        self.assertEqual(p.missile.pitch_rate,0.)
        self.assertGreater(p.missile.angular_rate,0.)
        q=advance_projectile(p)
        self.assertGreater(q.velocity[1],0.)
        energy=lambda x:.5*hypot(*x.velocity,x.missile.vertical_velocity_mps)**2+mm.G*x.missile.altitude_m
        self.assertLess(energy(q),energy(p))
        self.assertEqual(q.missile.return_layer,'cloud')

    def test_lost_straight_levels_memory_keeps_last_known_layer(self):
        p=body(1000,pitch=pi/8,z=7000,ever_locked=True,original_target='enemy',target_id='enemy',seeker_state='tracking',born_step=-2000,
               last_sample=mg.Measurement('enemy',(10000,0),(0,0),0,'upper'))
        world=SimpleNamespace(fixed_step=1,ships=(SimpleNamespace(ship_id='blue'),))
        a=mf.prepare(p,world,('blue',),mg.Environment())
        self.assertEqual(a.missile.seeker_state,'lost');self.assertIsNone(a.missile.goal_layer)
        self.assertLess(a.missile.pitch_rate,0);self.assertEqual(a.missile.angular_rate,0)
        advanced=replace(p,missile=replace(p.missile,profile=replace(p.missile.profile,lost_behavior='memory_search')))
        b=mf.prepare(advanced,world,('blue',),mg.Environment())
        self.assertEqual(b.missile.goal_layer,'upper')
        hidden=mg.Contact('enemy','red',(50000,40000),(300,400),'rain')
        c=mf.prepare(advanced,world,('blue',),mg.Environment(contacts=(hidden,)))
        self.assertEqual(b,c)
        self.assertEqual(a.missile.altitude_m,7000)

    def test_fast_boundary_interception_and_ttl_use_contact_layer(self):
        for speed in (2000,5000):
            # Complete cloud->upper halfway through one fixed step.
            p=body(speed,pitch=pi/6,z=10000-speed*sin(pi/6)/120)
            path=flight_segment(p);cross=path.transitions[0][0]
            self.assertGreater(cross,.49);self.assertLess(cross,.51)
            self.assertEqual(layer_at(p,path,cross-1e-6),'cloud')
            self.assertEqual(layer_at(p,path,cross),'upper')
            for fraction,layer in ((.2,'cloud'),(.8,'upper')):
                point=path.at(fraction)[0]
                shot=Projectile(2,'red','ciws',point,point,(0.,0.),10000,None,layer,
                                collision_radius_m=.1,interception_damage=9.)
                self.assertIsNotNone(contact_fraction(p,shot))
                wrong=replace(shot,height_layer='upper' if layer=='cloud' else 'cloud')
                self.assertIsNone(contact_fraction(p,wrong))
                _,removed,events=resolve((p,shot),{'blue':'blue','red':'red'},{},1)
                self.assertEqual(removed,{1,2});self.assertEqual(events[0]['height_layer'],layer)
                _,removed,_=resolve((p,shot),{'blue':'blue','red':'red'},{1:fraction-.05},1)
                self.assertFalse(removed)


class LayerDamageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import test_tactical_damage as damage
        damage.DamageTests.setUpClass();cls.fixture=damage.DamageTests()

    def sample(self,cross,*,ship_switch=None,target_layer='cloud'):
        from backend.high_wilderness_sidecar.tactical_damage import Edge
        from backend.high_wilderness_sidecar.tactical_gunnery import rotate,add
        from 高天荒野舰艇战术机动求解器 import LayerTransitionState
        b=self.fixture.battle();world=b.session.world
        ship=world.ships[0];m=replace(ship.motion,height_layer=target_layer)
        before=replace(world,ships=(replace(ship,motion=m),*world.ships[1:]))
        after=replace(before,fixed_step=world.fixed_step+1)
        if ship_switch is not None:
            m=replace(m,height_layer='cloud',layer_transition=LayerTransitionState('cloud','upper',1-ship_switch/60,1))
            before=replace(before,ships=(replace(ship,motion=m),*world.ships[1:]))
            after=replace(after,ships=(replace(ship,motion=replace(m,height_layer='upper',layer_transition=None)),*world.ships[1:]))
        # One narrow side-armor surface at the middle of the swept interval;
        # isolates layer timing from later contacts with the far side of a hull.
        edge=b.damage.edges[0][0]
        b.damage.edges[0]=(Edge(edge.key,(0.,-100.),(0.,100.),edge.protection,edge.thickness_mm,edge.maximum),)
        b.damage.cells[0]=();b.damage.radius[0]=100.
        state=replace(b.damage.initial,armor=((edge.maximum,),*b.damage.initial.armor[1:]))
        start=add(tuple(m.position_world_m.to_list()),rotate((-40.,0.),m.heading_rad))
        velocity=rotate((4800.,0.),m.heading_rad);vz=2400.
        p=body(hypot(4800,vz),pitch=atan2(vz,4800),z=10000-vz/60*cross)
        p=replace(p,ship_id=world.ships[1].ship_id,position=start,previous=start,velocity=velocity,deck_level=edge.key[1],
                  missile=replace(p.missile,heading=m.heading_rad))
        return b.damage.advance(before,after,(p,),state),p

    def test_ship_and_missile_switch_use_contact_time_and_total_energy(self):
        for cross,target,expected in ((.3,'cloud',0),(.7,'cloud',1),(.3,'upper',1),(.7,'upper',0)):
            result,p=self.sample(cross,target_layer=target)
            self.assertEqual(result[1].hits,expected)
            if expected:
                event=result[1].recent[-1]
                self.assertEqual(event['height_layer'],target)
                self.assertGreater(event['projectile_speed_mps'],5300)
                self.assertAlmostEqual(event['impact_fraction'],.5,delta=.001)
        for ship_switch,cross,expected in ((.4,.3,1),(.4,.7,0),(.8,.7,1),(.8,.3,0)):
            result,_=self.sample(cross,ship_switch=ship_switch)
            self.assertEqual(result[1].hits,expected)

class PursuitCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import test_missile_flight as combat
        combat.CombatTests.setUpClass();cls.fixture=combat.CombatTests()

    def test_real_vls_target_changes_layer_then_missile_dives_and_hits(self):
        b=self.fixture.battle('vls');world=b.session.world
        own,enemy=world.ships[0],world.ships[-1]
        position=replace(enemy.motion.position_world_m,x=own.motion.position_world_m.x,y=own.motion.position_world_m.y+16000)
        enemy=replace(enemy,motion=replace(enemy.motion,position_world_m=position,velocity_world_mps=replace(enemy.motion.velocity_world_mps,x=0.,y=0.)))
        b.session._world=replace(world,ships=(*world.ships[:-1],enemy));b.step()
        self.fixture.launch(b,target=True)
        switched=False;seen=set();deadline=None;rolled_back=False;trace=[]
        for _ in range(3000):
            b.step()
            if b.projectiles:
                p=next((p for p in b.projectiles if p.missile),None)
                if p:
                    deadline=deadline or p.expires
                    self.assertEqual(p.expires,deadline)
                    seen.add((p.height_layer,p.missile.maneuver_state))
                    if p.missile.age%120==0:trace.append((p.missile.age/60,p.position,p.missile.altitude_m,p.missile.pitch_rad,p.missile.seeker_state))
                    if not switched and p.missile.ever_locked and p.missile.age>180:
                        world=b.session.world;enemy=world.ships[-1]
                        b.session._world=replace(world,ships=(*world.ships[:-1],replace(enemy,motion=replace(enemy.motion,height_layer='cloud'))))
                        switched=True
                    if p.missile.maneuver_state=='diving' and not rolled_back:
                        before=(b.session.world,b.projectiles,b.damage_state,b.inventory.inventories)
                        def reject(*args):raise RuntimeError('projection failed')
                        with self.assertRaises(RuntimeError):b.step(project=reject)
                        self.assertEqual(before,(b.session.world,b.projectiles,b.damage_state,b.inventory.inventories))
                        rolled_back=True
            if any('missile' in r['projectile_type'] for r in b.damage_state.recent):break
        self.assertTrue(switched);self.assertTrue(rolled_back)
        self.assertIn(('upper','diving'),seen)
        self.assertTrue(any(layer=='cloud' for layer,_ in seen) or any(r['height_layer']=='cloud' and 'missile' in r['projectile_type'] for r in b.damage_state.recent),(trace,b.damage_state.recent))
        self.assertGreater(b.damage_state.hits,0,trace)
        self.assertEqual(b.damage_state.recent[-1]['height_layer'],'cloud')


if __name__=='__main__':unittest.main()
