"""7c exact geometry/occlusion equivalence and expanded authority guardrails."""
from copy import deepcopy
from dataclasses import replace
from math import cos, sin, pi, nextafter, inf
from random import Random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import missile_guidance as mg
from tools import tactical_7c_reference as reference
from tools.tactical_7c_oracle import snapshot, difference, plain, digests
from tools import test_aviation_flight as fixture


class OcclusionTests(unittest.TestCase):
    def test_known_segment_boundaries(self):
        for a, b, center, radius, expected in (
            ((0., 0.), (10., 0.), (5., 1.), 1., True),
            ((0., 0.), (10., 0.), (5., nextafter(1., inf)), 1., False),
            ((0., 0.), (10., 0.), (-1., 0.), 1., True),
            ((0., 0.), (0., 0.), (0., 0.), 0., True),
            ((0., 0.), (0., 0.), (1., 0.), 0., False),
            ((1e6, -1e6), (1e6, -1e6), (1e6+3., -1e6+4.), 5., True),
        ):
            with self.subTest(a=a, b=b, center=center):
                self.assertEqual(mg.segment_circle(a, b, center, radius), expected)

    def test_random_and_near_tangent_match_frozen_algorithm(self):
        rng = Random(7101)
        for n in range(20000):
            scale = rng.choice((.001, 1., 1e3, 1e6, 1e12))
            a, b, center = [tuple(rng.uniform(-scale, scale) for _ in range(2)) for _ in range(3)]
            if n % 7 == 0: b = a
            radius = rng.random()*scale
            self.assertEqual(mg.segment_circle(a, b, center, radius), reference.segment_circle(a, b, center, radius), (a, b, center, radius))
        for n in range(5000):
            a = tuple(rng.uniform(-1e6, 1e6) for _ in range(2)); theta = rng.random()*2*pi
            length = rng.uniform(.001, 1e5); radius = rng.uniform(0., 1e4); fraction = rng.choice((0., 1., rng.random()))
            b = a[0]+length*cos(theta), a[1]+length*sin(theta)
            center = a[0]+fraction*length*cos(theta)-radius*sin(theta), a[1]+fraction*length*sin(theta)+radius*cos(theta)
            for r in (nextafter(radius, 0.), radius, nextafter(radius, inf)):
                self.assertEqual(mg.segment_circle(a, b, center, r), reference.segment_circle(a, b, center, r), (a, b, center, r))

    def test_area_combinations_match_all_channel_and_emitter_rules(self):
        rng = Random(7102); layers = ('upper', 'cloud', 'rain')
        for _ in range(3000):
            areas = tuple(SimpleNamespace(kind=rng.choice(('chaff', 'thermal', 'electronic', 'decoy')), layer=rng.choice(layers),
                side=rng.choice(('red', 'blue')), position=(rng.uniform(-10, 10), rng.uniform(-10, 10)), radius=rng.uniform(0, 20)) for _ in range(rng.randrange(24)))
            args = (areas, (-10., 0.), rng.choice(layers), (10., 0.), rng.choice(layers))
            kw = dict(observer_side=rng.choice((None, 'red', 'blue')), seeker=rng.choice((None, 'radar', 'infrared', 'composite', 'anti_radiation')), target_jamming=rng.choice((True, False)))
            expected = reference.blocked_channels(*args, **kw)
            self.assertEqual(mg.blocked_channels(*args, **kw), expected)
            self.assertIsInstance(mg.blocked_channels(*args, **kw), frozenset)

    def test_irrelevant_regions_never_need_geometry_and_channels_stop_when_known(self):
        def area(kind, **changes):
            return SimpleNamespace(**dict(dict(kind=kind, layer='upper', side='red', position=(0., 0.), radius=10.), **changes))
        irrelevant = (area('electronic', side='blue'), area('chaff', layer='rain'), area('decoy'))
        with patch.object(mg, 'segment_circle', wraps=mg.segment_circle) as calls:
            self.assertFalse(mg.blocked_channels(irrelevant, (-1., 0.), 'upper', (1., 0.), 'cloud', observer_side='blue'))
            self.assertEqual(calls.call_count, 0)
            self.assertFalse(mg.blocked_channels((area('electronic'),), (-1., 0.), 'upper', (1., 0.), 'upper', observer_side='blue', seeker='anti_radiation', target_jamming=True))
            self.assertEqual(calls.call_count, 0)
            physical = (area('chaff'), area('chaff'), area('thermal'), area('thermal'))
            self.assertEqual(mg.blocked_channels(physical, (-1., 0.), 'upper', (1., 0.), 'upper', observer_side='blue'), {'chaff', 'thermal'})
            self.assertEqual(calls.call_count, 2)


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.FlightTests.setUpClass(); cls.fixtures = fixture.FlightTests()

    def test_snapshot_detached_repeatable_and_covers_previously_missing_fields(self):
        b, key = self.fixtures.battle('e1'); self.fixtures.command(b, 'launch', [key]); b.step()
        before = snapshot(b); self.assertEqual(before, snapshot(b))
        after = deepcopy(before); after['aviation']['flights'][key]['return_requested'] = True
        self.assertIn('return_requested', difference(before, after)['path'])
        self.assertNotEqual(digests(before)['aviation'], digests(after)['aviation'])
        b.aviation.flights[key]['return_requested'] = True
        self.assertFalse(before['aviation']['flights'][key]['return_requested'])
        self.assertIn('return_requested', difference(before, snapshot(b))['path'])
        self.assertIn('locks', before['observation']); self.assertIn('threats', before['defense'])
        self.assertIn('effects', before['ew']); self.assertIn('_ledger', before['inventory'][0])

    def test_failed_step_preserves_expanded_state_and_derived_caches(self):
        b, key = self.fixtures.battle('e1'); self.fixtures.command(b, 'launch', [key]); b.step()
        before = snapshot(b)
        caches = plain((b._availability_key, b._available, b.point_defense.forecasts, b.fire_control.work))
        def fail(*args): raise RuntimeError('7c reject candidate')
        with self.assertRaisesRegex(RuntimeError, '7c reject'): b.step(project=fail)
        self.assertIsNone(difference(before, snapshot(b)))
        self.assertEqual(caches, plain((b._availability_key, b._available, b.point_defense.forecasts, b.fire_control.work)))
        b.step(); self.assertNotEqual(before['world']['step'], snapshot(b)['world']['step'])

    def test_epoch_normalization_keeps_persistent_id_and_type_distinctions(self):
        self.assertEqual(plain({'epoch': 'ep1', 'id': 'aircraft.1'}, {'ep1': '<epoch>'}), {'epoch': '<epoch>', 'id': 'aircraft.1'})
        self.assertNotEqual(plain({(1, 'a'): 2}), plain({'(1, a)': 2}))
        self.assertNotEqual(plain((1, 2)), plain([1, 2]))
        self.assertEqual(difference({'x': [1]}, {'x': [2]})['path'], '$.x[0]')


if __name__ == '__main__': unittest.main()
