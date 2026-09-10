"""P2b authority tests: projectiles drive damage; no synthetic HP operations."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from math import hypot

from tools import test_tactical_gunnery as fixtures
GUN = fixtures.GUN
from backend.high_wilderness_sidecar import tactical_gunnery as tg
from backend.high_wilderness_sidecar.tactical_damage import segment
from backend.high_wilderness_sidecar.tactical_scheduler import TacticalScheduler
from tools.test_tactical_scheduler import Clock


class DamageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.GunneryTests.setUpClass()

    def battle(self, enemy=False):
        return tg.GunneryBattle(tg.prepare_trial_session(fixtures.GunneryTests.sample, fixtures.GunneryTests.config),
            fixtures.GunneryTests.scenario, fixtures.GunneryTests.config, damage_enabled=True, enemy_fire=enemy)

    def hp(self, b, module, ship=0):
        return b.session.world.ships[ship].devices.modules[b._indices[ship][module]].durability_points

    def shell(self, b, a, z, *, deck=0, target=0, id=None):
        """A high-speed swept shell in target-local space, emitted by other ship."""
        m = b.session.world.ships[target].motion
        start = tg.add(tuple(m.position_world_m.to_list()), tg.rotate(a, m.heading_rad))
        end = tg.add(tuple(m.position_world_m.to_list()), tg.rotate(z, m.heading_rad))
        velocity = ((end[0]-start[0])*60, (end[1]-start[1])*60)
        p = tg.Projectile(id or b._projectile_sequence+1, b.session.world.ships[1-target].ship_id,
            GUN, start, start, velocity, b.session.world.fixed_step+60, deck)
        b._projectile_sequence = max(b._projectile_sequence, p.id)
        b.projectiles += (p,)
        return p

    def test_grazing_and_miss_geometry(self):
        self.assertAlmostEqual(segment((0, 0), (10, 0), (3, 0), (7, 0)), .3)
        self.assertEqual(segment((0, 0), (10, 0), (10, 0), (10, 4)), 1)
        self.assertIsNone(segment((0, 0), (10, 0), (3, 1), (7, 1)))

    def test_sweep_engine_partial_then_destroyed_contribution(self):
        b = self.battle()
        original = b.session.world.ships[0].propulsion.available_units
        for n in range(3):
            self.shell(b, (-30, -50), (30, -50)); b.step()
            self.assertEqual(self.hp(b, 'main_engine_port'), max(0, 100-40*(n+1)))
            if n < 2:
                self.assertEqual(b.session.world.ships[0].propulsion.available_units, original)
        self.assertNotEqual(b.session.world.ships[0].propulsion.available_units, original)
        self.assertEqual(b.damage_state.hits, 3)

    def test_real_auto_fire_and_enemy_return_fire(self):
        b = self.battle(enemy=True); fixtures.GunneryTests().send(b)
        for _ in range(600):
            if b.ending: break
            b.step()
        self.assertTrue(all(s.shots > 0 for s in b.states))
        self.assertGreater(b.damage_state.hits, 0)
        self.assertTrue(all(s.motion.hull_integrity_fraction < 1 for s in b.session.world.ships))
        self.assertLess(self.hp(b, 'ammunition_magazine', 1), 100)

    def test_miss_expires_without_damage(self):
        b = self.battle(); self.shell(b, (500, 0), (600, 0))
        for _ in range(60): b.step()
        self.assertEqual(b.damage_state.expired, 1)
        self.assertEqual(b.damage_state.hits, 0)
        self.assertFalse(b.projectiles)

    def test_damage_projection_failure_rolls_back_every_domain(self):
        b = self.battle(); self.shell(b, (-30, -50), (30, -50))
        before = b.session.world, b.damage_state, b.projectiles, b.states, b.inventory.inventories
        def fail(*args): raise RuntimeError('projection rejected')
        with self.assertRaises(RuntimeError): b.step(project=fail)
        self.assertEqual(before, (b.session.world, b.damage_state, b.projectiles, b.states, b.inventory.inventories))
        b.step()
        self.assertEqual(self.hp(b, 'main_engine_port'), 60)

    def test_real_radar_hits_degrade_locked_fire_control(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(130): b.step()
        self.assertEqual(b.states[0].quality, 'normal')
        for _ in range(4):
            self.shell(b, (30, 10), (-30, 10), deck=1); b.step()
        self.assertEqual(self.hp(b, 'sensor_upper_starboard'), 0)
        self.assertEqual(b.states[0].quality, 'degraded')
        self.assertEqual(b.states[0].quality_reason, 'radar_unavailable')

    def test_one_shell_hits_once(self):
        b = self.battle()
        p = self.shell(b, (-30, -50), (30, -50))
        b.step(); first = b.damage_state.hits
        for _ in range(5): b.step()
        self.assertEqual(first, 1)
        self.assertEqual(b.damage_state.hits, first)
        self.assertFalse(any(v.id == p.id for v in b.projectiles))

    def test_nearest_present_ship_intercepts(self):
        b = self.battle()
        # Shot from an external fixture emitter crosses both hulls in one step.
        # Collision is not restricted to a selected target ship identifier.
        p = tg.Projectile(1, 'fixture.external', GUN, (0., -500.), (0., -500.), (0., 60000.), 20)
        _, state, batch = b.damage.advance(b.session.world, b.session.world, (p,), b.damage_state)
        self.assertEqual(state.hits, 1)
        self.assertEqual(state.recent[0]['ship_id'], 'ship.web.blue')
        self.assertEqual(batch.hull_damage[0][0], 'ship.web.blue')

    def test_attack_deck_and_height_layer_do_not_hit_other_layers(self):
        b = self.battle()
        p = self.shell(b, (-30, -50), (30, -50), deck=1)
        b.step()
        self.assertEqual(self.hp(b, 'main_engine_port'), 100)
        self.assertEqual(b.damage_state.hits, 0)
        b.projectiles = (replace(p, deck_level=0, height_layer='fixture.other'),)
        b.step()
        self.assertEqual(b.damage_state.hits, 0)

    def test_actual_cargo_hold_damage_updates_capacity_at_same_boundary(self):
        b = self.battle()
        inventory = b.inventory.inventories[0]
        cargo = inventory._value['cargo']
        for n in range(3):
            self.shell(b, (30, 10), (-30, 10)); b.step()
            self.assertEqual(b.inventory.inventories[0]._capacity(), 125000000 if n < 2 else 0)
            self.assertEqual(b.inventory.inventories[0]._value['cargo'], cargo)

    def test_live_weapon_hit_cancels_reload_and_firing(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(15): b.step()
        for _ in range(4):
            self.shell(b, (-30, -10), (30, -10), deck=1); b.step()
        self.assertEqual(self.hp(b, GUN), 0)
        self.assertFalse(b.inventory.inventories[0]._due)
        shots = b.states[0].shots
        for _ in range(150): b.step()
        self.assertEqual(b.states[0].shots, shots)
        self.assertEqual(b.states[0].status, 'destroyed')

    def test_armor_stops_with_local_wear_and_no_hull_damage(self):
        b = self.battle()
        # Kernel fixture only: actual design is unarmored. Thickness does not
        # silently change in the demo. Exercise the compiled armor branch.
        b.damage.edges[0] = tuple(replace(e, thickness_mm=100000, maximum=100) for e in b.damage.edges[0])
        b.damage_state = replace(b.damage_state, armor=(tuple(100. for _ in b.damage.edges[0]), b.damage_state.armor[1]))
        self.shell(b, (-30, -50), (30, -50)); b.step()
        hit = b.damage_state.recent[-1]
        self.assertEqual(hit['outcome'], 'stopped')
        self.assertLess(hit['armor_after'], hit['armor_before'])
        self.assertEqual(self.hp(b, 'main_engine_port'), 100)
        self.assertEqual(b.session.world.ships[0].motion.hull_integrity_fraction, 1)

    def test_ricochet_consumes_projectile_without_interior_damage(self):
        b = self.battle()
        b.damage.edges[0] = tuple(replace(e, thickness_mm=100000, maximum=100) for e in b.damage.edges[0])
        b.damage_state = replace(b.damage_state, armor=(tuple(100. for _ in b.damage.edges[0]), b.damage_state.armor[1]))
        self.shell(b, (-10.5, -10), (-9.5, 10)); b.step()
        self.assertEqual(b.damage_state.recent[-1]['outcome'], 'ricochet')
        self.assertEqual(self.hp(b, 'cic'), 100)
        self.assertFalse(b.projectiles)

    def test_local_armor_wear_rolls_back_on_publication_failure(self):
        b = self.battle()
        b.damage.edges[0] = tuple(replace(e, thickness_mm=100000, maximum=100) for e in b.damage.edges[0])
        b.damage_state = replace(b.damage_state, armor=(tuple(100. for _ in b.damage.edges[0]), b.damage_state.armor[1]))
        self.shell(b, (-30, -50), (30, -50))
        state = b.damage_state
        def fail(*args): raise RuntimeError('failed publication')
        with self.assertRaises(RuntimeError): b.step(project=fail)
        self.assertIs(b.damage_state, state)
        b.step()
        self.assertNotEqual(b.damage_state.armor, state.armor)

    def test_weapon_destroyed_at_reload_deadline_does_not_consume_reservation(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(15): b.step()
        deadline = b.inventory.inventories[0]._due[GUN]
        while b.session.world.fixed_step < deadline-1: b.step()
        ammo = b.inventory.inventories[0]._value['magazines'][0]['quantity']
        for _ in range(4): self.shell(b, (-30, -10), (30, -10), deck=1)
        b.step()
        self.assertEqual(self.hp(b, GUN), 0)
        inv = b.inventory.inventories[0]
        self.assertEqual(inv._value['magazines'][0]['quantity'], ammo)
        self.assertFalse(inv._due)

    def test_no_static_compilation_or_fingerprint_inside_steps(self):
        b = self.battle(); self.shell(b, (-30, -50), (30, -50))
        with (patch('backend.high_wilderness_sidecar.tactical_damage.compile_projectile_target_geometry', side_effect=AssertionError('compile')),
             patch('backend.high_wilderness_sidecar.tactical_gunnery.canonical_sha256', side_effect=AssertionError('hash'))):
            for _ in range(10): b.step()
        self.assertEqual(b.damage_state.hits, 1)

    def test_cic_destruction_ends_and_freezes(self):
        b = self.battle()
        for _ in range(3):
            self.shell(b, (-30, 0), (30, 0), target=1); b.step()
        self.assertEqual(self.hp(b, 'cic', 1), 0)
        self.assertEqual(b.ending['reason'], 'victory')
        self.assertFalse(b.projectiles)
        with self.assertRaises(Exception): b.step()
        self.assertFalse(b.withdraw())

    def test_withdraw_settles_current_reload_once_no_new_batch(self):
        b = self.battle(); fixtures.GunneryTests().send(b)
        for _ in range(15): b.step()
        inv = b.inventory.inventories[0]
        self.assertTrue(inv._due)
        b.withdraw()
        value = b.inventory.inventories[0]._value
        self.assertEqual(value['magazines'][0]['quantity'], 75)
        self.assertEqual(value['weapons'][0]['ready_rounds'], 1)
        self.assertFalse(b.withdraw())
        self.assertEqual(value, b.inventory.inventories[0]._value)

    def test_scheduler_stops_on_finishing_step_without_debt_catchup(self):
        b = self.battle()
        for n in range(3): self.shell(b, (-30, 0), (30, 0), target=1, id=n+1)
        clock = Clock(); q = TacticalScheduler(b.session, clock=clock, stepper=b.step, stop_when=lambda: b.ending is not None)
        q.resume(); clock.advance(4*1_000_000_000//60)
        self.assertEqual(q.pump(), 1)
        self.assertEqual(q.status.pause_reason, 'battle_finished')
        self.assertEqual(q.recover_debt(), 0)


if __name__ == '__main__': unittest.main()
