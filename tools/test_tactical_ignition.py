"""H5d real collision, event-only fireproofing, finite ammunition and save."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from tools import test_tactical_repair as repair, test_tactical_damage as hits, test_special_ammunition as special
from tools.test_deck_filling import filled
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, outfit_documents
from backend.high_wilderness_sidecar import tactical_ignition as ig, tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore

ROOT=Path(__file__).resolve().parents[1]


class IgnitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repair.TacticalRepairTests.setUpClass(); cls.f=repair.TacticalRepairTests()
        cls.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/h5d-preparation-policy.v7.json').read_text(encoding='utf-8'))
        cls.doc=cls.f.doc
        hull=filled(cls.doc['hull_binding']['hull'],'fireproof')
        cls.protected_doc=dict(cls.doc,hull_binding=outfit_documents.bind(hull,cls.f.index))
        cls.design=cls.compile(cls.doc)
        cls.protected=cls.compile(cls.protected_doc)

    @classmethod
    def compile(cls,doc,policy=None):
        return bp.compile_design(doc,cls.f.index,cls.f.loadout,policy or cls.policy,ship_id='ship.ignition.player')

    def record(self,design=None):
        r=bp.new_record(design or self.design,'instance.ignition.player')
        r['state']['cargo']=[dict(good_id=ig.GOOD,quantity=5),dict(good_id='cargo.engineering_parts',quantity=5)]
        for w in r['state']['weapons']: w.update(recipe_id=ig.RECIPE,ready_rounds=1)
        for m in r['state']['magazines']:m['quantity']=50
        for d in r['state']['damage_controls']:d['quantity_units']=100000
        for m in r['state']['modules']:
            if m['module_id']=='damage_control':m['operating_mode']='active'
        return r

    def battle(self,design=None,record=None):
        design=design or self.design
        return self.f.battle(design=design,record=record or self.record(design),allow=True)

    def shot(self,b,**kwargs):
        hits.DamageTests().shell(b,(-12,-50),(-12+500/60,-50),**kwargs)
        b.projectiles=(replace(b.projectiles[-1],projectile_key=ig.PROJECTILE),)

    def test_deck_compilation_and_exact_archive(self):
        d=self.protected
        decks={r['deck_level']:r['multiplier'] for r in d.resources.definition()['ignition_decks']}
        self.assertEqual(decks[0],.5);self.assertEqual(decks[1],1)
        self.assertEqual(bp.restore_design(d.archive(),self.f.index),d)
        self.assertNotIn('ignition',bp.new_record(d,'instance.empty')['state'])

    def test_real_hit_probability_only_changes_new_ignition(self):
        a=self.battle();b=self.battle(self.protected)
        with patch.object(ig,'sample',return_value=.4):
            for battle in (a,b):self.shot(battle);battle.step()
        self.assertTrue(a.fire.fires);self.assertFalse(b.fire.fires)
        self.assertTrue(all(e['probability']==.6 for e in a.fire.recent if e['kind']=='projectile_ignition'))
        self.assertTrue(all(e['probability']==.3 for e in b.fire.recent if e['kind']=='ignition_resisted'))
        for f in a.fire.fires:self.assertEqual(f.intensity_units,1000)  # No same-step extra burn.

    def test_existing_fire_damage_decay_and_cost_identical(self):
        a=self.battle();b=self.battle(self.protected)
        for battle in (a,b):self.f.ignite(battle);self.f.send(battle,enabled=True)
        with patch.object(ig.IgnitionRuntime,'apply',side_effect=AssertionError('no new ignition')):
            for _ in range(10):a.step();b.step()
        self.assertEqual(a.fire.fires,b.fire.fires)
        self.assertEqual(self.f.hp(a),self.f.hp(b))
        self.assertEqual(self.f.quantity(a),self.f.quantity(b))
        self.assertEqual(a.session.world.ships[0].motion.hull_integrity_fraction,b.session.world.ships[0].motion.hull_integrity_fraction)

    def test_no_event_no_sampling_or_design_revalidation(self):
        b=self.battle(self.protected)
        with patch.object(ig,'sample',side_effect=AssertionError('idle sample')),patch.object(ps,'verify_pack',side_effect=AssertionError('reparse')):
            for _ in range(60):b.step()
        self.assertFalse(b.fire.fires)

    def test_failed_step_does_not_commit_fire_or_redraw(self):
        b=self.battle();self.shot(b)
        before=(b.session.world,b.projectiles,b.damage_state,b.fire.fires,b.inventory.inventories)
        samples=[]
        def roll(seed,a,sid):
            value=original(seed,a,sid);samples.append(value);return value
        original=ig.sample
        with patch.object(ig,'sample',side_effect=roll):
            with self.assertRaises(RuntimeError):b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('failed observer')))
            self.assertEqual((b.session.world,b.projectiles,b.damage_state,b.fire.fires,b.inventory.inventories),before)
            count=len(samples);b.step();self.assertEqual(samples[:count],samples[count:])
        events=tuple(b.fire.recent);b.step()
        self.assertEqual([e for e in b.fire.recent if e['kind'].startswith('projectile_')],[e for e in events if e['kind'].startswith('projectile_')])

    def test_destroyed_module_armor_stop_and_miss_do_not_ignite(self):
        b=self.battle();self.shot(b)
        with patch.object(ig,'sample',return_value=0):
            b.step(device_operations=(self.f.operation(b,'main_engine_port',100),))
        self.assertNotIn('main_engine_port',[f.module_id for f in b.fire.fires])
        hull=ps.clone(self.doc['hull_binding']['hull'])
        for n in (0,2):hull['decks'][0]['regions'][0]['edge_armor'][n]['thickness_m']=.07
        armored=self.compile(dict(self.doc,hull_binding=outfit_documents.bind(hull,self.f.index)))
        b=self.battle(armored);self.shot(b)
        with patch.object(ig,'sample',side_effect=AssertionError('stopped hit')):b.step()
        self.assertEqual(b.damage_state.recent[-1]['outcome'],'stopped')
        self.assertFalse(b.fire.fires)
        hits.DamageTests().shell(b,(500,0),(510,0));b.projectiles=(replace(b.projectiles[-1],projectile_key=ig.PROJECTILE),)
        with patch.object(ig,'sample',side_effect=AssertionError('miss')):b.step()

    def test_same_projectile_unique_module_attempt_and_bounded_reignition(self):
        b=self.battle()
        with patch.object(ig,'sample',return_value=0):
            self.shot(b);b.step()
            keys=[(a.projectile_id,a.ship_index,a.module_id) for a in b.damage_state.ignition_attempts]
            self.assertTrue(keys);self.assertEqual(len(keys),len(set(keys)))
            self.shot(b);b.step()
        self.assertTrue(any(f.intensity_units==1999 for f in b.fire.fires))
        self.assertTrue(all(f.intensity_units<=10000 and f.remaining_steps<=3600 for f in b.fire.fires))

    def test_fire_save_restart_and_no_cargo_burn(self):
        b=self.battle();cargo=ps.clone(b.inventory.inventories[0]._value['cargo'])
        with patch.object(ig,'sample',return_value=0):self.shot(b);b.step()
        for _ in range(10):b.step()
        self.assertEqual(b.inventory.inventories[0]._value['cargo'],cargo)
        b.withdraw();result=st.capture(b)
        with TemporaryDirectory() as temp:
            store=st.SettlementStore(temp);store.stage(result);store.save(result['settlement_id'])
            r=st.SettlementStore(temp).load_ship('instance.ignition.player',1)
        again=self.battle(record=r)
        self.assertEqual(again.fire.fires,b.fire.fires)
        self.assertEqual(again.inventory.inventories[0]._value['cargo'],cargo)
        with patch.object(ig,'sample',side_effect=AssertionError('saved fire resampled')):again.step()

    def test_finite_preload_requires_cargo_and_survives_commit_retry(self):
        with TemporaryDirectory() as temp:
            store=PreparationStore(temp,self.f.index);store.create_ship(self.protected,'instance.preload')
            supply=dict(interface=bp.fuel.SUPPLY_INTERFACE,supply_id='supply.h5d',revision=0,ammunition_resources=50,
                cargo=[dict(good_id=ig.GOOD,quantity=5)],fuel_units=0)
            store.provision_supply(supply,self.policy['goods']);d=store.draft('prep.h5d',['instance.preload'],'supply.h5d')
            r=d['ships'][0];r['magazines'][0]['quantity']=20
            r['weapons'][0].update(action='preload',recipe_id=ig.RECIPE,batches=1)
            self.assertFalse(store.preview(d)['can_commit'])
            r['cargo']=[dict(good_id=ig.GOOD,quantity=2)]
            result=store.commit(d);self.assertEqual(PreparationStore(temp,self.f.index).commit(d),result)
            after=result['ships'][0]['after']['state']
            self.assertEqual(after['magazines'][0]['quantity'],15)
            self.assertEqual(next(c['quantity'] for c in after['cargo'] if c['good_id']==ig.GOOD),1)
            self.assertEqual(after['weapons'][0]['recipe_id'],ig.RECIPE)

    def test_real_auto_fire_ignites_fresh_technical_enemy(self):
        b=self.battle()
        b.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=1,weapon_id=special.GUN,kind='target',
            arguments=dict(ship_id=b.session.world.ships[1].ship_id,module_id='ammunition_magazine')))
        for _ in range(1000):
            if b.ending or any(e['kind']=='projectile_ignition' and e['ship_id']==b.session.world.ships[1].ship_id for e in b.fire.recent):break
            b.step()
        self.assertGreater(b.states[0].shots,0)
        self.assertTrue(any(e['kind']=='projectile_ignition' for e in b.fire.recent))
        self.assertTrue(any(e['projectile_type']==ig.PROJECTILE[0] for e in b.damage_state.recent))

    def test_zero_net_space_has_no_fireproof_bonus(self):
        from types import SimpleNamespace
        from 高天荒野舰艇编辑器领域层 import HullEditorDocument
        hull=ps.clone(self.protected_doc['hull_binding']['hull']);hull['decks']=hull['decks'][:1]
        r=hull['decks'][0]['regions'][0]
        r['vertices_m']=[[-7.5,-7.5],[7.5,-7.5],[7.5,7.5],[-7.5,7.5]];r['edge_armor']=r['edge_armor'][:4]
        compiled=HullEditorDocument(hull,self.f.index.registry).compile()
        self.assertEqual(compiled.decks[0].filling.usable_volume_m3,0)
        self.assertEqual(ig.definitions(SimpleNamespace(hull=compiled),self.policy['ignition'])[0]['multiplier'],1)

    def test_multilevel_module_uses_base_layer_and_single_roll(self):
        b=self.battle(self.protected)
        modules=b.inventory.inventories[0].pack.seed.resources.modules
        # Isolated compiled-module probe: occupied upper cells must not affect base-layer mapping.
        from types import SimpleNamespace
        m=next(m for m in modules if m.base_deck_level==0 and m.internal_cells)
        extended=replace(m,top_cells=(*m.top_cells,(1,0,0)))
        seed=replace(b.inventory.inventories[0].pack.seed,resources=replace(b.inventory.inventories[0].pack.seed.resources,modules=(extended,)))
        probe=SimpleNamespace(_definition=b.inventory.inventories[0]._definition,pack=SimpleNamespace(seed=seed))
        runtime=ig.IgnitionRuntime(SimpleNamespace(inventory=SimpleNamespace(inventories=(probe,))))
        self.assertEqual(runtime.modules[0][m.id],b.ignition.modules[0][m.id])
        self.assertEqual(b.ignition.modules[0][m.id],(m.base_deck_level,.5 if m.base_deck_level==0 else 1))
        a=ig.Attempt(1,b.session.world.ships[1].ship_id,0,m.id)
        with patch.object(ig,'sample',return_value=.4) as sample:
            fires,events=b.ignition.apply(b.session.world,(a,),())
        self.assertEqual(sample.call_count,1)
        self.assertEqual(events[0]['deck_level'],m.base_deck_level)
        self.assertEqual(bool(fires),m.base_deck_level!=0)

    def test_threshold_extremes_and_same_step_firefighting(self):
        b=self.battle(self.protected);m='custom.cargo'
        a=ig.Attempt(1,b.session.world.ships[1].ship_id,0,m)
        for roll,expected in ((0,True),(.299999,True),(.3,False),(.999999,False)):
            with patch.object(ig,'sample',return_value=roll):
                fires,_=b.ignition.apply(b.session.world,(a,),())
            self.assertEqual(bool(fires),expected)
        for probability,expected in ((0,False),(1,True)):
            policy=ps.clone(self.policy);policy['ignition']['ignition_probability']=probability
            other=self.battle(self.compile(self.doc,policy))
            with patch.object(ig,'sample',return_value=0):
                fires,_=other.ignition.apply(other.session.world,(a,),())
            self.assertEqual(bool(fires),expected)
        self.f.send(b,enabled=True);self.shot(b)
        with patch.object(ig,'sample',return_value=0):b.step()
        self.assertEqual(self.f.quantity(b),99900)
        self.assertTrue(any(e['kind']=='fire_suppressed' for e in b.fire.recent))
        self.assertFalse(any(e['kind'] in ('module_repaired','hull_repaired') for e in b.fire.recent))

    def test_policy_and_deck_validation_and_old_binding_unchanged(self):
        for change in ({'fireproof_multiplier':-1},{'ignition_probability':1.1},{'duration_steps':0},{'intensity_units':True}):
            p=ps.clone(self.policy);p['ignition'].update(change)
            with self.assertRaises(ps.ContractError):self.compile(self.doc,p)
        definition=self.design.resources.definition();definition['ignition_decks']*=2
        with self.assertRaises(ps.ContractError):ps.compile_resources(self.design.resources.seed,definition)
        old=self.f.design;archive=old.archive()
        b=self.f.battle(design=old)
        self.assertFalse(b.ignition.enabled);self.assertEqual(old.archive(),archive)
        self.assertNotIn('ignition',b.inventory.inventories[-1]._definition)

    def test_runtime_reload_shortage_cancel_and_settlement(self):
        f=special.SpecialAmmunitionTests()
        b=self.battle();f.shoot(b)
        self.assertEqual(b.projectiles[0].projectile_key,ig.PROJECTILE)
        self.assertEqual(f.inventory(b).summary()['reserved_cargo'][ig.GOOD],1)
        f.send(b,recipe_id='recipe.x1a.ordinary');b.step()
        self.assertEqual(f.inventory(b).summary()['reserved_cargo'][ig.GOOD],0)
        f.send(b,recipe_id=ig.RECIPE);b.step();b.withdraw()
        r=st.capture(b)['ships'][0]['after']
        self.assertEqual(next(c['quantity'] for c in r['state']['cargo'] if c['good_id']==ig.GOOD),4)
        self.assertEqual(r['state']['weapons'][0]['recipe_id'],ig.RECIPE)
        record=self.record();record['state']['cargo']=[]
        for w in record['state']['weapons']:w.update(ready_rounds=0,recipe_id=None)
        b=self.battle(record=record);f.send(b,recipe_id=ig.RECIPE);b.step()
        self.assertEqual(f.gun(b)['status'],'no_special_materials')
        from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
        with patch.object(InventorySession,'command',side_effect=AssertionError('unchanged shortage repeated')):
            for _ in range(30):b.step()

    def test_supply_upgrade_adds_only_new_goods_once(self):
        from backend.high_wilderness_sidecar.server import SidecarServer
        from backend.high_wilderness_sidecar.preparation_service import SUPPLY_ID
        with TemporaryDirectory() as temp:
            service=SidecarServer('backend.h5dtest',settlement_dir=Path(temp)/'store').preparation
            service.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/h5c-preparation-policy.v6.json').read_text(encoding='utf-8'))
            service.provision()
            with service.store.connection() as db:
                raw=service.store._decode(*db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(SUPPLY_ID,)).fetchone())
                raw['supply']['fuel_units']=37;raw['supply']['ammunition_resources']=9
                for g in raw['supply']['cargo']:g['quantity']=7
                service.store._write_supply(db,raw['supply'],raw['goods'])
            service.policy=self.policy;service.provision();service.provision()
            with service.store.connection() as db:
                after=service.store._decode(*db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(SUPPLY_ID,)).fetchone())['supply']
            self.assertEqual(after['fuel_units'],37);self.assertEqual(after['ammunition_resources'],9)
            self.assertEqual({g['good_id']:g['quantity'] for g in after['cargo']},
                {**{g['good_id']:7 for g in raw['supply']['cargo']},ig.GOOD:100})

    def test_empty_guns_default_to_ordinary_without_using_special_goods(self):
        r=self.record()
        for w in r['state']['weapons']:w.update(recipe_id=None,ready_rounds=0)
        b=self.battle(record=r)
        self.assertTrue(all(s.reload_recipe_id=='recipe.x1a.ordinary' for g,s in zip(b.guns,b.states) if g.ship_index==0))
        b.step()
        self.assertEqual(b.inventory.inventories[0].summary()['reserved_cargo'][ig.GOOD],0)

    def test_disconnected_regions_share_one_deck_effect(self):
        from types import SimpleNamespace
        from 高天荒野舰艇编辑器领域层 import HullEditorDocument
        hull=filled(self.doc['hull_binding']['hull'],'fireproof','deck.1')
        compiled=HullEditorDocument(hull,self.f.index.registry).compile()
        self.assertEqual(len(compiled.decks[1].region_ids),2)
        rows=ig.definitions(SimpleNamespace(hull=compiled),self.policy['ignition'])
        self.assertEqual(len(rows),2);self.assertEqual(next(r['multiplier'] for r in rows if r['deck_level']==1),.5)


if __name__=='__main__':unittest.main()
