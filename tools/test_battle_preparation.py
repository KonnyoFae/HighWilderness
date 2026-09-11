import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, persistent_ship as ps
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.storage import read_json
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256

ROOT = Path(__file__).resolve().parents[1]


def fixture(index=None):
    index = index or ResourceIndex(ROOT)
    load = lambda path: json.loads((ROOT/path).read_text(encoding='utf-8'))
    hull = load('舰艇数据/船壳蓝图夹具/阶段F常规有人战舰船壳.v1.json')
    outfit = load('舰艇数据/舾装方案夹具/阶段F常规有人战舰舾装.v1.json')
    # Export a distinct editor design; no known named-outfit migration can match it.
    hull.update(id='hull.preparation.custom', name='战前准备自建船壳', version=7)
    outfit.update(id='outfit.preparation.custom', name='战前准备自建栖装', version=4,
        hull_blueprint=dict(id=hull['id'], version=hull['version']))
    for module in outfit['modules']:
        if module['id'] == 'cargo_hold': module['id'] = 'custom.cargo'
    binding = outfit_documents.bind(hull, index)
    document = ps.decode(outfit_documents.encode(outfit, binding))
    loadout = load('舰艇数据/出航配置夹具/阶段F常规有人战舰出航.v1.json')
    deployment = {k: loadout[k] for k in 'id version crew fuel_units height_layer control_mode active_remote_core_instance_id'.split()}
    deployment['id'] = 'deployment.preparation.technical'
    policy = load('contracts/web_bridge/fixtures/x1a-preparation-policy.json')
    return document, deployment, policy


class BattlePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.document, cls.deployment, cls.policy = fixture(cls.index)
        cls.design = bp.compile_design(cls.document, cls.index, cls.deployment, cls.policy, ship_id='ship.preparation.player')

    def record(self):
        return bp.new_record(self.design, 'instance.preparation.player')

    def supply(self):
        return dict(interface=bp.SUPPLY_INTERFACE, supply_id='supply.test.finite', revision=1,
            ammunition_resources=100, cargo=[dict(good_id='cargo.special_alloy', quantity=20)])

    def test_saved_custom_editor_document_compiles_without_fixture_replacement(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'custom-outfit.json'
            path.write_text(ps.encode(self.document), encoding='utf-8')
            source, _ = read_json(path)
            design = bp.compile_design(source, self.index, self.deployment, self.policy, ship_id='ship.preparation.player')
        self.assertEqual(design, self.design)
        self.assertEqual(design.snapshot.hull.normalized_blueprint.id, 'hull.preparation.custom')
        self.assertIn('custom.cargo', {m.id for m in design.snapshot.outfit.instances})
        self.assertEqual(design.archive()['document'], self.document)
        original_modules = {m['id']: m for m in self.document['outfit']['modules']}
        for module in design.snapshot.outfit.normalized_plan.to_dict()['modules']:
            self.assertEqual({k:v for k,v in module.items() if k!='prototype'},
                {k:v for k,v in original_modules[module['id']].items() if k!='prototype'})

    def test_projection_preserves_thrust_and_source_files(self):
        original = self.index.listing(include_modules=True)['module_options']
        by_id = {r['prototype']['id']: r['prototype'] for r in original}
        source = {m['id']: by_id[m['prototype']['id']] for m in self.document['outfit']['modules']}
        for m in self.design.snapshot.outfit.instances:
            if m.actuator:
                self.assertEqual(m.actuator.thrust_n, source[m.id]['capability']['thrust_n'])
                self.assertEqual(m.prototype.capability.to_dict()['fuel_units_per_s'], 0)
        self.assertEqual(self.document, fixture(self.index)[0])

    def test_archive_roundtrip_and_corruption_rejected(self):
        self.assertEqual(bp.restore_design(ps.decode(ps.encode(self.design.archive())), self.index), self.design)
        for field in ('snapshot_sha256', 'resources_sha256', 'source_document_sha256'):
            archive = self.design.archive(); archive[field] = '0'*64
            with self.subTest(field=field), self.assertRaises(ContractError): bp.restore_design(archive, self.index)

    def test_changed_embedded_hull_and_catalog_rejected(self):
        doc = ps.clone(self.document); doc['hull_binding']['hull']['name'] = 'changed'
        with self.assertRaises(ContractError): bp.compile_design(doc, self.index, self.deployment, self.policy, ship_id='ship.test')
        policy = ps.clone(self.policy); policy['modules'][0]['prototype_sha256'] = '0'*64
        with self.assertRaises(ContractError): bp.compile_design(self.document, self.index, self.deployment, policy, ship_id='ship.test')

    def test_new_instances_have_independent_ids_and_no_free_inventory(self):
        a, b = self.record(), bp.new_record(self.design, 'instance.second')
        self.assertNotEqual(a['state']['instance_id'], b['state']['instance_id'])
        self.assertTrue(all(m['quantity']==0 for m in a['state']['magazines']))
        self.assertTrue(all(w['ready_rounds']==0 and w['recipe_id'] is None for w in a['state']['weapons']))
        self.assertEqual(a['state']['cargo'], [])
        a['state']['hull_integrity_fraction'] = .6
        self.assertEqual(b['state']['hull_integrity_fraction'], 1)

    def test_damage_ammunition_and_overcapacity_survive_draft(self):
        record = self.record(); state = record['state']
        state['revision'] = 7; state['hull_integrity_fraction'] = .62
        next(m for m in state['modules'] if m['module_id']=='custom.cargo')['durability_points'] = 0
        state['cargo'] = [dict(good_id='cargo.special_alloy', quantity=50)]
        state['magazines'][0]['quantity'] = 17
        state['weapons'][0].update(recipe_id='recipe.x1a.ordinary', ready_rounds=1, cooldown_steps=9)
        record['armor'][0]['durability'] *= .5
        before = ps.clone(record)
        draft = bp.new_draft('preparation.test', [(self.design,record)], self.supply())
        self.assertEqual(bp.validate_record(record,self.design), before)
        bp.validate_draft(draft, [(self.design,record)], self.supply())
        self.assertEqual(record,before)
        summary = ps.inventory_summary(ps.parse_instance(state,self.design.resources),self.design.resources)
        self.assertEqual(summary['capacity_cm3'], 0)
        self.assertTrue(summary['over_capacity'])
        self.assertTrue(all(w['action']=='keep' for w in draft['ships'][0]['weapons']))

    def test_ready_draft_roundtrip_and_stale_ship_or_supply(self):
        record, supply = self.record(), self.supply()
        ships = [(self.design, record)]
        draft = bp.new_draft('preparation.test', ships, supply)
        bp.validate_draft(ps.decode(ps.encode(draft)),ships,supply)
        state = record['state']; state['hull_integrity_fraction'] = .9
        with self.assertRaises(ContractError): bp.validate_draft(draft,ships,supply)
        state['hull_integrity_fraction'] = 1
        supply['ammunition_resources'] -= 1
        with self.assertRaises(ContractError): bp.validate_draft(draft,ships,supply)

    def test_multiple_ships_duplicate_ids_and_conflicting_goods(self):
        records = [(self.design,self.record()), (self.design,bp.new_record(self.design,'instance.second'))]
        draft = bp.new_draft('preparation.test',records,self.supply())
        self.assertEqual(len(bp.validate_draft(draft,records,self.supply())['ships']),2)
        with self.assertRaises(ContractError): bp.new_draft('preparation.test',[records[0],records[0]],self.supply())
        policy = ps.clone(self.policy); policy['goods'][0]['unit_volume_cm3'] += 1
        second = bp.compile_design(self.document,self.index,self.deployment,policy,ship_id='ship.second')
        with self.assertRaises(ContractError):
            bp.new_draft('preparation.test',[records[0],(second,bp.new_record(second,'instance.second'))],self.supply())

    def test_selections_are_draft_only_and_unknown_special_ammo_rejected(self):
        record = self.record(); ships = [(self.design,record)]
        before = ps.clone(record)
        draft = bp.new_draft('preparation.test',ships,self.supply())
        choice = draft['ships'][0]['weapons'][0]
        choice.update(action='discard_and_preload',recipe_id='recipe.x1a.ordinary',batches=1)
        bp.validate_draft(draft,ships,self.supply())
        self.assertEqual(record,before)
        choice['recipe_id'] = 'recipe.special.not_implemented'
        with self.assertRaises(ContractError): bp.validate_draft(draft,ships,self.supply())

    def test_invalid_numbers_missing_weapons_and_armor_rejected(self):
        record = self.record(); ships=[(self.design,record)]
        for quantity in (-1, True, 1.5):
            supply = self.supply(); supply['ammunition_resources'] = quantity
            with self.subTest(quantity=quantity), self.assertRaises(ContractError): bp.new_draft('preparation.test',ships,supply)
        draft = bp.new_draft('preparation.test',ships,self.supply()); draft['ships'][0]['weapons'].pop()
        with self.assertRaises(ContractError): bp.validate_draft(draft,ships,self.supply())
        record['armor'].pop()
        with self.assertRaises(ContractError): bp.validate_record(record,self.design)

    def test_design_seed_runs_current_flight_kernel_without_trial_tuning(self):
        from 高天荒野舰艇推进安全判定器 import load_propulsion_safety_profile
        from unittest.mock import patch
        profile = load_propulsion_safety_profile(ROOT/'舰艇数据/标定/T0推进安全技术替身配置.v1.json')
        seed = self.design.resources.seed
        session = ps.sf.SimplifiedFlightSession((seed,),profile,direct_ship_id=seed.contributions.ship_id)
        self.assertTrue(session.world.ships[0].authority_allowed)
        with patch.object(bp,'compile_design',side_effect=AssertionError('hot-loop design compile')), \
             patch.object(ps,'parse_instance',side_effect=AssertionError('hot-loop instance parse')):
            for _ in range(60): session.step()
        self.assertEqual(session.world.fixed_step,60)
        self.assertEqual(session.world.ships[0].motion.fuel_units,seed.motion.fuel_units)

    def test_preparation_record_uses_p3_parser(self):
        from types import SimpleNamespace
        from backend.high_wilderness_sidecar import tactical_settlement as ts
        record = self.record()
        record['state']['hull_integrity_fraction'] = .75
        binding = ps.InstanceBinding(self.design.resources,ps.parse_instance(record['state'],self.design.resources))
        edges = tuple(SimpleNamespace(key=tuple(r[k] for k in ('deck_id','deck_level','region_id','edge_index')),maximum=r['durability'])
                      for r in self.record()['armor'])
        template = SimpleNamespace(session=SimpleNamespace(_seeds=(self.design.resources.seed,)),
            inventory=SimpleNamespace(prepared=SimpleNamespace(bindings=(binding,))),damage=SimpleNamespace(edges=(edges,)))
        parsed, _ = ts.parse_record(record,template,0)
        self.assertEqual(parsed.instance.to_dict(),record['state'])


if __name__ == '__main__':
    unittest.main()
