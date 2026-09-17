"""5j.1 fixed lifetime migration, real VLS departure and speed presentation."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import unittest

from backend.high_wilderness_sidecar import missile_flight as flight
from backend.high_wilderness_sidecar.missile_flight_catalog import INTERFACE, normalize
from tools import test_missile_flight as combat

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'contracts/web_bridge/fixtures'


def read(stage):
    return json.loads((FIXTURES / f'tactical-missile-flight.{stage}.json').read_text(encoding='utf-8'))


class FixedCatalogTests(unittest.TestCase):
    def test_explicit_legacy_conversion_preserves_every_other_flight_property(self):
        for stage in ('5e', '5f', '5g'):
            with self.subTest(stage=stage):
                old = read(stage)
                before = deepcopy(old)
                converted = normalize(old)
                self.assertEqual(old, before)
                self.assertEqual(converted['interface'], INTERFACE)
                self.assertEqual(converted['damage'], old['damage'])
                for original, row in zip(old['models'], converted['models']):
                    expected = 600 if original.get('interceptor') else 2700 if '.turbojet.' in original['model_id'] else 1800
                    self.assertEqual(row['coast_steps'], expected)
                    self.assertEqual({k:v for k,v in row.items() if k not in ('coast_steps','minimum_climb_speed_mps','maximum_pitch_rad')},
                                     {k:v for k,v in original.items() if k != 'coast_steps'})

    def test_new_catalog_matches_approved_migration_and_accepts_per_model_tuning(self):
        published = read('5j')
        self.assertEqual(len(published['models']), 17)
        self.assertEqual(published['models'], normalize(read('5g'))['models'])
        tuned = deepcopy(published)
        tuned['models'][0]['coast_steps'] = 37*60
        self.assertEqual(normalize(tuned)['models'][0]['coast_steps'], 37*60)
        self.assertEqual(normalize(tuned)['models'][1], published['models'][1])

    def test_unknown_or_ambiguous_versions_and_durations_are_rejected(self):
        cases = []
        value = read('5g');value['interface'] = 'gaotian.missile-flight/unknown';cases.append(value)
        value = read('5g');value['models'][0]['model_id'] = 'unknown';cases.append(value)
        value = read('5g');value['models'][0]['coast_steps'] = [1200, 1800, 900];cases.append(value)
        for duration in ([1800, 1320, 900], True, -1, 0, 30.5):
            value = read('5j');value['models'][0]['coast_steps'] = duration;cases.append(value)
        value = read('5j');value['models'].append(deepcopy(value['models'][0]));cases.append(value)
        for value in cases:
            with self.subTest(value=value['models'][0]['coast_steps'], version=value['interface']):
                with self.assertRaises(ValueError):normalize(value)

    def test_phases_keep_fixed_deadline_and_cross_launch_only_changes_motion(self):
        for identity, profile in flight.profiles().items():
            with self.subTest(model=identity):
                seconds = 10 if profile.interceptor else 45 if '.turbojet.' in identity else 30
                self.assertEqual(profile.coast_steps, seconds*60)
                self.assertEqual(profile.lifetime(), profile.boost_steps+profile.engine_steps+seconds*60)
                self.assertEqual(profile.phase(profile.boost_steps-1), 'boost')
                self.assertEqual(profile.phase(profile.boost_steps), 'powered')
                self.assertEqual(profile.phase(profile.boost_steps+profile.engine_steps), 'coast')
                same, cross = profile.ballistics(1.), profile.ballistics(.7)
                self.assertEqual(same.lifetime_steps, cross.lifetime_steps)
                self.assertAlmostEqual(cross.muzzle_speed_mps, same.muzzle_speed_mps*.7)
                self.assertLessEqual(profile.range(.7), profile.range())


class FixedRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        combat.CombatTests.setUpClass()
        cls.fixture = combat.CombatTests()

    def test_real_vls_same_deadline_in_every_layer_without_spending_delay(self):
        for identity, seconds in (
            ('gtw.missile.5c.small.rocket.active_radar', 30),
            ('gtw.missile.5c.small.turbojet.infrared', 45),
            ('gtw.missile.5c.small.interceptor', 10),
        ):
            for layer in ('upper', 'cloud', 'rain'):
                with self.subTest(model=identity, layer=layer):
                    b = self.fixture.battle('vls', identity)
                    world = b.session.world
                    own = world.ships[0]
                    b.session._world = replace(world, ships=(replace(own, motion=replace(own.motion, height_layer='cloud')), *world.ships[1:]))
                    self.fixture.order(b, 'attack_layer', layer=layer)
                    self.fixture.order(b, 'point', point_m=self.fixture.point(b, 1000))
                    self.fixture.order(b, 'fire');b.step()
                    departure = b.missiles.pending[0]
                    p = departure.projectile
                    expected = p.missile.profile.boost_steps+p.missile.profile.engine_steps+seconds*60
                    self.assertEqual(p.expires, departure.due_step+expected)
                    self.assertEqual(p.missile.age, 0)
                    self.fixture.order(b, 'attack_layer', layer='cloud')
                    self.assertEqual(b.missiles.pending[0], departure)
                    while b.session.world.fixed_step < departure.due_step:b.step()
                    live = next(v for v in b.projectiles if v.id == p.id)
                    self.assertEqual(live.height_layer, layer)
                    self.assertEqual(live.missile.age, 0)
                    self.assertEqual(live.expires, p.expires)
                    view = next(v for v in b.view()['projectiles'] if v['id'] == p.id)['missile']
                    self.assertEqual(view['remaining_s'], expected/60)
                    self.assertEqual(view['speed_mps'], view['horizontal_speed_mps'])
                    self.assertEqual(view['vertical_speed_mps'], 0.)

    def test_speed_snapshot_separates_total_from_horizontal_without_changing_expiry(self):
        b = self.fixture.battle()
        source = b.session.world.ships[0].ship_id
        # A projection fixture, not a claim that vertical flight is already implemented.
        p = combat.missile(ship_id=source, velocity=(300., 0.))
        p = replace(p, missile=replace(p.missile, vertical_velocity_mps=-400.))
        b.projectiles = (p,)
        view = b.view()['projectiles'][0]['missile']
        self.assertEqual(view['speed_mps'], 500.)
        self.assertEqual(view['horizontal_speed_mps'], 300.)
        self.assertEqual(view['vertical_speed_mps'], -400.)
        self.assertEqual(b.projectiles[0].expires, p.expires)


if __name__ == '__main__':unittest.main()
