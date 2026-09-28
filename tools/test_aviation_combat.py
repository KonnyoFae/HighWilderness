"""AV3 real weapons, finite inventory, observed AA and swept aircraft damage."""
from copy import deepcopy
from dataclasses import replace
from math import hypot,pi
from unittest.mock import patch
import unittest
from backend.high_wilderness_sidecar import aviation_combat as combat,aviation_weapons as weapons
from backend.high_wilderness_sidecar import aviation_flight as af,aviation_logistics as al
from backend.high_wilderness_sidecar import missile_flight as mf,missile_guidance as mg
from backend.high_wilderness_sidecar import tactical_interception as collision
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from tools import test_aviation_flight as fixtures


class CombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FlightTests.setUpClass();cls.designs=fixtures.FlightTests.designs;cls.template=fixtures.FlightTests.template;cls.scenario=fixtures.FlightTests.scenario

    battle=fixtures.FlightTests.battle
    command=fixtures.FlightTests.command

    def attack(self,payload='small_bomb',model='f1',distance=1000.):
        b,key=self.battle(model)
        w=al.Work(b.inventory.inventories[0]);a=w.plane(key)
        a['loadout']={'p1':payload,'p2':payload} if model=='e1' else {'p1':payload} if payload else {}
        w.commit();self.command(b,'launch',[key]);f=b.aviation.flights[key]
        target=b.session.world.ships[-1].motion.position_world_m.to_list()
        f.update(position=(target[0],target[1]-distance),velocity=(0.,200.),speed=200.,heading=0.,
                 task=dict(kind='sea_patrol',layer='upper',point_m=target))
        return b,key

    def enemy_plane(self,b,model='f1',position=(500.,29500.),layer='upper'):
        inv=b.inventory.inventories[-1];p=inv._definition['aviation'];h=next(f['module_id'] for f in p['facilities'] if f['kind']=='aircraft_hangar');c=next(f['module_id'] for f in p['facilities'] if f['kind']=='aircraft_catapult')
        stock={'cargo:'+g['id']:100000 for g in inv._definition['goods']}
        al.prepare(inv,[dict(kind='acquire',model_id='gtw.aircraft.'+model),dict(kind='pilots',module_id=h,quantity={'f1':1,'e1':2,'b1':3}[model])],stock)
        key=inv._value['aviation']['manifest']['aircraft'][-1]['id']
        al.prepare(inv,[dict(kind='prepare',aircraft_id=key,module_id=h,loadout={}),dict(kind='load',aircraft_id=key,module_id=c)],stock)
        af.launch(b,b.session.world,b.inventory.inventories,b.aviation.flights,len(b.inventory.inventories)-1,[key],'enemy.test')
        b.aviation.flights[key].update(position=position,velocity=(0.,200.),speed=200.,heading=0.,layer=layer,
            task=dict(kind='observe',layer=layer,point_m=[position[0],position[1]+1500]))
        return key

    def test_plain_bomb_inherits_velocity_hits_real_ship_and_is_spent(self):
        b,key=self.attack();b.step();bomb=next(p for p in b.projectiles if p.source_aircraft_id==key and p.missile)
        self.assertEqual(bomb.velocity,b.aviation.flights[key]['velocity']);self.assertEqual(bomb.missile.profile.engine_steps,0)
        self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],{})
        self.assertGreater(bomb.flight_profile.maximum_range_m,0)
        for _ in range(380):
            b.step()
            if any(r['projectile_id']==bomb.id for r in b.damage_state.recent):break
        self.assertTrue(any(r['projectile_id']==bomb.id for r in b.damage_state.recent))
        self.assertTrue(b.aviation.flights[key]['return_requested']);self.assertNotIn(bomb.id,[p.id for p in b.projectiles])

    def test_each_powered_and_guided_payload_creates_its_own_weapon(self):
        for payload,model in [('small_missile','f1'),('large_missile','b1'),('small_guided_bomb','f1'),('large_guided_bomb','b1'),('large_bomb','b1')]:
            with self.subTest(payload=payload):
                b,key=self.attack(payload,model);b.step();p=next(p for p in b.projectiles if p.missile)
                self.assertEqual(p.missile.profile.model_id,'gtw.aviation.weapon.'+payload)
                self.assertEqual(p.missile.profile.target_kinds,('ship',));self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],{})
                self.assertEqual(p.missile.profile.allow_layer_change,'missile' in payload)

    def test_wrong_task_layer_arc_and_unobserved_target_do_not_spend_payload(self):
        for setting in ('task','layer','arc','unseen'):
            with self.subTest(setting=setting):
                b,key=self.attack('small_missile');f=b.aviation.flights[key]
                if setting=='task':f['task']['kind']='air_patrol'
                if setting=='layer':f['layer']='cloud';f['task']['layer']='cloud'
                if setting=='arc':f.update(heading=pi,velocity=(0.,-200.))
                if setting=='unseen':f['position']=(100000.,100000.)
                b.step();self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],{'p1':'small_missile'})

    def test_cannon_fires_and_rollback_restores_ammo_rng_events_and_sequence(self):
        b,key=self.attack(None);before=deepcopy(b.aviation.flights);snap=deepcopy(b.inventory.inventories[0]._value);seq=b._projectile_sequence
        def fail(*args):raise RuntimeError('reject weapon tick')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.aviation.flights,before);self.assertEqual(b.inventory.inventories[0]._value,snap)
        self.assertEqual(b._projectile_sequence,seq);self.assertFalse(b.aviation.recent);self.assertFalse(b.projectiles)
        b.step();self.assertEqual(af.plane(b.inventory.inventories[0],key)['cannon_rounds'],snap['aviation']['manifest']['aircraft'][0]['cannon_rounds']-1)
        self.assertEqual(b.aviation.flights[key]['shots'],1);self.assertEqual(len(b.projectiles),1)

    def test_small_interceptor_uses_air_task_and_damages_aircraft(self):
        b,key=self.attack('small_interceptor');enemy=self.enemy_plane(b)
        b.aviation.flights[key]['task']['kind']='air_patrol'
        for _ in range(200):
            b.step()
            if b.aviation.flights.get(enemy,{}).get('return_requested'):break
        self.assertTrue(any(e['weapon']=='small_interceptor' for e in b.aviation.recent if e['kind']=='fired'))
        self.assertTrue(enemy not in b.aviation.flights or b.aviation.flights[enemy]['hp']<80.)
        self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],{})

    def test_cannon_actual_aircraft_hit_triggers_independent_return(self):
        b,key=self.attack(None,distance=800.);enemy=self.enemy_plane(b);b.aviation.flights[key]['task']['kind']='air_patrol'
        for _ in range(160):
            b.step()
            if b.aviation.flights.get(enemy,{}).get('return_requested'):break
        self.assertTrue(b.aviation.flights[enemy]['return_requested']);self.assertLess(b.aviation.flights[enemy]['hp'],80.)
        self.assertEqual(af.plane(b.inventory.inventories[-1],enemy)['condition'],'damaged')
        self.assertFalse(b.aviation.flights[key]['return_requested'])

    def test_e1_self_defense_fires_while_observing_then_returns_when_empty(self):
        b,key=self.attack('self_defense','e1',distance=800.);self.enemy_plane(b)
        b.aviation.flights[key]['task']['kind']='observe'
        for _ in range(160):b.step()
        self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],{})
        self.assertTrue(b.aviation.flights[key]['return_requested'])
        self.assertEqual(sum(e['weapon']=='self_defense' for e in b.aviation.recent if e['kind']=='fired'),2)

    def test_real_swept_damage_kill_preserves_pilot_identity_in_salvage(self):
        b,key=self.attack(None);f=b.aviation.flights[key];crew=deepcopy(af.plane(b.inventory.inventories[0],key)['crew'])
        point=f['position'];shot=Projectile(100,b.session.world.ships[1].ship_id,'test',
            (point[0]-20,point[1]),(point[0]-20,point[1]),(2400.,0.),100,None,'upper',aircraft_damage=100.)
        b.projectiles=(shot,);b._projectile_sequence=100
        before=deepcopy(b.inventory.inventories[0]._value)
        def fail(*args):raise RuntimeError('reject damage tick')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertEqual(b.inventory.inventories[0]._value,before);self.assertEqual(b.aviation.flights[key]['hp'],80.)
        b.step()
        self.assertNotIn(key,b.aviation.flights);inv=b.inventory.inventories[0];a=af.plane(inv,key)
        self.assertEqual(a['location'],'destroyed');self.assertEqual(a['crew'],[])
        saved=[p for p in inv._value['aviation']['manifest']['personnel'] if p['id']==crew[0]['id']]
        self.assertEqual(len(saved),1);self.assertEqual(saved[0]['housing'],'salvage');inv.snapshot()

    def test_swept_collision_no_friendly_or_cross_layer_hit(self):
        b,key=self.attack(None);body=combat.bodies(b,b.session.world,b.inventory.inventories,b.aviation.flights)[0]
        shot=replace(body,id=2,aircraft_body=False,durability=None,aircraft_damage=100.,ship_id=b.session.world.ships[-1].ship_id)
        for changed in (replace(shot,ship_id=body.ship_id),replace(shot,height_layer='cloud')):
            self.assertFalse(collision.resolve((body,changed),b.damage.sides,{},0)[2])

    def test_guided_bomb_can_turn_but_never_gains_speed_or_changes_layer(self):
        b,key=self.attack('small_guided_bomb');b.step();p=next(p for p in b.projectiles if p.missile)
        target=mg.Contact('enemy',b._sides[1],(p.position[0]+100,p.position[1]+700),(0.,0.),'upper')
        env=mg.Environment((target,));old=hypot(*p.velocity)
        for step in range(2,80):
            p=mf.prepare(p,replace(b.session.world,fixed_step=step),b._sides,env);p=mf.advance(p)
            self.assertLessEqual(hypot(*p.velocity),old+1e-8);old=hypot(*p.velocity)
            self.assertEqual(p.height_layer,'upper');self.assertEqual(p.missile.vertical_velocity_mps,0.)
        self.assertNotEqual(p.velocity[0],0.)
        cross=replace(target,layer='cloud');p=mf.prepare(p,replace(b.session.world,fixed_step=81),b._sides,mg.Environment((cross,)))
        self.assertNotEqual(p.missile.seeker_state,'tracking');self.assertIsNone(p.missile.goal_layer)

    def test_explicit_categories_allow_future_aa_models_and_legacy_compatibility(self):
        from backend.high_wilderness_sidecar.missile_flight_catalog import normalize
        value=deepcopy(mf.catalog());value['models'][0]['target_kinds']=['aircraft','projectile'];normalized=normalize(value)
        p=mf.Profile(**normalized['models'][0]);self.assertTrue(weapons.can_target(p,'missile'));self.assertTrue(weapons.can_target(p,'aircraft'));self.assertFalse(weapons.can_target(p,'ship'))
        interceptor=mf.profiles()['gtw.missile.5c.small.interceptor'];self.assertTrue(weapons.can_target(interceptor,'aircraft'))
        value['models'][0]['target_kinds']=['unknown']
        with self.assertRaises(ValueError):normalize(value)

    def defense_battle(self,missiles=True):
        from tools import defense_fixture as defense
        from backend.high_wilderness_sidecar.sessions import ResourceIndex
        from backend.high_wilderness_sidecar import missile_logistics as ml
        from backend.high_wilderness_sidecar import battle_preparation as bp
        self.designs=[self.designs[0],defense.design(ResourceIndex(defense.ROOT),'aviation.enemy',True)]
        prepared=defense.record(self.designs[1],'aviation.enemy',auto=missiles);original=bp.new_record
        def record(d,key):return deepcopy(prepared) if d is self.designs[1] else original(d,key)
        with patch.object(bp,'new_record',side_effect=record):b,key=self.attack(None)
        point=b.session.world.ships[-1].motion.position_world_m.to_list()
        b.aviation.flights[key].update(position=(point[0],point[1]+1000),heading=pi,velocity=(0.,-200.),
            task=dict(kind='observe',layer='upper',point_m=point))
        b.enemy_fire=True
        return b,key

    def test_ship_ciws_observes_and_physically_damages_aircraft(self):
        b,key=self.defense_battle(False)
        for _ in range(220):
            b.step()
            if b.aviation.flights.get(key,{}).get('return_requested'):break
        self.assertGreater(sum(s.shots for s in b.states),0)
        self.assertLess(b.aviation.flights[key]['hp'],80.)
        self.assertTrue(b.aviation.flights[key]['return_requested'])

    def test_ship_interceptor_automatically_acquires_aircraft_and_spends_round(self):
        b,key=self.defense_battle();before=len(b.inventory.inventories[-1]._value['missiles']['launchers'][0]['ready'])
        b.states=tuple(replace(s,point_defense=False,target_policy='hold',target=None) for s in b.states)
        at=b.session.world.ships[-1].motion.position_world_m.to_list()
        b.aviation.flights[key]['position']=(at[0],at[1]+5000)
        b.aviation.flights[key]['task']['point_m']=[at[0],at[1]-5000]
        for _ in range(150):
            b.step()
            if b.missiles.states[1,'weapon_upper_port'].shots:break
        self.assertEqual(b.missiles.states[1,'weapon_upper_port'].shots,1)
        self.assertEqual(len(b.inventory.inventories[-1]._value['missiles']['launchers'][0]['ready']),before-1)
        interceptor=next(p for p in b.projectiles if p.missile)
        self.assertEqual(interceptor.interception_target_id,key)
        for _ in range(300):
            b.step()
            if any(e['round_id']==interceptor.id for e in b.point_defense.recent):break
        self.assertTrue(any(e['round_id']==interceptor.id and e['target_kind']=='aircraft' for e in b.point_defense.recent))

    def test_air_attack_capacity_limit_preserves_all_ammunition(self):
        b,key=self.attack('small_missile');before=deepcopy(af.plane(b.inventory.inventories[0],key))
        b.config={**b.config,'max_projectiles':0};b.step()
        self.assertEqual(af.plane(b.inventory.inventories[0],key)['loadout'],before['loadout'])
        self.assertEqual(af.plane(b.inventory.inventories[0],key)['cannon_rounds'],before['cannon_rounds'])
        self.assertFalse(b.aviation.recent)

    def test_self_defense_intercepts_an_actual_incoming_missile(self):
        b,key=self.attack('self_defense','e1',distance=2000.);f=b.aviation.flights[key]
        point=f['position'];p=weapons.profile('small_missile')
        incoming=Projectile(100,b.session.world.ships[-1].ship_id,'test.incoming',(point[0],point[1]+500),(point[0],point[1]+500),(0.,-650.),1000,None,'upper',
            (p.model_id,1),p.ballistics(1.),durability=8.,maximum_durability=8.,collision_radius_m=.045,
            missile=mf.Flight(p,'blast',0,-pi/2,(0.,0.),(0.,0.),b.session.world.ships[0].ship_id))
        b.projectiles=(incoming,);b._projectile_sequence=100;f['task']['kind']='observe'
        for _ in range(140):
            b.step()
            if any(e['projectile_id']==100 for e in b.point_defense.recent):break
        self.assertTrue(any(e['projectile_id']==100 and e['intercepted'] for e in b.point_defense.recent))

    def test_v19_archives_aa_choice_without_rewriting_v18(self):
        from pathlib import Path
        import json
        root=Path(__file__).resolve().parents[1]/'contracts/web_bridge/fixtures'
        old=json.loads((root/'ammunition-preparation-policy.v18.json').read_text(encoding='utf-8'))
        b,key=self.battle();p=b.inventory.inventories[0]._definition['aviation']
        self.assertNotIn('combat',old['aviation']);weapons.validate(p['combat'])
        from backend.high_wilderness_sidecar.aviation_catalog import model
        self.assertIn('small_interceptor',model(p['catalog'],'gtw.aircraft.f1')['hardpoints'][0]['compatible_payloads'])

    def test_cannon_only_sortie_returns_when_last_round_is_spent(self):
        b,key=self.attack(None);w=al.Work(b.inventory.inventories[0]);w.plane(key)['cannon_rounds']=1;w.commit()
        b.step();self.assertEqual(af.plane(b.inventory.inventories[0],key)['cannon_rounds'],0)
        b.step();self.assertTrue(b.aviation.flights[key]['return_requested']);self.assertIsNone(b.aviation.flights[key]['target_id'])

    def test_powered_airborne_missile_keeps_acquired_target_after_layer_change(self):
        b,key=self.attack('small_missile');b.step();p=next(p for p in b.projectiles if p.missile)
        target=mg.Contact(b.session.world.ships[-1].ship_id,b._sides[-1],(p.position[0],p.position[1]+1000),(0.,0.),'upper')
        for n in range(2,10):p=mf.prepare(p,replace(b.session.world,fixed_step=n),b._sides,mg.Environment((target,)))
        self.assertTrue(p.missile.ever_locked)
        p=mf.prepare(p,replace(b.session.world,fixed_step=10),b._sides,mg.Environment((replace(target,layer='cloud'),)))
        self.assertEqual(p.missile.target_id,target.id);self.assertEqual(p.missile.goal_layer,'cloud')
        self.assertEqual(p.height_layer,'upper')  # Actual travel must precede collision-layer change.

    def test_legacy_v18_carrier_still_launches_archived_loadout(self):
        import json
        from tools import test_aviation_logistics as old
        policy=json.loads((old.ROOT/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v18.json').read_text(encoding='utf-8'))
        with patch.object(old,'load_current',return_value=policy):self.designs=[old.carrier('ship.legacy.'+str(n),links=True) for n in range(2)]
        b,key=self.attack('small_missile');before=deepcopy(b.inventory.inventories[0]._definition)
        b.step();self.assertTrue(any(p.missile for p in b.projectiles));self.assertEqual(b.inventory.inventories[0]._definition,before)
        self.assertNotIn('combat',before['aviation'])


if __name__=='__main__':unittest.main()
