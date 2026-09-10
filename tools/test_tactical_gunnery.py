from dataclasses import replace
import json
from math import hypot, pi
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf, persistent_ship as ps
from backend.high_wilderness_sidecar import tactical_gunnery as tg
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_resources_runtime import ResourceOperation
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from tools.test_tactical_scheduler import Clock

ROOT = Path(__file__).resolve().parents[1]
GUN = 'weapon_upper_port'


class GunneryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(ROOT, with_command=True)
        cls.scenario = build_two_ship_scenario(ROOT)
        cls.config = json.loads((ROOT/'contracts/web_bridge/fixtures/p2a-gunnery.json').read_text(encoding='utf-8'))

    def battle(self, **config):
        config = dict(self.config, **config)
        session = tg.prepare_trial_session(self.sample, config)
        return tg.GunneryBattle(session, self.scenario, config)

    def send(self, b, kind='target', **arguments):
        if kind == 'target' and not arguments:
            arguments = dict(ship_id='ship.web.red', module_id=None)
        value = dict(epoch=b.session.world.epoch, generation=0, sequence=b.sequence+1,
                     weapon_id=GUN, kind=kind, arguments=arguments)
        b.submit(value)
        return value

    def advance(self, b, n):
        for _ in range(n):
            b.step()

    def gun(self, b):
        return b.view()['weapons'][0]

    def damage(self, b, module, sequence=1, fraction=1):
        hp = next(m.maximum_durability_points for m in b.session._seeds[0].devices.modules if m.instance_id == module)
        return DeviceOperation(b.session.world.epoch, 'ship.web.blue', module, sequence, 'damage', hp*fraction,
                               b.session.world.fixed_step, 'opening')

    def mode(self, b, target, value, sequence=1):
        return ResourceOperation(b.session.world.epoch, 'ship.web.blue', sequence, 'mode', target, value,
                                 b.session.world.fixed_step, 'opening')

    def test_no_target_no_autonomous_fire(self):
        b = self.battle()
        self.advance(b, 120)
        self.assertTrue(all(s.mode == 'auto' and s.shots == 0 for s in b.states))
        self.assertFalse(b.projectiles)
        self.assertEqual(self.gun(b)['ammo_resources'], 80)

    def test_auto_moving_target_acquires_lock_and_consumes_batches(self):
        b = self.battle()
        command = self.send(b)
        self.advance(b, 90)
        self.assertEqual(self.gun(b)['quality'], 'degraded')
        self.assertGreater(self.gun(b)['shots'], 0)  # unlocked ordinary guns still shoot
        b.step()
        self.assertEqual(self.gun(b)['quality'], 'normal')
        self.assertEqual(self.gun(b)['lock_sources'], ('sensor_upper_starboard',))
        self.advance(b, 220)
        gun = self.gun(b)
        self.assertGreaterEqual(gun['shots'], 3)
        self.assertEqual(gun['ammo_resources'], 80-5*(gun['shots']-1))
        target = b.session.world.ships[1].motion
        self.assertGreater(gun['aim_point_m'][0], target.position_world_m.x)
        self.assertFalse(b.submit(command))
        self.assertEqual(gun, self.gun(b))

    def test_intercept_uses_relative_velocity_and_rejects_unreachable(self):
        aim = tg.intercept((0, 0), (10, 0), (0, 100), (20, 0), 50)
        t = hypot(*aim)/50
        self.assertAlmostEqual(aim[0], 10*t)
        self.assertAlmostEqual(aim[1], 100)
        self.assertIsNone(tg.intercept((0, 0), (0, 0), (0, 100), (0, 60), 50))

    def test_module_aim_uses_sampled_target_pose_and_velocity(self):
        b = self.battle()
        self.send(b, ship_id='ship.web.red', module_id='cic')
        self.advance(b, 105)
        gun = self.gun(b)
        self.assertEqual(gun['target_module_id'], 'cic')
        contact = b._contacts[(0, 1)]
        module = b._modules[1]['cic']
        elapsed = (b.session.world.fixed_step-contact.step)/60
        offset = tg.rotate(module.anchor_m, contact.heading+contact.yaw*elapsed)
        position = tg.add(tg.add(contact.position, (contact.velocity[0]*elapsed, contact.velocity[1]*elapsed)), offset)
        velocity = tg.add(contact.velocity, (-contact.yaw*offset[1], contact.yaw*offset[0]))
        expected = tg.intercept(gun['origin_m'], (0, 0), position, velocity, 500)
        self.assertAlmostEqual(expected[0], gun['aim_point_m'][0])
        self.assertAlmostEqual(expected[1], gun['aim_point_m'][1])

    def test_radar_loss_degrades_immediately_and_firing_continues(self):
        b = self.battle()
        self.send(b); self.advance(b, 95)
        self.assertEqual(self.gun(b)['quality'], 'normal')
        b.step(device_operations=(self.damage(b, 'sensor_upper_starboard'),))
        self.assertEqual(self.gun(b)['quality'], 'degraded')
        self.assertEqual(self.gun(b)['quality_reason'], 'radar_unavailable')
        before = self.gun(b)['shots']
        self.advance(b, 250)
        self.assertGreater(self.gun(b)['shots'], before)
        contact = b._contacts[(0, 1)]
        truth = b.session.world.ships[1].motion
        self.assertNotEqual(contact.velocity, tuple(truth.velocity_world_mps.to_list()))

    def test_lock_cannot_transfer_to_other_target_and_recovers_after_outage(self):
        b = self.battle()
        self.send(b); self.advance(b, 95)
        b.step(resource_operations=(self.mode(b, 'sensor_upper_starboard', 'off'),))
        self.assertEqual(self.gun(b)['quality'], 'degraded')
        b.step(resource_operations=(self.mode(b, 'sensor_upper_starboard', 'active', 2),))
        self.assertEqual(self.gun(b)['quality_reason'], 'acquiring')
        self.advance(b, 90)
        self.assertEqual(self.gun(b)['quality'], 'normal')
        self.send(b, 'clear'); b.step(); self.send(b); b.step()
        self.assertEqual(self.gun(b)['quality_reason'], 'acquiring')

    def test_other_valid_lock_source_survives_primary_loss(self):
        b = self.battle()
        # Technical second source, with real health/power/mode eligibility; use
        # the installed fire-control module as the explicit second radar fixture.
        b._radars[0] = ('sensor_upper_starboard', 'fire_control')
        b._capabilities[0]['fire_control'] = dict(b._capabilities[0]['fire_control'], maximum_instrumented_range_m=50000)
        self.send(b); self.advance(b, 95)
        b.step(device_operations=(self.damage(b, 'sensor_upper_starboard'),))
        self.assertEqual(self.gun(b)['quality'], 'normal')
        self.assertEqual(self.gun(b)['lock_sources'], ('fire_control',))

    def test_unavailable_target_stops_instead_of_using_truth(self):
        b = self.battle(visual_range_m=100)
        self.send(b); self.advance(b, 95)
        b.step(device_operations=(self.damage(b, 'sensor_upper_starboard'),))
        self.assertEqual(self.gun(b)['status'], 'target_unavailable')
        count = self.gun(b)['shots']
        self.advance(b, 180)
        self.assertEqual(self.gun(b)['shots'], count)

    def test_manual_slew_single_click_retry_and_no_automatic_shots(self):
        b = self.battle()
        self.send(b, 'mode', mode='manual')
        self.send(b, 'aim', point=[300, 300])
        b.step()
        self.assertAlmostEqual(self.gun(b)['angle_rad'], b.guns[0].slew)
        self.advance(b, 120)
        self.assertEqual(self.gun(b)['shots'], 0)
        cmd = self.send(b, 'fire', point=[300, 300])
        b.step()
        self.assertEqual(self.gun(b)['shots'], 1)
        p = b.projectiles[-1]
        self.assertLess(abs(tg.wrap(atan_direction(p.velocity)-self.gun(b)['angle_rad'])), b.config['intrinsic_error_mdeg']*tg.RAD+1e-9)
        self.assertFalse(b.submit(cmd))
        self.advance(b, 240)
        self.assertEqual(self.gun(b)['shots'], 1)
        self.assertEqual(self.gun(b)['ready_rounds'], 1)
        self.assertEqual(self.gun(b)['ammo_resources'], 75)

    def test_switch_and_suspend_cancel_pending_manual_shot(self):
        b = self.battle()
        self.send(b, 'mode', mode='manual')
        self.send(b, 'fire', point=[-5, 300])
        self.send(b, 'mode', mode='auto')
        b.step()
        self.assertEqual(self.gun(b)['shots'], 0)
        self.send(b, 'mode', mode='manual')
        self.send(b, 'fire', point=[-5, 300]); b.suspend(); b.step()
        self.assertEqual(self.gun(b)['shots'], 0)
        self.assertIsNone(b.states[0].manual_point)

    def test_outside_arc_and_hull_obstruction_refuse_without_spending(self):
        b = self.battle()
        self.send(b, 'mode', mode='manual')
        self.send(b, 'fire', point=[-5, -900]); b.step()
        self.assertEqual(self.gun(b)['status'], 'out_of_arc')
        b.guns = (replace(b.guns[0], blocked=((350, 360), (0, 10))),)+b.guns[1:]
        self.send(b, 'aim', point=[-5, 300]); self.advance(b, 30)
        self.send(b, 'fire', point=[-5, 300]); b.step()
        self.assertEqual(self.gun(b)['status'], 'hull_blocked')
        self.assertEqual(self.gun(b)['ready_rounds'], 1)
        self.assertEqual(self.gun(b)['ammo_resources'], 80)

    def test_real_upper_hull_arc_precompiled_at_entry(self):
        b = self.battle()
        # Verify exact interval predicate, including the 0/360 tangent seam.
        gun = replace(b.guns[0], blocked=((10, 20), (350, 360)))
        for angle in (10, 15, 20, 350, 359, 0):
            self.assertTrue(b._hull_blocked(gun, angle*pi/180))
        self.assertFalse(b._hull_blocked(gun, 21*pi/180))

    def test_damage_and_power_failure_precede_reload_completion(self):
        for failure in ('damage', 'power'):
            with self.subTest(failure=failure):
                b = self.battle()
                self.send(b, 'mode', mode='manual'); self.send(b, 'fire', point=[-5, 300]); b.step()
                self.assertEqual(self.gun(b)['shots'], 1)
                self.advance(b, 119)
                if failure == 'damage':
                    b.step(device_operations=(self.damage(b, GUN),))
                else:
                    b.step(resource_operations=(self.mode(b, GUN, 'off'),))
                self.assertEqual(self.gun(b)['ready_rounds'], 0)
                self.assertEqual(self.gun(b)['ammo_resources'], 80)
                self.assertEqual(self.gun(b)['reload_steps'], 0)

    def test_project_failure_rolls_back_flight_inventory_shots_contacts_and_rng(self):
        b = self.battle()
        self.send(b, 'mode', mode='manual'); self.send(b, 'fire', point=[-5, 300])
        world, states = b.session.world, b.states
        inventory = tuple(i.checkpoint() for i in b.inventory.inventories)
        def fail(*_):
            raise RuntimeError('view failed')
        with self.assertRaises(RuntimeError):
            b.step(project=fail)
        self.assertIs(b.session.world, world)
        self.assertEqual(states, b.states)
        self.assertEqual(inventory, tuple(i.checkpoint() for i in b.inventory.inventories))
        self.assertFalse(b.projectiles)
        b.step(); self.assertEqual(len(b.projectiles), 1)

    def test_replayable_noise_and_hot_loop_has_no_static_parsing(self):
        a, b = self.battle(), self.battle()
        self.send(a); self.send(b)
        with patch.object(ps, '_validate', side_effect=AssertionError('full validation')), \
             patch.object(ps, 'canonical_sha256', side_effect=AssertionError('fingerprint')), \
             patch.object(tg, 'horizontal_fire_arc', side_effect=AssertionError('arc compile')), \
             patch.object(ps, 'decode', side_effect=AssertionError('JSON')):
            self.advance(a, 300); self.advance(b, 300)
        self.assertEqual(a.states, b.states)
        self.assertEqual(a.projectiles, b.projectiles)

    def test_bounded_projectiles_and_empty_reserves(self):
        b = self.battle(max_projectiles=1, projectile_lifetime_steps=500, initial_ammo=0)
        self.send(b); self.advance(b, 300)
        self.assertEqual(len(b.projectiles), 1)
        self.assertEqual(self.gun(b)['shots'], 1)
        self.assertEqual(self.gun(b)['ammo_resources'], 0)
        self.advance(b, 300)
        self.assertEqual(len(b.projectiles), 0)
        self.assertEqual(self.gun(b)['shots'], 1)

    def test_invalid_commands_cannot_reset_receipts_or_change_enemy_weapon(self):
        b = self.battle()
        value = self.send(b)
        for changed in (dict(value, sequence=4), dict(value, epoch='old'), dict(value, weapon_id='cic'),
                        dict(value, arguments=dict(ship_id='ship.web.blue', module_id=None))):
            with self.assertRaises(ps.ContractError):
                b.submit(changed)
        self.assertEqual(b.sequence, 1)
        with self.assertRaises(ps.ContractError):
            self.send(b, 'fire', point=[0, 0])


def atan_direction(velocity):
    from math import atan2
    return atan2(velocity[0], velocity[1])


class GunBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(ROOT, with_command=True)
        cls.scenario = build_two_ship_scenario(ROOT)

    def service(self):
        clock = Clock()
        s = RealtimeViewService('backend.gun_test', clock=clock)
        with patch('backend.high_wilderness_sidecar.realtime_view.build_sample_session', return_value=self.sample), \
             patch('backend.high_wilderness_sidecar.realtime_view.build_two_ship_scenario', return_value=self.scenario):
            self.call(s, 'create', dict(scenario_id='gtw.sample.web.two_ship.v1'))
        self.call(s, 'resume')
        return s, clock

    def call(self, s, kind, params=None):
        return s.dispatch(dict(method='tactical.realtime.'+kind, params=params or dict(scene_id=s.scheduler.world.epoch)), mode='tactical')

    def command(self, s, **change):
        return dict(epoch=s.scheduler.world.epoch, generation=s.scheduler.status.generation,
            sequence=s.gunnery.sequence+1, weapon_id=GUN, kind='target',
            arguments=dict(ship_id='ship.web.red', module_id=None), **change)

    def test_gun_reply_retry_pause_generation_and_read_does_not_fire(self):
        s, clock = self.service()
        value = self.command(s)
        self.call(s, 'gun', dict(scene_id=value['epoch'], input=value))
        self.assertEqual(s.scheduler.world.fixed_step, 0)
        self.assertFalse(s.gunnery.projectiles)
        self.call(s, 'pause')
        result = self.call(s, 'gun', dict(scene_id=value['epoch'], input=value))
        self.assertEqual(result['view']['gunnery']['command_sequence'], 1)
        old = dict(value, sequence=2)
        with self.assertRaises(ps.ContractError):
            self.call(s, 'gun', dict(scene_id=value['epoch'], input=old))
        self.call(s, 'resume')
        with self.assertRaises(ps.ContractError):
            self.call(s, 'gun', dict(scene_id=value['epoch'], input=old))
        self.assertEqual(s.gunnery.sequence, 1)

    def test_worker_pumps_guns_and_preserves_pause_clock(self):
        s, clock = self.service()
        value = self.command(s)
        self.call(s, 'gun', dict(scene_id=value['epoch'], input=value))
        for _ in range(60):
            clock.advance(16666667); s.tick()
        self.assertGreater(s.gunnery.states[0].shots, 0)
        self.call(s, 'pause')
        before = s.gunnery.view()
        clock.advance(500000000); s.tick()
        self.assertEqual(before, s.gunnery.view())


if __name__ == '__main__':
    unittest.main()
