"""P2a.1a metadata lifetime, phase ownership and real battle transactions."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar.tactical_target_roster import TargetRoster, Handle, Access
from tools import test_aviation_flight as flight_fixture
from tools import test_tactical_observation as observation_fixture
from tools.tactical_7c_oracle import snapshot, difference


def world():
    ships = tuple(NS(ship_id=key, command=NS(lifecycle=NS(physical_status='operational')),
                     motion=NS(height_layer='upper', hull_integrity_fraction=1., position=(0., 0.)))
                  for key in ('same', 'enemy'))
    return NS(epoch='scene.roster', fixed_step=0, ships=ships)


def projectile(identity, *, layer='upper', durability=10., missile=None):
    return NS(id=identity, ship_id='enemy', height_layer=layer, durability=durability,
              aircraft_body=False, missile=missile, position=(0., 1.))


def flight(identity='same', *, layer='upper', hp=50.):
    return dict(id=identity, owner=0, layer=layer, hp=hp, position=(0., 2.))


def inventories():
    return tuple(NS(_value={'aviation': {'manifest': {'aircraft': [dict(id='same', model_id='f1')]}}},
                    _definition={'aviation': {'catalog': {'aircraft': [dict(id='f1', name='F1')]},
                                              'combat': {'payloads': {
                                                  'small_bomb': dict(motion='bomb'),
                                                  'small_guided_bomb': dict(motion='guided_bomb'),
                                                  'small_missile': dict(motion='missile')}}}}) for _ in range(2))


def assert_roster(test, b):
    """Independent oracle from the current authority, including empty endings."""
    expected = {}
    if not b.ending:
        for n, s in enumerate(b.session.world.ships):
            if s.command.lifecycle.physical_status == 'operational' and s.motion.hull_integrity_fraction > 0:
                expected[('ship', s.ship_id)] = (b._sides[n], s.motion.height_layer)
        sides = {s.ship_id: b._sides[n] for n, s in enumerate(b.session.world.ships)}
        for p in b.projectiles:
            if p.durability is not None and p.durability > 0 and not p.aircraft_body:
                expected[('projectile', p.id)] = (sides[p.ship_id], p.height_layer)
        for key, f in b.aviation.flights.items():
            if f['hp'] > 0:
                expected[('aircraft', key)] = (b._sides[f['owner']], f['layer'])
    frame = b.target_roster.frame
    actual = {(r.handle.family, r.handle.target_id): (r.side, r.layer) for r in frame.query()}
    test.assertEqual(actual, expected)
    test.assertEqual(frame.epoch, b.session.world.epoch)


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.world = world(); self.inventories = inventories()
        self.roster = TargetRoster(self.world, ('blue', 'red'), {'same': '同名舰', 'enemy': '同名舰'})
        self.roster.rebuild(self.world, (), {}, self.inventories)

    def sync(self, projectiles=(), flights=None, candidate=None):
        p = self.roster.begin()
        p.synchronize(candidate or self.world, projectiles, flights or {}, self.inventories)
        self.roster.commit(p)
        return self.roster.frame

    def test_names_are_not_identity_and_families_and_id_types_are_distinct(self):
        frame = self.sync((projectile('same'), projectile(1), projectile('1')), {'same': flight()})
        self.assertEqual(len(list(frame.query())), 6)
        self.assertEqual(frame.get(Handle('ship', 'same')).name, '同名舰')
        self.assertEqual(frame.get(Handle('projectile', 'same')).category, 'shell')
        self.assertEqual(frame.get(Handle('aircraft', 'same')).category, 'aircraft')
        self.assertIsNot(frame.get(Handle('projectile', 1)), frame.get(Handle('projectile', '1')))
        with self.assertRaises(TypeError):frame.families[0].by_id['new'] = None

    def test_motion_and_nonlethal_damage_keep_frame_rows_and_groups(self):
        p = projectile(1); f = flight()
        first = self.sync((p,), {'same': f})
        for _ in range(5):
            p = deepcopy(p); p.position = (123., 456.); p.durability -= 1
            f = {**f, 'position': (9., 8.), 'hp': f['hp'] - 1}
            self.world.ships[0].motion.position = (100., 100.)
            self.assertIs(self.sync((p,), {'same': f}), first)
        self.assertIs(self.roster.frame.families[1].groups, first.families[1].groups)

    def test_group_queries_preserve_source_order_and_read_actual_layer(self):
        first = self.sync((projectile(2, layer='cloud'), projectile(1), projectile(3, layer='cloud')))
        rows = list(first.query(exclude_side='blue', layers=('upper', 'cloud'), categories=('shell',)))
        self.assertEqual([r.handle.target_id for r in rows], [2, 1, 3])
        second = self.sync((projectile(2, layer='rain'), projectile(1), projectile(3, layer='cloud')))
        self.assertEqual([r.handle.target_id for r in second.query(layers=('cloud',), categories=('shell',))], [3])
        self.assertEqual(first.get(Handle('projectile', 2)).layer, 'cloud')
        self.assertIs(first.get(Handle('projectile', 1)), second.get(Handle('projectile', 1)))
        self.assertIs(first.families[0], second.families[0])

    def test_removed_dead_and_ineligible_targets_do_not_accumulate(self):
        for identity in range(40):
            frame = self.sync((projectile(identity), projectile('physical', durability=None)), {'same': flight()})
            self.assertEqual(len(frame.families[1].rows), 1)
            self.assertIsNone(frame.get(Handle('projectile', 'physical')))
            frame = self.sync((projectile(identity, durability=0.),), {'same': flight(hp=0.)})
            self.assertFalse(frame.families[1].rows); self.assertFalse(frame.families[2].rows)
        candidate = deepcopy(self.world)
        candidate.ships[0].command.lifecycle.physical_status = 'exited'
        candidate.ships[1].motion.hull_integrity_fraction = 0.
        self.assertFalse(list(self.sync(candidate=candidate).query()))

    def test_bombs_use_payload_definition_without_changing_legacy_objects(self):
        items = []
        for n, key in enumerate(('small_bomb', 'small_guided_bomb', 'small_missile')):
            items.append(projectile(n, missile=NS(profile=NS(model_id='gtw.aviation.weapon.' + key))))
        before = deepcopy(items)
        frame = self.sync(items)
        self.assertEqual([r.category for r in frame.families[1].rows], ['bomb', 'bomb', 'missile'])
        self.assertEqual(items, before)

    def test_candidate_and_same_step_access_never_read_the_committed_pose(self):
        original = self.roster.frame
        a = deepcopy(self.world); a.ships[0].motion.position = (10., 0.)
        b = deepcopy(a); b.ships[0].motion.position = (20., 0.)
        p = self.roster.begin()
        first = p.bind(a, (), {}, self.inventories)
        second = p.bind(b, (), {}, self.inventories)
        row = original.get(Handle('ship', 'same'))
        self.assertIs(first.frame, second.frame)
        self.assertEqual(first.resolve(row).motion.position, (10., 0.))
        self.assertEqual(second.resolve(row).motion.position, (20., 0.))
        self.assertIs(self.roster.frame, original)
        b.ships[0].motion.height_layer = 'cloud'
        third = p.bind(b, (), {}, self.inventories)
        with self.assertRaises(ValueError):third.resolve(row)
        self.assertEqual(original.get(row.handle).layer, 'upper')
        self.assertEqual(third.frame.get(row.handle).layer, 'cloud')

    def test_failed_stale_foreign_and_closed_plans_do_not_change_roster(self):
        before = self.roster.frame
        failed = self.roster.begin()
        with self.assertRaises(ValueError):failed.projectiles((projectile(1), projectile(1)), self.inventories)
        self.assertIs(self.roster.frame, before)
        stale = self.roster.begin(); self.sync((projectile(3),))
        with self.assertRaises(ValueError):self.roster.commit(stale)
        foreign = TargetRoster(self.world, ('blue', 'red'), {}).begin()
        with self.assertRaises(ValueError):self.roster.commit(foreign)
        done = self.roster.begin(); self.roster.commit(done)
        with self.assertRaises(ValueError):done.clear()
        other = deepcopy(self.world); other.epoch = 'other'
        with self.assertRaises(ValueError):self.roster.begin().bind(other, (), {}, self.inventories)
        with self.assertRaises(ValueError):Access(before, other, (), {})

    def test_relaunch_rebuild_and_second_battle_do_not_reuse_entity_references(self):
        first = self.sync(flights={'same': flight()}); old = first.get(Handle('aircraft', 'same'))
        self.sync()
        airborne = {'same': {**flight(), 'position': (999., 0.)}}
        plan = self.roster.begin(); access = plan.bind(self.world, (), airborne, self.inventories)
        row = access.frame.get(old.handle)
        self.assertIsNot(row, old); self.assertIs(access.resolve(row), airborne['same'])
        self.roster.commit(plan)
        self.roster.rebuild(self.world, (), airborne, self.inventories)
        self.assertEqual(list(self.roster.frame.query()), list(access.frame.query()))
        other = deepcopy(self.world); other.epoch = 'second'
        second = TargetRoster(other, ('blue', 'red'), {})
        second.rebuild(other, (), {}, self.inventories)
        self.assertFalse(second.frame.families[2].rows)
        plan = self.roster.begin(); plan.clear(); self.roster.commit(plan)
        self.assertFalse(list(self.roster.frame.query()))


class BattleRosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        observation_fixture.ObservationTests.setUpClass()
        flight_fixture.FlightTests.setUpClass()
        cls.observation = observation_fixture.ObservationTests()
        cls.aviation = flight_fixture.FlightTests()

    def test_initial_and_moving_battle_keep_metadata_without_changing_observations(self):
        b = self.observation.battle(); assert_roster(self, b)
        first = b.target_roster.frame
        for _ in range(3):b.step(); assert_roster(self, b)
        self.assertIs(b.target_roster.frame, first)

    def test_launch_exact_retry_and_rejected_command_are_atomic(self):
        b, key = self.aviation.battle(); first = b.target_roster.frame
        self.assertFalse(first.families[2].rows)
        value = self.aviation.command(b, 'launch', [key]); assert_roster(self, b)
        launched = b.target_roster.frame
        self.assertFalse(b.aviation.submit(value)); self.assertIs(b.target_roster.frame, launched)
        with self.assertRaises(flight_fixture.ps.ContractError):self.aviation.command(b, 'launch', [key])
        self.assertIs(b.target_roster.frame, launched)
        for _ in range(3):b.step(); assert_roster(self, b)

    def test_candidate_and_outer_projection_failures_preserve_all_authority(self):
        b, key = self.aviation.battle(); self.aviation.command(b, 'launch', [key])
        self.aviation.command(b, 'task', [key], task=dict(kind='observe', layer='cloud', point_m=[0., 1000.]))
        from backend.high_wilderness_sidecar import aviation_ew
        f = b.aviation.flights[key]
        f.update(layer_goal='cloud', layer_progress=aviation_ew.model_for(b.inventory.inventories[0], key)['layer_change_steps']-1)
        before = snapshot(b); frame = b.target_roster.frame
        def fail(*args, **kwargs):raise RuntimeError('reject candidate')
        with patch.object(b.ew, 'environment', fail), self.assertRaisesRegex(RuntimeError, 'reject candidate'):b.step()
        self.assertIs(b.target_roster.frame, frame); self.assertIsNone(difference(before, snapshot(b)))
        with self.assertRaisesRegex(RuntimeError, 'reject candidate'):b.step(project=fail)
        self.assertIs(b.target_roster.frame, frame); self.assertIsNone(difference(before, snapshot(b)))
        b.step(); assert_roster(self, b)
        self.assertEqual(b.target_roster.frame.get(Handle('aircraft', key)).layer, 'cloud')

    def test_departure_and_flagship_loss_match_committed_world(self):
        from tools.test_tactical_disengagement import DisengagementTests
        helper = DisengagementTests()
        helper.setUp()
        try:
            helper.start(); b = helper.b
            helper.order('detach'); helper.pose(helper.n, y=-31000)
            before = b.target_roster.frame
            with self.assertRaisesRegex(RuntimeError, 'closing'):
                b.step(project=lambda *_: (_ for _ in ()).throw(RuntimeError('closing')))
            self.assertIs(b.target_roster.frame, before)
            b.step(); assert_roster(self, b)
            self.assertIsNone(b.target_roster.frame.get(Handle('ship', helper.escort)))
            self.assertIsNone(b.ending)
            cic = b.session._command_kernels[1].cic
            b.step(device_operations=(helper.damage(1, cic),))
            self.assertIsNotNone(b.ending); assert_roster(self, b)
        finally:helper.doCleanups()

    def test_actual_ship_layer_completion_and_forced_descent(self):
        from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
        b = self.observation.battle(); world = b.session.world; sid = world.ships[0].ship_id
        b.session.set_height_target(sid, 'cloud')
        s = b.session.world.ships[0]; segment = s.motion.layer_transition
        s = replace(s, motion=replace(s.motion, layer_transition=replace(segment, elapsed_s=segment.duration_s-1/120)))
        b.session._world = replace(b.session.world, ships=(s, *world.ships[1:]))
        b.step(); assert_roster(self, b)
        self.assertEqual(b.target_roster.frame.get(Handle('ship', sid)).layer, 'cloud')
        kernel = b.session._device_kernels[0]; mid = 'lift_tank'; index = kernel.by_id[mid]
        s = b.session.world.ships[0]
        b.step(device_operations=(DeviceOperation(b.session.world.epoch, sid, mid, s.devices.modules[index].sequence+1,
                    'damage', kernel.seed.modules[index].maximum_durability_points, b.session.world.fixed_step+1, 'closing'),))
        s = b.session.world.ships[0]; self.assertIsNotNone(s.descent)
        s = replace(s, descent=replace(s.descent, progress=1-1/(120*s.descent.duration_s)))
        b.session._world = replace(b.session.world, ships=(s, *b.session.world.ships[1:]))
        b.step(); assert_roster(self, b)
        self.assertEqual(b.target_roster.frame.get(Handle('ship', sid)).layer, 'rain')

    def test_projectile_expiry_is_removed_at_real_step_boundary(self):
        from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
        b = self.observation.battle()
        p = Projectile(9001, b.session.world.ships[-1].ship_id, 'test', (1e6, 1e6), (1e6, 1e6),
                       (10., 0.), 1, None, 'upper', durability=10., maximum_durability=10.)
        b.projectiles = (p,)
        b.target_roster.rebuild(b.session.world, b.projectiles, b.aviation.flights, b.inventory.inventories)
        self.assertIsNotNone(b.target_roster.frame.get(Handle('projectile', p.id)))
        b.step(); assert_roster(self, b)
        self.assertIsNone(b.target_roster.frame.get(Handle('projectile', p.id)))

    def test_recovery_removes_only_aircraft_then_relaunch_registers_again(self):
        from backend.high_wilderness_sidecar import aviation_flight as af
        b, key = self.aviation.battle(); self.aviation.command(b, 'launch', [key])
        old = b.target_roster.frame.get(Handle('aircraft', key))
        inv = b.inventory.inventories[0]
        spec = next(f for f in inv._definition['aviation']['facilities'] if f['kind'] == 'aircraft_arrester')
        f = b.aviation.flights[key]
        f.update(return_requested=True, position=af.origin(b, b.session.world, 0, spec['module_id'])[0],
                 velocity=(0., 0.), receiver=(0, spec['module_id']), recovery_progress=spec['recovery_steps']-1)
        b.step(); assert_roster(self, b)
        self.assertNotIn(key, b.aviation.flights)
        inv = b.inventory.inventories[0]
        hangar = next(f['module_id'] for f in inv._definition['aviation']['facilities'] if f['kind'] == 'aircraft_hangar')
        catapult = next(f['module_id'] for f in inv._definition['aviation']['facilities'] if f['kind'] == 'aircraft_catapult')
        def order(value):
            b.aviation.submit(dict(epoch=b.session.world.epoch, generation=0, sequence=b.aviation.sequence+1,
                                   ship_id=b.session.world.ships[0].ship_id, order=value))
        # The fixture prep supplied only the first sortie. Seed another service
        # batch explicitly; the production command must still consume it.
        from backend.high_wilderness_sidecar import aviation_logistics as al
        work = al.Work(inv)
        work.cargo(work.costs(work.plane(key), 'prepare', {}), 1)
        work.commit()
        order(dict(kind='prepare', aircraft_id=key, module_id=hangar, loadout={}))
        # Cancel completes the authorized job under the existing aviation policy.
        order(dict(kind='cancel', aircraft_id=key))
        order(dict(kind='load', aircraft_id=key, module_id=catapult))
        order(dict(kind='cancel', aircraft_id=key))
        self.aviation.command(b, 'launch', [key]); assert_roster(self, b)
        self.assertIsNot(b.target_roster.frame.get(old.handle), old)

    def test_aircraft_destroyed_and_explicit_end_clear_roster(self):
        b, key = self.aviation.battle(); self.aviation.command(b, 'launch', [key])
        b.aviation.flights[key]['hp'] = 0.
        b.step(); assert_roster(self, b)
        self.assertNotIn(key, b.aviation.flights)
        self.assertTrue(b.withdraw()); assert_roster(self, b)
        cleared = b.target_roster.frame
        self.assertFalse(b.withdraw()); self.assertIs(b.target_roster.frame, cleared)

    def test_failed_launch_metadata_construction_does_not_commit_inventory(self):
        b, key = self.aviation.battle(); before = snapshot(b); frame = b.target_roster.frame
        from backend.high_wilderness_sidecar.tactical_target_roster import Plan
        with patch.object(Plan, 'aircraft', side_effect=RuntimeError('metadata failure')):
            with self.assertRaisesRegex(RuntimeError, 'metadata failure'):self.aviation.command(b, 'launch', [key])
        self.assertIsNone(difference(before, snapshot(b))); self.assertIs(b.target_roster.frame, frame)
        self.aviation.command(b, 'launch', [key]); assert_roster(self, b)


if __name__ == '__main__':unittest.main()
