"""Display history stays separate from committed flight and persistence."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical_presentation import FlightHistory, MAX_FINISHED_FLIGHTS, HISTORY_STEPS
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical import render_static
from tools import test_tactical_gunnery as fixtures
from tools import test_tactical_damage as damage_fixtures
from tools.test_tactical_scheduler import Clock


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.GunneryTests.setUpClass()

    def test_actual_shot_born_and_expired_between_publications_is_retained(self):
        clock = Clock()
        with TemporaryDirectory() as folder:
            service = RealtimeViewService('backend.visual', clock=clock, settlement_dir=Path(folder))
            battle = fixtures.GunneryTests().battle(projectile_lifetime_steps=2)
            fixtures.GunneryTests().send(battle, 'clear')
            service._attach(battle, render_static(fixtures.GunneryTests.scenario))
            service.scheduler.resume()
            for _ in range(30):
                service.last_read = clock(); clock.advance(70_000_000); service.tick()
            fixtures.GunneryTests().send(battle, 'mode', mode='manual')
            fixtures.GunneryTests().send(battle, 'fire', point=[-5, 300])
            start = service.latest['fixed_step']
            service.last_read = clock(); clock.advance(70_000_000); service.tick()
            view = service.read()['view']
            self.assertEqual(view['gunnery']['weapons'][0]['shots'], 1)
            self.assertEqual(view['gunnery']['projectiles'], [])
            trail = view['presentation']['finished_projectiles'][0]
            self.assertGreater(trail['born_step'], start)
            self.assertEqual(trail['end_step']-trail['born_step'], 2)
            self.assertIsNone(trail['impact'])
            self.assertEqual(view, service.read()['view'])
            self.assertEqual(service.scheduler.world.fixed_step, view['fixed_step'])

    def test_real_swept_impact_uses_committed_endpoint_and_failed_step_adds_nothing(self):
        factory = damage_fixtures.DamageTests()
        battle = factory.battle()
        projectile = factory.shell(battle, (-30, -50), (30, -50))
        with TemporaryDirectory() as folder:
            service = RealtimeViewService('backend.hit', settlement_dir=Path(folder))
            service._attach(battle, render_static(fixtures.GunneryTests.scenario))
            before = service.presentation.view()
            with patch.object(battle, 'step', side_effect=RuntimeError('not committed')):
                with self.assertRaises(RuntimeError): service.scheduler._stepper()
            self.assertEqual(service.presentation.view(), before)
            service.scheduler._stepper()
            hit = battle.damage_state.recent[0]
            trail = service.presentation.view()['finished_projectiles'][0]
            self.assertEqual(trail['id'], projectile.id)
            self.assertEqual(trail['origin_m'], projectile.position)
            self.assertEqual(trail['end_m'], hit['position_m'])
            self.assertEqual(trail['end_step'], hit['step'])
            self.assertEqual(trail['impact']['outcome'], hit['outcome'])
            self.assertEqual(battle.damage_state.hits, 1)

    def test_live_history_tracks_launch_once_and_withdraw_has_no_fake_impact(self):
        history = FlightHistory()
        p = Projectile(1, 'ship.blue', 'weapon', (1, 2), (1, 2), (5000, 0), 100)
        history.record(5, [p]); history.record(6, [replace(p, position=(84, 2))])
        self.assertEqual(history.launch(1), dict(born_step=5, origin_m=(1, 2), expires_step=100))
        history.record(6, [])
        self.assertIsNone(history.view()['finished_projectiles'][0]['impact'])
        self.assertEqual(history.view()['finished_projectiles'][0]['end_m'], (84, 2))

    def test_projectile_history_keeps_its_launch_layer_after_flight_ends(self):
        history = FlightHistory()
        p = Projectile(1, 'ship.blue', 'weapon', (1, 2), (1, 2), (5000, 0), 100, height_layer='cloud')
        history.record(5, [p]); history.record(6, [])
        self.assertEqual(history.view()['finished_projectiles'][0]['height_layer'], 'cloud')

    def test_history_is_bounded_reports_overflow_and_expires_by_simulation_time(self):
        history = FlightHistory()
        shots = [Projectile(n, 'ship', 'gun', (0, 0), (0, 0), (5000, 0), 120) for n in range(MAX_FINISHED_FLIGHTS+4)]
        history.record(0, shots); history.record(1, [])
        view = history.view()
        self.assertEqual(len(view['finished_projectiles']), MAX_FINISHED_FLIGHTS)
        self.assertEqual(view['dropped_projectiles'], 4)
        history.record(1+HISTORY_STEPS, [])
        self.assertEqual(len(history.finished), MAX_FINISHED_FLIGHTS)
        history.record(2+HISTORY_STEPS, [])
        self.assertFalse(history.finished)


if __name__ == '__main__': unittest.main()
