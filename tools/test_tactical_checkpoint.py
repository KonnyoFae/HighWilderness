from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf, tactical_checkpoint as cp
from backend.high_wilderness_sidecar.tactical_command_runtime import ExitOperation
from tools import test_tactical_command_runtime as command_tests
from tools.test_simplified_flight import command
from 高天荒野舰艇数据契约 import ContractError


class CheckpointTests(unittest.TestCase):
    session = command_tests.CommandTests.session
    damage = command_tests.CommandTests.damage
    resource = command_tests.CommandTests.resource
    fly = command_tests.CommandTests.fly

    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(Path(__file__).resolve().parents[1],
            with_command=True, allow_test_device_rebuild=True)

    def restore(self, s, payload=None, **kwargs):
        return cp.loads(cp.dumps(s) if payload is None else payload, s._seeds, s._profile,
            direct_ship_id=s._direct, allow_test_device_rebuild=s._allow_test_device_rebuild, **kwargs)

    def pair(self, s, count=90, inputs=None):
        before, last = s.world, s.last_result
        b = self.restore(s)
        self.assertIs(s.world, before); self.assertIs(s.last_result, last)
        self.assertNotEqual(s.world.epoch, b.world.epoch)
        self.assertEqual(s.world.ships, b.world.ships)
        self.assertIsNone(b.last_result)
        for n in range(count):
            for session in (s, b):
                session.step(**(inputs(session, n) if inputs else {}))
            self.assertEqual(s.world.ships, b.world.ships, n)
            self.assertEqual(s.last_result, b.last_result, n)
        return b

    def test_fresh_roundtrip_and_only_authoritative_fields(self):
        s = self.session()
        encoded = cp.dumps(s)
        for absent in ('cache_key', 'available_units', 'output_percent_units', 'schedule', 'epoch', 'diagnostics', 'allocations'):
            self.assertNotIn('"'+absent+'":', encoded)
        self.pair(s, inputs=lambda _, n: {'control': command()} if n == 0 else {})

    def test_startup_response_deceleration_and_turning_resume_at_exact_steps(self):
        seed = replace(self.sample._seeds[0], initially_ready=False)
        s = sf.SimplifiedFlightSession((seed, *self.sample._seeds[1:]), self.sample._profile,
            direct_ship_id=self.sample._direct, allow_test_device_rebuild=True)
        phases = set()
        for n in range(240):
            s.step(command() if n == 0 else sf.directional_control() if n == 165 else
                command('yaw.counterclockwise', yaw=100) if n == 185 else None)
            phases.update(e.engine.phase for e in s.world.ships[0].propulsion.engines)
            if n in (0, 15, 59, 60, 90, 164, 165, 170, 185, 190, 239):
                self.pair(s, 0)
        self.assertTrue({'starting', 'running', 'stopping'} <= phases)
        self.pair(s, 150)

    def test_all_response_stages_rebuild_and_continue(self):
        s = self.session()
        b = self.restore(s)
        for n in range(100):
            control = command() if n == 0 else sf.directional_control() if n == 70 else None
            s.step(control); b.step(control)
            self.assertEqual(s.world.ships, b.world.ships)
            self.assertEqual(s.last_result, b.last_result)
            b = self.restore(s)
            self.assertEqual(s.world.ships, b.world.ships)
        self.pair(s)

    def test_trip_latch_mode_crew_policy_and_explicit_reset_survive(self):
        seed = self.sample._seeds[0]
        modules = tuple(replace(m, prototype=replace(m.prototype, automation=replace(m.prototype.automation,
            level='manual', automated_functions=()))) if m.id.startswith(('main_engine', 'thruster')) else m for m in seed.resources.modules)
        seed = replace(seed, resources=replace(seed.resources, modules=modules))
        s = sf.SimplifiedFlightSession((seed, *self.sample._seeds[1:]), self.sample._profile,
            direct_ship_id=self.sample._direct, allow_test_device_rebuild=True)
        s.step(resource_operations=(self.resource(s, 'crew', 'ordinary', 0),))
        s.step(resource_operations=(self.resource(s, 'crew', 'ordinary', 10),))
        self.assertTrue(any(s.world.ships[0].resources.latched))
        def inputs(current, n):
            if n == 0:
                return dict(resource_operations=(self.resource(current, 'reset', 'main_engine_port', None),))
            if n == 1:
                return dict(control=command(), resource_operations=(self.resource(current, 'policy', '', current.world.ships[0].resources.policy),))
            return {}
        self.pair(s, inputs=inputs)
        self.pair(s, 15)  # last-operation policy object must survive JSON too

    def test_destroyed_host_partial_hp_and_rebuild_continue_without_duplicate_damage(self):
        s = self.session(); self.fly(s)
        op = replace(self.damage(s, 'generator', phase='closing'), amount=3)
        s.step(device_operations=(op,))
        b = self.restore(s)
        # The previous closing boundary is the next opening boundary; this is a
        # conflicting receipt, not a fresh accepted damage operation after load.
        with self.assertRaises(ContractError):
            b.step(device_operations=(replace(op, epoch=b.world.epoch, phase='opening'),))
        s.step(device_operations=(self.damage(s, 'main_engine_port'),))
        self.pair(s, inputs=lambda current, n: dict(device_operations=(self.damage(current, 'main_engine_port', rebuild=True),)) if n == 3 else {})

    def test_falling_and_rebuilt_cic_keep_loss_history(self):
        s = self.session(); self.fly(s)
        s.step(device_operations=(self.damage(s, 'cic', phase='closing'),))
        self.pair(s, inputs=lambda current, n: dict(device_operations=(self.damage(current, 'cic', rebuild=True),)) if n == 2 else {})
        self.pair(s)
        self.assertEqual(s.world.ships[0].command.lifecycle.physical_status, 'falling')
        with self.assertRaises(ContractError): self.restore(s).step(command())

    def test_remote_restoration_does_not_reacquire_authority(self):
        s = self.session(remote=True); self.fly(s)
        s.step(resource_operations=(self.resource(s, 'mode', 'remote_core', 'off'),))
        self.pair(s, inputs=lambda current, n: dict(resource_operations=(self.resource(current, 'mode', 'remote_core', 'active'),)) if n == 1 else {})
        self.pair(s)
        self.assertEqual(s.world.ships[0].command.lifecycle.command_status, 'scene_command')
        self.assertFalse(s.world.ships[0].authority_allowed)

    def test_both_exit_phases_and_reasons_remain_frozen(self):
        for phase in ('opening', 'closing'):
            for reason in ('scripted_transfer', 'fell_below_scene'):
                s = self.session(); self.fly(s)
                if reason == 'fell_below_scene':
                    s.step(device_operations=(self.damage(s, 'lift_tank'),))
                s.step(exit_operations=(ExitOperation(s.world.epoch, s._direct,
                    s.world.fixed_step+(phase == 'closing'), phase, reason),))
                self.pair(s)

    def test_external_fuel_and_emergency_blockers_are_preserved(self):
        s = self.session(); self.fly(s)
        for reason in ('fuel_unavailable', 'emergency_cut'):
            s.step(events=(sf.AvailabilityEvent(s.world.epoch, s._direct, 'main_engine_port', reason, True, 1,
                s.world.fixed_step, 'opening'),))
        self.pair(s)

    def test_restored_stable_loop_has_no_boundary_hash_or_dependency_rechecks(self):
        s = self.session(); self.fly(s); b = self.restore(s)
        with patch.object(cp, '_binding', side_effect=AssertionError('hot hash')), \
             patch('backend.high_wilderness_sidecar.tactical_resources_runtime._allocate_power', side_effect=AssertionError('hot allocation')), \
             patch('backend.high_wilderness_sidecar.tactical_command_runtime.project_tactical_ship_lifecycle', side_effect=AssertionError('hot command')):
            for _ in range(60): b.step()

    def test_safety_ceiling_and_release_hold_survive(self):
        seed = self.sample._seeds[0]
        seed = replace(seed, model=replace(seed.model, runtime=replace(seed.model.runtime,
            safe_longitudinal_mps2=.001)))
        s = sf.SimplifiedFlightSession((seed, *self.sample._seeds[1:]), self.sample._profile,
            direct_ship_id=self.sample._direct, allow_test_device_rebuild=True)
        for n in range(2): s.step(command() if n == 0 else None)
        self.assertTrue(any(g.ceiling < 100 for g in s.world.ships[0].propulsion.governors))
        self.pair(s, 0)
        # Removing demand permits a normal safety release; reconstruct every
        # boundary through its hold interval, including the release candidate.
        b = self.restore(s)
        for n in range(s._profile.release_hold_steps+5):
            control = sf.directional_control() if n == 0 else None
            s.step(control); b.step(control)
            self.assertEqual(s.world.ships, b.world.ships)
            self.assertEqual(s.last_result, b.last_result)
            b = self.restore(s)

    def test_version_resources_and_fixture_permission_are_bound(self):
        s = self.session(); s.step()
        payload = cp.dumps(s)
        with self.assertRaises(ContractError):
            cp.loads(payload, s._seeds, s._profile, direct_ship_id=s._direct)
        changed = replace(s._seeds[0], model=replace(s._seeds[0].model,
            runtime=replace(s._seeds[0].model.runtime, current_mass_kg=s._seeds[0].model.runtime.current_mass_kg+1)))
        with self.assertRaises(ContractError):
            cp.loads(payload, (changed, *s._seeds[1:]), s._profile, direct_ship_id=s._direct, allow_test_device_rebuild=True)
        for key in ('interface', 'policy', 'resources_sha256'):
            value = json.loads(payload); value[key] = 'foreign'
            with self.assertRaises(ContractError): self.restore(s, json.dumps(value))

    def test_malformed_and_inconsistent_payloads_are_rejected_atomically(self):
        s = self.session(); s.step(command())
        before, last = s.world, s.last_result
        raw = json.loads(cp.dumps(s))
        paths = [
            (('fixed_step',), True),
            (('ships', 0, 'motion', 'heading_rad'), float('nan')),
            (('ships', 0, 'motion', 'fuel_units'), 0),
            (('ships', 0, 'motion', 'fixed_step_index'), 9),
            (('ships', 0, 'motion', 'layer_transition'), {}),
            (('ships', 0, 'propulsion', 'engines', 0, 'engine', 'next_transition_step'), 9000),
            (('ships', 0, 'propulsion', 'engines', 0, 'blocked', 0), True),
            (('ships', 0, 'propulsion', 'engines', 0, 'versions', 0), 9000),
            (('ships', 0, 'devices', 'modules', 0, 'durability_points'), -1),
            (('ships', 0, 'devices', 'modules', 0, 'sequence'), 2),
            (('ships', 0, 'resources', 'latched', 0), True),
            (('ships', 0, 'resources', 'input_revision'), 2),
            (('ships', 0, 'command', 'loss_step'), 9000),
            (('ships', 0, 'command', 'lifecycle', 'physical_status'), 'exited'),
            (('ships', 1, 'authority_allowed'), True),
        ]
        for path, value in paths:
            current = json.loads(json.dumps(raw)); node = current
            for key in path[:-1]: node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ContractError): self.restore(s, json.dumps(current))
            self.assertIs(s.world, before); self.assertIs(s.last_result, last)
        for text in ('{', 'null', cp.dumps(s)[:-1]+',"policy":"duplicate"}', cp.dumps(s)[:-1]+',"extra":true}'):
            with self.assertRaises(ContractError): self.restore(s, text)
        del raw['ships'][0]['resources']['latched']
        with self.assertRaises(ContractError): self.restore(s, json.dumps(raw))

    def test_save_requires_idle_owner_and_old_epoch_requests_are_rejected(self):
        s = self.session(); s.step()
        before = s.world
        with self.assertRaises(ContractError):
            s.step(project=lambda *_: cp.dumps(s))
        self.assertIs(s.world, before)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ContractError): pool.submit(cp.dumps, s).result()
        b = self.restore(s)
        with self.assertRaises(ContractError): b.step(device_operations=(self.damage(s, 'cic'),))

    def test_unsupported_partial_domain_checkpoint_is_rejected(self):
        s = sf.SimplifiedFlightSession(tuple(replace(seed, command=None) for seed in self.sample._seeds),
            self.sample._profile, direct_ship_id=self.sample._direct)
        with self.assertRaises(ContractError): cp.dumps(s)


if __name__ == '__main__': unittest.main()
