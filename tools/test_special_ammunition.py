import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, outfit_documents
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.tactical_ammunition import ORDINARY, ARMOR_PIERCING, AP_RECIPE
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture
from tools import test_tactical_damage as damage_fixture

ROOT=Path(__file__).resolve().parents[1]
ORDINARY_RECIPE='recipe.x1a.ordinary'
GUN='weapon_upper_port'


class SpecialAmmunitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)
        cls.document,cls.deploy,cls.old_policy=fixture(cls.index)
        cls.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/s1-preparation-policy.v2.json').read_text(encoding='utf-8'))
        cls.design=bp.compile_design(cls.document,cls.index,cls.deploy,cls.policy,ship_id='ship.special.player')
        hull=ps.clone(cls.document['hull_binding']['hull'])
        # Symmetric aft slopes cover the test hit without overloading the hull.
        for index in (0,2):hull['decks'][0]['regions'][0]['edge_armor'][index]['thickness_m']=.07
        cls.armored=bp.compile_design(dict(cls.document,hull_binding=outfit_documents.bind(hull,cls.index)),cls.index,cls.deploy,cls.policy,ship_id='ship.special.player')
        cls.template,cls.scenario,_=RealtimeViewService('backend.special',ROOT)._template()

    def record(self,design=None,recipe=ORDINARY_RECIPE,rounds=1,cargo=5,ammo=50):
        result=bp.new_record(design or self.design,'instance.special.player')
        result['state']['cargo']=[dict(good_id='cargo.special_alloy',quantity=cargo)]
        result['state']['magazines'][0]['quantity']=ammo
        for w in result['state']['weapons']:w.update(recipe_id=recipe,ready_rounds=rounds)
        return result

    def battle(self,design=None,record=None,**kwargs):
        design=design or self.design
        record=record or self.record(design,**kwargs)
        b=deployment.build([(design,record)],record['state']['instance_id'],self.template,self.scenario)[0]
        b.enemy_fire=False
        return b

    def send(self,b,kind='ammunition',**args):
        value=dict(epoch=b.session.world.epoch,generation=0,sequence=b.sequence+1,weapon_id=GUN,kind=kind,arguments=args)
        b.submit(value);return value

    def gun(self,b):return b.view()['weapons'][0]
    def inventory(self,b):return b.inventory.inventories[0]
    def steps(self,b,n):
        for _ in range(n):b.step()

    def shoot(self,b):
        x,y=self.gun(b)['origin_m']
        self.send(b,'mode',mode='manual');self.send(b,'fire',point=[x,y+500]);b.step()
        self.assertEqual(b.states[0].shots,1)
        return next(p for p in b.projectiles if p.ship_id==b.session._direct)

    def hit(self,b,key):
        # Actual 500 m/s swept collision across the port edge at y=-50 m.
        damage_fixture.DamageTests().shell(b,(-12,-50),(-12+500/60,-50))
        b.projectiles=(replace(b.projectiles[-1],projectile_key=key),)
        b.step();return b.damage_state.recent[-1]

    def test_real_penetration_advantage_and_unarmored_tradeoff(self):
        ordinary=self.hit(self.battle(self.armored),ORDINARY)
        special=self.hit(self.battle(self.armored),ARMOR_PIERCING)
        self.assertEqual(ordinary['outcome'],'stopped');self.assertEqual(special['outcome'],'penetrated')
        self.assertGreater(special['module_damage'],0)
        a=self.battle();c=self.battle()
        x=self.hit(a,ORDINARY);y=self.hit(c,ARMOR_PIERCING)
        self.assertEqual(x['module_damage'],40);self.assertEqual(y['module_damage'],32)
        self.assertAlmostEqual((1-c.session.world.ships[0].motion.hull_integrity_fraction)/(1-a.session.world.ships[0].motion.hull_integrity_fraction),.8)
        self.assertEqual(y['projectile_type'],ARMOR_PIERCING[0])

    def test_preparation_consumes_both_resources_once_and_restarts(self):
        with TemporaryDirectory() as directory:
            store=PreparationStore(directory,self.index);store.create_ship(self.design,'instance.special.player')
            supply=dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.special',revision=0,ammunition_resources=50,
                cargo=[dict(good_id='cargo.special_alloy',quantity=10)])
            store.provision_supply(supply,self.policy['goods'])
            draft=store.draft('preparation.special',['instance.special.player'],'supply.special')
            row=draft['ships'][0];row['magazines'][0]['quantity']=20
            row['cargo']=[dict(good_id='cargo.special_alloy',quantity=2)]
            row['weapons'][0].update(action='preload',recipe_id=AP_RECIPE,batches=1)
            bad=ps.clone(draft);bad['ships'][0]['cargo']=[]
            self.assertFalse(store.preview(bad)['can_commit'])
            self.assertEqual(store.draft('preparation.unchanged',['instance.special.player'],'supply.special')['ships'][0]['magazines'][0]['quantity'],0)
            result=store.commit(draft)
            self.assertEqual(result,PreparationStore(directory,self.index).commit(draft))
            after=result['ships'][0]['after']['state']
            self.assertEqual(after['magazines'][0]['quantity'],15)
            self.assertEqual(after['cargo'][0]['quantity'],1)
            self.assertEqual(after['weapons'][0]['recipe_id'],AP_RECIPE)
            self.assertEqual(result['supply_after']['cargo'][0]['quantity'],8)

    def test_loaded_round_and_flying_projectile_keep_identity(self):
        b=self.battle();self.send(b,recipe_id=AP_RECIPE);b.step()
        self.assertEqual(self.gun(b)['loaded_recipe_id'],ORDINARY_RECIPE)
        p=self.shoot(b);self.assertEqual(p.projectile_key,ORDINARY)
        self.assertEqual(self.gun(b)['loading_recipe_id'],AP_RECIPE)
        inv=self.inventory(b);self.assertEqual(inv.summary()['reserved_cargo']['cargo.special_alloy'],1)
        self.steps(b,120)
        self.assertEqual(self.gun(b)['loaded_recipe_id'],AP_RECIPE)
        self.assertEqual(self.inventory(b)._value['cargo'][0]['quantity'],4)
        self.assertTrue(all(p.projectile_key==ORDINARY for p in b.projectiles))

    def test_switch_at_completion_boundary_cancels_old_reservation(self):
        b=self.battle(rounds=0,recipe=None);self.send(b,recipe_id=AP_RECIPE);b.step()
        self.steps(b,119)
        self.assertEqual(self.gun(b)['reload_steps'],1)
        self.send(b,recipe_id=ORDINARY_RECIPE);b.step()
        self.assertEqual(self.gun(b)['loading_recipe_id'],ORDINARY_RECIPE)
        self.assertEqual(self.inventory(b)._value['cargo'][0]['quantity'],5)
        self.assertEqual(self.inventory(b).summary()['reserved_cargo']['cargo.special_alloy'],0)
        self.assertEqual(self.inventory(b)._value['magazines'][0]['quantity'],50)

    def test_cancelled_step_rolls_back_inventory_and_due_time(self):
        b=self.battle(rounds=0,recipe=None);self.send(b,recipe_id=AP_RECIPE)
        before=self.inventory(b).snapshot().to_dict()
        with self.assertRaises(RuntimeError):b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('fixture')))
        self.assertEqual(before,self.inventory(b).snapshot().to_dict());self.assertFalse(self.inventory(b)._due)
        b.step();self.assertEqual(self.inventory(b).summary()['reserved_cargo']['cargo.special_alloy'],1)

    def test_switch_then_immediate_withdraw_does_not_finish_cancelled_batch(self):
        b=self.battle(rounds=0,recipe=None);self.send(b,recipe_id=AP_RECIPE);b.step()
        command=self.send(b,recipe_id=ORDINARY_RECIPE)
        self.assertEqual(self.inventory(b).summary()['reserved_cargo']['cargo.special_alloy'],0)
        self.assertFalse(b.submit(command))
        b.withdraw();after=st.capture(b)['ships'][0]['after']['state']
        self.assertEqual(after['cargo'][0]['quantity'],5)
        self.assertEqual(after['magazines'][0]['quantity'],50)
        self.assertEqual(after['weapons'][0]['ready_rounds'],0)

    def test_missing_materials_are_atomic_and_unchanged_steps_do_not_retry(self):
        b=self.battle(rounds=0,recipe=None,cargo=0);self.send(b,recipe_id=AP_RECIPE);b.step()
        self.assertEqual(self.gun(b)['status'],'no_special_materials')
        self.assertEqual(self.inventory(b)._value['magazines'][0]['quantity'],50)
        with patch.object(InventorySession,'command',side_effect=AssertionError('unchanged failed reload repeated')):
            self.steps(b,30)
        self.send(b,recipe_id=ORDINARY_RECIPE);b.step()
        self.assertEqual(self.gun(b)['loading_recipe_id'],ORDINARY_RECIPE)

    def test_destruction_before_completion_releases_special_materials(self):
        from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
        b=self.battle(rounds=0,recipe=None);self.send(b,recipe_id=AP_RECIPE);b.step();self.steps(b,119)
        b.step(device_operations=(DeviceOperation(b.session.world.epoch,b.session._direct,GUN,1,'damage',1000,b.session.world.fixed_step,'opening'),))
        self.assertEqual(self.inventory(b).summary()['reserved_cargo']['cargo.special_alloy'],0)
        self.assertEqual(self.inventory(b)._value['cargo'][0]['quantity'],5)
        self.assertEqual(self.gun(b)['ready_rounds'],0)

    def test_settlement_finishes_special_reload_and_persists_next_battle(self):
        record=self.record(recipe=AP_RECIPE)
        b=self.battle(record=record);p=self.shoot(b);self.assertEqual(p.projectile_key,ARMOR_PIERCING)
        b.withdraw();result=st.capture(b)
        after=result['ships'][0]['after']['state']
        self.assertEqual(after['cargo'][0]['quantity'],4)
        self.assertEqual(after['magazines'][0]['quantity'],45)
        self.assertEqual(after['weapons'][0]['recipe_id'],AP_RECIPE)
        with TemporaryDirectory() as directory:
            store=PreparationStore(directory,self.index);store.create_ship(self.design,record['state']['instance_id'])
            with store.connection() as db:store._write_ship(db,record)
            store.stage(result);receipt=store.save(result['settlement_id'])
            self.assertEqual(receipt,store.save(result['settlement_id']))
            saved=PreparationStore(directory,self.index).load_ship(record['state']['instance_id'],1)
            next_b=self.battle(record=saved)
        self.assertEqual(self.gun(next_b)['loaded_recipe_id'],AP_RECIPE)
        self.assertEqual(self.inventory(next_b)._value['cargo'],after['cargo'])
        self.assertIsNone(next_b.states[0].target)

    def test_command_retry_and_unknown_recipe_are_safe(self):
        b=self.battle();command=self.send(b,recipe_id=AP_RECIPE)
        self.assertFalse(b.submit(command))
        old=b.sequence,b.states
        with self.assertRaises(ps.ContractError):self.send(b,recipe_id='recipe.unknown')
        self.assertEqual(old,(b.sequence,b.states))
        view=b.view();view['weapons'][0]['recipe_options'][-1]['cargo_costs'].clear()
        self.assertTrue(self.inventory(b)._recipes[AP_RECIPE]['cargo_costs'])

    def test_active_projectile_uses_compiled_profiles_without_material_lookup(self):
        b=self.battle(self.armored)
        with patch('backend.high_wilderness_sidecar.tactical_damage.compile_profiles',side_effect=AssertionError('step profile compilation')), \
             patch.object(type(self.index.registry),'base_armor',side_effect=AssertionError('step armor lookup')), \
             patch.object(type(self.index.registry),'structure',side_effect=AssertionError('step material lookup')):
            hit=self.hit(b,ARMOR_PIERCING)
            self.steps(b,30)
        self.assertEqual(hit['outcome'],'penetrated')

    def test_old_design_stays_bound_and_all_new_recipes_validate_at_entry(self):
        old=bp.compile_design(self.document,self.index,self.deploy,self.old_policy,ship_id='ship.special.player')
        self.assertEqual(bp.restore_design(old.archive(),self.index),old)
        self.assertEqual(len(self.battle(old).view()['weapons'][0]['recipe_options']),1)
        policy=ps.clone(self.policy);policy['projectiles'][-1]['version']=2;policy['recipes'][-1]['projectile']['version']=2
        bad=bp.compile_design(self.document,self.index,self.deploy,policy,ship_id='ship.special.player')
        with self.assertRaisesRegex(ps.ContractError,'不支持'):self.battle(bad)


if __name__=='__main__':unittest.main()
