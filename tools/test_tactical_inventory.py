from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import persistent_ship as ps, simplified_flight as sf, tactical_inventory as ti
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.persistent_ship_fixture import definition, loaded
from 高天荒野舰艇数据契约 import ContractError

GUN = 'weapon_upper_port'
MAG = 'ammunition_magazine'
FUSE = 'cargo.advanced_fuse'
ALLOY = 'cargo.special_alloy'


class TacticalInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(Path(__file__).resolve().parents[1], with_command=True)
        cls.pack = ps.compile_resources(cls.sample._seeds[0], definition(cls.sample._seeds[0]))
        cls.instance = loaded(cls.pack, 'instance.blue')

    def inventory(self, edit=None, pack=None):
        pack = pack or self.pack
        value = loaded(pack, 'instance.blue').to_dict()
        if edit:
            edit(value)
        return ti.InventorySession(pack, ps.parse_instance(value, pack))

    def command(self, inventory, kind, target=GUN, **args):
        command = dict(epoch=inventory.epoch, sequence=inventory.sequence+1, kind=kind, target=target, **args)
        inventory.command(**command)
        return command

    def start(self, inventory, recipe='recipe.special'):
        self.command(inventory, 'discharge', quantity=1, cooldown_steps=0)
        return self.command(inventory, 'start_reload', recipe_id=recipe)

    def run_steps(self, inventory, count):
        for _ in range(count):
            inventory.advance(inventory.fixed_step+1)

    def weapon(self, inventory):
        return inventory.snapshot().to_dict()['weapons'][0]

    def ammo(self, inventory):
        return inventory.snapshot().to_dict()['magazines'][0]['quantity']

    def health(self, inventory, **changes):
        result = {x['module_id']: x['durability_points'] for x in inventory.snapshot().to_dict()['modules']}
        result.update(changes)
        return result

    def test_batch_reserves_then_atomically_consumes_at_deadline(self):
        i = self.inventory()
        volume = i.summary()['used_volume_cm3']
        self.start(i)
        self.assertEqual(i.summary()['reserved_ammunition'][MAG], 5)
        self.assertEqual(i.summary()['reserved_cargo'][FUSE], 2)
        self.assertEqual(i.summary()['used_volume_cm3'], volume)
        self.run_steps(i, 119)
        self.assertEqual(self.ammo(i), 40)
        self.assertEqual(self.weapon(i)['reload']['remaining_steps'], 1)
        i.advance(120)
        self.assertEqual(self.ammo(i), 35)
        self.assertEqual(self.weapon(i)['ready_rounds'], 1)
        self.assertIsNone(self.weapon(i)['reload'])
        self.assertEqual(i.summary()['used_volume_cm3'], volume-5002000)
        self.assertEqual(i.summary()['reserved_cargo'][FUSE], 0)
        self.command(i, 'discharge', quantity=1, cooldown_steps=0)
        self.assertEqual(self.ammo(i), 35)  # firing never pays base cost again

    def test_multi_material_shortage_is_atomic_and_does_not_take_sequence(self):
        i = self.inventory(lambda v: next(c for c in v['cargo'] if c['good_id'] == ALLOY).update(quantity=0))
        self.command(i, 'discharge', quantity=1, cooldown_steps=0)
        before = i.checkpoint()
        with self.assertRaises(ContractError):
            self.command(i, 'start_reload', recipe_id='recipe.special')
        self.assertEqual(before, i.checkpoint())
        self.command(i, 'start_reload', recipe_id='recipe.ordinary')

    def test_cancel_releases_without_refund_or_free_rounds(self):
        i = self.inventory()
        self.start(i)
        self.run_steps(i, 119)
        self.command(i, 'cancel_reload')
        self.run_steps(i, 2)
        self.assertEqual(self.ammo(i), 40)
        self.assertEqual(self.weapon(i)['ready_rounds'], 0)
        self.assertEqual(i.summary()['reserved_cargo'][ALLOY], 0)
        self.assertFalse(any(c['reason'] == 'reload' for c in i.changes()))

    def test_retries_conflicts_old_commands_and_epoch(self):
        i = self.inventory()
        command = self.start(i)
        self.run_steps(i, 120)
        before = i.checkpoint()
        self.assertFalse(i.command(**command))
        self.assertEqual(before, i.checkpoint())
        with self.assertRaises(ContractError):
            i.command(**dict(command, recipe_id='recipe.ordinary'))
        with self.assertRaises(ContractError):
            i.command(**dict(command, sequence=1))
        with self.assertRaises(ContractError):
            i.command(**dict(command, epoch='other'))

    def test_reload_continues_after_checkpoint_with_receipt_and_summary(self):
        i = self.inventory()
        command = self.start(i)
        self.run_steps(i, 37)
        restored = ti.InventorySession.restore(self.pack, i.checkpoint())
        self.assertEqual(restored.checkpoint(), i.checkpoint())
        self.assertFalse(restored.command(**command))
        self.run_steps(i, 83)
        self.run_steps(restored, 83)
        self.assertEqual(restored.checkpoint(), i.checkpoint())
        self.assertFalse(restored.command(**command))
        self.assertEqual(self.ammo(restored), 35)

    def test_pause_and_cooldown_are_fixed_step_not_wall_time(self):
        i = self.inventory()
        self.command(i, 'discharge', quantity=1, cooldown_steps=180)
        self.command(i, 'start_reload', recipe_id='recipe.ordinary')
        before = i.checkpoint()
        for _ in range(10):
            i.advance(0)
            i.summary()
        self.assertEqual(before, i.checkpoint())
        self.run_steps(i, 120)
        self.assertEqual(self.weapon(i)['cooldown_steps'], 60)
        with self.assertRaises(ContractError):
            self.command(i, 'discharge', quantity=1, cooldown_steps=0)
        self.run_steps(i, 60)
        self.command(i, 'discharge', quantity=1, cooldown_steps=0)

    def test_settlement_finishes_only_active_and_is_frozen_idempotent(self):
        i = self.inventory()
        self.command(i, 'discharge', quantity=1, cooldown_steps=180)
        self.command(i, 'start_reload', recipe_id='recipe.special')
        self.run_steps(i, 10)
        self.assertTrue(i.prepare_settlement('battle.result.1'))
        before = i.checkpoint()
        self.assertEqual(self.weapon(i)['cooldown_steps'], 170)
        self.assertEqual(self.ammo(i), 35)
        self.assertFalse(i.prepare_settlement('battle.result.1'))
        restored = ti.InventorySession.restore(self.pack, before)
        self.assertFalse(restored.prepare_settlement('battle.result.1'))
        self.assertEqual(before, restored.checkpoint())
        with self.assertRaises(ContractError):
            i.prepare_settlement('battle.result.2')
        with self.assertRaises(ContractError):
            i.advance(11)
        with self.assertRaises(ContractError):
            self.command(i, 'discharge', quantity=1, cooldown_steps=0)
        empty = self.inventory()
        self.command(empty, 'discharge', quantity=1, cooldown_steps=0)
        empty.prepare_settlement('battle.result.1')
        self.assertEqual(self.weapon(empty)['ready_rounds'], 0)
        self.assertEqual(self.ammo(empty), 40)

    def test_weapon_and_source_magazine_destruction_cancel_before_due(self):
        for module_id in (GUN, MAG):
            with self.subTest(module=module_id):
                i = self.inventory()
                self.start(i)
                self.run_steps(i, 119)
                i.advance(120, health=self.health(i, **{module_id: 0}))
                self.assertEqual(self.weapon(i)['ready_rounds'], 0)
                self.assertIsNone(self.weapon(i)['reload'])
                self.assertEqual(self.ammo(i), 40)
                self.assertEqual(i.summary()['reserved_cargo'][FUSE], 0)
                i.prepare_settlement('battle.result.1')
                self.assertEqual(self.weapon(i)['ready_rounds'], 0)

    def test_hold_damage_preserves_reservations_overcapacity_and_consumption(self):
        i = self.inventory()
        self.start(i)
        hp = self.health(i)['cargo_hold']
        i.advance(1, health=self.health(i, cargo_hold=hp/2))
        self.assertEqual(i.summary()['capacity_cm3'], 125000000)
        i.advance(2, health=self.health(i, cargo_hold=0))
        self.assertTrue(i.summary()['over_capacity'])
        self.assertEqual(i.summary()['reserved_cargo'][ALLOY], 1)
        with self.assertRaises(ContractError):
            self.command(i, 'load_cargo', FUSE, quantity=1)
        with self.assertRaises(ContractError):
            self.command(i, 'consume_cargo', FUSE, quantity=9)
        self.command(i, 'consume_cargo', FUSE, quantity=8)
        self.command(i, 'unload_cargo', ALLOY, quantity=19)
        restored = ti.InventorySession.restore(self.pack, i.checkpoint())
        restored.prepare_settlement('battle.result.1')
        self.assertEqual(restored.summary()['used_volume_cm3'], 0)
        self.assertEqual(self.ammo(restored), 35)

    def test_capacity_mixed_goods_and_reserved_ammunition(self):
        i = self.inventory()
        self.command(i, 'unload_cargo', FUSE, quantity=10)
        self.command(i, 'load_cargo', ALLOY, quantity=5)
        self.assertEqual(i.summary()['used_volume_cm3'], 125000000)
        before = i.checkpoint()
        with self.assertRaises(ContractError):
            self.command(i, 'load_cargo', FUSE, quantity=1)
        self.assertEqual(before, i.checkpoint())
        self.start(i, 'recipe.ordinary')
        with self.assertRaises(ContractError):
            self.command(i, 'unload_ammunition', MAG, quantity=36)
        self.command(i, 'unload_ammunition', MAG, quantity=35)
        self.command(i, 'load_ammunition', MAG, quantity=95)
        with self.assertRaises(ContractError):
            self.command(i, 'load_ammunition', MAG, quantity=1)

    def test_batch_sizes_one_magazine_and_belt_with_whole_batch_topup(self):
        for cost, rounds in ((7, 1), (3, 6), (8, 100)):
            with self.subTest(cost=cost, rounds=rounds):
                resource = definition(self.pack.seed)
                resource['weapons'][0]['ready_capacity'] = rounds * 2
                resource['recipes'][0].update(ammo_cost=cost, rounds=rounds)
                pack = ps.compile_resources(self.pack.seed, resource)
                i = self.inventory(lambda v: v['weapons'][0].update(ready_rounds=0), pack=pack)
                self.command(i, 'start_reload', recipe_id='recipe.ordinary')
                self.run_steps(i, 120)
                self.assertEqual(self.weapon(i)['ready_rounds'], rounds)
                self.assertEqual(self.ammo(i), 40-cost)
                with self.assertRaises(ContractError):
                    self.command(i, 'start_reload', recipe_id='recipe.special')
                self.command(i, 'start_reload', recipe_id='recipe.ordinary')
                i.prepare_settlement('battle.result.1')
                self.assertEqual(self.weapon(i)['ready_rounds'], 2*rounds)
                self.assertEqual(self.ammo(i), 40-2*cost)

    def test_two_weapons_contend_for_one_pool(self):
        seed = self.pack.seed
        module = next(m for m in seed.resources.modules if m.id == GUN)
        design = next(m for m in seed.devices.modules if m.instance_id == GUN)
        seed = replace(seed, resources=replace(seed.resources, modules=seed.resources.modules+(replace(module, id='weapon.second'),),
            modes=seed.resources.modes+('active',)), devices=replace(seed.devices,
            modules=seed.devices.modules+(replace(design, instance_id='weapon.second'),),
            initial_durability_points=seed.devices.initial_durability_points+(design.maximum_durability_points,)))
        pack = ps.compile_resources(seed, definition(seed))
        i = self.inventory(lambda v: ([w.update(ready_rounds=0) for w in v['weapons']],
            v['magazines'][0].update(quantity=9)), pack=pack)
        self.command(i, 'start_reload', GUN, recipe_id='recipe.special')
        before = i.checkpoint()
        with self.assertRaises(ContractError):
            self.command(i, 'start_reload', 'weapon.second', recipe_id='recipe.special')
        self.assertEqual(before, i.checkpoint())
        self.command(i, 'cancel_reload', GUN)
        self.command(i, 'start_reload', 'weapon.second', recipe_id='recipe.special')
        i.prepare_settlement('battle.result.1')
        self.assertEqual(self.ammo(i), 4)
        self.assertEqual(sum(w['ready_rounds'] for w in i.snapshot().to_dict()['weapons']), 1)

    def test_runtime_no_full_validation_or_hash_and_idle_no_copy(self):
        i = self.inventory()
        with patch.object(ps, '_validate', side_effect=AssertionError('runtime validation')), \
             patch.object(ps, 'compile_resources', side_effect=AssertionError('runtime compilation')), \
             patch.object(ps, 'canonical_sha256', side_effect=AssertionError('runtime hash')), \
             patch.object(ps, 'encode', side_effect=AssertionError('runtime json')):
            with patch.object(ti, 'deepcopy', side_effect=AssertionError('idle copy')):
                self.run_steps(i, 600)
            self.start(i)
            self.run_steps(i, 120)
            i.prepare_settlement('battle.result.1')
        self.assertEqual(self.ammo(i), 35)

    def test_checkpoint_rejects_bad_summary_and_input_is_detached(self):
        i = self.inventory()
        self.start(i)
        payload = i.checkpoint()
        for edit in (
            lambda v: v.update(interface='unknown'),
            lambda v: v.update(sequence=True),
            lambda v: v['baseline'].update(ammunition=999),
            lambda v: v['changes'].append(dict(resource='cargo.unknown', reason='consume', delta=-1)),
            lambda v: v.update(settlement_id='battle.result.1'),
            lambda v: v['last'].update(quantity=2)):
            v = ps.decode(payload)
            edit(v)
            with self.assertRaises(ContractError):
                ti.InventorySession.restore(self.pack, ps.encode(v))
        snapshot = i.snapshot().to_dict()
        snapshot['magazines'][0]['quantity'] = 0
        self.assertEqual(self.ammo(i), 40)
        with ThreadPoolExecutor(1) as executor:
            with self.assertRaises(ContractError):
                executor.submit(i.checkpoint).result()

    def test_summary_is_bounded_and_reconciles_many_transactions(self):
        i = self.inventory()
        for _ in range(100):
            self.command(i, 'load_cargo', FUSE, quantity=1)
            self.command(i, 'consume_cargo', FUSE, quantity=1)
        self.assertEqual(len(i.changes()), 2)
        self.assertEqual(i.changes()[0]['delta'], -100)
        self.assertEqual(ti.InventorySession.restore(self.pack, i.checkpoint()).checkpoint(), i.checkpoint())

    def battle(self):
        return ti.enter_battle((ps.InstanceBinding(self.pack, self.instance),), self.sample._profile,
                               direct_instance_id='instance.blue')

    def test_real_flight_candidate_failure_rolls_back_both_domains(self):
        battle = self.battle()
        session = battle.prepared.session
        i = battle.inventories[0]
        cmd = dict(epoch=i.epoch, sequence=1, kind='discharge', target=GUN, quantity=1, cooldown_steps=0)
        before, world = i.checkpoint(), session.world
        def fail(*_):
            raise RuntimeError('projection failed')
        with self.assertRaises(RuntimeError):
            battle.step(inventory_commands=((0, cmd),), project=fail)
        self.assertIs(session.world, world)
        self.assertEqual(battle.inventories[0].checkpoint(), before)
        battle.step(inventory_commands=((0, cmd),))
        self.assertEqual(battle.inventories[0].fixed_step, session.world.fixed_step)
        self.assertEqual(battle.export_instances()[0].to_dict()['weapons'][0]['ready_rounds'], 0)
        # An invalid second command also rolls back the first and flight.
        cmd2 = dict(cmd, sequence=2, kind='start_reload', quantity=None, cooldown_steps=None, recipe_id='recipe.special')
        before, world = battle.inventories[0].checkpoint(), session.world
        with self.assertRaises(ContractError):
            battle.step(inventory_commands=((0, cmd2), (0, dict(cmd2, sequence=3))))
        self.assertIs(session.world, world)
        self.assertEqual(before, battle.inventories[0].checkpoint())

    def test_real_device_damage_and_new_battle_cooldown(self):
        battle = self.battle()
        session = battle.prepared.session
        i = battle.inventories[0]
        self.command(i, 'discharge', quantity=1, cooldown_steps=180)
        self.command(i, 'start_reload', recipe_id='recipe.special')
        hp = self.health(i)['cargo_hold']
        battle.step(device_operations=(DeviceOperation(session.world.epoch, 'ship.web.blue', 'cargo_hold',
            1, 'damage', hp, session.world.fixed_step, 'opening'),))
        self.assertTrue(battle.inventories[0].summary()['over_capacity'])
        battle.prepare_settlement('battle.result.1')
        saved = battle.export_instances()[0]
        self.assertEqual(saved.to_dict()['weapons'][0]['cooldown_steps'], 179)
        self.assertEqual(saved.to_dict()['magazines'][0]['quantity'], 35)
        second = ti.enter_battle((ps.InstanceBinding(self.pack, saved),), self.sample._profile, direct_instance_id='instance.blue')
        self.assertTrue(second.inventories[0].summary()['over_capacity'])
        self.assertEqual(self.weapon(second.inventories[0])['cooldown_steps'], 179)
        self.assertNotEqual(second.inventories[0].epoch, i.epoch)
        second.step()
        self.assertEqual(self.weapon(second.inventories[0])['cooldown_steps'], 178)

    def test_flight_projection_cannot_mutate_committed_inventory(self):
        battle = self.battle()
        before = battle.inventories[0].checkpoint()
        def illegal(*_):
            self.command(battle.inventories[0], 'discharge', quantity=1, cooldown_steps=0)
        with self.assertRaises(ContractError):
            battle.step(project=illegal)
        self.assertEqual(battle.prepared.session.world.fixed_step, 0)
        self.assertEqual(battle.inventories[0].checkpoint(), before)

    def test_real_weapon_damage_cancels_on_completion_boundary(self):
        battle = self.battle()
        self.start(battle.inventories[0])
        for _ in range(119):
            battle.step()
        session = battle.prepared.session
        hp = self.health(battle.inventories[0])[GUN]
        battle.step(device_operations=(DeviceOperation(session.world.epoch, 'ship.web.blue', GUN,
            1, 'damage', hp, session.world.fixed_step, 'opening'),))
        saved = battle.export_instances()[0].to_dict()
        self.assertEqual(saved['magazines'][0]['quantity'], 40)
        self.assertEqual(saved['weapons'][0]['ready_rounds'], 0)
        self.assertIsNone(saved['weapons'][0]['reload'])

    def test_real_wrapper_hot_loop_does_not_parse_inventory(self):
        battle = self.battle()
        self.start(battle.inventories[0], 'recipe.ordinary')
        with patch.object(ps, '_validate', side_effect=AssertionError('runtime validation')), \
             patch.object(ps, 'canonical_sha256', side_effect=AssertionError('runtime hash')), \
             patch.object(ps, 'decode', side_effect=AssertionError('runtime json')):
            for _ in range(180):
                battle.step()
        self.assertEqual(self.ammo(battle.inventories[0]), 35)

    def test_multi_ship_settlement_preparation_rolls_back_as_a_unit(self):
        packs = tuple(ps.compile_resources(s, definition(s)) for s in self.sample._seeds)
        battle = ti.enter_battle(tuple(ps.InstanceBinding(p, loaded(p, 'instance.'+str(n))) for n, p in enumerate(packs)),
            self.sample._profile, direct_instance_id='instance.0')
        self.start(battle.inventories[0])
        battle.inventories[1].prepare_settlement('battle.different')
        before = tuple(i.checkpoint() for i in battle.inventories)
        with self.assertRaises(ContractError):
            battle.prepare_settlement('battle.result.1')
        self.assertEqual(tuple(i.checkpoint() for i in battle.inventories), before)

    def test_partial_weapon_damage_keeps_reload_and_destroyed_host_cancels(self):
        seed = self.pack.seed
        device = next(m for m in seed.devices.modules if m.instance_id == GUN)
        seed = replace(seed, devices=replace(seed.devices, modules=tuple(
            replace(m, host_instance_id='cic') if m.instance_id == GUN else m for m in seed.devices.modules)))
        pack = ps.compile_resources(seed, definition(seed))
        i = self.inventory(pack=pack)
        self.start(i)
        i.advance(1, health=self.health(i, **{GUN: device.maximum_durability_points/2}))
        self.assertIsNotNone(self.weapon(i)['reload'])
        i.advance(2, health=self.health(i, cic=0))
        self.assertIsNone(self.weapon(i)['reload'])
        self.assertEqual(self.ammo(i), 40)


if __name__ == '__main__':
    unittest.main()
