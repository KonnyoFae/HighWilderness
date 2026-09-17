"""3d real damage -> finite stock loss -> local blast -> persistent settlement."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from tools import test_tactical_spatial_fire as spatial_tests, test_tactical_damage as hit_tests
from tools import test_tactical_fire as fire_tests
from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_settlement as settlement
from backend.high_wilderness_sidecar import tactical_magazine as magazine, battle_preparation as preparation
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.persistent_ship_fixture import definition, loaded
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_fire import Fire

MAG = 'ammunition_magazine'


class MagazineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spatial_tests.SpatialFireTests.setUpClass()
        cls.f = spatial_tests.SpatialFireTests()
        doc = ps.clone(cls.f.f.doc)
        second = ps.clone(next(m for m in doc['outfit']['modules'] if m['id'] == MAG))
        second['id'] = 'magazine.second'
        second['placement']['anchor_half_cell'] = [0,14]
        doc['outfit']['modules'].append(second)
        next(m for m in doc['outfit']['modules'] if m['id'] == 'crew_quarters_upper')['placement']['anchor_half_cell'] = [-2,6]
        cls.two = cls.f.f.compile(doc, load_current(Path(__file__).resolve().parents[1]))

    def battle(self, *, quantity=50, hp=100, design=None, record=None):
        design = design or self.f.design
        r = record or self.f.f.record(design)
        if record is None:
            next(m for m in r['state']['modules'] if m['module_id'] == MAG)['durability_points'] = hp
            next(m for m in r['state']['magazines'] if m['module_id'] == MAG)['quantity'] = quantity
        return self.f.battle(design=design, record=r)

    def destroy(self, b, target=MAG):
        b.step(device_operations=(fire_tests.TacticalFireTests().operation(b,target,100),))

    def stock(self, b, target=MAG):
        return next(m['quantity'] for m in b.inventory.inventories[0]._value['magazines'] if m['module_id'] == target)

    def hp(self, b, target=MAG):
        return b.session.world.ships[0].devices.modules[b._indices[0][target]].durability_points

    def test_real_shell_destruction_consumes_actual_stock_and_adds_local_damage(self):
        b = self.battle(hp=35)
        enemy = b.session.world.ships[1]
        hit_tests.DamageTests().shell(b,(-30,30),(30,30));b.step()
        event, = b.magazines.recent
        self.assertEqual((event['cause'],event['deck_level'],event['ammunition_resources']),('projectile',0,50))
        self.assertEqual(self.stock(b),0)
        self.assertEqual(self.hp(b),0)
        self.assertLess(self.hp(b,'fire_control'),100)
        self.assertEqual(b.session.world.ships[1].devices,enemy.devices)
        self.assertEqual(b.session.world.ships[1].motion.hull_integrity_fraction,enemy.motion.hull_integrity_fraction)
        self.assertIn(dict(resource='ammunition',reason='magazine_detonation',delta=-50), b.inventory.inventories[0].changes())
        self.assertEqual(b.view()['damage']['magazine_detonations'],1)

    def test_damage_and_fire_without_destruction_do_not_roll_or_explode(self):
        b = self.battle()
        hit_tests.DamageTests().shell(b,(-30,30),(30,30));b.step()
        self.assertGreater(self.hp(b),0)
        zone = next(z for z in b.fire.zones[0].values() if not z.surface and MAG in z.modules)
        self.f.fire(b,zone)
        for _ in range(30):b.step()
        self.assertGreater(self.hp(b),0)
        self.assertEqual(b.magazines.count,0)
        self.assertEqual(self.stock(b),50)

    def test_empty_destroyed_magazine_has_no_explosion(self):
        b = self.battle(quantity=0)
        hull = b.session.world.ships[0].motion.hull_integrity_fraction
        self.destroy(b)
        self.assertEqual(b.magazines.count,0)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,hull)
        self.assertFalse(b.inventory.inventories[0].changes())

    def test_internal_fire_can_destroy_magazine_on_actual_deck(self):
        b = self.battle(hp=.01)
        zone = next(z for z in b.fire.zones[0].values() if not z.surface and MAG in z.modules)
        self.f.fire(b,zone)
        b.step()
        event, = b.magazines.recent
        self.assertEqual(event['cause'],'fire')
        self.assertEqual(event['deck_level'],zone.level)
        self.assertEqual(self.stock(b),0)

    def test_surface_fire_cannot_destroy_internal_magazine_through_armor(self):
        b = self.battle(hp=.01)
        zone = min((z for z in b.fire.zones[0].values() if z.surface),key=lambda z:abs(z.center[1]-30))
        self.f.fire(b,zone)
        b.step()
        self.assertEqual(self.hp(b),.01)
        self.assertEqual(b.magazines.count,0)

    def test_no_chain_or_next_step_delayed_chain_and_other_deck_untouched(self):
        b = self.battle(design=self.two)
        upper = self.hp(b,'crew_quarters_upper')
        self.destroy(b)
        self.assertEqual(self.hp(b,'magazine.second'),0)
        self.assertEqual(b.magazines.count,1)
        self.assertEqual(self.hp(b,'crew_quarters_upper'),upper)
        self.assertEqual(self.stock(b,'magazine.second'),50)
        for _ in range(3):b.step()
        self.assertEqual(b.magazines.count,1)
        self.assertEqual(self.stock(b,'magazine.second'),50)

    def test_independent_same_step_destructions_each_explode_once(self):
        b = self.battle(design=self.two)
        helper = fire_tests.TacticalFireTests()
        b.step(device_operations=tuple(helper.operation(b,k,100) for k in (MAG,'magazine.second')))
        self.assertEqual(b.magazines.count,2)
        self.assertEqual(self.stock(b),0)
        self.assertEqual(self.stock(b,'magazine.second'),0)
        self.assertEqual(sum(e['ammunition_resources'] for e in b.magazines.recent),100)
        b.step();self.assertEqual(b.magazines.count,2)

    def test_blast_strength_and_radius_follow_remaining_stock(self):
        small = self.battle(quantity=1);large = self.battle(quantity=50)
        for b in (small,large):self.destroy(b)
        a,c = small.magazines.recent[0],large.magazines.recent[0]
        self.assertLess(a['radius_m'],c['radius_m'])
        self.assertAlmostEqual(c['hull_damage_fraction'],a['hull_damage_fraction']*50)
        self.assertEqual(self.hp(small,'fire_control'),100)
        self.assertLess(self.hp(large,'fire_control'),100)

    def test_loaded_rounds_survive_and_partial_damage_does_not_delete_stock(self):
        b = self.battle()
        before = ps.clone(b.inventory.inventories[0]._value['weapons'])
        self.destroy(b)
        self.assertEqual(b.inventory.inventories[0]._value['weapons'],before)

    def test_multi_magazine_reload_cancels_whole_batch_without_spending_other_stock(self):
        b = self.battle(design=self.two)
        pack = ps.compile_resources(b.session._seeds[0], definition(b.session._seeds[0]))
        value = loaded(pack,'instance.split.reload').to_dict()
        next(m for m in value['magazines'] if m['module_id']==MAG)['quantity'] = 2
        inv = InventorySession(pack,ps.parse_instance(value,pack))
        gun = b.guns[0].module_id
        inv.command(epoch=inv.epoch,sequence=1,kind='discharge',target=gun,quantity=1,cooldown_steps=0)
        inv.command(epoch=inv.epoch,sequence=2,kind='start_reload',target=gun,recipe_id='recipe.special')
        self.assertEqual(inv.summary()['reserved_ammunition'],{MAG:2,'magazine.second':3})
        cargo = ps.clone(inv._value['cargo'])
        inv.consume_destroyed_magazine(MAG,2)
        self.assertIsNone(inv._value['weapons'][0]['reload'])
        self.assertEqual(inv._value['cargo'],cargo)
        self.assertEqual(next(m['quantity'] for m in inv._value['magazines'] if m['module_id']=='magazine.second'),40)
        self.assertEqual(sum(inv.summary()['reserved_cargo'].values()),0)
        restored = InventorySession.restore(pack,inv.checkpoint())
        self.assertEqual(restored.changes(),inv.changes())
        before = inv.checkpoint()
        with self.assertRaises(ps.ContractError):inv.consume_destroyed_magazine(MAG,2)
        self.assertEqual(inv.checkpoint(),before)

    def test_blast_armor_damage_is_local_and_does_not_cross_decks_or_ships(self):
        b = self.battle()
        b.damage_state = replace(b.damage_state,armor=tuple(tuple(1000. for _ in edges) for edges in b.damage.edges))
        before = b.damage_state.armor
        self.destroy(b)
        event = b.magazines.recent[0]
        changed = [e for e,a,v in zip(b.damage.edges[0],before[0],b.damage_state.armor[0]) if a!=v]
        self.assertTrue(changed)
        self.assertTrue(all(e.key[1]==0 and magazine.segment_distance((0,30),e.start,e.end)<event['radius_m'] for e in changed))
        self.assertEqual(before[1],b.damage_state.armor[1])

    def test_distances_cover_cell_boundaries_and_filled_tank_polygons(self):
        poly = ((0.,0.),(5.,0.),(5.,5.),(0.,5.))
        self.assertEqual(magazine.polygon_distance((2,2),poly),0)
        self.assertEqual(magazine.polygon_distance((5,2),poly),0)
        self.assertEqual(magazine.polygon_distance((8,2),poly),3)
        self.assertEqual(magazine.bounds_distance((8,9),((0,0),(5,5))),5)

    def test_detonation_precedes_reload_completion_and_releases_special_goods(self):
        b = self.battle();inv = b.inventory.inventories[0];gun = b.guns[0].module_id
        inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='discharge',target=gun,quantity=1,cooldown_steps=0)
        inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='start_reload',target=gun,recipe_id='recipe.h5d.incendiary')
        due = inv._due[gun];cargo = ps.clone(inv._value['cargo'])
        self.assertGreater(sum(inv.summary()['reserved_cargo'].values()),0)
        for _ in range(due-1):b.step()
        self.destroy(b)
        inv = b.inventory.inventories[0]
        self.assertEqual(inv.fixed_step,due)
        self.assertEqual(inv._value['cargo'],cargo)
        self.assertEqual(sum(inv.summary()['reserved_cargo'].values()),0)
        self.assertEqual(self.stock(b),0)
        self.assertEqual(inv._value['weapons'][0]['ready_rounds'],0)
        self.assertIsNone(inv._value['weapons'][0]['reload'])
        self.assertNotIn(gun,inv._due)
        self.assertFalse(any(e['reason']=='reload' for e in inv.changes()))

    def test_failed_step_rolls_back_stock_damage_history_and_retry(self):
        b = self.battle(hp=35)
        hit_tests.DamageTests().shell(b,(-30,30),(30,30))
        before = (b.session.world,b.damage_state,b.projectiles,b.inventory.inventories,b.magazines.recent)
        with self.assertRaisesRegex(RuntimeError,'failed observer'):
            b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('failed observer')))
        self.assertEqual((b.session.world,b.damage_state,b.projectiles,b.inventory.inventories,b.magazines.recent),before)
        self.assertEqual(self.stock(b),50)
        b.step()
        self.assertEqual(b.magazines.count,1)
        self.assertEqual(self.stock(b),0)

    def test_save_reload_preserves_zero_stock_and_does_not_retroactively_explode(self):
        b = self.battle();self.destroy(b);b.withdraw()
        result = settlement.capture(b)
        with TemporaryDirectory() as folder:
            store = settlement.SettlementStore(folder);store.stage(result);store.save(result['settlement_id'])
            record = settlement.SettlementStore(folder).load_ship('instance.ignition.player',1)
            again = self.battle(record=record)
            self.assertEqual(self.stock(again),0)
            self.assertEqual(self.hp(again),0)
            again.step();self.assertEqual(again.magazines.count,0)

    def test_old_destroyed_stock_is_not_a_new_destruction(self):
        b = self.battle(hp=0)
        b.step();self.assertEqual(b.magazines.count,0)
        self.assertEqual(self.stock(b),50)

    def test_idle_steps_do_not_parse_policy_or_mutate_resource_revision(self):
        b = self.battle();revision = b.inventory.inventories[0]._reservation_revision
        with patch.object(magazine,'policy',side_effect=AssertionError('reparse')):
            for _ in range(60):b.step()
        self.assertEqual(b.inventory.inventories[0]._reservation_revision,revision)


if __name__ == '__main__':unittest.main()
