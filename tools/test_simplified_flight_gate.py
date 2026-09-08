from dataclasses import replace
from math import cos, sin, pi
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import verify_simplified_flight_gate as gate
from tools import test_simplified_propulsion as fixtures
from tools import test_simplified_flight as flight_tests
from backend.high_wilderness_sidecar import simplified_flight as flight


class FlightGateTests(unittest.TestCase):
    def test_percentiles_and_worst_tail_are_not_averaged_away(self):
        result = gate.summary(list(range(1, 101)), 1)
        self.assertEqual(result["p95_ms"], 95)
        self.assertEqual(result["p99_ms"], 99)
        self.assertFalse(result["passed"])
        samples = [1.] * 3600
        self.assertTrue(gate.summary(samples, 4)["passed"])
        samples[-40:] = [20.] * 40
        self.assertFalse(gate.summary(samples, 4)["passed"])
        self.assertFalse(gate.summary([1.] * 3600, 61)["passed"])
        for bad in ([], [float("nan")], [-1], [float("inf")]):
            with self.assertRaises(ValueError):
                gate.summary(bad, 1)

    def test_frozen_workloads_have_required_window_and_bounded_concentrated_events(self):
        case = gate.make_case("damage_bursts")
        self.assertEqual((case["warmup_steps"], case["measured_steps"]), (600, 3600))
        frames = {}
        versions = {}
        for event in case["availability"]:
            self.assertTrue(600 <= event["step"] < 4200)
            key = (event["ship_id"], event["engine_id"], event["reason"])
            old = versions.get(key, (0, False))
            self.assertGreaterEqual(event["version"], old[0])
            if event["version"] == old[0]:
                self.assertEqual(event["active"], old[1])
            versions[key] = (event["version"], event["active"])
            frames[event["step"]] = frames.get(event["step"], 0) + 1
        self.assertEqual(max(frames.values()), 25)
        self.assertEqual(len(case["availability"]), 588)
        self.assertFalse(any(active for _, active in versions.values()))

    def test_new_old_policy_differences_are_explicit_and_hand_checkable(self):
        report = gate.policy_differences()
        values = {c["case"]: c for c in report["cases"]}
        self.assertEqual(values["one_side_destroyed"]["old_force_y_n"], 30)
        self.assertEqual(values["one_side_destroyed"]["new_force_y_n"], 70)
        self.assertEqual(values["turning_residual_translation"]["old_force_y_n"], -2)
        self.assertEqual(values["turning_residual_translation"]["new_force_y_n"], 0)

    def test_ledger_detects_missing_invalidation_and_wrong_output(self):
        s = flight.build_sample_session(Path(__file__).resolve().parents[1])
        event = flight.AvailabilityEvent(s.world.epoch, "ship.web.blue", "main_engine_port",
            "actuator_destroyed", True, 1, 0, "opening")
        # Deliberately leave the event unapplied to the world.
        with self.assertRaises(AssertionError):
            gate.check_ledger(s, {}, (event,))
        s.step(events=(event,))
        gate.check_ledger(s, {}, (event,))
        ship = s.world.ships[0]
        corrupted = replace(ship, propulsion=replace(ship.propulsion, output_percent_units=(99,) * 6))
        s._world = replace(s.world, ships=(corrupted, *s.world.ships[1:]))
        with self.assertRaises(AssertionError):
            gate.check_ledger(s, {}, (event,))


class AnalyticMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample = flight.build_sample_session(Path(__file__).resolve().parents[1])
        seed = sample._seeds[0]
        cls.model = replace(seed.model, runtime=flight.MotionParameters(100, 200, 1e6, 1e6, True),
            structure_points_body_m=(flight.dynamics.Vec2(),), tuning=replace(seed.model.tuning, turn_scale=1))
        cls.motion = replace(seed.motion, position_world_m=flight.dynamics.Vec2(3, 7),
            velocity_world_mps=flight.dynamics.Vec2(), heading_rad=0, yaw_rate_radps=0)
        cls.drag = flight.dynamics.calculate_tactical_drag(cls.model, cls.motion)

    def make(self, design, heading):
        table = fixtures.compile_rows([design])
        return flight.SimplifiedFlightSession((flight.ShipSeed(table, self.model, replace(self.motion, heading_rad=heading)),),
            flight_tests.SAFE, direct_ship_id="fixture.ship")

    def test_all_translation_axes_at_four_headings_match_discrete_closed_form(self):
        axes = ((0, 1), (0, -1), (-1, 0), (1, 0))
        with patch.object(flight.dynamics, "calculate_tactical_drag", return_value=self.drag):
            for d, (x, y) in enumerate(axes):
                for heading in (0, pi / 2, pi, -pi / 2):
                    s = self.make(fixtures.engine("a", 100, point=(-25, 17), direction=(x, y)), heading)
                    for n in range(120):
                        s.step(flight_tests.command(flight.DIRECTIONAL_CHANNELS[d]) if n == 0 else None)
                    initial = s.world.ships[0].motion
                    for _ in range(60):
                        s.step()
                    final = s.world.ships[0].motion
                    ax, ay = cos(heading) * x - sin(heading) * y, sin(heading) * x + cos(heading) * y
                    for position, velocity, p0, v0, acceleration in (
                        (final.position_world_m.x, final.velocity_world_mps.x, initial.position_world_m.x, initial.velocity_world_mps.x, ax),
                        (final.position_world_m.y, final.velocity_world_mps.y, initial.position_world_m.y, initial.velocity_world_mps.y, ay)):
                        self.assertAlmostEqual(velocity, v0 + acceleration, places=10)
                        self.assertAlmostEqual(position, p0 + v0 + acceleration * 60 * 61 / (2 * 60**2), places=10)
                    self.assertEqual(final.yaw_rate_radps, 0)

    def test_pure_turning_accelerates_angle_without_translation(self):
        with patch.object(flight.dynamics, "calculate_tactical_drag", return_value=self.drag):
            for sign, direction in ((1, "yaw.counterclockwise"), (-1, "yaw.clockwise")):
                s = self.make(fixtures.engine("a", 10, category="maneuver_thruster", point=(sign * 2, 0)), 0)
                for n in range(120):
                    s.step(flight_tests.command(direction, yaw=100) if n == 0 else None)
                initial = s.world.ships[0].motion
                for _ in range(60):
                    s.step()
                final = s.world.ships[0].motion
                alpha = sign * .1
                self.assertAlmostEqual(final.yaw_rate_radps, initial.yaw_rate_radps + alpha, places=10)
                angle = initial.heading_rad + initial.yaw_rate_radps + alpha * 60 * 61 / (2 * 60**2)
                self.assertAlmostEqual(final.heading_rad, (angle + pi) % (2 * pi) - pi, places=10)
                self.assertEqual(final.position_world_m, initial.position_world_m)
                self.assertEqual(final.velocity_world_mps, initial.velocity_world_mps)


if __name__ == "__main__":
    unittest.main()
