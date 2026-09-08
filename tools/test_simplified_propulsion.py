from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_propulsion as sp
from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession
from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, ResourceReference
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from tools import test_realtime_flight as flight_tests

ROOT = Path(__file__).resolve().parents[1]


def engine(name, thrust, *, category="main_engine", point=(0, 0), direction=(0, 1), response=1):
    cap = ModuleCapability.parse(dict(kind=category, thrust_n=thrust, local_thrust_axis="+Y",
        fuel_units_per_s=0, response_time_s=response, startup_time_s=1 if category == "main_engine" else 0),
        "$", propulsion_capability_version=2)
    return sp.EngineDesign(name, ResourceReference("fixture.engine", 3), cap, point, direction)


def compile_rows(rows, **changes):
    args = dict(ship_id="fixture.ship", snapshot_sha256="a" * 64, catalog_sha256="b" * 64,
        design_mass_kg=100, design_inertia_kg_m2=200, engines=rows)
    args.update(changes)
    return sp.compile_contributions(**args)


def quantities(compiled, units):
    return tuple(Fraction(v, compiled.unit_denominator) for v in units)


class SimplifiedPropulsionTests(unittest.TestCase):
    def test_weighted_subsets_have_no_balancing_or_health_input(self):
        c = compile_rows([engine("a", 40, point=(-20, 5)), engine("b", 30, point=(3, 8)), engine("c", 30)])
        for size in range(4):
            for available in combinations(("a", "b", "c"), size):
                expected = sum(dict(a=40, b=30, c=30)[i] for i in available)
                r = c.remaining(available)
                self.assertEqual(quantities(c, r.totals_units), (expected, 0, 0, 0, 0, 0))
                self.assertEqual(Fraction(*r.ratios[0]), Fraction(expected, 100))
        self.assertEqual(Fraction(*c.remaining(("a", "c")).ratios[0]), Fraction(7, 10))

    def test_turning_is_independent_and_uses_fixed_signed_lever(self):
        c = compile_rows([engine("main", 100, point=(-100, 0)),
            engine("left", 3, category="maneuver_thruster", point=(2, 9)),
            engine("right", 5, category="maneuver_thruster", point=(-4, 9)),
            engine("center", 100, category="maneuver_thruster")])
        self.assertEqual(quantities(c, c.intact_totals_units), (100, 0, 0, 0, 6, 20))
        self.assertEqual(quantities(c, c.remaining(("main", "right", "center")).totals_units), (100, 0, 0, 0, 0, 20))
        self.assertEqual(quantities(c, c.remaining(("left",)).totals_units), (0, 0, 0, 0, 6, 0))

    def test_four_translation_axes_and_fractional_torque_are_exact(self):
        rows = [engine(str(i), i + 1, direction=axis) for i, axis in enumerate(sp.AXES)]
        rows += [engine("fraction", 0.1, category="maneuver_thruster", point=(0.3, 0))]
        c = compile_rows(rows)
        self.assertEqual(quantities(c, c.intact_totals_units), (1, 2, 3, 4, Fraction(3, 100), 0))
        self.assertEqual(c.unit_denominator, 100)

    def test_empty_all_destroyed_and_missing_channels(self):
        c = compile_rows([])
        self.assertEqual(c.intact_totals_units, (0,) * 6)
        self.assertEqual(c.remaining(()).ratios, ((0, 1),) * 6)
        self.assertEqual(c.remaining(()).present_channels, (False,) * 6)
        c = compile_rows([engine("a", 20)])
        self.assertTrue(c.remaining(()).present_channels[0])
        self.assertEqual(Fraction(*c.remaining(()).ratios[0]), 0)

    def test_response_keeps_exact_duration_not_just_ceil(self):
        c = compile_rows([engine("a", 10, response=1.01)])
        self.assertEqual(c.engines[0].response_steps, 61)
        self.assertEqual(c.engines[0].response_seconds, (101, 100))
        self.assertEqual(c.engines[0].startup_steps, 60)

    def test_source_order_identity_and_deep_isolation(self):
        rows = [engine("b", 30), engine("a", 40)]
        c = compile_rows(rows)
        self.assertEqual(c, compile_rows(reversed(rows)))
        rows.clear()
        self.assertEqual(len(c.engines), 2)
        require_deeply_immutable(c)
        export = c.to_dict()
        export["engines"][0]["contribution_units"][0] = 999
        self.assertEqual(c.engines[0].contribution_units[0], 40)
        with self.assertRaises(FrozenInstanceError):
            c.engines[0].index = 8
        changed = compile_rows([engine("a", 40), engine("b", 30)], catalog_sha256="c" * 64)
        self.assertNotEqual(changed.source_sha256, c.source_sha256)

    def test_invalid_sources_rejected(self):
        valid = engine("a", 10)
        for rows in ([valid, valid], [replace(valid, direction_body=(0.6, 0.8))],
                     [replace(valid, application_point_m=(float("nan"), 0))],
                     [replace(valid, direction_body=(False, 1))],
                     [replace(valid, instance_id="bad name")]):
            with self.subTest(rows=rows), self.assertRaises(ContractError):
                compile_rows(rows)
        for changes in (dict(snapshot_sha256="wrong"), dict(design_mass_kg=0),
                        dict(design_inertia_kg_m2=float("inf"))):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                compile_rows([valid], **changes)
        with self.assertRaises(TypeError):
            compile_rows([replace(valid, application_point_m=[0, 0])])
        c = compile_rows([valid])
        for ids in (("a", "a"), ("missing",), (True,)):
            with self.assertRaises(ContractError):
                c.remaining(ids)

    def test_snapshot_compiler_ignores_balancing_and_rejects_wrong_sources(self):
        session = RealtimeFlightSession(ROOT)
        context = session.resources.propulsion.ships[0].aggregation_context
        with patch("高天荒野舰艇无界面舾装编译器.aggregate_actuators", side_effect=AssertionError("balance called")), \
             patch("高天荒野舰艇运行时参数编译器.aggregate_actuators", side_effect=AssertionError("runtime called")), \
             patch("高天荒野舰艇定向推进控制桥.bind_directional_outfit_propulsion", side_effect=AssertionError("legacy binding called")):
            c = sp.compile_snapshot_contributions(context.ship_id, context.snapshot, context.catalog)
        self.assertEqual(quantities(c, c.intact_totals_units), (10000000, 0, 0, 0, 27500000, 22500000))
        self.assertEqual(len(c.engines), 6)
        with self.assertRaises(ContractError):
            sp.compile_snapshot_contributions(context.ship_id, context.snapshot, replace(context.catalog, id="foreign.catalog"))
        missing = replace(context.snapshot, outfit=replace(context.snapshot.outfit, actuators=()))
        with self.assertRaises(ContractError):
            sp.compile_snapshot_contributions(context.ship_id, missing, context.catalog)

    def test_session_prepares_once_and_does_not_switch_flight_rules(self):
        with patch("backend.high_wilderness_sidecar.realtime_flight.compile_snapshot_contributions",
                   wraps=sp.compile_snapshot_contributions) as compiler:
            session = RealtimeFlightSession(ROOT)
            tables = session.resources.simplified_propulsion
            self.assertEqual(compiler.call_count, 2)
            before = session.world.scene.to_dict()["interface"]
            session.advance(session.accept(flight_tests.RealtimeFlightTests.request(self, session)))
            self.assertEqual(compiler.call_count, 2)
            self.assertIs(session.resources.simplified_propulsion, tables)
            self.assertEqual(session.world.scene.to_dict()["interface"], before)


if __name__ == "__main__":
    unittest.main()
