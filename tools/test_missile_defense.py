from dataclasses import replace
import unittest
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import tactical_interception as collision, missile_flight as mf, tactical_settlement as settlement
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from tools.defense_fixture import design,battle,ROOT
from tools import test_tactical_ballistics as ballistic


class MissileDefenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.basic=design(cls.index,'player');cls.advanced=design(cls.index,'advanced',True)
        cls.enemy=design(cls.index,'enemy');cls.ally=design(cls.index,'ally')
        cls.template,cls.scenario,_=RealtimeViewService('backend.defense.tests')._template()
        ballistic.BallisticsTests.setUpClass();cls.f=ballistic.BallisticsTests()

    def battle(self,advanced=False,allies=False):
        designs={'player':self.advanced if advanced else self.basic}
        if allies:designs['ally']=self.ally
        designs['enemy']=self.enemy
        b=battle(designs,self.template,self.scenario,2 if allies else 1);b.enemy_fire=False;b.step();return b

    def incoming(self,b,distance=6000.,speed=1500.,hp=6.,x=0.,layer='upper',caliber=120):
        profile=self.f.profile(caliber);b._projectile_sequence+=1
        p=Projectile(b._projectile_sequence,b.session.world.ships[-1].ship_id,'test.incoming',(x,distance),(x,distance),(0.,-speed),
            b.session.world.fixed_step+4000,None,layer,(f'projectile.3a.{caliber}mm.ordinary',1),replace(profile,drag=False),
            durability=hp,maximum_durability=hp,collision_radius_m=caliber/2000)
        b.projectiles+=p,;return p

    def mode(self,b,mid,value='off',n=0,kind='mode'):
        b.observation.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=b.observation.sequence+1,ship_id=b.session.world.ships[n].ship_id,kind=kind,target=mid,value=value))

    def test_outer_target_gets_one_then_actual_interception(self):
        b=self.battle();target=self.incoming(b);before=b.inventory.inventories[0].snapshot().to_dict()['missiles']['launchers'][0]['ready']
        for _ in range(360):
            b.step()
            if any(e['projectile_id']==target.id for e in b.point_defense.recent):break
        events=[e for e in b.point_defense.recent if e['projectile_id']==target.id]
        self.assertTrue(events,b.missiles.view())
        self.assertEqual(events[0]['durability_before']-events[0]['durability_after'],6.)
        self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)
        self.assertTrue(events[0]['intercepted'])
        self.assertEqual(len(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready']),len(before)-1)
        self.assertEqual(b.states[0].shots,0)

    def test_linear_prediction_matches_sampled_hull_contacts_and_rotating_fallback(self):
        from math import cos,sin
        from backend.high_wilderness_sidecar.tactical_point_defense import predict
        b=self.battle();world=b.session.world;original=world.ships[0]
        for x in (-500.,0.,9.,80.):
            for speed in (500.,5000.):
                for yaw in (0.,.01):
                    with self.subTest(x=x,speed=speed,yaw=yaw):
                        p=self.incoming(b,distance=6000,speed=speed,x=x)
                        motion=replace(original.motion,yaw_rate_radps=yaw,
                            velocity_world_mps=replace(original.motion.velocity_world_mps,x=2.,y=15.))
                        ship=replace(original,motion=motion);candidate=replace(world,ships=(ship,*world.ships[1:]))
                        path=[];t=0.;seconds=min(30.,(p.expires-world.fixed_step)/60)
                        while True:
                            position,_=predict(p,t);dx=position[0]-motion.position_world_m.x-motion.velocity_world_mps.x*t
                            dy=position[1]-motion.position_world_m.y-motion.velocity_world_mps.y*t
                            angle=-motion.heading_rad-yaw*t;c,s=cos(angle),sin(angle)
                            path.append((t,(c*dx-s*dy,s*dx+c*dy)))
                            if t>=seconds:break
                            t=min(seconds,t+.1)
                        hits=b.damage.contacts_on_path(0,ship,path)
                        actual=b.point_defense.predict_collisions(0,p,candidate)
                        self.assertEqual(bool(actual),bool(hits))
                        if hits:
                            self.assertEqual(actual[0][0],ship.ship_id)
                            self.assertAlmostEqual(actual[0][1],min(h[0] for h in hits.values()),places=7)

    def test_no_automatic_low_speed_small_near_miss_or_inner_circle(self):
        for kwargs in (dict(speed=500),dict(hp=None,caliber=50),dict(x=500),dict(distance=1000),dict(layer='cloud')):
            with self.subTest(kwargs=kwargs):
                b=self.battle();self.incoming(b,**kwargs)
                for _ in range(4):b.step()
                self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,0)

    def test_unavailable_ciws_removes_inner_circle(self):
        b=self.battle();b.states=tuple(replace(s,point_defense=False,target_policy='hold',target=None) for s in b.states);self.incoming(b,distance=1000)
        for _ in range(4):b.step()
        self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)

    def test_failed_step_keeps_missile_and_inventory_uncommitted(self):
        b=self.battle();self.incoming(b);before=(b.projectiles,b.missiles.states,b.inventory.inventories[0].snapshot())
        def fail(*args):raise RuntimeError('rollback')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(before,(b.projectiles,b.missiles.states,b.inventory.inventories[0].snapshot()))
        b.step();self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)

    def test_integrated_devices_work_without_computer_and_sensor_toggle_is_separate(self):
        b=self.battle(True);self.mode(b,'fire_control');self.mode(b,'sensor_upper_starboard');self.incoming(b)
        b.step();self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)
        world=b.session.world;available=b._availability(world)[1]
        self.assertFalse(b.observation.sources(0,b.projectiles[0].id,world,available)[0])
        self.assertTrue(any(d['integrated'] and d['used'] for d in b.observation.frame.devices if d['ship_index']==0))
        self.mode(b,'weapon_upper_port',kind='sensor_mode');b.step()
        self.assertEqual(b.session.world.ships[0].resources.modes[b._indices[0]['weapon_upper_port']],'active')
        self.assertEqual(next(d for d in b.observation.frame.devices if d['ship_index']==0 and d['module_id']=='weapon_upper_port')['reason'],'sensor_disabled')
        ordinary=self.battle();self.mode(ordinary,'fire_control');self.incoming(ordinary);ordinary.step()
        self.assertEqual(ordinary.missiles.states[0,'weapon_upper_port'].shots,0)

    def test_data_link_coordinates_but_separate_ships_can_duplicate_without_link(self):
        for linked,expected in ((True,1),(False,2)):
            b=self.battle(allies=True)
            if not linked:self.mode(b,'datalink.0',n=1)
            self.incoming(b);b.step()
            self.assertEqual(sum(s.shots for (n,_),s in b.missiles.states.items() if n<2),expected)

    def test_proximity_sweep_is_separate_from_body_and_once_only(self):
        b=self.battle();target=self.incoming(b,distance=50.,hp=12)
        target=replace(target,position=(50.,0.),previous=(50.,0.),velocity=(-5000.,0.))
        shot=replace(target,id=target.id+1,ship_id=b.session.world.ships[0].ship_id,position=(0.,7.),previous=(0.,7.),velocity=(2000.,0.),
                     durability=3,collision_radius_m=.04,interception_radius_m=8.,interception_damage=6.)
        updated,removed,events=collision.resolve((target,shot),b.damage.sides,{},b.session.world.fixed_step)
        self.assertEqual(removed,{shot.id});self.assertEqual(updated[target.id].durability,6);self.assertEqual(len(events),1)
        self.assertIsNone(collision.contact_fraction(shot,target))
        self.assertFalse(collision.resolve((target,replace(shot,position=(0.,9.))),b.damage.sides,{},b.session.world.fixed_step)[1])
        self.assertEqual(shot.collision_radius_m,.04)
        for changed in (replace(shot,height_layer='cloud'),replace(shot,ship_id=target.ship_id)):
            self.assertFalse(collision.resolve((target,changed),b.damage.sides,{},b.session.world.fixed_step)[1])
        self.assertFalse(collision.resolve((replace(target,durability=None),shot),b.damage.sides,{},b.session.world.fixed_step)[1])
        self.assertFalse(collision.resolve((target,shot),b.damage.sides,{target.id:.01},b.session.world.fixed_step)[1])

    def test_reacquires_after_other_weapon_removes_target_without_new_round(self):
        b=self.battle();old=self.incoming(b);b.step()
        interceptor=next(p for p in b.projectiles if p.missile)
        b.projectiles=tuple(p for p in b.projectiles if p.id!=old.id)
        new=self.incoming(b,distance=6500)
        # Two other incoming targets may not be visible to the seeker.
        self.incoming(b,distance=7000,layer='cloud')
        self.incoming(b,distance=20000)
        b.step();changed=next(p for p in b.projectiles if p.id==interceptor.id)
        self.assertEqual(changed.missile.target_id,new.id)
        self.assertEqual(changed.interception_target_id,new.id)
        self.assertEqual(changed.missile.born_step,interceptor.missile.born_step)
        self.assertEqual(changed.expires,interceptor.expires)
        self.assertEqual(changed.missile.age,interceptor.missile.age+1)
        self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)

    def test_supplement_only_after_real_hit_and_no_ciws_inner_circle(self):
        b=self.battle();b.states=tuple(replace(s,point_defense=False,target_policy='hold',target=None) for s in b.states)
        target=self.incoming(b,hp=12)
        hit=False
        for _ in range(300):
            b.step()
            events=[e for e in b.point_defense.recent if e['projectile_id']==target.id]
            if not events:self.assertLessEqual(b.missiles.states[0,'weapon_upper_port'].shots,1)
            else:
                hit=True
                self.assertEqual(events[0]['durability_after'],6)
                if b.missiles.states[0,'weapon_upper_port'].shots==2:break
        self.assertTrue(hit);self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,2)

    def test_advanced_ciws_can_intercept_with_no_global_computer(self):
        b=self.battle(True);self.mode(b,'fire_control');self.mode(b,'sensor_upper_starboard')
        target=self.incoming(b,distance=1000,speed=1000,hp=3)
        for _ in range(100):
            b.step()
            if b.point_defense.kills:break
        self.assertGreater(b.states[0].shots,0)
        self.assertEqual(b.missiles.states[0,'weapon_upper_port'].shots,0)
        self.assertTrue(any(e['projectile_id']==target.id and e['intercepted'] for e in b.point_defense.recent))

    def test_mass_and_preparation_survive_result_roundtrip(self):
        from backend.high_wilderness_sidecar import battle_preparation as bp
        a=self.battle();b=self.battle(True)
        basic=a.session.world.ships[0].height_navigation;advanced=b.session.world.ships[0].height_navigation
        self.assertAlmostEqual(advanced.dry_mass_kg-basic.dry_mass_kg,1900.)
        self.assertGreater(advanced.base_duration_s,basic.base_duration_s)
        self.assertEqual(bp.restore_design(self.advanced.archive(),self.index),self.advanced)
        self.incoming(b);b.step();b.withdraw()
        result=settlement.validate_result(settlement.capture(b))
        after=result['ships'][0]['after'];bp.validate_record(after,self.advanced)
        self.assertEqual(len(after['state']['missiles']['launchers'][0]['ready']),7)
        self.assertFalse(b.projectiles);self.assertFalse(b.missiles.pending)

    def test_vls_manual_interception_delayed_departure_and_cross_layer(self):
        from backend.high_wilderness_sidecar import persistent_ship as ps, missile_logistics as ml
        from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
        d=design(self.index,'vls',vls=True)
        b=battle({'player':d,'enemy':self.enemy},self.template,self.scenario);b.enemy_fire=False
        inv=b.inventory.inventories[0];ml.apply(inv,dict(module_id='weapon_upper_port',kind='auto_fire',enabled=False));b.step()
        # View and command use the actual integer identity, not a ship proxy.
        target=self.incoming(b,distance=12000,layer='cloud');b.step();world=b.session.world
        def order(kind,**values):
            command=dict(epoch=world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=world.ships[0].ship_id,
                         order=dict(module_id='weapon_upper_port',kind=kind,**values))
            b.missiles.submit(command);return command
        with self.assertRaises(ps.ContractError):order('target',target_id=world.ships[-1].ship_id)
        order('attack_layer',layer='cloud');order('target',target_id=target.id);command=order('fire')
        self.assertFalse(b.missiles.submit(command));b.step();self.assertEqual(len(b.missiles.pending),1)
        pending=b.missiles.pending[0];self.assertEqual(pending.projectile.height_layer,'cloud')
        self.assertEqual(pending.projectile.missile.ratio,.7)
        self.assertEqual(len(inv._value['missiles']['launchers'][0]['ready']),16) # original transaction snapshot unchanged
        self.assertEqual(len(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready']),15)
        b.step(device_operations=(DeviceOperation(world.epoch,world.ships[0].ship_id,'weapon_upper_port',1,'damage',10000.,b.session.world.fixed_step,'opening'),))
        for _ in range(pending.due_step-b.session.world.fixed_step):b.step()
        p=next(p for p in b.projectiles if p.id==pending.projectile.id)
        self.assertEqual(p.missile.age,0);self.assertEqual(p.height_layer,'cloud')
        self.assertEqual(p.expires-pending.due_step,p.missile.profile.lifetime('cloud'))


if __name__=='__main__':unittest.main()
