"""3c real surface ignition, local spreading, single-target suppression and save."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from tools import test_tactical_ignition as ignition_tests, test_tactical_fire as fire_tests
from tools.test_tactical_fire import TARGET
from backend.high_wilderness_sidecar import tactical_ignition as ignition, tactical_ammunition as ammo
from backend.high_wilderness_sidecar import tactical_spatial_fire as spatial, tactical_settlement as settlement
from backend.high_wilderness_sidecar import battle_preparation as preparation, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_fire import Fire


class SpatialFireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ignition_tests.IgnitionTests.setUpClass(); cls.f = ignition_tests.IgnitionTests()
        cls.design = cls.f.compile(cls.f.doc, load_current(Path(__file__).resolve().parents[1]))

    def battle(self, design=None, record=None):
        b = self.f.battle(design or self.design, record)
        b.states = tuple(replace(s,target_policy='hold',target=None) for s in b.states)
        return b

    def fire(self, b, zone=None, *, units=1000, steps=600, spread=120, random_state=123):
        z = zone or next(z for z in b.fire.zones[0].values() if z.surface and not z.modules and z.neighbors)
        f = Fire(0,z.modules[0] if z.modules else None,units,steps,z.id,spread,random_state)
        b.fire.fires = (f,)
        b.fire._write_fires(b.inventory.inventories,b.fire.fires)
        return f

    def shot(self, b, *, legacy=False):
        self.f.shot(b)
        b.projectiles = (replace(b.projectiles[-1],projectile_key=ignition.PROJECTILE if legacy else ammo.SURFACE_INCENDIARY),)

    def armored(self,b):
        # The legal design fixture has bare edges; supply explicit armor for
        # the armor-isolation tests without altering the saved design catalogue.
        b.damage.edges[0]=tuple(replace(e,maximum=100.,thickness_mm=100000) for e in b.damage.edges[0])
        b.damage_state=replace(b.damage_state,armor=(tuple(100. for _ in b.damage.edges[0]),b.damage_state.armor[1]))

    def test_new_ammunition_has_low_penetration_and_historical_design_is_unchanged(self):
        b = self.battle()
        self.assertAlmostEqual(b.damage.profiles[ammo.SURFACE_INCENDIARY].penetration.reference_penetration_mm,
            b.damage.profiles[ammo.ORDINARY].penetration.reference_penetration_mm*.02)
        self.assertEqual(b.damage.profiles[ignition.PROJECTILE].penetration.reference_penetration_mm,
            b.damage.profiles[ammo.ORDINARY].penetration.reference_penetration_mm)
        self.assertEqual(preparation.restore_design(self.f.design.archive(),self.f.f.index).archive(),self.f.design.archive())
        self.assertEqual(b.view()['weapons'][0]['incendiary_effect'],'surface')

    def test_stopped_real_shell_ignites_surface_without_internal_or_hull_damage(self):
        b = self.battle()
        self.armored(b)
        hull = b.session.world.ships[0].motion.hull_integrity_fraction
        self.shot(b)
        with patch.object(ignition,'sample',return_value=0.): b.step()
        self.assertNotEqual(b.damage_state.recent[-1]['outcome'],'penetrated')
        self.assertTrue(b.fire.fires)
        self.assertTrue(all(b.fire.zones[0][f.zone_id].surface for f in b.fire.fires))
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,hull)

    def test_prepared_gun_consumes_and_fires_new_ammunition_into_spatial_ignition(self):
        b=self.battle();enemy=b.session.world.ships[1].ship_id
        b.submit(dict(epoch=b.session.world.epoch,generation=0,sequence=1,weapon_id=b.guns[0].module_id,
            kind='target',arguments=dict(ship_id=enemy,module_id='ammunition_magazine')))
        for _ in range(1000):
            if b.ending or any(f.ship_index==1 and f.zone_id for f in b.fire.fires):break
            b.step()
        self.assertGreater(b.states[0].shots,0)
        self.assertTrue(any(f.ship_index==1 and f.zone_id for f in b.fire.fires))
        self.assertTrue(any(e['source_ship_id']==b.session.world.ships[0].ship_id and e['projectile_version']==2
            for e in b.damage_state.recent))
        self.assertEqual(sum(-e['delta'] for e in b.inventory.inventories[0].changes() if e['reason']=='discharge'),b.states[0].shots)

    def test_penetrating_shell_can_ignite_real_internal_regions_on_hit_deck(self):
        b = self.battle(); self.shot(b)
        with patch.object(ignition,'sample',return_value=0.): b.step()
        self.assertEqual(b.damage_state.recent[-1]['outcome'],'penetrated')
        zones = [b.fire.zones[0][f.zone_id] for f in b.fire.fires]
        self.assertTrue(any(not z.surface for z in zones)); self.assertTrue(all(z.level==0 for z in zones))

    def test_surface_fire_burns_only_its_local_armor_and_no_internal_hull(self):
        b = self.battle(); self.armored(b); f = self.fire(b); z = b.fire.zones[0][f.zone_id]
        armor=b.damage_state.armor; world=b.session.world
        b.step()
        self.assertEqual(b.session.world.ships[0].devices,world.ships[0].devices)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction,world.ships[0].motion.hull_integrity_fraction)
        changed={e.key for e,a,v in zip(b.damage.edges[0],armor[0],b.damage_state.armor[0]) if a!=v}
        self.assertTrue(changed);self.assertLessEqual(changed,set(z.armor))
        self.assertEqual(armor[1],b.damage_state.armor[1])

    def test_internal_fire_damages_occupied_module_and_not_other_decks(self):
        b = self.battle(); z = next(z for z in b.fire.zones[0].values() if not z.surface and z.modules)
        self.fire(b,z); before=b.session.world.ships[0].devices
        b.step();after=b.session.world.ships[0].devices
        changed={m.instance_id for m,a,v in zip(b.session._seeds[0].devices.modules,before.modules,after.modules) if a.durability_points!=v.durability_points}
        self.assertEqual(changed,set(z.modules))

    def test_all_adjacency_is_same_deck_same_domain_and_no_self_links(self):
        b=self.battle()
        for zones in b.fire.zones:
            for z in zones.values():
                for key in z.neighbors:
                    other=zones[key]
                    self.assertEqual((z.level,z.surface),(other.level,other.surface))
                    self.assertNotEqual(z.id,key);self.assertIn(z.id,other.neighbors)

    def test_spread_interval_newborn_delay_and_finite_children(self):
        b=self.battle();f=self.fire(b,spread=1)
        z=b.fire.zones[0][f.zone_id]
        with patch.object(spatial,'random_step',return_value=(321,0.)): b.step()
        children=[f for f in b.fire.fires if f.zone_id!=z.id]
        self.assertTrue(children);self.assertLessEqual({f.zone_id for f in children},set(z.neighbors))
        self.assertTrue(all(f.spread_steps==120 and f.remaining_steps==599 and f.intensity_units==499 for f in children))
        before={f.zone_id for f in b.fire.fires};b.step()
        self.assertEqual({f.zone_id for f in b.fire.fires},before)

    def test_expiring_fire_does_not_spread_or_create_negative_durability(self):
        b=self.battle();self.fire(b,steps=1,spread=1)
        with patch.object(spatial,'random_step',side_effect=AssertionError('burned-out fire spread')): b.step()
        self.assertFalse(b.fire.fires)
        self.assertTrue(all(v>=0 for values in b.damage_state.armor for v in values))

    def test_fireproofing_reduces_new_surface_ignition_and_spread(self):
        normal=self.battle();protected=self.battle(self.f.protected)
        for b in (normal,protected):
            self.shot(b)
            with patch.object(ignition,'sample',return_value=.4): b.step()
        self.assertTrue(normal.fire.fires);self.assertFalse(protected.fire.fires)
        # At intensity 0.999: unprotected probability ~0.25, protected ~0.125.
        for b in (normal,protected):
            self.fire(b,spread=1)
            with patch.object(spatial,'random_step',return_value=(321,.2)):b.step()
        self.assertGreater(len(normal.fire.fires),1);self.assertEqual(len(protected.fire.fires),1)

    def test_one_device_does_not_extinguish_two_fires_in_one_step(self):
        b=self.battle();factory=fire_tests.TacticalFireTests()
        factory.ignite(b,50,target=TARGET);factory.ignite(b,50,target='generator');factory.send(b,enabled=True)
        before=factory.quantity(b);b.step()
        self.assertEqual(len(b.fire.fires),1);self.assertEqual(factory.quantity(b),before-49)
        b.step();self.assertFalse(b.fire.fires)

    def test_device_keeps_current_fire_when_stronger_fire_appears(self):
        b=self.battle();factory=fire_tests.TacticalFireTests()
        factory.ignite(b,1000,target=TARGET);factory.send(b,enabled=True);b.step()
        factory.ignite(b,5000,target='generator');b.step()
        self.assertEqual(b.fire.controllers[0].fire_target,TARGET)
        self.assertEqual(next(f for f in b.fire.fires if f.module_id=='generator').intensity_units,4999)

    def test_failed_step_rolls_back_spread_armor_inventory_and_retry(self):
        b=self.battle();self.fire(b,spread=1)
        before=(b.session.world,b.damage_state,b.fire.fires,b.fire.controllers,b.inventory.inventories)
        with patch.object(spatial,'random_step',return_value=(321,0.)):
            with self.assertRaises(RuntimeError):b.step(project=lambda *a:(_ for _ in ()).throw(RuntimeError('failed view')))
            self.assertEqual((b.session.world,b.damage_state,b.fire.fires,b.fire.controllers,b.inventory.inventories),before)
            b.step()
        self.assertGreater(len(b.fire.fires),1)

    def test_save_restart_reentry_preserves_regions_spread_timer_and_rng(self):
        b=self.battle();self.fire(b,spread=1)
        with patch.object(spatial,'random_step',return_value=(321,0.)):b.step()
        fires=b.fire.fires;b.withdraw();result=settlement.capture(b)
        with TemporaryDirectory() as folder:
            store=settlement.SettlementStore(folder);store.stage(result);store.save(result['settlement_id'])
            record=settlement.SettlementStore(folder).load_ship('instance.ignition.player',1)
            again=self.battle(record=record)
            self.assertEqual(again.fire.fires,fires);self.assertEqual(again.damage_state.armor,b.damage_state.armor)
            again.step();self.assertTrue(all(f.spread_steps==119 for f in again.fire.fires))
            self.assertFalse(again.fire.controllers[0].enabled)

    def test_invalid_region_duplicate_timer_and_binding_are_rejected(self):
        b=self.battle();self.fire(b);b.withdraw();record=settlement.capture(b)['ships'][0]['after']
        for mutate in (lambda r:r['state']['fires'][0].update(zone_id='fire.unknown'),
                       lambda r:r['state']['fires'][0].update(spread_steps=121),
                       lambda r:r['state']['fires'][0].update(module_id=TARGET),
                       lambda r:r['state']['fires'].append(ps.clone(r['state']['fires'][0]))):
            bad=ps.clone(record);mutate(bad)
            with self.assertRaises(ps.ContractError):preparation.validate_record(bad,self.design)

    def test_read_and_pause_do_not_change_fire_countdowns_or_random_state(self):
        b=self.battle();self.fire(b,spread=1);before=b.fire.fires
        b.suspend()
        for _ in range(10):b.view()
        self.assertEqual(b.fire.fires,before)


if __name__=='__main__':unittest.main()
