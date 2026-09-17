"""3e actual damage, finite casualties, priority staffing and cross-battle state."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from tools import test_tactical_spatial_fire as spatial, test_tactical_damage as hits, test_tactical_fire as fires
from tools import test_simplified_flight as flight_tests
from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_settlement as settlement
from backend.high_wilderness_sidecar import tactical_personnel as personnel, simplified_flight as flight
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession

QUARTERS = 'crew_quarters_base'


class PersonnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spatial.SpatialFireTests.setUpClass();cls.f = spatial.SpatialFireTests()

    def battle(self,crew=None,record=None,manual=False):
        r = record or self.f.f.record(self.f.design)
        if crew:
            for row in r['state']['crew']:row['count'] = crew.get(row['crew_type'],row['count'])
        b = self.f.battle(record=r)
        if manual:
            # Named runtime fixture: original tutorial automation is retained
            # in production. These tests exercise genuinely staffed functions.
            kernel = b.session._resource_kernels[0]
            for mid,module in tuple(kernel.modules.items()):
                if module.prototype.category in ('main_engine','maneuver_thruster','weapon'):
                    value = replace(module,prototype=replace(module.prototype,automation=replace(module.prototype.automation,
                        level='manual',automated_functions=())))
                    kernel.modules[mid] = value;b._modules[0][mid] = value
            kernel.seed = replace(kernel.seed,modules=tuple(kernel.modules.values()))
            ship = b.session.world.ships[0]
            b.session._world = replace(b.session.world,ships=(replace(ship,resources=replace(ship.resources,cache_key=None)),*b.session.world.ships[1:]))
        return b

    def hit(self,b,deck=0,count=1):
        for _ in range(count):
            hits.DamageTests().shell(b,(-30,23),(30,-37),deck=deck);b.step()

    def people(self,b):return b.personnel.view()['ships'][0]

    def test_real_shell_changes_typed_fit_wounded_and_exact_source_deck(self):
        b = self.battle();self.hit(b)
        self.assertEqual(b.damage_state.recent[-1]['module_ids'],[QUARTERS])
        self.assertEqual(dict(b.session.world.ships[0].resources.crew)['ordinary'],9)
        self.assertEqual(self.people(b)['wounded'],1)
        event, = b.personnel.recent
        self.assertEqual((event['cause'],event['deck_level'],event['module_id']),('projectile',0,QUARTERS))
        self.assertEqual(b.personnel.records[1],None)

    def test_both_quarters_produce_half_entry_population_with_typed_death_carry(self):
        b = self.battle();self.hit(b,0,3);self.hit(b,1,3)
        p = self.people(b)
        self.assertEqual((p['fit'],p['wounded'],p['dead']),(9,8,1))
        self.assertEqual(p['fit']+p['wounded']+p['dead'],18)
        self.assertEqual(next(r for r in p['types'] if r['crew_type']=='ordinary'),dict(crew_type='ordinary',fit=5,wounded=4,dead=1))
        self.assertIsNone(b.ending)
        self.assertEqual(dict(b.session.world.ships[0].resources.allocations)['cic'],(('officer',1.),))

    def test_no_hit_or_non_quarter_damage_does_not_remove_personnel(self):
        b = self.battle()
        hits.DamageTests().shell(b,(-30,-50),(30,-50));b.step()
        self.assertEqual(self.people(b)['fit'],18)
        self.assertIsNone(b.personnel.records[0])
        for _ in range(20):b.step()
        self.assertFalse(b.personnel.recent)

    def test_overkill_and_destroyed_quarter_do_not_repeat_casualties(self):
        b = self.battle()
        for _ in range(10):hits.DamageTests().shell(b,(-30,23),(30,-37))
        b.step();before = ps.clone(b.personnel.records[0])
        self.assertEqual(self.people(b)['fit'],15)
        self.hit(b,0,2)
        self.assertEqual(b.personnel.records[0],before)

    def test_internal_quarter_fire_accumulates_small_damage_into_casualties(self):
        b = self.battle()
        z = next(z for z in b.fire.zones[0].values() if not z.surface and QUARTERS in z.modules)
        self.f.fire(b,z,units=10000,steps=1000,spread=999)
        for _ in range(75):b.step()
        self.assertLess(self.people(b)['fit'],18)
        self.assertTrue(all(e['cause']=='fire' and e['module_id']==QUARTERS for e in b.personnel.recent))
        self.assertGreater(b.personnel.records[0]['statuses'][0]['loss_fraction'],0)

    def test_surface_and_other_internal_fires_do_not_cause_quarter_casualties(self):
        for surface in (False,True):
            b = self.battle()
            z = next(z for z in b.fire.zones[0].values() if z.surface==surface and
                     (not z.modules if surface else 'custom.cargo' in z.modules))
            self.f.fire(b,z,units=10000,steps=1000,spread=999)
            for _ in range(10):b.step()
            self.assertEqual(self.people(b)['fit'],18)
            self.assertFalse(b.personnel.recent)

    def test_magazine_blast_uses_actual_quarter_loss_once(self):
        b = self.battle()
        # Relocate only the blast epicentre in this focused geometry test.
        b.magazines.locations[0]['ammunition_magazine'] = {0:(-5.,5.)}
        b.step(device_operations=(fires.TacticalFireTests().operation(b,'ammunition_magazine',100),))
        q = [e for e in b.personnel.recent if e['module_id']==QUARTERS]
        self.assertEqual(len(q),1)
        self.assertEqual(q[0]['cause'],'magazine_detonation')
        self.assertTrue(all(e['deck_level']==0 for e in b.personnel.recent))
        self.assertEqual(self.people(b)['fit']+self.people(b)['wounded']+self.people(b)['dead'],18)

    def test_failed_step_rolls_back_people_work_and_retry(self):
        b = self.battle();hits.DamageTests().shell(b,(-30,23),(30,-37))
        before = (b.session.world,b.inventory.inventories,b.personnel.records,b.personnel.recent)
        with self.assertRaisesRegex(RuntimeError,'failed view'):
            b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('failed view')))
        self.assertEqual((b.session.world,b.inventory.inventories,b.personnel.records,b.personnel.recent),before)
        b.step();self.assertEqual(self.people(b)['wounded'],1)

    def test_save_restart_reentry_preserves_fit_wounded_dead_and_fractions(self):
        b = self.battle();self.hit(b,0,3);self.hit(b,1,2)
        record_before = ps.clone(b.personnel.records[0]);fit = b.session.world.ships[0].resources.crew
        wounded = self.people(b)['wounded'];b.withdraw()
        with TemporaryDirectory() as folder:
            store = settlement.SettlementStore(folder);result = settlement.capture(b)
            store.stage(result);store.save(result['settlement_id'])
            record = settlement.SettlementStore(folder).load_ship('instance.ignition.player',1)
            again = self.battle(record=record)
            self.assertEqual(again.personnel.records[0],record_before)
            self.assertEqual(again.session.world.ships[0].resources.crew,fit)
            self.assertEqual(self.people(again)['wounded'],wounded)
            again.step();self.assertFalse(again.personnel.recent)

    def test_fractional_loss_alone_is_persisted_and_bad_states_rejected(self):
        b = self.battle()
        z = next(z for z in b.fire.zones[0].values() if not z.surface and QUARTERS in z.modules)
        self.f.fire(b,z);b.step()
        self.assertEqual(self.people(b)['fit'],18)
        self.assertIsNotNone(b.personnel.records[0])
        value = b.inventory.inventories[0].snapshot().to_dict()
        self.assertEqual(ps.parse_instance(value,b.inventory.inventories[0].pack).to_dict(),value)
        for key,value_to_set in (('loss_fraction',1),('death_fraction',-1),('wounded',1000),('crew_type','unknown')):
            bad = ps.clone(value);bad['personnel']['statuses'][0][key] = value_to_set
            with self.assertRaises(ps.ContractError):ps.parse_instance(bad,b.inventory.inventories[0].pack)

    def test_old_unclassified_wounded_remain_aboard_and_preserve_safety_lock(self):
        r = self.f.f.record(self.f.design);r['state']['wounded_aboard'] = 3
        b = self.battle(record=r);self.hit(b)
        self.assertEqual(self.people(b)['unclassified_wounded'],3)
        self.assertEqual(self.people(b)['wounded'],4)
        ship = b.session.world.ships[0];kernel = b.session._command_kernels[0]
        resources = replace(ship.resources,crew=tuple((k,0) for k,_ in ship.resources.crew),revision=ship.resources.revision+1)
        command = kernel.resolve(ship.command,ship.devices,resources,ship.motion,mass=ship.motion.fuel_units+100,
            step=b.session.world.fixed_step)
        self.assertTrue(command.crew_lock)

    def test_priority_and_zero_staff_block_weapon_without_division_by_zero(self):
        b = self.battle(crew={'ordinary':5},manual=True)
        self.assertEqual(b.crew_efficiency(b.session.world,0,'main_engine_port','engine.throttle'),.5)
        self.assertEqual(b.crew_efficiency(b.session.world,0,b.guns[0].module_id,'weapon.fire'),0)
        b.step();self.assertEqual(b.states[0].status,'crew_unavailable')

    def test_half_staff_reload_is_slower_and_checkpoint_roundtrip_is_valid(self):
        b = self.battle(crew={'ordinary':8});inv = b.inventory.inventories[0];gun = b.guns[0].module_id
        inv.command(epoch=inv.epoch,sequence=1,kind='discharge',target=gun,quantity=1,cooldown_steps=0)
        b.step();inv = b.inventory.inventories[0]
        reload = inv.snapshot().to_dict()['weapons'][0]['reload']
        self.assertEqual(reload['crew_work_rate'],.5)
        self.assertEqual(reload['remaining_steps'],inv._recipes[reload['recipe_id']]['reload_steps']*2)
        self.assertEqual(InventorySession.restore(inv.pack,inv.checkpoint()).checkpoint(),inv.checkpoint())

    def test_half_staff_slows_turret(self):
        normal = self.battle(manual=True);short = self.battle(crew={'ordinary':8},manual=True)
        for b in (normal,short):
            b.states = tuple(replace(s,mode='manual',manual_point=(100,1000)) if n==0 else s for n,s in enumerate(b.states))
            b.step()
        self.assertAlmostEqual(short.states[0].angle,normal.states[0].angle*.5)
        self.assertNotEqual(normal.states[0].angle,0)

    def test_losing_all_reload_staff_cancels_reserved_batch_even_with_automated_fire(self):
        b = self.battle(crew={'ordinary':8});inv = b.inventory.inventories[0];gun = b.guns[0].module_id
        inv.command(epoch=inv.epoch,sequence=1,kind='discharge',target=gun,quantity=1,cooldown_steps=0)
        b.step();self.assertIsNotNone(b.inventory.inventories[0]._value['weapons'][0]['reload'])
        self.hit(b,count=3);inv = b.inventory.inventories[0]
        self.assertEqual(inv._weapon_work_rates[gun],0)
        self.assertIsNone(inv._value['weapons'][0]['reload'])
        self.assertEqual(sum(inv.summary()['reserved_ammunition'].values()),0)
        self.assertEqual(inv._value['weapons'][0]['ready_rounds'],0)

    def test_partial_engine_staffing_reduces_actual_acceleration(self):
        normal = self.battle(manual=True);short = self.battle(crew={'ordinary':8},manual=True)
        for b in (normal,short):
            for n in range(180):b.step(flight_tests.command() if n==0 else None)
        self.assertLess(short.session.world.ships[0].motion.velocity_world_mps.y,normal.session.world.ships[0].motion.velocity_world_mps.y)
        self.assertGreater(short.session.world.ships[0].motion.velocity_world_mps.y,0)

    def test_partial_damage_control_reduces_work_and_charges_only_actual_effect(self):
        normal = self.battle();short = self.battle(crew={'veteran_damage_control':1})
        helper = fires.TacticalFireTests()
        for b in (normal,short):helper.ignite(b,1000);helper.send(b,enabled=True);b.step()
        self.assertEqual(normal.fire.fires[0].intensity_units,899)
        self.assertEqual(short.fire.fires[0].intensity_units,949)
        self.assertEqual(normal.inventory.inventories[0]._value['damage_controls'][0]['quantity_units'],99900)
        self.assertEqual(short.inventory.inventories[0]._value['damage_controls'][0]['quantity_units'],99950)

    def test_automated_weapon_function_ignores_manual_shortage(self):
        b = self.battle(crew={'ordinary':0});kernel = b.session._resource_kernels[0];mid = b.guns[0].module_id
        module = kernel.modules[mid]
        kernel.modules[mid] = replace(module,prototype=replace(module.prototype,automation=replace(module.prototype.automation,
            automated_functions=tuple(set(module.prototype.automation.automated_functions)|{'weapon.fire'}))))
        self.assertEqual(kernel.crew_efficiency(b.session.world.ships[0].resources,mid,'weapon.fire'),1)
        self.assertEqual(kernel.crew_efficiency(b.session.world.ships[0].resources,mid,'weapon.reload'),0)

    def test_idle_steps_do_not_recompute_personnel_policy_or_staffing(self):
        b = self.battle()
        with patch.object(personnel,'policy',side_effect=AssertionError('reparse')):
            for _ in range(20):b.step()
        self.assertFalse(b.personnel.recent)
        self.assertIsNone(b.personnel.records[0])


if __name__=='__main__':unittest.main()
