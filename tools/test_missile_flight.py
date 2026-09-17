"""5e actual launcher consumption, immutable flight and continuous contacts."""
from dataclasses import replace
from math import hypot, pi, cos, sin
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import missile_flight as mf, missile_logistics as ml, persistent_ship as ps
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical_ballistics import flight_segment, FlightProfile
from backend.high_wilderness_sidecar.tactical_interception import contact_fraction,resolve
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.test_missile_logistics import document,ROOT,LAUNCHER,MODEL


def missile(speed=1000.,**kw):
    p=mf.profiles()[MODEL]
    f=mf.Flight(p,'blast',0,0.,(10000.,0.),(0.,0.),None,phase='coast',age=p.boost_steps+p.engine_steps)
    return replace(Projectile(1,'blue',LAUNCHER,(0.,0.),(0.,0.),(speed,0.),10000,None,'upper',
        (MODEL+'.blast',1),p.ballistics(1.),durability=p.durability,maximum_durability=p.durability,
        collision_radius_m=.04,missile=f),**kw)


class FlightTests(unittest.TestCase):
    def test_constant_acceleration_curved_segment(self):
        path=mf.Segment((0.,0.),(100.,0.),100.,0.,2.,40.,.2)
        pos,vel=path.at(1)
        self.assertAlmostEqual(hypot(*vel),108)
        # Independent midpoint integration verifies both curvature and acceleration.
        dt=.2/20000
        ref=[sum((100+40*(i+.5)*dt)*trig(2*(i+.5)*dt)*dt for i in range(20000)) for trig in (cos,sin)]
        for a,b in zip(pos,ref):self.assertAlmostEqual(a,b,places=7)
        straight=replace(path,angular_rate=0.)
        self.assertEqual(straight.at(1)[0],(20.8,0.))

    def test_three_phases_turn_loss_and_zero_speed(self):
        f=missile().missile;p=f.profile
        boost=mf.steer(replace(f,age=0),100.,(0.,1000.),(0.,0.))
        self.assertEqual(boost.angular_rate,0.)
        self.assertEqual(boost.acceleration,p.boost_acceleration)
        coast=mf.steer(f,1000.,(0.,1000.),(0.,0.))
        self.assertAlmostEqual(coast.acceleration,-p.max_g*9.8)
        engine=mf.steer(replace(f,age=p.boost_steps),1000.,(0.,1000.),(0.,0.))
        self.assertAlmostEqual(engine.acceleration,p.engine_acceleration-p.max_g*9.8)
        self.assertEqual(mf.steer(f,1000.,(1000.,0.),(0.,0.)).acceleration,0.)
        zero=mf.steer(f,0.,(0.,1000.),(0.,0.))
        self.assertEqual((zero.angular_rate,zero.acceleration),(0.,0.))
        restart=mf.steer(replace(f,age=p.boost_steps),0.,(0.,1000.),(0.,0.))
        self.assertEqual(restart.angular_rate,0.);self.assertGreater(restart.acceleration,0.)
        self.assertLess(abs(mf.steer(f,10.,(0.,1000.),(0.,0.)).angular_rate),abs(mf.steer(f,100.,(0.,1000.),(0.,0.)).angular_rate))
        cross=mf.steer(replace(f,age=p.boost_steps,ratio=.7),p.speed_cap*.7,(1000.,0.),(0.,0.))
        self.assertEqual(cross.acceleration,0.)
        self.assertEqual(p.lifetime(),p.boost_steps+p.engine_steps+p.coast_steps)

    def test_fast_curved_swept_interception_and_hp(self):
        for speed in (2000.,5000.):
            target=missile(speed)
            target=replace(target,missile=replace(target.missile,angular_rate=.5,acceleration=80.))
            center=flight_segment(target).at(.5)[0]
            shot=Projectile(2,'red','ciws',(center[0],center[1]-10),(center[0],center[1]-10),(0.,1200.),100,
                            None,'upper',collision_radius_m=.015,interception_damage=1.)
            self.assertIsNotNone(contact_fraction(target,shot))
            active,removed,events=resolve((target,shot),{'blue':'blue','red':'red'},{},1)
            self.assertEqual(active[1].durability,2.);self.assertEqual(removed,{2})
            self.assertEqual(active[1].missile,target.missile)
            self.assertFalse(events[0]['intercepted'])
            self.assertIsNone(contact_fraction(target,replace(shot,position=(center[0],center[1]+100))))


class CombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.designs={}
        for kind in ('turret','vls'):
            doc,dep=document(cls.index,launcher='gtw.module.launcher.5c.vls' if kind=='vls' else 'gtw.module.launcher.5c.small.rocket.active_radar')
            for m in doc['outfit']['modules']:
                if kind=='vls' and m['id']==LAUNCHER:m['placement']['deck_id']='deck.0'
                if m['id']=='sensor_upper_starboard':m['prototype']=dict(id='gtw.module.sensor.5d.radar',version=1)
                if m['id']=='fire_control':m['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
            cls.designs[kind]=bp.compile_design(doc,cls.index,dep,load_current(ROOT),ship_id='ship.missile.'+kind)
        cls.template,cls.scenario,_=RealtimeViewService('backend.missile-flight')._template()

    def test_actual_catalog_outfits_import_compile_and_restore(self):
        from tempfile import TemporaryDirectory
        from backend.high_wilderness_sidecar.server import SidecarServer
        with TemporaryDirectory() as folder:
            service=SidecarServer('backend.fixture-import.5e',settlement_dir=folder).preparation
            for identity in ('rocket','turbojet','vls'):
                resource=next(d for d,_ in service.editor.index.resources.values() if d['id']=='gtw.outfit.5e.'+identity)
                key='instance.fixture.5e.'+identity
                service.import_ship(dict(instance_id=key,source=dict(kind='resource',value=resource['key'])))
                from backend.high_wilderness_sidecar.preparation_service import SUPPLY_ID
                with service.store.connection() as db:design,record=service.store._inputs(db,[key],SUPPLY_ID)[0][0]
                self.assertEqual(bp.restore_design(design.archive(),service.editor.index),design)
                self.assertTrue(record['state']['missiles']['launchers'])

    def battle(self,kind='turret',model=MODEL,head='blast'):
        d=self.designs[kind];record=bp.new_record(d,'instance.missile.'+kind)
        inv=InventorySession(d.resources,ps.parse_instance(record['state'],d.resources))
        orders=[]
        if kind=='vls':orders.append(dict(module_id=LAUNCHER,kind='model',model_id=model))
        orders.extend((dict(module_id=LAUNCHER,kind='warhead',warhead_id=head),dict(module_id=LAUNCHER,kind='load')))
        ml.prepare(inv,orders,{'cargo:'+g['id']:100000 for g in inv._definition['goods']})
        record['state']=inv.snapshot().to_dict()
        b=deployment.build([(d,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False;b.step()
        return b

    def order(self,b,kind,**kw):
        c=dict(epoch=b.session.world.epoch,generation=0,sequence=b.missiles.sequence+1,ship_id=b.session.world.ships[0].ship_id,
               order=dict(module_id=LAUNCHER,kind=kind,**kw))
        b.missiles.submit(c);return c

    def point(self,b,distance=10000):
        m=b.session.world.ships[0].motion
        return [m.position_world_m.x,m.position_world_m.y+distance]

    def launch(self,b,target=False):
        self.order(b,'target',target_id=b.session.world.ships[-1].ship_id) if target else self.order(b,'point',point_m=self.point(b))
        command=self.order(b,'fire')
        for _ in range(180):
            b.step()
            if b.missiles.states[0,LAUNCHER].shots:return command
        self.fail(b.missiles.view())

    def test_real_turret_fire_and_default_off(self):
        b=self.battle();before=len(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready'])
        self.assertFalse(b.inventory.inventories[0]._value['missiles']['launchers'][0]['auto_fire'])
        self.launch(b)
        self.assertEqual(len(b.projectiles),1);self.assertEqual(b.projectiles[0].missile.phase,'boost')
        self.assertEqual(len(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready']),before-1)
        self.assertEqual(b.missiles.view()['ships'][0]['launchers'][0]['shots'],1)
        for _ in range(10):b.step()
        self.assertEqual(b.missiles.states[0,LAUNCHER].shots,1)
        self.assertEqual(b.projectiles[0].missile.age,10)

    def test_opt_in_auto_targeting_and_cross_layer_round_identity(self):
        b=self.battle('vls');self.order(b,'auto_fire',enabled=True)
        b.step();self.assertEqual(b.missiles.states[0,LAUNCHER].shots,1)
        self.assertEqual(b.missiles.pending[0].projectile.aimed_ship_id,b.session.world.ships[-1].ship_id)
        self.order(b,'auto_fire',enabled=False)
        before=b.missiles.sequence
        with self.assertRaises(ps.ContractError):self.order(b,'attack_layer',layer='rain')
        self.assertEqual(b.missiles.sequence,before)
        self.order(b,'attack_layer',layer='cloud');self.order(b,'point',point_m=self.point(b))
        self.order(b,'fire')
        for _ in range(100):
            b.step()
            if b.missiles.states[0,LAUNCHER].shots==2:break
        d=b.missiles.pending[-1];p=d.projectile
        self.assertEqual(p.height_layer,'cloud');self.assertEqual(p.missile.ratio,.7)
        self.assertAlmostEqual(hypot(*p.velocity),p.missile.profile.launch_speed*.7)
        self.assertEqual(p.expires-d.due_step,p.missile.profile.lifetime())
        self.order(b,'attack_layer',layer='upper')
        self.assertEqual(b.missiles.pending[-1],d)

    def test_unsupported_seeker_does_not_launch_as_radar(self):
        b=self.battle('vls',model='gtw.missile.5c.small.interceptor')
        self.order(b,'point',point_m=self.point(b))
        # All 17 current types now fly. Simulate a missing flight registration
        # to retain this explicit no-silent-fallback boundary test.
        from unittest.mock import patch
        supported={k:v for k,v in mf.profiles().items() if not v.interceptor}
        with patch.object(mf,'profiles',return_value=supported):
            with self.assertRaises(ps.ContractError):self.order(b,'fire')
            self.order(b,'auto_fire',enabled=True);b.step()
        self.assertFalse(b.missiles.pending);self.assertEqual(b.missiles.states[0,LAUNCHER].shots,0)
        self.assertEqual(b.missiles.states[0,LAUNCHER].status,'model_unavailable')

    def test_vls_delay_is_irrevocable_and_does_not_age(self):
        b=self.battle('vls');self.launch(b)
        self.assertFalse(b.projectiles);d=b.missiles.pending[0]
        self.assertEqual(d.projectile.missile.age,0)
        self.order(b,'clear');self.order(b,'attack_layer',layer='cloud')
        b.step(device_operations=(DeviceOperation(b.session.world.epoch,b.session.world.ships[0].ship_id,LAUNCHER,1,'damage',1e9,b.session.world.fixed_step,'opening'),))
        while b.session.world.fixed_step<d.due_step:b.step()
        self.assertEqual(len(b.projectiles),1);p=b.projectiles[0]
        self.assertEqual(p.height_layer,'upper');self.assertEqual(p.missile.age,0)
        self.assertEqual(p.expires-b.session.world.fixed_step,p.missile.profile.lifetime())
        self.assertFalse(b.missiles.pending)

    def test_vls_fires_all_azimuths_without_computing_turret_occlusion(self):
        with patch('backend.high_wilderness_sidecar.tactical_missiles.horizontal_fire_arc', side_effect=AssertionError('VLS must not solve a horizontal arc')):
            for angle in (0., pi/2, pi, -pi/2):
                b=self.battle('vls');motion=b.session.world.ships[0].motion
                origin=b.missiles.geometry[0,LAUNCHER].anchor
                from backend.high_wilderness_sidecar.tactical_gunnery import rotate,add
                origin=add(tuple(motion.position_world_m.to_list()),rotate(origin,motion.heading_rad))
                self.order(b,'point',point_m=[origin[0]+sin(angle)*10000,origin[1]+cos(angle)*10000])
                self.order(b,'fire');b.step()
                self.assertEqual(b.missiles.states[0,LAUNCHER].shots,1)
                self.assertEqual(len(b.missiles.pending),1)
                velocity=b.missiles.pending[0].projectile.velocity
                self.assertAlmostEqual(velocity[0]/hypot(*velocity),sin(angle),places=4)
                self.assertAlmostEqual(velocity[1]/hypot(*velocity),cos(angle),places=4)

    def test_vls_vertical_launch_still_requires_range_and_working_equipment(self):
        b=self.battle('vls');before=b.inventory.inventories[0].snapshot()
        self.order(b,'point',point_m=self.point(b,100000));self.order(b,'fire');b.step()
        self.assertEqual(b.missiles.states[0,LAUNCHER].status,'out_of_range')
        self.assertFalse(b.missiles.pending)
        self.assertEqual(b.inventory.inventories[0].snapshot().to_dict()['missiles']['launchers'][0]['ready'],before.to_dict()['missiles']['launchers'][0]['ready'])
        self.order(b,'point',point_m=self.point(b));self.order(b,'fire')
        b.step(device_operations=(DeviceOperation(b.session.world.epoch,b.session.world.ships[0].ship_id,LAUNCHER,1,'damage',1e9,b.session.world.fixed_step,'opening'),))
        self.assertEqual(b.missiles.states[0,LAUNCHER].shots,0)
        self.assertFalse(b.missiles.pending)
        self.assertFalse(any(e['kind']=='fired' for e in b.missiles.recent))

    def test_failed_step_and_duplicate_command_do_not_double_spend(self):
        b=self.battle('vls');self.order(b,'point',point_m=self.point(b));c=self.order(b,'fire')
        before=b.inventory.inventories[0].snapshot();states=b.missiles.states.copy()
        def fail(*args):raise RuntimeError('forced failure')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.inventory.inventories[0].snapshot(),before);self.assertEqual(b.missiles.states,states)
        self.assertFalse(b.missiles.pending);self.assertFalse(b.missiles.submit(c))
        b.step();self.assertEqual(len(b.missiles.pending),1);self.assertEqual(b.missiles.states[0,LAUNCHER].shots,1)

    def test_withdraw_spends_pending_round(self):
        b=self.battle('vls');self.launch(b);b.withdraw()
        self.assertFalse(b.missiles.pending);self.assertEqual(b.ending['removed_projectiles'],1)
        self.assertEqual(len(b.inventory.inventories[0]._value['missiles']['launchers'][0]['ready']),15)
        from backend.high_wilderness_sidecar import tactical_settlement as settlement
        result=settlement.validate_result(settlement.capture(b))
        after=result['ships'][0]['after'];bp.validate_record(after,self.designs['vls'])
        rebuilt=deployment.build([(self.designs['vls'],after)],after['state']['instance_id'],self.template,self.scenario)[0]
        self.assertEqual(len(rebuilt.inventory.inventories[0]._value['missiles']['launchers'][0]['ready']),15)
        self.assertFalse(rebuilt.missiles.pending)

    def test_actual_target_launch_reaches_enemy_and_resolves_speed_damage(self):
        b=self.battle();self.launch(b,target=True)
        self.assertEqual(b.projectiles[0].missile.seeker_state,'tracking')
        initial_heading=b.projectiles[0].missile.heading
        for _ in range(600):
            b.step()
            if b.damage_state.hits or b.ending:break
            for p in b.projectiles:
                if p.missile and p.missile.phase=='boost':self.assertAlmostEqual(p.missile.heading,initial_heading)
        self.assertGreater(b.damage_state.hits,0)
        hit=b.damage_state.recent[-1]
        self.assertEqual(hit['source_ship_id'],b.session.world.ships[0].ship_id)
        self.assertEqual(hit['projectile_type'],MODEL+'.blast')
        self.assertGreater(hit['projectile_speed_mps'],30.)

    def test_seeker_activation_and_changed_layer_weather_do_not_steer_from_hidden_truth(self):
        b=self.battle();world=b.session.world;source,enemy=world.ships
        def at(x,layer):
            return replace(world,ships=(source,replace(enemy,motion=replace(enemy.motion,
                position_world_m=replace(enemy.motion.position_world_m,x=x,y=0),height_layer=layer))))
        p=missile(ship_id=source.ship_id)
        p=replace(p,missile=replace(p.missile,born_step=world.fixed_step,age=0,launch_point=(30000.,0.)))
        off=mf.prepare(p,at(10000.,'upper'),b._sides)
        self.assertEqual(off.missile.seeker_state,'midcourse');self.assertIsNone(off.missile.target_id)
        p=replace(p,missile=replace(p.missile,launch_point=(1000.,0.)))
        wrong_layer=mf.prepare(p,at(10000.,'cloud'),b._sides)
        self.assertEqual(wrong_layer.missile.seeker_state,'search')
        tracked=mf.prepare(p,at(10000.,'upper'),b._sides)
        self.assertEqual(tracked.missile.seeker_state,'tracking')
        retained=mf.prepare(tracked,at(10000.,'cloud'),b._sides)
        self.assertEqual(retained.missile.seeker_state,'tracking');self.assertEqual(retained.height_layer,'upper')
        lost=mf.prepare(retained,at(10000.,'rain'),b._sides)
        self.assertEqual(lost.missile.seeker_state,'lost');self.assertIsNone(lost.missile.target_id)
        reacquired=mf.prepare(lost,at(5000.,'rain'),b._sides)
        self.assertEqual(reacquired.missile.target_id,enemy.ship_id)

    def test_high_speed_ship_sweep_all_heads_and_friendly_layer_filter(self):
        from tools.test_tactical_damage import DamageTests
        from math import atan2
        for model in (MODEL,'gtw.missile.5c.medium.turbojet.infrared'):
            for head in ('blast','incendiary'):
                for speed in (2000.,5000.):
                    b=self.battle('vls',model,head)
                    p=DamageTests().shell(b,(-30,0),(-30+speed/60,0),target=0)
                    profile=mf.profiles()[model]
                    f=replace(missile().missile,profile=profile,warhead=head,heading=atan2(p.velocity[1],p.velocity[0]),born_step=b.session.world.fixed_step-10000,age=10000)
                    p=replace(p,missile=f,height_layer='upper',projectile_key=(model+'.'+head,1),flight_profile=profile.ballistics(1.))
                    b.projectiles=(p,);b.step()
                    self.assertEqual(b.damage_state.hits,1)
                    self.assertAlmostEqual(b.damage_state.recent[0]['projectile_speed_mps'],speed)
        for friendly,layer in ((True,'upper'),(False,'cloud')):
            b=self.battle();p=DamageTests().shell(b,(-30,0),(50,0),target=0)
            f=replace(missile().missile,heading=atan2(p.velocity[1],p.velocity[0]))
            b.projectiles=(replace(p,ship_id=b.session.world.ships[0].ship_id if friendly else p.ship_id,
                                  height_layer=layer,missile=f,projectile_key=(MODEL+'.blast',1)),)
            b.step();self.assertEqual(b.damage_state.hits,0)

    def test_lifetime_zero_speed_and_coast_detection(self):
        b=self.battle();world=b.session.world;p=missile(0.,ship_id=world.ships[0].ship_id,position=(100000.,0.),expires=world.fixed_step+5)
        p=replace(p,missile=replace(p.missile,born_step=world.fixed_step-p.missile.age))
        b.projectiles=(p,);b._projectile_sequence=p.id
        targets=b.observation.targets(world,(p,));target=next(t for t in targets if t.id==p.id)
        self.assertEqual(target.kind,'missile');self.assertFalse(target.powered)
        for _ in range(4):b.step();self.assertEqual(len(b.projectiles),1)
        b.step();self.assertFalse(b.projectiles);self.assertEqual(b.damage_state.expired,1)

    def test_actual_infrared_ciws_intercepts_durable_coasting_missile(self):
        from tools.test_tactical_observation import ObservationTests
        ObservationTests.setUpClass();b=ObservationTests().battle(kind='infrared')
        own=b.session.world.ships[0];enemy=b.session.world.ships[-1]
        pos=(own.motion.position_world_m.x,own.motion.position_world_m.y+1200)
        p=missile(1000.,ship_id=enemy.ship_id,position=pos,previous=pos,velocity=(0.,-1000.),expires=1000)
        p=replace(p,missile=replace(p.missile,born_step=-p.missile.age,heading=-pi/2,launch_point=(pos[0],pos[1]-50000)))
        b.projectiles=(p,);b._projectile_sequence=p.id
        for _ in range(180):
            b.step()
            if b.point_defense.kills:break
        self.assertEqual(b.point_defense.kills,1)
        self.assertEqual([e['durability_after'] for e in b.point_defense.recent if e['projectile_id']==p.id],[2.,1.,0.])
        self.assertFalse(any(e['projectile_id']==p.id for e in b.damage_state.recent))


if __name__=='__main__':unittest.main()
