"""Real counter-thrust, zero-rate settling, and independent translation."""
from dataclasses import replace
from pathlib import Path
import unittest
from backend.high_wilderness_sidecar.simplified_flight import build_sample_session
from 高天荒野舰艇定向推进控制桥 import directional_control, DirectionalPropulsionControlInput, ChannelPropulsionCommand

ROOT = Path(__file__).resolve().parents[1]


class YawBrakeTests(unittest.TestCase):
    def test_old_control_roundtrip_stays_v2_and_stabilization_is_explicit_v3(self):
        old = directional_control()
        self.assertEqual(old.to_dict()['interface'], 'gaotian.tactical-propulsion-control/v2alpha1')
        self.assertNotIn('automatic_yaw_brake', old.to_dict())
        new = directional_control(automatic_yaw_brake=True)
        self.assertEqual(DirectionalPropulsionControlInput.parse(new.to_dict()), new)
        with self.assertRaises(ValueError):
            replace(directional_control((ChannelPropulsionCommand('yaw.clockwise', None, 50),)), automatic_yaw_brake=True)

    def test_turn_toggle_brakes_both_directions_without_erasing_translation(self):
        for direction in ('yaw.clockwise', 'yaw.counterclockwise'):
            session = build_sample_session(ROOT, with_command=True)
            translation = ChannelPropulsionCommand('translation.forward', 'half', None)
            turn = directional_control((translation, ChannelPropulsionCommand(direction, None, 50)))
            for _ in range(90): session.step(turn)
            index = next(i for i, s in enumerate(session.world.ships) if s.ship_id == session._direct)
            before = session.world.ships[index].motion.yaw_rate_radps
            self.assertGreater(abs(before), .001)
            brake = directional_control((translation,), automatic_yaw_brake=True)
            session.step(brake)
            self.assertNotEqual(session.world.ships[index].motion.yaw_rate_radps, 0.)
            counter = False
            for _ in range(1800):
                session.step()
                ship = session.world.ships[index]
                counter |= ship.propulsion.output_percent_units[5 if before > 0 else 4] > 0
                if ship.motion.yaw_rate_radps == 0 and not any(ship.propulsion.output_percent_units[4:]): break
            self.assertTrue(counter)
            self.assertEqual(ship.motion.yaw_rate_radps, 0.)
            self.assertEqual(ship.control.channel_commands[0].commanded_notch, 'half')
            for _ in range(120): session.step()
            self.assertEqual(session.world.ships[index].motion.yaw_rate_radps, 0.)

    def test_missing_counterthrusters_cannot_magically_stop_rotation(self):
        from backend.high_wilderness_sidecar.tactical_yaw_brake import requested
        session = build_sample_session(ROOT, with_command=True)
        ship = session.world.ships[0]
        ship = replace(ship, motion=replace(ship.motion, yaw_rate_radps=.2))
        state = replace(ship.propulsion, available_units=(0,)*6, output_percent_units=(0,)*6)
        self.assertEqual(requested(ship, state, session._seeds[0]), (0, 0))

    def test_braking_checkpoint_continues_the_same_physical_motion(self):
        from backend.high_wilderness_sidecar import tactical_checkpoint as cp
        session = build_sample_session(ROOT, with_command=True)
        turn = directional_control((ChannelPropulsionCommand('yaw.clockwise', None, 50),))
        for _ in range(90): session.step(turn)
        session.step(directional_control(automatic_yaw_brake=True))
        restored = cp.loads(cp.dumps(session), session._seeds, session._profile,
            direct_ship_id=session._direct, allow_test_device_rebuild=session._allow_test_device_rebuild)
        self.assertEqual(session.world.ships, restored.world.ships)
        for _ in range(180):
            session.step(); restored.step()
            self.assertEqual(session.world.ships, restored.world.ships)


if __name__ == '__main__': unittest.main()
