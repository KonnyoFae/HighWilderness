"""D1a resource/prepare persistence tests; no claim of actual repair effects."""
import json
import unittest
from dataclasses import asdict, replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import damage_control_resources as dc, tactical_inventory as ti
from backend.high_wilderness_sidecar import prepared_deployment as deployment, tactical_settlement as st
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.test_battle_preparation import fixture, ROOT

DEVICE = 'damage_control'
GUN = 'weapon_upper_port'


class DamageControlResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ResourceIndex(ROOT)
        cls.document, cls.deploy, cls.old_policy = fixture(cls.index)
        cls.policy = json.loads((ROOT/'contracts/web_bridge/fixtures/d1-preparation-policy.v3.json').read_text(encoding='utf-8'))
        cls.design = bp.compile_design(cls.document, cls.index, cls.deploy, cls.policy, ship_id='ship.d1.player')
        cls.pack = cls.design.resources
        cls.template, cls.scenario, _ = RealtimeViewService('backend.d1')._template()

    def inventory(self, parts=8, quantity=0, pack=None):
        pack = pack or self.pack
        v = ps.fresh_instance(pack, 'instance.d1.player').to_dict()
        v['cargo'] = [dict(good_id=dc.PARTS, quantity=parts)]
        for d in v['damage_controls']:
            d['quantity_units'] = quantity
        return ti.InventorySession(pack, ps.parse_instance(v, pack))

    def send(self, inv, kind='start_damage_control_preparation', target=DEVICE, **args):
        command = dict(epoch=inv.epoch, sequence=inv.sequence+1, kind=kind, target=target, **args)
        inv.command(**command)
        return command

    def steps(self, inv, n):
        for _ in range(n):
            inv.advance(inv.fixed_step+1)

    def device(self, inv, key=DEVICE):
        return next(d for d in inv.snapshot().to_dict()['damage_controls'] if d['module_id'] == key)

    def parts(self, inv):
        return next(c['quantity'] for c in inv._value['cargo'] if c['good_id'] == dc.PARTS)

    def health(self, inv, key=DEVICE):
        return {k: 0 if k == key else hp for k, hp in inv._health.items()}

    def test_exact_versioned_empty_state_and_legacy_unchanged(self):
        value = ps.fresh_instance(self.pack, 'instance.empty').to_dict()
        self.assertEqual(value['interface'], dc.INSTANCE_INTERFACE)
        self.assertEqual(value['damage_controls'], [dict(module_id=DEVICE, quantity_units=0, preparation=None)])
        self.assertEqual(bp.restore_design(self.design.archive(), self.index), self.design)
        legacy = bp.compile_design(self.document, self.index, self.deploy, self.old_policy, ship_id='ship.legacy')
        self.assertEqual(legacy.resources.definition()['interface'], ps.RESOURCE_INTERFACE)
        self.assertNotIn('damage_controls', ps.fresh_instance(legacy.resources, 'instance.old').to_dict())
        self.assertEqual(legacy.archive()['policy'], self.old_policy)
        for field in ('interface', 'damage_controls'):
            bad = ps.clone(value)
            if field == 'interface': bad[field] = ps.INTERFACE
            else: del bad[field]
            with self.assertRaises(ps.ContractError): ps.parse_instance(bad, self.pack)

    def test_definition_and_state_reject_missing_wrong_or_unbounded_values(self):
        for update in (dict(capacity_units=0), dict(preparation_steps=True), dict(cargo_costs=[]),
                       dict(cargo_costs=[dict(good_id='cargo.special_alloy', quantity=1)]), dict(module_id=GUN)):
            definition = self.pack.definition(); definition['damage_controls'][0].update(update)
            with self.subTest(update=update), self.assertRaises(ps.ContractError):
                ps.compile_resources(self.pack.seed, definition)
        for update in (dict(quantity_units=-1), dict(quantity_units=100001), dict(quantity_units=1.5),
                       dict(preparation=dict(remaining_steps=0)), dict(preparation=dict(remaining_steps=301))):
            value = self.inventory().snapshot().to_dict(); value['damage_controls'][0].update(update)
            with self.subTest(update=update), self.assertRaises(ps.ContractError): ps.parse_instance(value, self.pack)

    def test_prepare_reserves_volume_then_pays_once_at_deadline(self):
        inv = self.inventory()
        volume = inv.summary()['used_volume_cm3']
        self.send(inv)
        self.assertEqual(inv.summary()['reserved_cargo'][dc.PARTS], 2)
        self.assertEqual(inv.summary()['used_volume_cm3'], volume)
        self.steps(inv, 299)
        self.assertEqual(self.device(inv)['preparation']['remaining_steps'], 1)
        self.assertEqual(self.parts(inv), 8)
        inv.advance(300)
        self.assertEqual(self.device(inv), dict(module_id=DEVICE, quantity_units=100000, preparation=None))
        self.assertEqual(self.parts(inv), 6)
        self.assertEqual(inv.summary()['used_volume_cm3'], volume-2000000)
        inv.advance(300)
        self.assertEqual(self.parts(inv), 6)

    def test_empty_only_consume_refill_and_no_free_stock(self):
        inv = self.inventory(); inv.prepare_damage_control(DEVICE)
        self.send(inv, 'consume_damage_control', quantity=12345)
        before = inv.checkpoint()
        with self.assertRaises(ps.ContractError): self.send(inv)
        self.assertEqual(before, inv.checkpoint())
        self.assertEqual(self.device(inv)['quantity_units'], 87655)
        self.send(inv, 'consume_damage_control', quantity=87655)
        self.send(inv); self.steps(inv, 300)
        self.assertEqual(self.device(inv)['quantity_units'], 100000)
        self.assertEqual(self.parts(inv), 4)
        self.assertEqual(sum(c['delta'] for c in inv.changes() if c['resource']=='damage_control:'+DEVICE), 100000)

    def test_shortage_cancel_and_idempotency_are_atomic(self):
        inv = self.inventory(parts=1); before = inv.checkpoint()
        with self.assertRaises(ps.ContractError): self.send(inv)
        self.assertEqual(inv.checkpoint(), before)
        self.send(inv, 'load_cargo', target=dc.PARTS, quantity=1)
        command = self.send(inv)
        self.assertFalse(inv.command(**command))
        bad = dict(command, kind='cancel_damage_control_preparation')
        with self.assertRaises(ps.ContractError): inv.command(**bad)
        self.steps(inv, 299); self.send(inv, 'cancel_damage_control_preparation'); self.steps(inv, 2)
        self.assertEqual(self.parts(inv), 2)
        self.assertEqual(self.device(inv)['quantity_units'], 0)
        self.assertEqual(inv.summary()['reserved_cargo'][dc.PARTS], 0)
        self.assertFalse(any(c['reason']=='damage_control_preparation' for c in inv.changes()))

    def test_destruction_and_collapse_take_precedence_over_completion(self):
        for collapse in (False, True):
            inv = self.inventory(); self.send(inv); self.steps(inv, 299)
            inv.advance(300, **(dict(hull_integrity=0) if collapse else dict(health=self.health(inv))))
            self.assertIsNone(self.device(inv)['preparation'])
            self.assertEqual(self.parts(inv), 8)
            self.assertEqual(self.device(inv)['quantity_units'], 0)
            with self.assertRaises(ps.ContractError): self.send(inv)
            inv.prepare_settlement('settlement.destroyed')
            self.assertEqual(self.parts(inv), 8)

        inv = self.inventory(); self.send(inv)
        value = inv.snapshot().to_dict()
        value['hull_integrity_fraction'] = 0
        value['service'] = dict(status='destroyed', reasons=['hull_structure_collapsed'])
        restored = ti.InventorySession(self.pack, ps.parse_instance(value, self.pack))
        restored.advance(1)
        self.assertIsNone(self.device(restored)['preparation'])
        self.assertEqual(self.parts(restored), 8)

    def test_damage_reducing_cargo_capacity_does_not_delete_reservation(self):
        inv = self.inventory(); self.send(inv)
        inv.advance(1, health=self.health(inv, 'custom.cargo'))
        self.assertTrue(inv.summary()['over_capacity'])
        self.assertEqual(inv.summary()['reserved_cargo'][dc.PARTS], 2)
        with self.assertRaises(ps.ContractError): self.send(inv, 'load_cargo', target=dc.PARTS, quantity=1)
        self.steps(inv, 299)
        self.assertEqual(self.parts(inv), 6)
        self.assertEqual(self.device(inv)['quantity_units'], 100000)

    def test_host_destruction_cancels_and_retained_store_is_not_refilled(self):
        seed = self.pack.seed
        host = next(m.instance_id for m in seed.devices.modules if m.instance_id != DEVICE and m.host_instance_id is None)
        seed = replace(seed, devices=replace(seed.devices, modules=tuple(
            replace(m, host_instance_id=host) if m.instance_id == DEVICE else m for m in seed.devices.modules)))
        definition = self.pack.definition(); definition['source_seed_sha256'] = ps.canonical_sha256(asdict(seed))
        pack = ps.compile_resources(seed, definition)
        inv = self.inventory(pack=pack); self.send(inv)
        inv.advance(1, health=self.health(inv, host))
        self.assertIsNone(self.device(inv)['preparation'])
        self.assertEqual(self.parts(inv), 8)
        with self.assertRaises(ps.ContractError): self.send(inv)
        inv = self.inventory(quantity=10000)
        inv.advance(1, health=self.health(inv))
        with self.assertRaises(ps.ContractError): self.send(inv, 'consume_damage_control', quantity=1)
        inv.prepare_settlement('settlement.damaged')
        self.assertEqual(self.device(inv)['quantity_units'], 10000)

    def test_mixed_legacy_fleet_draft_keeps_device_identities_explicit(self):
        old = bp.compile_design(self.document, self.index, self.deploy, self.old_policy, ship_id='ship.d1.old')
        ships = [(self.design, bp.new_record(self.design, 'instance.new')), (old, bp.new_record(old, 'instance.old'))]
        supply = dict(interface=bp.SUPPLY_INTERFACE, supply_id='supply.mixed', revision=0, ammunition_resources=0, cargo=[])
        draft = bp.new_draft('preparation.mixed', ships, supply)
        self.assertEqual(draft['ships'][1]['damage_controls'], [])
        self.assertEqual(bp.validate_draft(draft, ships, supply), draft)
        bad = ps.clone(draft); del bad['ships'][0]['damage_controls']
        with self.assertRaises(ps.ContractError): bp.validate_draft(bad, ships, supply)
        bad = ps.clone(draft); bad['ships'][1]['damage_controls'] = [dict(module_id=DEVICE, prepare=True)]
        with self.assertRaises(ps.ContractError): bp.validate_draft(bad, ships, supply)

    def test_completion_projection_failure_preserves_deadline_and_payment(self):
        record = bp.new_record(self.design, 'instance.d1.player')
        record['state'] = self.inventory().snapshot().to_dict()
        battle = deployment.build([(self.design,record)], 'instance.d1.player', self.template, self.scenario)[0]
        battle.enemy_fire = False
        inv = battle.inventory.inventories[0]
        command = dict(epoch=inv.epoch, sequence=inv.sequence+1, kind='start_damage_control_preparation', target=DEVICE)
        battle.step(inventory_commands=((0,command),))
        for _ in range(299): battle.step()
        inv = battle.inventory.inventories[0]
        self.assertEqual(self.device(inv)['preparation']['remaining_steps'], 1)
        before, world = inv.checkpoint(), battle.session.world
        def fail(*_): raise RuntimeError('publish failed')
        with self.assertRaises(RuntimeError): battle.step(project=fail)
        self.assertEqual(battle.inventory.inventories[0].checkpoint(), before)
        self.assertEqual(battle.session.world, world)
        battle.step()
        self.assertEqual(self.device(battle.inventory.inventories[0])['quantity_units'], 100000)
        self.assertEqual(self.parts(battle.inventory.inventories[0]), 6)

    def test_multiple_devices_and_weapon_share_one_reserved_cargo_pool(self):
        # Bounded inventory fixture with two independent installed module identities.
        seed = self.pack.seed
        m = next(m for m in seed.resources.modules if m.id == DEVICE)
        d = next(d for d in seed.devices.modules if d.instance_id == DEVICE)
        n = next(n for n, x in enumerate(seed.resources.modules) if x.id == DEVICE)
        seed = replace(seed, resources=replace(seed.resources, modules=seed.resources.modules+(replace(m, id='damage_control.second'),),
                modes=seed.resources.modes+(seed.resources.modes[n],)),
            devices=replace(seed.devices, modules=seed.devices.modules+(replace(d, instance_id='damage_control.second'),),
                initial_durability_points=seed.devices.initial_durability_points+(d.maximum_durability_points,)))
        definition = self.pack.definition(); definition['source_seed_sha256'] = ps.canonical_sha256(asdict(seed))
        definition['damage_controls'].append(dict(definition['damage_controls'][0], module_id='damage_control.second'))
        definition['recipes'][0]['cargo_costs'] = [dict(good_id=dc.PARTS, quantity=1)]
        pack = ps.compile_resources(seed, definition)
        inv = self.inventory(parts=3, pack=pack)
        self.send(inv, 'load_ammunition', target='ammunition_magazine', quantity=5)
        self.send(inv, 'start_reload', target=GUN, recipe_id='recipe.x1a.ordinary')
        self.send(inv)
        before = inv.checkpoint()
        with self.assertRaises(ps.ContractError): self.send(inv, target='damage_control.second')
        with self.assertRaises(ps.ContractError): self.send(inv, 'consume_cargo', target=dc.PARTS, quantity=1)
        self.assertEqual(inv.checkpoint(), before)
        self.steps(inv, 300)
        self.assertEqual(self.parts(inv), 0)
        self.assertEqual(self.device(inv)['quantity_units'], 100000)
        self.assertEqual(self.device(inv, 'damage_control.second')['quantity_units'], 0)

    def test_idle_steps_do_not_reparse_or_clone(self):
        inv = self.inventory(); self.send(inv)
        with patch.object(ps, 'compile_resources', side_effect=AssertionError('recompile')), \
             patch.object(ps, '_validate', side_effect=AssertionError('validate')), \
             patch.object(ti, 'deepcopy', side_effect=AssertionError('clone')):
            self.steps(inv, 299)
        inv.advance(300)
        self.assertEqual(self.device(inv)['quantity_units'], 100000)

    def test_resource_roundtrip_and_reservation_validation(self):
        inv = self.inventory(); self.send(inv); self.steps(inv, 123)
        restored = ti.InventorySession.restore(self.pack, inv.checkpoint())
        self.assertEqual(restored.checkpoint(), inv.checkpoint())
        self.steps(restored, 177)
        self.assertEqual(self.parts(restored), 6)
        bad = inv.snapshot().to_dict(); bad['cargo'][0]['quantity'] = 1
        with self.assertRaises(ps.ContractError): ps.parse_instance(bad, self.pack)
        bad = inv.snapshot().to_dict(); bad['damage_controls'][0]['quantity_units'] = 1
        with self.assertRaises(ps.ContractError): ps.parse_instance(bad, self.pack)

    def test_settlement_finishes_existing_preparation_only(self):
        inv = self.inventory(); inv.prepare_settlement('settlement.idle')
        self.assertEqual(self.device(inv)['quantity_units'], 0)
        inv = self.inventory(); self.send(inv); self.steps(inv, 1)
        before = inv.snapshot().to_dict()['modules']
        inv.prepare_settlement('settlement.ending')
        self.assertEqual(self.parts(inv), 6)
        self.assertEqual(self.device(inv)['quantity_units'], 100000)
        self.assertEqual(inv.snapshot().to_dict()['modules'], before)
        self.assertFalse(inv.prepare_settlement('settlement.ending'))
        self.assertEqual(ti.InventorySession.restore(self.pack, inv.checkpoint()).checkpoint(), inv.checkpoint())
        with self.assertRaises(ps.ContractError): self.send(inv, 'consume_damage_control', quantity=1)

    def test_all_ship_prepare_commit_restart_and_exact_retry(self):
        with TemporaryDirectory() as directory:
            store = PreparationStore(directory, self.index)
            for n in range(2): store.create_ship(self.design, 'instance.d1.'+str(n))
            supply = dict(interface=bp.SUPPLY_INTERFACE, supply_id='supply.d1', revision=0, ammunition_resources=0,
                cargo=[dict(good_id=dc.PARTS, quantity=10)])
            store.provision_supply(supply, self.policy['goods'])
            draft = store.draft('preparation.d1', ['instance.d1.0','instance.d1.1'], 'supply.d1')
            self.assertEqual(draft['interface'], dc.DRAFT_INTERFACE)
            before = ps.clone(draft)
            for row in draft['ships']:
                row['cargo'] = [dict(good_id=dc.PARTS, quantity=4)]
                row['damage_controls'][0]['prepare'] = True
            bad = ps.clone(draft); bad['ships'][1]['cargo'][0]['quantity'] = 1
            self.assertFalse(store.preview(bad)['can_commit'])
            with self.assertRaises(ps.ContractError): store.commit(bad)
            self.assertEqual(store.draft('preparation.d1',['instance.d1.0','instance.d1.1'],'supply.d1'),before)
            preview = store.preview(draft)
            self.assertTrue(preview['can_commit'])
            receipt = store.commit(draft)
            self.assertEqual(receipt, preview['result'])
            reopened = PreparationStore(directory, self.index)
            self.assertEqual(reopened.commit(draft), receipt)
            self.assertEqual(receipt['supply_after']['cargo'][0]['quantity'], 2)
            for row in receipt['ships']:
                self.assertEqual(row['after']['state']['damage_controls'][0]['quantity_units'], 100000)
                self.assertEqual(row['after']['state']['cargo'][0]['quantity'], 2)
            keep = reopened.draft('preparation.keep',['instance.d1.0'],'supply.d1')
            self.assertFalse(keep['ships'][0]['damage_controls'][0]['prepare'])
            again = reopened.commit(keep)['ships'][0]
            self.assertEqual(again['before']['state']['damage_controls'], again['after']['state']['damage_controls'])
            self.assertEqual(again['changes'], [])

    def test_real_flight_transaction_failure_and_saved_redeployment(self):
        record = bp.new_record(self.design, 'instance.d1.player')
        record['state'] = self.inventory(quantity=40000).snapshot().to_dict()
        battle = deployment.build([(self.design, record)], 'instance.d1.player', self.template, self.scenario)[0]
        battle.enemy_fire = False
        inv = battle.inventory.inventories[0]
        command = dict(epoch=inv.epoch, sequence=inv.sequence+1, kind='consume_damage_control', target=DEVICE, quantity=40000)
        before, world = inv.checkpoint(), battle.session.world
        def fail(*_): raise RuntimeError('projection failed')
        with self.assertRaises(RuntimeError): battle.step(inventory_commands=((0,command),), project=fail)
        self.assertEqual(inv.checkpoint(), before)
        self.assertEqual(battle.session.world, world)
        battle.step(inventory_commands=((0,command),))
        inv = battle.inventory.inventories[0]
        command = dict(epoch=inv.epoch, sequence=inv.sequence+1, kind='start_damage_control_preparation', target=DEVICE)
        battle.step(inventory_commands=((0,command),))
        self.assertEqual(self.parts(battle.inventory.inventories[0]), 8)
        battle.withdraw()
        result = st.capture(battle)
        self.assertEqual(result['ships'][0]['after']['state']['damage_controls'][0]['quantity_units'], 100000)
        with TemporaryDirectory() as directory:
            store = st.SettlementStore(directory)
            store.stage(result); store.save(result['settlement_id'])
            reopened = st.SettlementStore(directory)
            reopened.save(result['settlement_id'])
            saved = reopened.load_ship('instance.d1.player', 1)
            again = deployment.build([(self.design,saved)],'instance.d1.player',self.template,self.scenario)[0]
            self.assertEqual(self.device(again.inventory.inventories[0])['quantity_units'], 100000)
            self.assertEqual(self.parts(again.inventory.inventories[0]), 6)


if __name__ == '__main__':
    unittest.main()
