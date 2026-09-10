from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import persistent_ship as ps, simplified_flight as sf
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.persistent_ship_fixture import definition, loaded
from tools.test_simplified_flight import command
from 高天荒野舰艇数据契约 import ContractError


class PersistentShipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(Path(__file__).resolve().parents[1], with_command=True)
        cls.packs = tuple(ps.compile_resources(s, definition(s)) for s in cls.sample._seeds)
        cls.instances = tuple(loaded(p, 'instance.' + str(i)) for i, p in enumerate(cls.packs))

    def battle(self, instances=None):
        return ps.enter_battle(tuple(ps.InstanceBinding(p, i) for p, i in zip(self.packs, instances or self.instances)),
            self.sample._profile, direct_instance_id='instance.0')

    def edit(self, change):
        value = self.instances[0].to_dict()
        change(value)
        return ps.parse_instance(value, self.packs[0])

    def test_resource_and_instance_are_canonical_and_detached(self):
        raw = definition(self.sample._seeds[0])
        pack = ps.compile_resources(self.sample._seeds[0], raw)
        raw['goods'].clear()
        self.assertTrue(pack.definition()['goods'])
        value = self.instances[0].to_dict()
        value['modules'].reverse(); value['cargo'].reverse()
        self.assertEqual(ps.parse_instance(value, pack), self.instances[0])
        value['modules'][0]['durability_points'] = 0
        self.assertNotEqual(ps.parse_instance(value, pack), self.instances[0])
        self.assertEqual(ps.loads(ps.dumps(self.instances[0], pack), pack), self.instances[0])

    def test_damaged_ship_redeploys_with_engine_loss_and_same_inventory(self):
        instance = self.edit(lambda v: (v.update(hull_integrity_fraction=.72, revision=7),
            next(m for m in v['modules'] if m['module_id'] == 'main_engine_port').update(durability_points=0)))
        b = self.battle((instance, self.instances[1]))
        ship = b.session.world.ships[0]
        self.assertEqual(ship.motion.hull_integrity_fraction, .72)
        self.assertEqual(ship.devices.modules[b.session._device_kernels[0].by_id['main_engine_port']].durability_points, 0)
        expected = self.packs[0].seed.contributions.remaining(e.instance_id for e in self.packs[0].seed.contributions.engines
            if e.instance_id != 'main_engine_port')
        self.assertEqual(ship.propulsion.available_units, expected.totals_units)
        self.assertEqual(ps.export_instances(b), (instance, self.instances[1]))
        b.session.step()
        result = ps.export_instances(b)
        self.assertEqual(result[0], instance)
        second = self.battle(result)
        self.assertNotEqual(second.session.world.epoch, b.session.world.epoch)
        self.assertEqual(ps.export_instances(second), result)

    def test_partial_engine_damage_does_not_scale_thrust(self):
        instance = self.edit(lambda v: next(m for m in v['modules'] if m['module_id'] == 'main_engine_port').update(durability_points=1))
        b = self.battle((instance, self.instances[1]))
        self.assertEqual(b.session.world.ships[0].propulsion.available_units, self.packs[0].seed.contributions.intact_totals_units)

    def test_hold_damage_changes_only_capacity_even_when_all_holds_destroyed(self):
        b = self.battle()
        s = b.session
        index = s._device_kernels[0].by_id['cargo_hold']
        hp = s._seeds[0].devices.modules[index].maximum_durability_points
        before = ps.inventory_summary(self.instances[0], self.packs[0])
        self.assertEqual(before['capacity_cm3'], 125000000)
        self.assertEqual(before['used_volume_cm3'], 100010000)
        for sequence, damage in ((1, hp / 2), (2, hp / 2)):
            s.step(device_operations=(DeviceOperation(s.world.epoch, 'ship.web.blue', 'cargo_hold', sequence,
                'damage', damage, s.world.fixed_step, 'opening'),))
            result = ps.export_instances(b)
            summary = ps.inventory_summary(result[0], self.packs[0])
            self.assertEqual(summary['capacity_cm3'], 125000000 if sequence == 1 else 0)
            self.assertEqual(result[0].to_dict()['cargo'], self.instances[0].to_dict()['cargo'])
        self.assertTrue(summary['over_capacity'])
        with self.assertRaises(ContractError): ps.validate_initial_loading(result[0], self.packs[0])
        self.assertEqual(ps.export_instances(self.battle(result)), result)

    def test_loading_exact_capacity_and_one_more_is_rejected(self):
        exact = self.edit(lambda v: v.update(cargo=[dict(good_id='cargo.special_alloy', quantity=25)]))
        ps.validate_initial_loading(exact, self.packs[0])
        excessive = self.edit(lambda v: v.update(cargo=[dict(good_id='cargo.special_alloy', quantity=26)]))
        with self.assertRaises(ContractError): ps.validate_initial_loading(excessive, self.packs[0])

    def reserved(self):
        def reserve(v):
            v['weapons'][0].update(ready_rounds=0, recipe_id=None, reload=dict(recipe_id='recipe.special', remaining_steps=65,
                magazine_allocations=[dict(module_id='ammunition_magazine', quantity=5)]))
        return self.edit(reserve)

    def test_reservations_roundtrip_without_consuming_or_releasing_volume(self):
        i = self.reserved()
        summary = ps.inventory_summary(i, self.packs[0])
        self.assertEqual(summary['reserved_ammunition'], {'ammunition_magazine': 5})
        self.assertEqual(summary['reserved_cargo'], {'cargo.advanced_fuse': 2, 'cargo.special_alloy': 1})
        self.assertEqual(summary['used_volume_cm3'], ps.inventory_summary(self.instances[0], self.packs[0])['used_volume_cm3'])
        self.assertEqual(ps.loads(ps.dumps(i, self.packs[0]), self.packs[0]), i)
        damaged = i.to_dict()
        next(m for m in damaged['modules'] if m['module_id'] == 'cargo_hold')['durability_points'] = 0
        restored = ps.parse_instance(damaged, self.packs[0])
        self.assertEqual(ps.inventory_summary(restored, self.packs[0])['reserved_cargo'], summary['reserved_cargo'])
        with self.assertRaises(ContractError): self.battle((i, self.instances[1]))

    def test_wrong_reservations_and_mixed_rounds_are_rejected(self):
        changes = [lambda v: v['magazines'][0].update(quantity=4),
            lambda v: v['cargo'][0].update(quantity=1),
            lambda v: v['weapons'][0]['reload'].update(remaining_steps=121),
            lambda v: v['weapons'][0]['reload']['magazine_allocations'][0].update(quantity=4),
            lambda v: v['weapons'][0].update(ready_rounds=1, recipe_id='recipe.ordinary')]
        for change in changes:
            with self.subTest(change=change):
                v = self.reserved().to_dict(); change(v)
                with self.assertRaises(ContractError): ps.parse_instance(v, self.packs[0])

    def test_unknown_missing_duplicate_fields_and_bad_numbers(self):
        changes = [lambda v: v.update(extra=True), lambda v: v.pop('cargo'),
            lambda v: v.update(revision=True), lambda v: v.update(revision=-1),
            lambda v: v.update(hull_integrity_fraction=float('nan')),
            lambda v: v.update(hull_integrity_fraction=1.1),
            lambda v: v['modules'].append(v['modules'][0]), lambda v: v['modules'].pop(),
            lambda v: v['modules'][0].update(module_id='unknown'),
            lambda v: v['magazines'][0].update(quantity=101),
            lambda v: v['cargo'][0].update(quantity=.5), lambda v: v['cargo'][0].update(quantity=2**53),
            lambda v: v['cargo'][0].update(good_id='cargo.unknown'),
            lambda v: v['weapons'][0].update(aim_state={}),
            lambda v: v.update(engine_latches=['unknown']),
            lambda v: v.update(service=dict(status='available', reasons=['cic_destroyed']))]
        for change in changes:
            with self.subTest(change=change):
                v = self.instances[0].to_dict(); change(v)
                with self.assertRaises(ContractError): ps.parse_instance(v, self.packs[0])
        for payload in ('{"a":1,"a":2}', '{"x":Infinity}', '{', '[]', 'null'):
            with self.assertRaises(ContractError): ps.loads(payload, self.packs[0])

    def test_binding_rejects_other_ship_resource_version_and_legacy_data(self):
        with self.assertRaises(ContractError): ps.dumps(self.instances[0], self.packs[1])
        raw = self.packs[0].definition(); raw['version'] += 1
        new = ps.compile_resources(self.packs[0].seed, raw)
        with self.assertRaises(ContractError): ps.dumps(self.instances[0], new)
        with self.assertRaises(ContractError): ps.parse_instance(dict(inventory=[dict(munition_id='old', units=1)]), self.packs[0])
        with self.assertRaises(ContractError): ps.compile_resources(self.packs[0].seed, dict(bulk_cargo_capacity_kg=100))
        with self.assertRaises(ContractError): ps.dumps(self.instances[0], replace(self.packs[0], source_sha256='0'*64))
        for expected in (dict(expected_instance_id='other'), dict(expected_revision=1), dict(expected_revision=True)):
            with self.assertRaises(ContractError): ps.loads(self.instances[0].payload_json, self.packs[0], **expected)
        self.assertEqual(ps.loads(self.instances[0].payload_json, self.packs[0],
            expected_instance_id='instance.0', expected_revision=0), self.instances[0])

    def test_unsupported_fuel_change_and_duplicate_instances_reject(self):
        changed = self.edit(lambda v: v.update(fuel_units=0))
        self.assertEqual(ps.loads(changed.payload_json, self.packs[0]), changed)
        with self.assertRaises(ContractError): self.battle((changed, self.instances[1]))
        with self.assertRaises(ContractError): ps.enter_battle((ps.InstanceBinding(self.packs[0], self.instances[0]),)*2,
            self.sample._profile, direct_instance_id='instance.0')

    def test_two_precision_tiers_and_resource_references_are_strict(self):
        mutations = [lambda d: d['fire_control'].update(third=d['fire_control']['normal']),
            lambda d: d['fire_control']['degraded'].update(direction_error_mdeg=-1),
            lambda d: d['goods'][0].update(unit_volume_cm3=0),
            lambda d: d['holds'].clear(), lambda d: d['recipes'][0].update(rounds=2),
            lambda d: d['weapons'][0]['turret'].update(slew_mdeg_per_s=0),
            lambda d: d['recipes'][0]['projectile'].update(version=2),
            lambda d: d['recipes'][0]['projectile'].update(id='missing'),
            lambda d: d['recipes'][1]['cargo_costs'][0].update(good_id='missing'),
            lambda d: d.update(source_seed_sha256='0'*64)]
        for mutate in mutations:
            raw = definition(self.sample._seeds[0]); mutate(raw)
            with self.assertRaises(ContractError): ps.compile_resources(self.sample._seeds[0], raw)

    def test_latches_and_service_loss_are_preserved_but_not_silently_reactivated(self):
        i = self.edit(lambda v: v.update(engine_latches=['main_engine_port']))
        self.assertEqual(ps.loads(ps.dumps(i, self.packs[0]), self.packs[0]), i)
        with self.assertRaises(ContractError): self.battle((i, self.instances[1]))
        for status, cause in [('disabled', 'direct_control_link_lost'), ('destroyed', 'hull_structure_collapsed'), ('withdrawn', 'scripted_transfer')]:
            i = self.edit(lambda v: v.update(service=dict(status=status, reasons=[cause])))
            with self.assertRaises(ContractError): self.battle((i, self.instances[1]))

    def test_export_observes_actual_damage_and_failed_entry_does_not_mutate(self):
        b = self.battle()
        s = b.session; index = s._device_kernels[0].by_id['cic']
        s.step(device_operations=(DeviceOperation(s.world.epoch, 'ship.web.blue', 'cic', 1, 'damage',
            s._seeds[0].devices.modules[index].maximum_durability_points, 0, 'opening'),))
        exported = ps.export_instances(b)
        self.assertEqual(exported[0].to_dict()['service']['status'], 'disabled')
        before = s.world
        with self.assertRaises(ContractError): self.battle(exported)
        self.assertIs(before, s.world)

    def test_export_owner_active_boundary_and_no_hot_loop_parsing(self):
        b = self.battle()
        with ThreadPoolExecutor(1) as pool:
            with self.assertRaises(ContractError): pool.submit(ps.export_instances, b).result()
        with self.assertRaises(ContractError): b.session.step(project=lambda *_: ps.export_instances(b))
        with patch.object(ps, 'compile_resources', side_effect=AssertionError('hot-loop compile')), \
             patch.object(ps, 'canonical_sha256', side_effect=AssertionError('hot-loop fingerprint')):
            for _ in range(10): b.session.step()
        b.session.step(command())
        with self.assertRaises(ContractError): ps.export_instances(b)


if __name__ == '__main__':
    unittest.main()
