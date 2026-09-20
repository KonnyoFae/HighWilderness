"""F1/F2 accuracy, within-step reuse and transactional invalidation."""
from dataclasses import replace
from math import hypot
from pathlib import Path
from random import Random
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_ballistics as flight
from backend.high_wilderness_sidecar import tactical_gunnery as tg
from backend.high_wilderness_sidecar import tactical_targeting as targeting
from backend.high_wilderness_sidecar.preparation_policy import load_current
from tools.fire_control_reference import time_to_distance as reference, UncachedSolutions
from tools.fire_control_f2_reference import Runtime as F2Runtime
from tools import test_tactical_targeting as fixtures


def profiles():
    policy = load_current(Path(__file__).resolve().parents[1])
    return tuple(flight.compile_profile(p, 1200) for p in policy['projectiles'] if p.get('ballistics'))


def physical_distance(profile, speed, seconds):
    distance = 0.
    while seconds > 1e-10:
        dt = min(1/60, seconds)
        dx, speed = flight.scalar(speed, dt, flight.step_coefficient(profile, speed, dt))
        distance += dx; seconds -= dt
    return distance


class InverseTests(unittest.TestCase):
    def test_all_profiles_cross_layer_transonic_and_high_speed_against_actual_flight(self):
        for profile in profiles():
            for speed in (1., 50., profile.muzzle_speed_mps*.7, profile.muzzle_speed_mps,
                          256., 304., 320., 336., 384., 480., 640., 960., 1600., 2000., 5000.):
                for fraction in (.001, .15, .6, .995):
                    seconds = profile.lifetime_steps/60*fraction
                    distance = physical_distance(profile, speed, seconds)
                    actual = flight.time_to_distance(profile, speed, distance)
                    with self.subTest(profile=profile, speed=speed, seconds=seconds):
                        self.assertIsNotNone(actual)
                        # At most 5 cm along the path, including Mach-curve corners.
                        self.assertLess(abs(physical_distance(profile, speed, actual)-distance), .05)
                        old = reference(profile, speed, distance)
                        self.assertIsNotNone(old)
                        self.assertLess(abs(actual-old), .003)

    def test_expiry_and_degenerate_inputs(self):
        for profile in profiles():
            speed = profile.muzzle_speed_mps
            # The continuous predictor can differ from midpoint flight by a few
            # millimetres; exercise comfortably on both sides of the deadline.
            limit = profile.lifetime_steps/60
            self.assertIsNotNone(flight.time_to_distance(profile, speed, physical_distance(profile,speed,limit-.01)))
            self.assertIsNone(flight.time_to_distance(profile, speed, physical_distance(profile,speed,limit+.01)))
            self.assertIsNone(flight.time_to_distance(profile, speed, 1e12))
            self.assertIsNone(flight.time_to_distance(profile, 0, 1))
            self.assertEqual(flight.time_to_distance(profile, 0, 0), 0)
        linear = replace(profiles()[0], drag=False, lifetime_steps=60)
        self.assertEqual(flight.time_to_distance(linear, 5000, 5000), 1)
        self.assertIsNone(flight.time_to_distance(linear, 5000, 5000.01))

    def test_moving_and_rotating_muzzle_solutions_against_physical_path(self):
        rng = Random(53); solved = 0
        for profile in profiles():
            for ratio in (.7, 1.):
                for _ in range(15):
                    origin = (rng.uniform(-50,50),rng.uniform(-50,50))
                    inherited = (rng.uniform(-150,150),rng.uniform(-150,150))
                    target = (rng.uniform(-3000,3000),rng.uniform(100,15000))
                    velocity = (rng.uniform(-90,90),rng.uniform(-90,90))
                    result = flight.intercept(origin,inherited,target,velocity,profile,ratio)
                    if result is None: continue
                    aim, seconds = result; solved += 1
                    delta = tuple(a-o for a,o in zip(aim,origin)); length = hypot(*delta)
                    v = tuple(i+d/length*profile.muzzle_speed_mps*ratio for i,d in zip(inherited,delta))
                    speed = hypot(*v); distance = physical_distance(profile,speed,seconds)
                    miss = hypot(*(o+x/speed*distance-p-u*seconds for o,x,p,u in zip(origin,v,target,velocity)))
                    self.assertLess(miss,.08)
        self.assertGreater(solved,100)


class ReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TargetingTests.setUpClass()
        cls.fixture = fixtures.TargetingTests()

    def test_cache_requires_identical_observation_geometry_profile_and_selection(self):
        b = self.fixture.battle(); state = replace(b.states[0],target=(1,None))
        contact = tg.Contact(1,(0,1000),(10,0),0,0,'normal',0)
        profile = b._gun_flights[0][state.reload_recipe_id]
        cache = targeting.StepSolutions(2)
        args = [b,state,contact,2,(0,0),(0,0),profile,1.]
        with patch.object(targeting,'solution',wraps=targeting.solution) as solve:
            first = cache.solve(*args); self.assertEqual(cache.solve(*args),first)
            self.assertEqual(solve.call_count,1)
            for at, value in [(1,replace(state,target=(1,'cic'))),
                              (2,replace(contact,velocity=(15,0))),
                              (2,replace(contact,step=2)),
                              (2,replace(contact,quality='degraded')),
                              (4,(1,0)),(5,(0,1)),(6,replace(profile,mass_kg=profile.mass_kg*2)),(7,.7)]:
                changed = list(args); changed[at] = value; cache.solve(*changed)
            self.assertEqual(solve.call_count,9)
        with self.assertRaises(ValueError):cache.solve(*args[:3],3,*args[4:])
        impossible = list(args); impossible[2] = replace(contact,position=(1e7,1e7))
        self.assertIsNone(cache.solve(*impossible)); self.assertIsNone(cache.solve(*impossible))
        self.assertEqual(cache.metrics()['negative_cache_hits'],1)

    def test_cache_only_preserves_targets_shots_inventory_and_aim_exactly(self):
        cached, uncached = self.fixture.battle(), self.fixture.battle()
        cached.fire_control=F2Runtime();uncached.fire_control=F2Runtime()
        hits = 0
        for step in range(110):
            if step == 40:
                for b in (cached,uncached):
                    self.fixture.send(b,'target',ship_id='ship.web.red',module_id='cic')
            if step == 70:
                for b in (cached,uncached):self.fixture.send(b,'auto_target')
            cached.step(); hits += cached.fire_control_metrics['cache_hits']
            with patch.object(targeting,'StepSolutions',UncachedSolutions):uncached.step()
            self.assertEqual(cached.states,uncached.states)
            self.assertEqual(cached.projectiles,uncached.projectiles)
            self.assertEqual([v._value for v in cached.inventory.inventories],
                             [v._value for v in uncached.inventory.inventories])
        self.assertGreater(hits,100)

    def test_failed_step_discards_solutions_and_metrics_and_retry_recomputes(self):
        b = self.fixture.battle(); before = dict(b.fire_control_metrics)
        with patch.object(targeting,'solution',wraps=targeting.solution) as solve:
            def fail(*args):raise RuntimeError('projection failed')
            with self.assertRaises(RuntimeError):b.step(project=fail)
            count = solve.call_count; self.assertGreater(count,0)
            self.assertEqual(b.fire_control_metrics,before)
            b.step(); self.assertGreater(solve.call_count,count)
            self.assertEqual(b.fire_control_metrics['step'],b.session.world.fixed_step)


if __name__ == '__main__':unittest.main()
