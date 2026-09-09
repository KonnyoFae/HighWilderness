from dataclasses import replace
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar import tactical_devices as td
from tools import test_simplified_propulsion as fixtures
from tools import test_simplified_flight as prior
from 高天荒野舰艇数据契约 import ContractError


class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = sf.build_sample_session(Path(__file__).resolve().parents[1], with_devices=True)
        cls.sample_seeds = s._seeds
        cls.model = replace(s._seeds[0].model, runtime=sf.MotionParameters(100, 200, 1000, 1000, True),
            structure_points_body_m=(sf.dynamics.Vec2(),))
        cls.motion = replace(s._seeds[0].motion, position_world_m=sf.dynamics.Vec2(),
            velocity_world_mps=sf.dynamics.Vec2(), yaw_rate_radps=0, heading_rad=0)

    def session(self, hp=None, rebuild=True, blockers=()):
        designs = [fixtures.engine('a', 40), replace(fixtures.engine('b', 30), host_instance_id='child'),
            fixtures.engine('c', 30)]
        table = fixtures.compile_rows(designs)
        modules = (td.ModuleDesign('a', 100), td.ModuleDesign('b', 100, 'child'), td.ModuleDesign('c', 100),
            td.ModuleDesign('child', 100, 'host'), td.ModuleDesign('host', 100), td.ModuleDesign('other', 100))
        devices = td.DeviceSeed(table.source_sha256, modules, hp or (100,) * 6)
        return sf.SimplifiedFlightSession((sf.ShipSeed(table, self.model, self.motion,
            initial_blockers=blockers, devices=devices),), prior.SAFE, direct_ship_id='fixture.ship',
            allow_test_device_rebuild=rebuild)

    def op(self, s, module='b', amount=100, kind='damage', phase='opening', sequence=None, ship=None):
        ship = ship or s.world.ships[0].ship_id
        index = next(i for i, v in enumerate(s.world.ships) if v.ship_id == ship)
        dk = s._device_kernels[index]
        if sequence is None:
            sequence = s.world.ships[index].devices.modules[dk.by_id[module]].sequence + 1
        return td.DeviceOperation(s.world.epoch, ship, module, sequence, kind, amount,
            s.world.fixed_step + (phase == 'closing'), phase)

    def test_partial_damage_is_separate_from_propulsion_and_noop_reuses_state(self):
        s = self.session()
        initial = s.world.ships[0]
        with patch.object(td.DeviceKernel, 'reasons', side_effect=AssertionError('unnecessary dependency check')):
            s.step(device_operations=(self.op(s, amount=25),))
            state = s.world.ships[0]
            self.assertEqual(state.devices.modules[1].durability_points, 75)
            self.assertIs(state.propulsion, initial.propulsion)
            s.step()
            self.assertIs(s.world.ships[0].devices, state.devices)

    def test_full_damage_host_chain_and_multireason_rebuild(self):
        s = self.session()
        s.step(device_operations=(self.op(s, module='host'),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        self.assertEqual(s.world.ships[0].devices.modules[1].durability_points, 100)
        s.step(device_operations=(self.op(s),))
        self.assertEqual(s.world.ships[0].propulsion.engines[1].blocked[:2], (True, True))
        s.step(device_operations=(self.op(s, amount=0, kind='test_rebuild'),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        s.step(device_operations=(self.op(s, module='host', amount=0, kind='test_rebuild'),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 100)

    def test_initial_damage_keeps_intact_baseline_and_rejects_manual_owned_reasons(self):
        s = self.session((100, 0, 100, 100, 100, 100))
        self.assertEqual(s._seeds[0].contributions.intact_totals_units[0], 100)
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        event = sf.AvailabilityEvent(s.world.epoch, 'fixture.ship', 'b', 'actuator_destroyed', False, 1, 0, 'opening')
        with self.assertRaises(ContractError):
            s.step(events=(event,))
        with self.assertRaises(ContractError):
            self.session(blockers=(('b', ('host_destroyed',)),))

    def test_duplicate_conflict_stale_and_gaps_are_atomic(self):
        s = self.session()
        e = self.op(s, amount=25)
        s.step(device_operations=(e, e))
        self.assertEqual(s.world.ships[0].devices.modules[1].durability_points, 75)
        for operations in ((self.op(s, sequence=1),), (self.op(s, sequence=3),),
                (self.op(s), self.op(s, amount=1))):
            before, last = s.world, s.last_result
            with self.assertRaises(ContractError):
                s.step(device_operations=operations)
            self.assertIs(s.world, before)
            self.assertIs(s.last_result, last)

    def test_boundary_damage_preserves_delivered_interval_and_inertia(self):
        a, b, c = self.session(), self.session(), self.session()
        for n in range(120):
            for s in (a, b, c):
                s.step(prior.command() if n == 0 else None)
        a.step()
        b.step(device_operations=(self.op(b, phase='closing'),))
        c.step(device_operations=(self.op(c, phase='opening'),))
        self.assertEqual(a.world.ships[0].motion, b.world.ships[0].motion)
        self.assertGreater(b.world.ships[0].motion.velocity_world_mps.y, c.world.ships[0].motion.velocity_world_mps.y)
        self.assertGreater(c.world.ships[0].motion.velocity_world_mps.y, 0)
        self.assertEqual(b.world.ships[0].propulsion.output_percent_units[0], 7000)

    def test_rebuild_requires_explicit_fixture_option_and_obeys_power_and_timing(self):
        s = self.session(rebuild=False)
        with self.assertRaises(ContractError):
            s.step(device_operations=(self.op(s, kind='test_rebuild', amount=0),))
        with self.assertRaises(ContractError):
            s.step(device_operations=(self.op(s, kind='repair', amount=20),))
        s = self.session()
        s.step(prior.command(), device_operations=(self.op(s),), events=(sf.AvailabilityEvent(
            s.world.epoch, 'fixture.ship', 'b', 'power_unavailable', True, 1, 0, 'opening'),))
        s.step(device_operations=(self.op(s, kind='test_rebuild', amount=0),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        s.step(events=(sf.AvailabilityEvent(s.world.epoch, 'fixture.ship', 'b', 'power_unavailable', False,
            2, s.world.fixed_step, 'opening'),))
        state = s.world.ships[0].propulsion
        self.assertEqual(state.available_units[0], 100)
        self.assertEqual(state.engines[1].engine.phase, 'starting')
        self.assertEqual(state.engines[1].engine.actual_output_percent, 0)

    def test_invalid_resource_graph_and_initial_durability(self):
        s = self.session()
        seed, table = s._seeds[0].devices, s._seeds[0].contributions
        invalid = [replace(seed, propulsion_source_sha256='wrong'), replace(seed, modules=seed.modules * 2),
            replace(seed, initial_durability_points=(101,) * 6), replace(seed, initial_durability_points=(float('nan'),) * 6),
            replace(seed, modules=(replace(seed.modules[0], host_instance_id='missing'), *seed.modules[1:])),
            replace(seed, modules=(*seed.modules[:4], replace(seed.modules[4], host_instance_id='child'), seed.modules[5]))]
        for value in invalid:
            with self.assertRaises(ContractError):
                td.DeviceKernel(value, table)

    def test_invalid_operations_reject_without_mutation(self):
        s = self.session()
        good = self.op(s)
        for op in (replace(good, epoch='wrong'), replace(good, module_id='missing'), replace(good, amount=True),
                replace(good, amount=float('inf')), replace(good, amount=-1), replace(good, fixed_step=True),
                replace(good, phase='middle'), replace(good, sequence=True), replace(good, fixed_step=10)):
            before = s.world
            with self.assertRaises(ContractError):
                s.step(device_operations=(op,))
            self.assertIs(s.world, before)

    def test_projection_and_later_ship_failure_roll_back_devices_receipts_and_propulsion(self):
        s = sf.SimplifiedFlightSession(self.sample_seeds, prior.SAFE, direct_ship_id='ship.web.blue')
        op = self.op(s, module='main_engine_port', amount=1e9)
        before, last = s.world, s.last_result
        with self.assertRaises(RuntimeError):
            s.step(device_operations=(op,), project=lambda *_: (_ for _ in ()).throw(RuntimeError('projection')))
        self.assertIs(s.world, before)
        self.assertIs(s.last_result, last)
        with patch.object(s._device_kernels[1], 'boundary', side_effect=RuntimeError('second ship')):
            with self.assertRaises(RuntimeError):
                s.step(device_operations=(op,))
        self.assertIs(s.world, before)
        s.step(device_operations=(op,))
        self.assertEqual(s.world.ships[0].devices.modules[s._device_kernels[0].by_id['main_engine_port']].durability_points, 0)

    def test_unrelated_zero_contribution_and_batch_net_change(self):
        s = self.session()
        with patch.object(td.DeviceKernel, 'reasons', side_effect=AssertionError('unrelated engine scan')):
            s.step(device_operations=(self.op(s, module='other'),))
        state = s.world.ships[0].propulsion
        a = self.op(s)
        b = self.op(s, sequence=a.sequence+1, kind='test_rebuild', amount=0)
        s.step(device_operations=(a, b))
        self.assertIs(s.world.ships[0].propulsion, state)
        # A zero-contribution engine still has a durability record and own reason.
        table = fixtures.compile_rows([fixtures.engine('z', 10, category='maneuver_thruster', point=(0, 0))])
        devices = td.DeviceSeed(table.source_sha256, (td.ModuleDesign('z', 100),), (100,))
        z = sf.SimplifiedFlightSession((sf.ShipSeed(table, self.model, self.motion, devices=devices),),
            prior.SAFE, direct_ship_id='fixture.ship')
        z.step(device_operations=(self.op(z, module='z'),))
        self.assertTrue(z.world.ships[0].propulsion.engines[0].blocked[0])

    def test_random_domain_ledger_matches_full_host_recompute(self):
        s = self.session()
        rng, health = random.Random(5201), dict.fromkeys(('a','b','c','child','host','other'), 100)
        for _ in range(300):
            name = rng.choice(tuple(health))
            reset = rng.random() < .25
            amount = 0 if reset else rng.choice((0, 17, 100, 1000))
            health[name] = 100 if reset else max(0, health[name] - amount)
            s.step(device_operations=(self.op(s, module=name, amount=amount,
                kind='test_rebuild' if reset else 'damage', phase=rng.choice(('opening','closing'))),))
            ship = s.world.ships[0]
            self.assertEqual([m.durability_points for m in ship.devices.modules], list(health.values()))
            expected = 40*(health['a']>td.EPS) + 30*(health['c']>td.EPS)
            expected += 30*all(health[i]>td.EPS for i in ('b','child','host'))
            self.assertEqual(ship.propulsion.available_units[0], expected)


if __name__ == '__main__':
    unittest.main()
