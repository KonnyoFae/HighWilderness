from dataclasses import replace
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf
from tools import test_simplified_propulsion as fixtures
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇推进安全判定器 import PropulsionSafetyProfile
from 高天荒野舰艇推进时间内核 import advance_propulsion_time_boundary, PropulsionTimeCommand
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand
from 高天荒野舰艇定向推进控制桥 import directional_control

ROOT = Path(__file__).resolve().parents[1]
SAFE = PropulsionSafetyProfile("fixture.safety", 1, .9, 1000, 900, 3)


def command(channel="translation.forward", notch="full", yaw=None):
    return directional_control((ChannelPropulsionCommand(channel, notch if yaw is None else None, yaw),))


class SimplifiedFlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample = sf.build_sample_session(ROOT)
        seed = sample._seeds[0]
        cls.model = replace(seed.model, runtime=sf.MotionParameters(100, 200, 1000, 1000, True),
            structure_points_body_m=(sf.dynamics.Vec2(),))
        cls.motion = replace(seed.motion, position_world_m=sf.dynamics.Vec2(),
            velocity_world_mps=sf.dynamics.Vec2(), heading_rad=0, yaw_rate_radps=0)
        cls.zero_drag = sf.dynamics.calculate_tactical_drag(cls.model, cls.motion)

    def session(self, rows=None, *, ready=True, blockers=(), model=None):
        table = fixtures.compile_rows([fixtures.engine("a", 100)] if rows is None else rows)
        return sf.SimplifiedFlightSession((sf.ShipSeed(table, model or self.model, self.motion, ready, blockers),),
            SAFE, direct_ship_id="fixture.ship")

    def event(self, s, *, engine="a", reason="actuator_destroyed", active=True, version=1, phase="opening"):
        return sf.AvailabilityEvent(s.world.epoch, "fixture.ship", engine, reason, active, version,
            s.world.fixed_step + (phase == "closing"), phase)

    def run_steps(self, s, n, control=None):
        with patch.object(sf.dynamics, "calculate_tactical_drag", return_value=self.zero_drag):
            for i in range(n):
                s.step(control if i == 0 else None)

    def test_shared_time_rules_match_strict_reference_with_midcourse_changes(self):
        for category in ("main_engine", "maneuver_thruster"):
            design = fixtures.engine("a", 100, category=category, point=(1, 0), response=1.01)
            table = fixtures.compile_rows([design])
            kernel = sf.PropulsionKernel(table)
            state = kernel.initial(False, ())
            reference = state.engines[0].engine
            d = 0 if category == "main_engine" else 4
            for n in range(240):
                target = 100 if n < 37 or n >= 90 else 25 if n < 61 else 0
                requests = tuple(target if i == d else 0 for i in range(6))
                time_command = PropulsionTimeCommand.main_engine(sf.NOTCH[target]) if d == 0 else PropulsionTimeCommand.maneuver_thruster(target)
                reference = advance_propulsion_time_boundary(reference, design.capability, n, time_command).state
                state, _ = kernel.boundary(state, n, requests, ())
                self.assertEqual(state.engines[0].engine, reference, (category, n))

    def test_weighted_damage_and_recovery_with_multiple_reasons(self):
        s = self.session([fixtures.engine("a", 40), fixtures.engine("b", 30), fixtures.engine("c", 30)])
        self.run_steps(s, 120, command())
        s.step(events=(self.event(s, engine="b"), self.event(s, engine="b", reason="power_unavailable")))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        s.step(events=(self.event(s, engine="b", active=False, version=2),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 70)
        s.step(events=(self.event(s, engine="b", reason="power_unavailable", active=False, version=2),))
        state = s.world.ships[0].propulsion
        self.assertEqual(state.available_units[0], 100)
        self.assertEqual(state.engines[1].engine.phase, "starting")
        self.assertEqual(state.output_percent_units[0], 7000)
        self.run_steps(s, 119)
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units[0], 10000)

    def test_duplicate_stale_conflicting_and_atomic_coalescing(self):
        s = self.session()
        e = self.event(s, version=2)
        s.step(events=(e, e))
        s.step(events=(self.event(s, active=False, version=1),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 0)
        before = s.world
        with self.assertRaises(ContractError):
            s.step(events=(self.event(s, active=False, version=2),))
        self.assertIs(s.world, before)
        # Same boundary ends with a power block; no temporary restoration/startup.
        s.step(events=(self.event(s, active=False, version=3), self.event(s, reason="power_unavailable")))
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units[0], 0)
        self.assertFalse(any(f[2][1] == "engine_start_requested" for f in s.last_result.events if len(f) == 3))

    def test_closing_damage_delivers_current_interval_opening_damage_does_not(self):
        for phase in ("opening", "closing"):
            s = self.session()
            self.run_steps(s, 120, command())
            before = s.world.ships[0].motion
            with patch.object(sf.dynamics, "calculate_tactical_drag", return_value=self.zero_drag):
                s.step(events=(self.event(s, phase=phase),))
                after = s.world.ships[0].motion
                self.assertAlmostEqual(after.velocity_world_mps.y, before.velocity_world_mps.y + (1 / 60 if phase == "closing" else 0))
                self.assertAlmostEqual(after.position_world_m.y, before.position_world_m.y + after.velocity_world_mps.y / 60)
                s.step()
                self.assertEqual(s.world.ships[0].motion.velocity_world_mps, after.velocity_world_mps)
            self.assertEqual(s.world.ships[0].propulsion.schedule, ())
            self.assertEqual(after.fuel_units, before.fuel_units)

    def test_destroyed_on_due_boundary_cannot_emit_ghost_upstage(self):
        s = self.session()
        s.step(command())
        self.assertEqual(s.world.ships[0].propulsion.schedule[0][0], 2)
        s.step(events=(self.event(s, phase="closing"),))
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units, (0,) * 6)
        self.assertEqual(s.world.ships[0].propulsion.schedule, ())

    def test_turning_damage_is_independent_and_main_has_no_bias(self):
        s = self.session([fixtures.engine("a", 100, point=(-50, 0)),
            fixtures.engine("left", 20, category="maneuver_thruster", point=(5, 0)),
            fixtures.engine("right", 30, category="maneuver_thruster", point=(-5, 0))])
        self.run_steps(s, 120, command())
        self.assertEqual(s.world.ships[0].motion.yaw_rate_radps, 0)
        s.step(events=(self.event(s, engine="left"),))
        self.assertEqual(s.world.ships[0].propulsion.available_units, (100, 0, 0, 0, 0, 150))
        self.run_steps(s, 120, command("yaw.clockwise", yaw=100))
        self.assertLess(s.world.ships[0].motion.yaw_rate_radps, 0)

    def test_reverse_waits_until_opposing_output_zero(self):
        s = self.session([fixtures.engine("a", 100), fixtures.engine("b", 100, direction=(0, -1))])
        self.run_steps(s, 120, command())
        for n in range(150):
            s.step(command("translation.reverse") if n == 0 else None)
            output = s.world.ships[0].propulsion.output_percent_units
            self.assertFalse(output[0] and output[1])
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units[:2], (0, 10000))

    def test_stable_engines_and_schedule_reused_without_parsing_or_recompilation(self):
        s = self.session()
        self.run_steps(s, 120, command())
        old = s.world.ships[0].propulsion
        with patch.object(sf, "_apply_command", side_effect=AssertionError("Repeated command")), \
             patch.object(sf, "_commit_due_transition", side_effect=AssertionError("Spurious deadline")), \
             patch.object(sf.EngineRuntimeState, "parse", side_effect=AssertionError("Engine parse")), \
             patch.object(sf, "canonical_sha256", side_effect=AssertionError("Hot hash")):
            self.run_steps(s, 30)
        self.assertIs(s.world.ships[0].propulsion, old)

    def test_failed_projection_and_invalid_events_preserve_committed_world(self):
        s = self.session()
        self.run_steps(s, 120, command())
        before, last = s.world, s.last_result
        with self.assertRaisesRegex(RuntimeError, "projection"):
            s.step(events=(self.event(s),), project=lambda *_: (_ for _ in ()).throw(RuntimeError("projection")))
        self.assertIs(s.world, before)
        self.assertIs(s.last_result, last)
        for e in (replace(self.event(s), epoch="foreign"), replace(self.event(s), fixed_step=999),
                  replace(self.event(s), reason="unknown"), replace(self.event(s), active=1)):
            with self.assertRaises(ContractError):
                s.step(events=(e,))
            self.assertIs(s.world, before)
        s.step(events=(self.event(s),))
        self.assertEqual(s.world.ships[0].propulsion.available_units[0], 0)

    def test_authority_loss_clears_command_and_requires_new_command_after_restore(self):
        s = self.session()
        self.run_steps(s, 10, command())
        e = sf.AuthorityEvent(s.world.epoch, "fixture.ship", False, 1, s.world.fixed_step, "opening")
        s.step(authority_events=(e,))
        with self.assertRaises(ContractError):
            s.step(command())
        s.step(authority_events=(replace(e, allowed=True, version=2, fixed_step=s.world.fixed_step),))
        self.assertEqual(s.world.ships[0].control, directional_control())
        with self.assertRaises(ContractError):
            s.step(command(), ship_id="enemy")

    def test_joint_safety_veto_release_and_overg(self):
        table = fixtures.compile_rows([fixtures.engine("a", 100), fixtures.engine("b", 100)])
        kernel = sf.PropulsionKernel(table)
        state = kernel.initial(True, ())
        def load(outputs):
            return sf.dynamics.LoadMetrics(outputs[0] / 3, 1, sf.dynamics.Vec2(), 0)
        state, _ = kernel.boundary(state, 0, (100, 0, 0, 0, 0, 0), ())
        state, _ = kernel.boundary(state, 2, (100, 0, 0, 0, 0, 0), (), load=load, profile=SAFE)
        self.assertEqual(state.output_percent_units[0], 0)
        self.assertEqual(state.governors[0].ceiling, 0)
        for n in (3, 4, 5):
            state, _ = kernel.boundary(state, n, (100, 0, 0, 0, 0, 0), (), load=load, profile=SAFE)
        self.assertEqual(state.governors[0].ceiling, 100)
        state, _ = kernel.boundary(state, 7, (100, 0, 0, 0, 0, 0), (), load=load, profile=SAFE, overg=True)
        self.assertGreater(state.output_percent_units[0], 0)

    def test_compact_safety_matches_existing_whole_vector_governor(self):
        from 高天荒野舰艇整舰推进安全判定 import (
            WholeShipActuatorBoundary, WholeShipPropulsionLoadSample, evaluate_whole_ship_propulsion_safety,
        )
        from 高天荒野舰艇受控推进时间边界 import preview_governed_propulsion_time_boundary
        from 高天荒野舰艇推进安全判定器 import PropulsionHardAvailability
        from 高天荒野舰艇推进通道合同 import DirectionalPropulsionGovernorState
        designs = [fixtures.engine("a", 100), fixtures.engine("b", 100)]
        kernel = sf.PropulsionKernel(fixtures.compile_rows(designs))
        state = kernel.initial(True, ())
        engines = tuple(s.engine for s in state.engines)
        governors = tuple(DirectionalPropulsionGovernorState.initial(c) for c in sf.DIRECTIONAL_CHANNELS)
        for n in range(140):
            requested = command(notch="quarter" if 90 <= n < 110 else "full")
            target = requested.channel_commands[0].requested_percent
            gain = 1 if n < 70 else .2
            rows = tuple(WholeShipActuatorBoundary("translation.forward", design.capability,
                preview_governed_propulsion_time_boundary(e, design.capability, n,
                    PropulsionTimeCommand.main_engine(sf.NOTCH[target])), PropulsionHardAvailability())
                for design, e in zip(designs, engines))
            def old_load(vector):
                return WholeShipPropulsionLoadSample("a" * 64, vector, sum(v for _, v in vector.outputs) / 100 * gain, 1)
            old = evaluate_whole_ship_propulsion_safety(SAFE, governors, requested, rows,
                fixed_step_index=n, load_context_sha256="a" * 64, load_evaluator=old_load, crew_safety_lock_enabled=True)
            def new_load(outputs):
                return sf.dynamics.LoadMetrics(outputs[0] / 10000 * gain, 1, sf.dynamics.Vec2(), 0)
            state, _ = kernel.boundary(state, n, (target, 0, 0, 0, 0, 0), (), load=new_load, profile=SAFE)
            engines = tuple(r.state for r in old.engine_results)
            governors = old.governors
            self.assertEqual(tuple(s.engine for s in state.engines), engines, n)
            self.assertEqual(tuple((g.ceiling, g.reasons, g.limited_since, g.release_since) for g in state.governors),
                tuple((g.safety_ceiling_percent, g.safety_reasons, g.safety_limited_since_step, g.release_candidate_since_step)
                      for g in governors), n)

    def test_crew_limit_and_unsafe_motion_are_not_skipped_without_commands(self):
        s = self.session(model=replace(self.model, runtime=replace(self.model.runtime, safe_longitudinal_mps2=.001)))
        limited_boundaries = 0
        for n in range(60):
            self.run_steps(s, 1, command() if n == 0 else None)
            limited_boundaries += "structure_limit" in s.world.ships[0].propulsion.governors[0].reasons
            self.assertEqual(s.world.ships[0].propulsion.output_percent_units[0], 0)
        self.assertGreater(limited_boundaries, 0)
        kernel = sf.PropulsionKernel(fixtures.compile_rows([fixtures.engine("a", 100)]))
        state = kernel.initial(True, ())
        state, _ = kernel.boundary(state, 0, (100, 0, 0, 0, 0, 0), ())
        crew_profile = PropulsionSafetyProfile("fixture.crew", 1, .9, 2, 1.5, 3)
        def load(outputs):
            return sf.dynamics.LoadMetrics(0, 3 if outputs[0] else 1, sf.dynamics.Vec2(), 0)
        limited, _ = kernel.boundary(state, 2, (100, 0, 0, 0, 0, 0), (), load=load, profile=crew_profile)
        unlocked, _ = kernel.boundary(state, 2, (100, 0, 0, 0, 0, 0), (), load=load, profile=crew_profile, crew_lock=False)
        self.assertEqual(limited.output_percent_units[0], 0)
        self.assertGreater(unlocked.output_percent_units[0], 0)

    def test_event_sequence_matches_independent_full_sums_without_drift(self):
        rows = [fixtures.engine("a", .1), fixtures.engine("b", .2), fixtures.engine("c", .3),
            fixtures.engine("left", .7, category="maneuver_thruster", point=(.3, 0))]
        s = self.session(rows)
        table = s._seeds[0].contributions
        flags = {(e.instance_id, r): False for e in table.engines for r in sf.REASONS[:-1]}
        rng = random.Random(912)
        for n in range(400):
            key = rng.choice(tuple(flags))
            flags[key] = not flags[key]
            event = self.event(s, engine=key[0], reason=key[1], active=flags[key], version=n + 1,
                phase="opening" if n % 2 else "closing")
            s.step(command() if n == 0 else None, events=(event, event))
            state = s.world.ships[0].propulsion
            expected = tuple(sum(e.contribution_units[d] for e in table.engines
                if not any(flags[e.instance_id, r] for r in sf.REASONS[:-1])) for d in range(6))
            self.assertEqual(state.available_units, expected)
            actual = tuple(sum(e.contribution_units[d] * slot.engine.actual_output_percent
                for e, slot in zip(table.engines, state.engines)) for d in range(6))
            self.assertEqual(state.output_percent_units, actual)
            self.assertLessEqual(len(state.schedule), len(table.engines))
            for e, slot in zip(table.engines, state.engines):
                if any(flags[e.instance_id, r] for r in sf.REASONS[:-1]):
                    self.assertEqual(slot.engine.actual_output_percent, 0)
                    self.assertIsNone(slot.engine.next_transition_step)

    def test_automatic_brake_and_empty_ship(self):
        s = self.session([fixtures.engine("a", 100), fixtures.engine("reverse", 100, direction=(0, -1))])
        self.run_steps(s, 120, command())
        self.run_steps(s, 200, directional_control(automatic_brake=True))
        self.assertTrue(s.world.ships[0].control.automatic_brake)
        self.assertEqual(s.world.ships[0].propulsion.output_percent_units[:2], (0, 2500))
        speed = s.world.ships[0].motion.velocity_world_mps.y
        self.run_steps(s, 10)
        self.assertAlmostEqual(s.world.ships[0].motion.velocity_world_mps.y, speed - .25 * 10 / 60)
        empty = self.session([])
        self.run_steps(empty, 5, command())
        self.assertEqual(empty.world.ships[0].propulsion.output_percent_units, (0,) * 6)

    def test_reentrant_projection_and_numeric_failure_do_not_commit(self):
        s = self.session()
        before = s.world
        with self.assertRaises(ContractError):
            s.step(command(), project=lambda *_: s.step())
        self.assertIs(s.world, before)
        with patch.object(sf.dynamics, "_load_metrics", return_value=sf.dynamics.LoadMetrics(float("nan"), 1, sf.dynamics.Vec2(), 0)):
            with self.assertRaises(ContractError):
                s.step(command())
        self.assertIs(s.world, before)

    def test_zero_hull_closes_propulsion_and_authority(self):
        s = self.session()
        self.run_steps(s, 120, command())
        original = sf.dynamics._integrate_delivered_actuation
        def collapse(*args):
            motion, diagnostic = original(*args)
            return replace(motion, hull_integrity_fraction=0), diagnostic
        with patch.object(sf.dynamics, "_integrate_delivered_actuation", side_effect=collapse):
            s.step()
        ship = s.world.ships[0]
        self.assertEqual(ship.propulsion.output_percent_units, (0,) * 6)
        self.assertEqual(ship.propulsion.available_units, (0,) * 6)
        self.assertFalse(ship.authority_allowed)
        with self.assertRaises(ContractError):
            s.step(command())


if __name__ == "__main__":
    unittest.main()
