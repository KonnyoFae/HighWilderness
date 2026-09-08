from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar.realtime_flight import RealtimeFlightSession
from 高天荒野舰艇内部步骤证明 import internal_step_proof_scope, validate_internal_record
from 高天荒野舰艇推进状态合同 import EngineRuntimeState
from 高天荒野舰艇推进时间内核 import PropulsionTimeCommand, validate_committed_propulsion_time_state
from 高天荒野舰艇数据契约 import ContractError
from tools.tactical_realtime_baseline import INPUT_INTERFACE, load_tape, TAPE
from 高天荒野舰艇受控推进完整安全适配器 import FullyGovernedPropulsionOpening
import 高天荒野舰艇受控推进时间边界 as boundary

ROOT = Path(__file__).resolve().parents[1]


class StepProofTests(unittest.TestCase):
    def setUp(self):
        session = RealtimeFlightSession(ROOT)
        context = session.resources.propulsion.ships[0].aggregation_context
        binding = next(b for b in context.bindings if b.actuator_category == "main_engine")
        self.engine = next(e for e in session.world.scene.ships[0].propulsion_state.engines
                           if e.actuator_instance_id == binding.actuator_instance_id)
        self.capability = context.catalog.module(binding.prototype).capability
        self.command = PropulsionTimeCommand.main_engine("full")

    def preview(self):
        return boundary.preview_governed_propulsion_time_boundary(self.engine, self.capability, 0, self.command)

    def commit(self, preview, **overrides):
        values = dict(current_state=self.engine, fixed_step_index=0, effective_target_percent=100, allow_upstage=True)
        values.update(overrides)
        return boundary.commit_governed_propulsion_time_boundary(preview, self.capability, **values)

    def test_same_object_reuses_but_new_object_and_new_scope_revalidate(self):
        changed = replace(self.engine, actuator_instance_id=self.engine.actuator_instance_id + ".other")
        with patch.object(EngineRuntimeState, "parse", wraps=EngineRuntimeState.parse) as parse:
            with internal_step_proof_scope():
                first = validate_internal_record(self.engine, EngineRuntimeState)
                self.assertIs(validate_internal_record(self.engine, EngineRuntimeState), first)
                self.assertEqual(parse.call_count, 1)
                validate_internal_record(changed, EngineRuntimeState)
                self.assertEqual(parse.call_count, 2)
            with internal_step_proof_scope():
                validate_internal_record(self.engine, EngineRuntimeState)
                self.assertEqual(parse.call_count, 3)
            validate_internal_record(self.engine, EngineRuntimeState)
            self.assertEqual(parse.call_count, 4)

    def test_generated_preview_reused_and_forged_preview_rejected(self):
        with internal_step_proof_scope():
            preview = self.preview()
            with patch.object(boundary, "preview_governed_propulsion_time_boundary", wraps=boundary.preview_governed_propulsion_time_boundary) as producer:
                self.commit(preview)
                self.assertEqual(producer.call_count, 0)
                with self.assertRaises(ContractError):
                    self.commit(replace(preview, capability_sha256="0" * 64))
                self.assertEqual(producer.call_count, 1)

    def test_preview_cannot_cross_changed_state_step_or_capability(self):
        with internal_step_proof_scope():
            preview = self.preview()
            with self.assertRaises(ContractError):
                self.commit(preview, fixed_step_index=1)
            with self.assertRaises(ContractError):
                self.commit(preview, current_state=replace(self.engine, actuator_instance_id="engine.other"))
            changed = replace(self.capability, values=tuple((k, 123.0 if k == "thrust_n" else v) for k, v in self.capability.values))
            with self.assertRaises(ContractError):
                boundary.commit_governed_propulsion_time_boundary(preview, changed, current_state=self.engine,
                    fixed_step_index=0, effective_target_percent=100, allow_upstage=True)

    def test_producer_proof_expires_and_exception_cleans_scope(self):
        try:
            with internal_step_proof_scope():
                preview = self.preview()
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        with patch.object(boundary, "preview_governed_propulsion_time_boundary", wraps=boundary.preview_governed_propulsion_time_boundary) as producer:
            with internal_step_proof_scope():
                self.commit(preview)
            self.assertEqual(producer.call_count, 1)

    def test_shape_proof_does_not_skip_time_dependent_validation(self):
        with internal_step_proof_scope():
            result = self.commit(self.preview())
            state = result.state
            validate_committed_propulsion_time_state(state, self.capability, 0)
            self.assertIsNotNone(state.next_transition_step)
            with self.assertRaises(ContractError):
                validate_committed_propulsion_time_state(state, self.capability, state.next_transition_step)

    def test_internal_opening_used_directly_but_copy_has_no_proof(self):
        session = RealtimeFlightSession(ROOT)
        control = load_tape(TAPE)["cases"][0]["steps"][0]["control"]
        pending = session.accept(dict(interface=INPUT_INTERFACE, scene_id=session.world.resource_epoch,
            input_seq=1, target_step=0, command="control", arguments=dict(ship_id="ship.web.blue", control=control)))
        with patch.object(FullyGovernedPropulsionOpening, "parse", wraps=FullyGovernedPropulsionOpening.parse) as parser:
            session.advance(pending)
            self.assertEqual(parser.call_count, 0)
            opening = session._last_resolution.scene_resolution.fully_governed_openings[0][1]
            with internal_step_proof_scope():
                validate_internal_record(replace(opening), FullyGovernedPropulsionOpening)
            self.assertEqual(parser.call_count, 1)


if __name__ == "__main__":
    unittest.main()
