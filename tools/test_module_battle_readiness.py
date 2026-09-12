"""Regression checks for actual editor module coverage and repaired entry paths."""
import json
from pathlib import Path
import unittest

from backend.high_wilderness_sidecar import battle_preparation as bp, prepared_deployment
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.simplified_propulsion import POLICY
from tools.test_battle_preparation import fixture

ROOT = Path(__file__).resolve().parents[1]


class ModuleReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.policy = load_current(ROOT)

    def test_every_current_inventory_prototype_and_variant_is_bound(self):
        modules = [m for d, s in self.index.resources.values() if d['kind'] == 'ModulePrototypeCatalog' for m in s['modules']]
        required = {(m['id'], m['version']) for m in modules if m['category'] in ('cargo_hold', 'ammunition_magazine', 'weapon', 'damage_control')}
        self.assertEqual(required, {(r['prototype']['id'], r['prototype']['version']) for r in self.policy['modules']})
        for rule in self.policy['modules']:
            original = next(m for m in modules if (m['id'], m['version']) == (rule['prototype']['id'], rule['prototype']['version']))
            self.assertEqual(bp.canonical_sha256(original), rule['prototype_sha256'])
            if original['category'] == 'weapon':
                self.assertEqual(rule['binding']['ready_capacity'], original['capability']['ready_round_capacity'])
        timing = {(r['prototype']['id'], r['prototype']['version']) for r in self.policy['propulsion_timing']}
        self.assertEqual(timing, {(m['id'], m['version']) for m in modules if m['category'] in ('main_engine', 'maneuver_thruster')})

    def test_missing_modules_are_reported_together_by_name_and_instance(self):
        document, deployment, _ = fixture(self.index)
        policy = bp.ps.clone(self.policy)
        policy['modules'] = []
        with self.assertRaises(bp.ps.ContractError) as raised:
            bp.compile_design(document, self.index, deployment, policy, ship_id='ship.missing')
        message = raised.exception.message
        for name in ('契约夹具货仓', 'custom.cargo', '契约夹具自动炮塔', 'weapon_upper_port', '契约夹具损管设备', 'damage_control', '契约夹具弹药库'):
            self.assertIn(name, message)
        self.assertNotIn('武器配方', message)

    def test_historical_archive_restores_without_new_policy_or_fingerprint(self):
        archive = json.loads((ROOT/'artifacts/x1a1-preparation-contract/design-archive.json').read_text(encoding='utf-8'))
        design = bp.restore_design(archive, self.index)
        self.assertEqual(design.archive(), archive)
        self.assertEqual(design.resources.seed.contributions.policy, POLICY)

    def test_unmanned_damage_control_consumes_finite_resources_and_extinguishes(self):
        document, deployment, _ = fixture(self.index)
        next(m for m in document['outfit']['modules'] if m['id'] == 'damage_control')['prototype'] = dict(id='gtw.module.fixture.unmanned.damage_control', version=1)
        design = bp.compile_design(document, self.index, deployment, self.policy, ship_id='ship.unmanned.repair')
        record = bp.new_record(design, 'instance.unmanned.repair')
        record['state']['damage_controls'][0]['quantity_units'] = 10000
        next(m for m in record['state']['modules'] if m['module_id'] == 'damage_control')['operating_mode'] = 'active'
        template, scenario, _ = RealtimeViewService('backend.unmanned.repair')._template()
        battle = prepared_deployment.build([(design, record)], 'instance.unmanned.repair', template, scenario, allow_test_ignition=True)[0]
        battle.enemy_fire = False
        sid = battle.session.world.ships[0].ship_id
        battle.fire.submit(dict(epoch=battle.session.world.epoch, sequence=1, kind='test_ignite', ship_id=sid,
                                module_id='custom.cargo', arguments=dict(intensity_units=1000, duration_steps=600)))
        battle.fire.submit(dict(epoch=battle.session.world.epoch, sequence=2, kind='enabled', ship_id=sid,
                                module_id='damage_control', arguments=dict(enabled=True)))
        for _ in range(10):
            battle.step()
        self.assertFalse(battle.fire.fires)
        self.assertEqual(battle.inventory.inventories[0]._value['damage_controls'][0]['quantity_units'], 9010)
        self.assertTrue(any(e['kind'] == 'fire_extinguished' for e in battle.fire.recent))


if __name__ == '__main__':
    unittest.main()
