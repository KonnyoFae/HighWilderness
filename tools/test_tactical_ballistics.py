"""3a authority checks: new guns, timed drag flight and swept collision."""
import json
import unittest
from dataclasses import replace
from math import hypot,pi
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.high_wilderness_sidecar import tactical_ballistics as flight
from backend.high_wilderness_sidecar import tactical_gunnery as tg, persistent_ship as ps
from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment as deployment, tactical_settlement as settlement
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_damage import Edge
from tools.test_battle_preparation import fixture
from tools import test_tactical_damage as damage_fixtures, test_tactical_gunnery as gun_fixtures
GUN=gun_fixtures.GUN

ROOT=Path(__file__).resolve().parents[1]


class BallisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.policy=load_current(ROOT)
        cls.designs={}
        for caliber in (30,50,75,120):
            doc,loadout,_=fixture(cls.index)
            next(m for m in doc['outfit']['modules'] if m['id']==GUN)['prototype']=dict(id=f'gtw.module.gun.{caliber}mm',version=1)
            cls.designs[caliber]=bp.compile_design(doc,cls.index,loadout,cls.policy,ship_id=f'ship.caliber.{caliber}')
        cls.template,cls.scenario,_=RealtimeViewService('backend.ballistics')._template()
        gun_fixtures.GunneryTests.setUpClass()

    def profile(self,caliber=75,kind='ordinary'):
        p=next(p for p in self.policy['projectiles'] if p['id']==f'projectile.3a.{caliber}mm.{kind}')
        return flight.compile_profile(p,1200)

    def battle(self,caliber=75,kind='ordinary',layer='upper',speed=None):
        design=self.designs[caliber]
        if layer!='upper' or speed is not None:
            a=design.archive();a['deployment']['height_layer']=layer
            if speed is not None:
                next(p for p in a['policy']['projectiles'] if p['id']==f'projectile.3a.{caliber}mm.{kind}')['speed_mmps']=speed*1000
            design=bp.compile_design(a['document'],self.index,a['deployment'],a['policy'],ship_id=a['ship_id'])
        record=bp.new_record(design,f'instance.caliber.{caliber}')
        recipe=f'recipe.3a.{caliber}mm.{kind}'
        spec=design.resources.definition()['weapons'][0]
        record['state']['weapons'][0].update(recipe_id=recipe,ready_rounds=spec['ready_capacity'])
        record['state']['magazines'][0]['quantity']=100
        record['state']['cargo']=[dict(good_id='cargo.special_alloy',quantity=5),dict(good_id='cargo.high_energy_fuel',quantity=5)]
        b=deployment.build([(design,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False
        return b

    def send(self,b,kind,**args):
        return gun_fixtures.GunneryTests().send(b,kind,**args)

    def shoot(self,b,distance=500):
        x,y=b.view()['weapons'][0]['origin_m']
        self.send(b,'mode',mode='manual');self.send(b,'fire',point=[x,y+distance]);b.step()
        self.assertEqual(b.states[0].shots,1,b.states[0].status)
        return next(p for p in b.projectiles if p.ship_id==b.session._direct)

    def test_every_caliber_and_type_launches_its_own_profile_and_damage_mapping(self):
        for caliber in self.designs:
            for kind in ('ordinary','armor_piercing','incendiary'):
                with self.subTest(caliber=caliber,kind=kind):
                    b=self.battle(caliber,kind);p=self.shoot(b)
                    expected=self.profile(caliber,kind)
                    self.assertEqual(p.flight_profile,expected)
                    self.assertAlmostEqual(hypot(*p.velocity),expected.muzzle_speed_mps)
                    self.assertEqual(p.expires-b.session.world.fixed_step,expected.lifetime_steps)
                    self.assertIn(p.projectile_key,b.damage.profiles)
                    self.assertEqual(b.inventory.inventories[0]._cooldown[GUN]-b.session.world.fixed_step,b.guns[0].cooldown_steps)

    def test_new_guns_prepare_consume_save_and_reenter_with_exact_ammunition(self):
        for caliber,design in self.designs.items():
            with self.subTest(caliber=caliber),TemporaryDirectory() as directory:
                store=PreparationStore(directory,self.index);identity=f'instance.caliber.{caliber}'
                store.create_ship(design,identity)
                store.provision_supply(dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.guns',revision=0,
                    ammunition_resources=100,cargo=[]),self.policy['goods'])
                draft=store.draft('preparation.guns',[identity],'supply.guns')
                row=draft['ships'][0];row['magazines'][0]['quantity']=100
                recipe=f'recipe.3a.{caliber}mm.ordinary'
                row['weapons'][0].update(action='preload',recipe_id=recipe,batches=1)
                saved=store.commit(draft);record=saved['ships'][0]['after']
                spec=next(r for r in self.policy['recipes'] if r['id']==recipe)
                self.assertEqual(record['state']['magazines'][0]['quantity'],100-spec['ammo_cost'])
                b=deployment.build([(design,record)],identity,self.template,self.scenario)[0];b.enemy_fire=False
                self.shoot(b);b.withdraw()
                result=settlement.validate_result(settlement.capture(b));after=result['ships'][0]['after']
                # Disk serialization and design restoration reproduce all saved state.
                path=Path(directory)/'result.json';path.write_text(ps.encode(after),encoding='utf-8')
                restored=bp.restore_design(json.loads(design.archive_json),self.index)
                other=deployment.build([(restored,json.loads(path.read_text(encoding='utf-8')))],identity,self.template,self.scenario)[0]
                self.assertEqual(other.inventory.inventories[0]._value['weapons'],after['state']['weapons'])
                self.assertEqual(other.inventory.inventories[0]._value['magazines'],after['state']['magazines'])
                self.assertEqual(other._gun_flights[0][recipe],self.profile(caliber))

    def test_cross_layer_symmetry_ttl_identity_and_two_layer_rejection(self):
        shots=[]
        for own,target in [('upper','cloud'),('rain','cloud')]:
            b=self.battle(layer=own)
            before=b.sequence
            with self.assertRaises(ps.ContractError):self.send(b,'layer',layer='rain' if own=='upper' else 'upper')
            self.assertEqual(b.sequence,before)
            self.send(b,'layer',layer=target);p=self.shoot(b);shots.append(p)
            self.assertEqual(p.height_layer,target)
            self.assertAlmostEqual(hypot(*p.velocity),self.profile().muzzle_speed_mps*flight.CROSS_LAYER_SPEED)
            self.send(b,'layer',layer=None);self.send(b,'ammunition',recipe_id='recipe.3a.75mm.armor_piercing')
            b.step();current=next(q for q in b.projectiles if q.id==p.id)
            self.assertEqual((current.height_layer,current.expires,current.projectile_key),(target,p.expires,p.projectile_key))
            self.assertLess(hypot(*current.velocity),hypot(*p.velocity))
        self.assertEqual(shots[0].flight_profile,shots[1].flight_profile)
        self.assertLess(flight.reference_range(self.profile(),.7),flight.reference_range(self.profile()))

    def test_loaded_round_retains_flight_and_inherits_translation_and_muzzle_rotation(self):
        b=self.battle();world=b.session.world;own=world.ships[0]
        motion=replace(own.motion,velocity_world_mps=replace(own.motion.velocity_world_mps,x=15,y=8),yaw_rate_radps=.2)
        b.session._world=replace(world,ships=(replace(own,motion=motion),world.ships[1]))
        self.send(b,'ammunition',recipe_id='recipe.3a.75mm.armor_piercing')
        p=self.shoot(b);m=b.session.world.ships[0].motion
        offset=tg.rotate(b.guns[0].anchor,m.heading_rad)
        inherited=(m.velocity_world_mps.x-m.yaw_rate_radps*offset[1],m.velocity_world_mps.y+m.yaw_rate_radps*offset[0])
        self.assertAlmostEqual(hypot(p.velocity[0]-inherited[0],p.velocity[1]-inherited[1]),900)
        self.assertTrue(p.projectile_key[0].endswith('.ordinary'))
        self.assertEqual(b.view()['weapons'][0]['ballistics']['speed_mps'],900)

    def test_drag_prediction_and_5000_mps_do_not_reverse_or_extend_lifetime(self):
        calibration=json.loads((ROOT/'contracts/web_bridge/fixtures/tactical-ballistics.3a.json').read_text(encoding='utf-8'))
        self.assertEqual(calibration['medium'],dict(density_kg_m3=flight.MEDIUM_DENSITY,sound_speed_mps=flight.SOUND_SPEED))
        self.assertEqual(calibration['cross_layer_speed_retention'],flight.CROSS_LAYER_SPEED)
        for caliber in self.designs:
            profile=self.profile(caliber)
            for speed in (profile.muzzle_speed_mps,2000,5000):
                p=tg.Projectile(1,'source',GUN,(0.,0.),(0.,0.),(speed,0.),profile.lifetime_steps,flight_profile=profile)
                for _ in range(profile.lifetime_steps):
                    position,velocity=flight.flight_segment(p).at(1)
                    self.assertGreater(velocity[0],0);self.assertLess(velocity[0],p.velocity[0])
                    p=replace(p,position=position,velocity=velocity)
                predicted=flight.distance_after(profile,speed,profile.lifetime_steps/60)
                self.assertAlmostEqual(p.position[0]/predicted,1,delta=.0005)
                self.assertLess(p.position[0],speed*profile.lifetime_steps/60)

    def test_2000_and_5000_mps_configuration_runs_through_real_auto_fire(self):
        for speed in (2000,5000):
            b=self.battle(speed=speed)
            self.send(b,'target',ship_id='ship.web.red',module_id=None)
            for _ in range(240):
                b.step()
                if b.damage_state.hits:break
            self.assertGreater(b.states[0].shots,0)
            self.assertGreater(b.damage_state.hits,0)
            event=b.damage_state.recent[-1]
            self.assertTrue(event['projectile_type'].endswith('75mm.ordinary'))
            self.assertGreater(event['projectile_speed_mps'],speed*.8)
            self.assertLess(event['projectile_speed_mps'],speed)

    def test_drag_intercept_predicts_moving_target_with_inherited_velocity(self):
        profile=self.profile();origin=(0.,0.);inherited=(25.,10.);target=(1000.,2000.);velocity=(45.,-12.)
        aim,time=flight.intercept(origin,inherited,target,velocity,profile,.7)
        length=hypot(*aim);muzzle=profile.muzzle_speed_mps*.7
        initial=(inherited[0]+aim[0]/length*muzzle,inherited[1]+aim[1]/length*muzzle)
        p=tg.Projectile(1,'source',GUN,origin,origin,initial,profile.lifetime_steps,flight_profile=profile)
        remaining=time
        while remaining>1e-9:
            dt=min(1/60,remaining);position,v=flight.flight_segment(p,dt).at(1);p=replace(p,position=position,velocity=v);remaining-=dt
        self.assertLess(hypot(p.position[0]-target[0]-velocity[0]*time,p.position[1]-target[1]-velocity[1]*time),.25)
        self.assertIsNone(flight.intercept(origin,inherited,(1e6,0),(0,0),profile,1))

    def collision(self,speed,*,y=0,rotation=0,moving=0,expires=10,origin=-20,step=1):
        b=damage_fixtures.DamageTests().battle();world=b.session.world;ship=world.ships[0]
        old=replace(ship,motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,x=0,y=0),heading_rad=0))
        new=replace(old,motion=replace(old.motion,position_world_m=replace(old.motion.position_world_m,x=moving/60),
            velocity_world_mps=replace(old.motion.velocity_world_mps,x=moving),heading_rad=rotation,yaw_rate_radps=rotation*60))
        before=replace(world,ships=(old,world.ships[1]));after=replace(world,fixed_step=step,ships=(new,world.ships[1]))
        b.damage.edges[0]=(Edge(('deck.0',0,'test',0),(0.,-10.),(0.,10.),1,100000,100),)
        b.damage.cells[0]=();b.damage.radius[0]=11
        p=tg.Projectile(1,world.ships[1].ship_id,GUN,(origin,y),(origin,y),(speed,0),expires,flight_profile=self.profile())
        return b,before,after,p

    def test_high_speed_sweep_moving_rotating_and_grazing_targets(self):
        for speed in (2000,5000):
            for rotation,moving,y,hit in [(0,0,0,True),(.1,80,0,True),(0,0,10,True),(0,0,10.01,False)]:
                with self.subTest(speed=speed,rotation=rotation,moving=moving,y=y):
                    b,before,world,p=self.collision(speed,y=y,rotation=rotation,moving=moving)
                    survivors,state,_=b.damage.advance(before,world,(p,),b.damage_state)
                    self.assertEqual(state.hits,int(hit))
                    if hit:
                        event=state.recent[-1]
                        self.assertGreater(event['impact_fraction'],0);self.assertLess(event['impact_fraction'],1)
                        self.assertLess(event['projectile_speed_mps'],speed)
                    else:self.assertEqual(len(survivors),1)

    def test_lifetime_deadline_allows_earlier_contact_but_never_after_or_at_expiry(self):
        b,before,world,p=self.collision(5000,expires=1)
        self.assertEqual(b.damage.advance(before,world,(p,),b.damage_state)[1].hits,1)
        self.assertEqual(b.damage.advance(before,replace(world,fixed_step=2),(p,),b.damage_state)[1].hits,0)
        distance=flight.flight_segment(p).at(1)[0][0]-p.position[0]
        p=replace(p,position=(-distance,0))
        survivors,state,_=b.damage.advance(before,world,(p,),b.damage_state)
        self.assertEqual((len(survivors),state.hits,state.expired),(0,0,1))
        self.assertAlmostEqual(state.expired_flights[0]['position_m'][0],0)

    def test_friendly_pass_through_and_impacts_resolve_by_time(self):
        b,before,world,p=self.collision(5000)
        b.damage.sides[p.ship_id]=b.damage.sides[world.ships[0].ship_id]
        survivors,state,_=b.damage.advance(before,world,(p,),b.damage_state)
        self.assertEqual((len(survivors),state.hits),(1,0))
        b.damage.sides[p.ship_id]='opponent'
        later=replace(p,id=1,position=(-40,0));earlier=replace(p,id=2,position=(-10,0))
        state=b.damage.advance(before,world,(later,earlier),b.damage_state)[1]
        self.assertEqual([hit['projectile_id'] for hit in state.recent],[2,1])


if __name__=='__main__':unittest.main()
