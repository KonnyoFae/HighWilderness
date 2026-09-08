from dataclasses import dataclass, FrozenInstanceError, replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession
from tools.tactical_realtime_baseline import INPUT_INTERFACE, TAPE, load_tape
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from 高天荒野舰艇数据契约 import ContractError
import 高天荒野舰艇推进时间内核 as timing
from 高天荒野舰艇实际推进聚合器 import CompiledActualPropulsionContexts

ROOT = Path(__file__).resolve().parents[1]


class RealtimeFlightTests(unittest.TestCase):
    def request(self, session):
        control = load_tape(TAPE)["cases"][0]["steps"][0]["control"]
        return dict(interface=INPUT_INTERFACE, scene_id=session.world.resource_epoch,
            input_seq=session.world.last_input_seq + 1, target_step=session.world.scene.fixed_step_index,
            command="control", arguments=dict(ship_id="ship.web.blue", control=control))

    def test_nested_mutable_graph_rejected(self):
        @dataclass(frozen=True)
        class Container:
            value: object
        with self.assertRaises(TypeError):
            require_deeply_immutable(Container(({},)))
        require_deeply_immutable(Container((1, "x")))

    def test_resources_export_isolated_and_world_frozen(self):
        session = RealtimeFlightSession(ROOT)
        with self.assertRaises(FrozenInstanceError):
            session.world.revision = 99
        with self.assertRaises(FrozenInstanceError):
            session.resources.ships[0].snapshot.source_sha256 = "0" * 64
        description = session.resources.describe()
        description["manifest"].clear()
        self.assertTrue(session.resources.describe()["manifest"])
        with self.assertRaises(FrozenInstanceError):
            session._timing._entries = {}

    def test_pending_ownership_stale_input_and_projection_rollback(self):
        session, other = RealtimeFlightSession(ROOT), RealtimeFlightSession(ROOT)
        before = session.world
        request = self.request(session)
        pending = session.accept(request)
        with self.assertRaises(RuntimeError):
            session.accept(request)
        with self.assertRaises(RuntimeError):
            other.advance(pending)
        def fail_projection(*args):
            raise RuntimeError("projection failed")
        with self.assertRaisesRegex(RuntimeError, "projection failed"):
            session.advance(pending, project=fail_projection)
        self.assertIs(session.world, before)
        self.assertIsNone(session.observe()["arbitration"])
        with self.assertRaises(RuntimeError):
            session.advance(pending)
        session.advance(session.accept(request))
        self.assertEqual(session.world.revision, 1)
        self.assertEqual(other.world.revision, 0)
        with self.assertRaises(ContractError):
            session.accept(request)

    def test_static_compilation_strict_and_scopes_do_not_leak(self):
        session = RealtimeFlightSession(ROOT)
        context = session.resources.propulsion.ships[0].aggregation_context
        # Corrupt frozen object assembled outside the supported compiler must fail.
        with self.assertRaises(ContractError):
            CompiledActualPropulsionContexts((replace(context, bindings=()),))
        binding = context.bindings[0]
        capability = context.catalog.module(binding.prototype).capability
        changed = replace(capability, values=tuple((k, 999999.0 if k == "thrust_n" else v) for k, v in capability.values))
        with timing.compiled_propulsion_timing_scope(session._timing):
            with self.assertRaises(ContractError):
                timing._resolve_timing_capability(changed, binding.actuator_category)
        # A different valid capability is strictly parsed outside the session scope.
        with patch.object(timing, "_parse_exact_timing_capability", wraps=timing._parse_exact_timing_capability) as strict:
            timing._resolve_timing_capability(changed, binding.actuator_category)
            self.assertEqual(strict.call_count, 1)

    def test_input_dictionary_changes_cannot_change_prepared_command(self):
        session = RealtimeFlightSession(ROOT)
        request = self.request(session)
        pending = session.accept(request)
        expected = pending.control.to_dict()
        request["arguments"]["control"].clear()
        session.advance(pending)
        self.assertEqual(pending.control.to_dict(), expected)
        self.assertEqual(session.world.last_input_seq, 1)


if __name__ == "__main__":
    unittest.main()
