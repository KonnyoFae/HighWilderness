"""Paused single-step, I9/v7 arbitration, receipts, atomicity and safety regression."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar.tactical import TacticalService, SCENARIO_ID, INPUT_INTERFACE
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇完整受控推进场景 import validate_fully_governed_scene_context
from 高天荒野舰艇战术舰队指挥 import (
    TacticalShipOrder, issue_tactical_ship_order, initialize_tactical_fleet_command_state,
    advance_commanded_tactical_scene_step, TacticalDirectControlFrame,
)
from 高天荒野舰艇统一战术场景 import initialize_tactical_scene
from 高天荒野舰艇战术机动求解器 import TacticalControlInput, Vec2


FORWARD = directional_control((ChannelPropulsionCommand("translation.forward", "full", None),))
LEFT = directional_control((ChannelPropulsionCommand("yaw.counterclockwise", None, 25),))
BRAKE = directional_control(automatic_brake=True)


class PausedControlTests(unittest.TestCase):
    def create(self):
        service = TacticalService("backend.test")
        service.dispatch(dict(method="tactical.create", params=dict(scenario_id=SCENARIO_ID)))
        service.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
        return service

    def input(self, service, control=FORWARD, **updates):
        return dict(interface=INPUT_INTERFACE, scene_id=service.scene_id, input_seq=service.last_input_seq + 1,
            target_step=service.scenario.scene.fixed_step_index, command="control",
            arguments=dict(ship_id="ship.web.blue", control=control.to_dict()), **updates)

    def step(self, service, value=None):
        return service.dispatch(dict(method="tactical.step", params=dict(scene_id=service.scene_id,
            input=self.input(service) if value is None else value)))

    def assert_unchanged(self, service, before):
        self.assertEqual(canonical_sha256(service.scenario.scene), before[0])
        self.assertEqual(canonical_sha256(service.command_state), before[1])
        self.assertEqual(service.last_input_seq, before[2])

    def before(self, service):
        return canonical_sha256(service.scenario.scene), canonical_sha256(service.command_state), service.last_input_seq

    def test_single_step_exact_clock_receipt_and_paused_reads(self):
        service = self.create()
        value = self.input(service)
        result = self.step(service, value)
        self.assertEqual(result["fixed_step"], 1)
        self.assertEqual(result["time_s"], 1 / 60)
        self.assertTrue(result["paused"])
        self.assertIsNone(result["static"])
        receipt = result["control_state"]
        self.assertEqual(receipt["last_input_sha256"], canonical_sha256(value))
        self.assertEqual(receipt["last_input_seq"], 1)
        self.assertEqual(receipt["last_executed_step"], 0)
        self.assertEqual(receipt["last_arbitration"]["source"], "player_direct")
        self.assertEqual(receipt["last_arbitration"]["resulting_scene_sha256"], canonical_sha256(service.scenario.scene))
        before = self.before(service)
        for _ in range(3):
            read = service.dispatch(dict(method="tactical.inspect", params=dict(scene_id=service.scene_id, known_static_sha256=service.static_sha256)))
            self.assertEqual(read, result)
        self.assert_unchanged(service, before)
        result["control_state"]["requested_control"]["automatic_brake"] = True
        self.assertFalse(service.last_input["arguments"]["control"]["automatic_brake"])

    def test_duplicate_stale_wrong_scene_and_red_input_do_not_consume_sequence(self):
        service = self.create()
        first = self.input(service); self.step(service, first)
        cases = [(first, "input_duplicate")]
        for key, value, code in [("target_step", 0, "input_step"), ("target_step", 2, "input_step"),
                                  ("scene_id", "scene.old", "scene_mismatch"), ("input_seq", True, "input_integer")]:
            bad = self.input(service); bad[key] = value; cases.append((bad, code))
        red = self.input(service); red["arguments"]["ship_id"] = "ship.web.red"; cases.append((red, "direct_ship_mismatch"))
        malformed = self.input(service); malformed["arguments"]["control"]["extra"] = True; cases.append((malformed, "object.keys"))
        for bad, code in cases:
            before = self.before(service)
            with self.subTest(code=code), self.assertRaisesRegex(ContractError, code): self.step(service, bad)
            self.assert_unchanged(service, before)
        self.assertEqual(self.step(service)["control_state"]["last_input_seq"], 2)

    def test_editor_mode_blocks_step_and_roundtrip_keeps_exact_state(self):
        service = self.create(); self.step(service, self.input(service, LEFT))
        before = self.before(service)
        service.dispatch(dict(method="tactical.set_mode", params=dict(mode="editor")))
        with self.assertRaisesRegex(ContractError, "mode_required"): self.step(service)
        self.assert_unchanged(service, before)
        service.dispatch(dict(method="tactical.set_mode", params=dict(mode="tactical")))
        result = self.step(service, self.input(service, directional_control()))
        self.assertTrue(all(c["target_output_percent"] == 0 for c in result["control_state"]["requested_control"]["channel_commands"][4:]))

    def test_projection_failure_rolls_back_scene_and_receipt(self):
        service = self.create(); before = self.before(service)
        with patch.object(service, "_snapshot", side_effect=RuntimeError("projection failed")):
            with self.assertRaises(RuntimeError): self.step(service)
        self.assert_unchanged(service, before)
        self.assertEqual(self.step(service)["fixed_step"], 1)

    def test_adapter_matches_full_v7_core_and_honors_engine_response(self):
        service = self.create()
        baseline = build_two_ship_scenario(Path(__file__).resolve().parent)
        sequence = [FORWARD] * 10 + [LEFT] * 3 + [BRAKE] * 3 + [directional_control()] * 2
        for control in sequence:
            result = self.step(service, self.input(service, control))
            baseline.step({"ship.web.blue": control, "ship.web.red": directional_control()})
            self.assertEqual(canonical_sha256(service.scenario.scene), canonical_sha256(baseline.scene))
            validate_fully_governed_scene_context(service.scenario.scene, service.scenario.propulsion_context)
            self.assertEqual(result["control_state"]["command_state_sha256"], canonical_sha256(service.command_state))
        self.assertEqual(result["control_state"]["fuel_units"], 800)
        self.assertGreater(result["ships"][0]["speed_mps"], 0)
        self.assertTrue(any(e["actual_percent"] > 0 for e in result["control_state"]["engines"]))

    def test_direct_loss_role_and_tuning_source_are_enforced(self):
        service = self.create()
        ship = service.scenario.scene.ships[0]
        falling = replace(ship, lifecycle_state=replace(ship.lifecycle_state, physical_status="falling"))
        service.scenario.scene = replace(service.scenario.scene, ships=(falling, *service.scenario.scene.ships[1:]))
        service.command_state = replace(service.command_state, source_scene_sha256=canonical_sha256(service.scenario.scene))
        self.assertFalse(service.snapshot()["control_state"]["available"])
        before = self.before(service)
        with self.assertRaisesRegex(ContractError, "direct_after_loss"): self.step(service)
        self.assert_unchanged(service, before)
        service = self.create()
        service.command_state = replace(service.command_state, tuning_profile_sha256="0" * 64)
        with self.assertRaisesRegex(ContractError, "tuning_mismatch"): self.step(service)
        service = self.create()
        service.command_state = replace(service.command_state, assignments=(replace(service.command_state.assignments[0], role="ordinary_ship"),))
        with self.assertRaisesRegex(ContractError, "direct_role"): self.step(service)

    def test_resource_mismatch_and_rts_cannot_be_silently_adapted(self):
        service = self.create()
        service.scenario.propulsion_context = replace(service.scenario.propulsion_context,
            execution=replace(service.scenario.propulsion_context.execution, resource_bundle_sha256="0" * 64))
        with self.assertRaisesRegex(ContractError, "execution_lineage"): self.step(service)
        service = self.create()
        service.command_state = issue_tactical_ship_order(service.command_state, service.scenario.scene,
            TacticalShipOrder("order.test.hold", "ship.web.blue", "hold", 0))
        with self.assertRaisesRegex(ContractError, "directional_scope"): self.step(service)

    def test_no_fuel_hard_limit_is_not_bypassed(self):
        service = self.create(); ship = service.scenario.scene.ships[0]
        instance = ship.combat_state.instance
        instance = replace(instance, operational_state=replace(instance.operational_state, fuel_units=0.0))
        ship = replace(ship, combat_state=replace(ship.combat_state, instance=instance), motion_state=replace(ship.motion_state, fuel_units=0.0))
        service.scenario.scene = replace(service.scenario.scene, ships=(ship, *service.scenario.scene.ships[1:]))
        service.command_state = replace(service.command_state, source_scene_sha256=canonical_sha256(service.scenario.scene))
        result = self.step(service, self.input(service, replace(FORWARD, overg_requested=True)))
        self.assertEqual(result["control_state"]["fuel_units"], 0)
        self.assertTrue(all(e["actual_percent"] == 0 for e in result["control_state"]["engines"]))
        self.assertEqual(result["ships"][0]["speed_mps"], 0)

    def test_legacy_i9_entry_still_accepts_legacy_control_on_fresh_resources(self):
        service = self.create(); scenario = service.scenario
        scene = initialize_tactical_scene(scenario.bindings, scenario.projectile_catalog, scenario.timing_catalog,
            initial_motion_states={s.ship_id: s.motion_state for s in scenario.scene.ships},
            initial_combat_states={s.ship_id: s.combat_state for s in scenario.scene.ships},
            continuous_damage_profile=scenario.continuous_damage_profile)
        state = initialize_tactical_fleet_command_state(scene, tuning=service.command_tuning, player_side_id="side.blue",
            assignments=service.command_state.assignments, direct_control_ship_id="ship.web.blue")
        control = TacticalDirectControlFrame(TacticalControlInput(move_body=Vec2(0, 1)))
        result = advance_commanded_tactical_scene_step(scene, state, scenario.bindings, scenario.timing_catalog,
            scenario.projectile_catalog, scenario.material_registry, service.command_tuning,
            continuous_damage_profile=scenario.continuous_damage_profile, direct_control=control)
        self.assertEqual(result.scene_resolution.resulting_scene.fixed_step_index, 1)
        self.assertEqual(result.applications[0].source, "player_direct")
        self.assertEqual(result.applications[0].controls, control.controls)

    def advance(self, service, count=60, value=None):
        return service.dispatch(dict(method="tactical.advance", params=dict(scene_id=service.scene_id,
            input=self.input(service) if value is None else value, step_count=count)))

    def test_bounded_preview_matches_individual_steps_without_burning_fuel(self):
        service, baseline = self.create(), self.create()
        initial = canonical_sha256(service.scenario.scene)
        accepted = self.advance(service)
        self.assertFalse(accepted["paused"])
        self.assertEqual(accepted["advance_state"]["executed_steps"], 0)
        self.assertEqual(canonical_sha256(service.scenario.scene), initial)
        for _ in range(60):
            service.advance_one()
            self.step(baseline)
            self.assertEqual(canonical_sha256(service.scenario.scene), canonical_sha256(baseline.scenario.scene))
            self.assertEqual(service.snapshot()["control_state"]["fuel_units"], 800)
        end = service.snapshot()
        self.assertEqual(end["fixed_step"], 60)
        self.assertEqual(end["time_s"], 1)
        self.assertTrue(end["paused"])
        self.assertEqual(end["advance_state"]["status"], "completed")
        self.assertGreater(end["ships"][0]["position_m"][1], -300)
        before = self.before(service)
        service.advance_one()
        self.assert_unchanged(service, before)

    def test_preview_rejections_pause_and_mode_exit_preserve_executed_prefix(self):
        service = self.create()
        for count in (True, 1, 59, 301, 601, 300.0):
            with self.assertRaisesRegex(ContractError, "advance_count"): self.advance(service, count)
        red = self.input(service); red["arguments"]["ship_id"] = "ship.web.red"
        with self.assertRaisesRegex(ContractError, "direct_ship_mismatch"): self.advance(service, value=red)
        value = self.input(service)
        self.advance(service, 300, value)
        with self.assertRaisesRegex(ContractError, "advance_active"): self.advance(service, value=value)
        with self.assertRaisesRegex(ContractError, "advance_active"): self.step(service)
        for _ in range(3): service.advance_one()
        paused = service.dispatch(dict(method="tactical.pause", params=dict(scene_id=service.scene_id)))
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["advance_state"]["executed_steps"], 3)
        before = self.before(service); service.advance_one(); self.assert_unchanged(service, before)
        with self.assertRaisesRegex(ContractError, "input_duplicate"): self.advance(service, value=value)
        self.advance(service)
        service.advance_one()
        service.dispatch(dict(method="tactical.set_mode", params=dict(mode="editor")))
        self.assertEqual(service.advance_state["status"], "stopped")
        before = self.before(service); service.advance_one(); self.assert_unchanged(service, before)
        with self.assertRaisesRegex(ContractError, "mode_required"): self.advance(service)

    def test_failed_preview_keeps_only_successful_steps_and_receipt(self):
        service = self.create(); self.advance(service)
        service.advance_one(); before = self.before(service)
        with patch.object(service, "_snapshot", side_effect=RuntimeError("projection failed")):
            service.advance_one()
        self.assert_unchanged(service, before)
        self.assertEqual(service.advance_state["status"], "failed")
        self.assertEqual(service.advance_state["executed_steps"], 1)
        self.assertTrue(service.snapshot()["paused"])
        self.assertEqual(self.step(service)["fixed_step"], 2)
        self.assertIsNone(service.advance_state)

    def test_lost_preview_acceptance_is_queryable_before_first_step(self):
        service = self.create(); value = self.input(service)
        accepted = self.advance(service, 600, value)
        self.assertEqual(service.snapshot(service.static_sha256), accepted)
        self.assertEqual(accepted["advance_state"]["input_sha256"], canonical_sha256(value))
        service.pause()
        self.assertEqual(service.last_input_seq, 0)
        self.assertEqual(service.advance_state["executed_steps"], 0)
        self.step(service, value)  # zero completed steps did not consume the input

    def test_static_validation_reuse_is_scoped_to_one_step_and_exact_object(self):
        import 高天荒野舰艇实际推进聚合器 as aggregation
        service = self.create(); context = service.scenario.propulsion_context.ship("ship.web.blue").aggregation_context
        original = aggregation.verify_derived_ship_snapshot_fingerprint
        with patch.object(aggregation, "verify_derived_ship_snapshot_fingerprint", wraps=original) as verify:
            with aggregation.propulsion_step_validation_scope():
                context.__post_init__(); context.__post_init__()
                self.assertEqual(verify.call_count, 1)
                replace(context).__post_init__()  # distinct object is independently validated
                self.assertEqual(verify.call_count, 2)
            context.__post_init__(); context.__post_init__()
            self.assertEqual(verify.call_count, 4)
        source = context.snapshot.source_sha256
        try:
            object.__setattr__(context.snapshot, "_source_sha256", "0" * 64)
            with aggregation.propulsion_step_validation_scope(), self.assertRaises(ContractError):
                context.__post_init__()
        finally:
            object.__setattr__(context.snapshot, "_source_sha256", source)

    def test_tactical_resource_projection_has_separate_identity_and_keeps_source_rates(self):
        from backend.high_wilderness_sidecar.tactical_resources import compile_tactical_fuel_resources
        from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, OutfitPlanInput
        service = self.create(); current = service.scenario
        # Use a strict resource as input; a synthetic nonzero rate makes the no-burn boundary explicit.
        value = current.propulsion_context.ship("ship.web.blue").aggregation_context.catalog.to_dict()
        for module in value["modules"]:
            if module["category"] in ("main_engine", "maneuver_thruster"):
                module["capability"]["fuel_units_per_s"] = 2.5
        catalog = ModulePrototypeCatalog.parse(value)
        plan = current.bindings[0].snapshot.outfit.normalized_plan
        before = canonical_sha256(catalog)
        projected, _, manifest = compile_tactical_fuel_resources(catalog, plan)
        self.assertEqual(canonical_sha256(catalog), before)
        self.assertNotEqual(projected.id, catalog.id)
        self.assertEqual(manifest["source_catalog_sha256"], before)
        for module in projected.modules:
            if module.category in ("main_engine", "maneuver_thruster"):
                source = next(m for m in catalog.modules if m.reference.id + ".tactical_no_burn" == module.reference.id)
                self.assertEqual(source.capability.to_dict()["fuel_units_per_s"], 2.5)
                capability = module.capability.to_dict()
                self.assertEqual(capability["fuel_units_per_s"], 0)
                capability["fuel_units_per_s"] = 2.5
                self.assertEqual(capability, source.capability.to_dict())


if __name__ == "__main__": unittest.main()
