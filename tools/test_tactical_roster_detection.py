"""P2a.1b stage isolation, deferred samples and exact legacy sensor behavior."""
from copy import deepcopy
from dataclasses import replace
import gc
import unittest
import weakref
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_observation as obs, tactical_detection as detection
from backend.high_wilderness_sidecar import aviation_flight as af, aviation_ew as ew
from tools import test_tactical_observation as ships, test_aviation_flight as aviation
from tools.test_missile_maneuver import body
from tools.profile_tactical_roster_detection import references


def reference(owner, name):
    return next(fn for cls, key, fn in references()[0] if cls is owner and key == name)


def incoming(b, identity=100, layer='upper', **changes):
    p = body(800., layer=layer)
    return replace(p, **dict(id=identity, ship_id=b.session.world.ships[-1].ship_id) | changes)


class DetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ships.ObservationTests.setUpClass(); aviation.FlightTests.setUpClass()
        cls.ships = ships.ObservationTests(); cls.aviation = aviation.FlightTests()

    def test_metadata_filters_before_entity_resolution_and_samples(self):
        b = self.ships.battle(); w = b.session.world
        ps = (incoming(b, 1), incoming(b, 2, 'rain'), incoming(b, 3, 'cloud'),
              incoming(b, 4, ship_id=w.ships[0].ship_id))
        stage = b.observation.target_stage(w, ps)
        with patch.object(detection, 'Target', side_effect=AssertionError('premature snapshot')):
            selected = list(stage.query(b._sides[0], 'upper'))
            self.assertEqual([t.id for t in selected], [w.ships[-1].ship_id, 1, 3])
            self.assertEqual(len(stage._values), 3)
        expected = tuple(t for t in b.observation.targets(w, ps) if t.side != b._sides[0] and t.layer != 'rain')
        self.assertEqual(tuple(t.freeze() for t in selected), expected)
        self.assertEqual([t.id for t in stage.query(b._sides[0], 'cloud', include_ids={2, 3}, exclude_ids={3})], [2])

    def test_disabled_sensors_never_resolve_or_snapshot_targets(self):
        b = self.ships.battle(); w = b.session.world; o = b.observation
        available = deepcopy(b._availability(w)[1])
        for n, devices in enumerate(o.sensors):
            for mid in devices:available[n][mid] = 'disabled'
        stage = o.target_stage(w, (incoming(b),))
        with patch.object(detection.Candidate, '__init__', side_effect=AssertionError('disabled sensor resolved a target')):
            actual = o.plan(w, available, stage.access.projectiles, target_stage=stage)
        expected = reference(obs.ObservationRuntime, 'plan')(o, w, available, stage.access.projectiles)
        self.assertEqual(actual, expected)

    def test_invisible_projectile_does_not_compute_threat_or_sample(self):
        b = self.ships.battle(); w = b.session.world
        ps = (incoming(b, position=(1.e8, 1.e8)),)
        stage = b.observation.target_stage(w, ps)
        with patch.object(stage, 'threat', side_effect=AssertionError('out of range threat')):
            actual = b.observation.plan(w, b._availability(w)[1], ps, target_stage=stage)
        expected = reference(obs.ObservationRuntime, 'plan')(b.observation, w, b._availability(w)[1], ps)
        self.assertEqual(actual, expected)

    def test_same_step_motion_and_layer_are_separate_candidates(self):
        b = self.ships.battle(); w = b.session.world; ship = w.ships[-1]
        p = incoming(b); before = b.target_roster.frame
        first = b.observation.target_stage(w, (p,))
        moved = replace(ship, motion=replace(ship.motion, position_world_m=replace(ship.motion.position_world_m, x=1234.), height_layer='cloud'))
        candidate = replace(w, ships=(*w.ships[:-1], moved))
        second = b.observation.target_stage(candidate, (replace(p, height_layer='rain'),))
        self.assertIs(b.target_roster.frame, before)
        self.assertEqual([t.layer for t in first.query(b._sides[0], 'upper')], ['upper', 'upper'])
        self.assertEqual([t.layer for t in second.query(b._sides[0], 'upper')], ['cloud'])
        with self.assertRaises(ValueError):b.observation.plan(candidate, b._availability(candidate)[1], (p,), target_stage=first)

    def test_bomb_category_keeps_legacy_missile_semantics_and_vertical_payload(self):
        b = self.ships.battle(); w = b.session.world; p = incoming(b)
        p = replace(p, missile=replace(p.missile, profile=replace(p.missile.profile, model_id='gtw.aviation.weapon.small_guided_bomb'),
                                       phase='coast', altitude_m=5500., vertical_velocity_mps=-600.))
        stage = b.observation.target_stage(w, (p,))
        row = stage.access.frame.families[1].rows[0]
        self.assertEqual(row.category, 'bomb')
        actual = next(t for t in stage.query(b._sides[0], 'upper') if t.id == p.id).freeze()
        expected = next(t for t in b.observation.targets(w, (p,)) if t.id == p.id)
        self.assertEqual(actual, expected); self.assertEqual(actual.kind, 'missile')

    def test_threat_is_recomputed_when_only_another_ship_moves(self):
        b = self.ships.battle(); w = b.session.world; ship = w.ships[0]
        at = tuple(ship.motion.position_world_m.to_list())
        p = incoming(b, position=(at[0], at[1]+800.), velocity=(0., -800.), expires=w.fixed_step+600)
        first = b.observation.target_stage(w, (p,))
        t = next(t for t in first.query(b._sides[0], 'upper') if t.id == p.id)
        self.assertTrue(t.threat)
        shifted = replace(ship, motion=replace(ship.motion, position_world_m=replace(ship.motion.position_world_m, x=at[0]+10000.)))
        other = replace(w, ships=(shifted, *w.ships[1:]))
        second = b.observation.target_stage(other, (p,))
        self.assertFalse(next(t for t in second.query(b._sides[0], 'upper') if t.id == p.id).threat)
        self.assertTrue(t.threat)

    def test_flight_pose_and_model_survive_mutation_recovery_and_inventory_removal(self):
        b, key = self.aviation.battle('e1'); self.aviation.command(b, 'launch', [key])
        fs = b.aviation.flights; inventories = tuple(i.fork() for i in b.inventory.inventories)
        expected = af.targets(fs, inventories, b._sides)[0]
        stage = b.observation.target_stage(b.session.world, (), fs, inventories)
        fs[key].update(position=(999., 999.), layer='rain', velocity=(1., 2.), hp=1.)
        inventories[0]._value['aviation']['manifest']['aircraft'].clear()
        del fs[key]
        actual = next(t for t in stage.query(b._sides[-1], 'cloud') if t.id == key).freeze()
        self.assertEqual(actual, expected)

    def test_retained_tracks_and_memory_hold_old_samples(self):
        b = self.ships.battle(); o = b.observation; w = b.session.world; available = b._availability(w)[1]
        o.frame = o.plan(w, available, ())
        target = w.ships[-1]; key = (0, target.ship_id); old = o.frame.local[key].target
        changed = replace(target, motion=replace(target.motion, position_world_m=replace(target.motion.position_world_m, x=400.), heading_rad=.2))
        candidate = replace(w, fixed_step=w.fixed_step+1, ships=(*w.ships[:-1], changed))
        actual = o.plan(candidate, available, ())
        expected = reference(obs.ObservationRuntime, 'plan')(o, candidate, available, ())
        self.assertEqual(actual, expected); self.assertIs(actual.local[key].target, old)
        o.frame = actual
        lost = o.plan(candidate, available, (), occluded=lambda *args: True)
        self.assertFalse(lost.tracks[key].valid); self.assertIs(lost.tracks[key].target, old)

    def test_reused_stage_rechecks_occlusion_and_retains_capacity_order(self):
        b = self.ships.battle(kind='dual'); o = b.observation; w = b.session.world; available = b._availability(w)[1]
        ps = tuple(incoming(b, n, position=(0., 1000.+n)) for n in range(100, 116))
        stage = o.target_stage(w, ps)
        for blocker in (None, lambda n, mid, t: t.id in (100, 101), lambda *args: True, None):
            actual = o.plan(w, available, ps, target_stage=stage, occluded=blocker)
            expected = reference(obs.ObservationRuntime, 'plan')(o, w, available, ps, occluded=blocker)
            self.assertEqual(actual, expected)
            o.frame = actual

    def test_aircraft_non_acquisition_filters_contacts_without_resampling(self):
        b, key = self.aviation.battle('e1'); self.aviation.command(b, 'launch', [key])
        w = b.session.world; f = b.aviation.flights[key]; model = ew.model_for(b.inventory.inventories[0], key)
        stage = b.observation.target_stage(w, (), b.aviation.flights, b.inventory.inventories)
        af.sample(b, w, f, model, stage, ())
        self.assertTrue(f['contacts']); before = deepcopy(f)
        next_world = replace(w, fixed_step=w.fixed_step+1)
        next_stage = b.observation.target_stage(next_world, (), b.aviation.flights, b.inventory.inventories)
        with patch.object(detection, 'Target', side_effect=AssertionError('resampled between intervals')):
            af.sample(b, next_world, f, model, next_stage, ())
        legacy = deepcopy(before)
        targets = b.observation.targets(next_world, ())+af.targets(b.aviation.flights, b.inventory.inventories, b._sides)
        reference(af, 'sample')(b, next_world, legacy, model, targets, ())
        self.assertEqual(f, legacy)

    def test_large_ship_signature_can_exceed_nominal_sensor_range(self):
        b = self.ships.battle(); o = b.observation; w = b.session.world
        own, enemy = w.ships[0], w.ships[-1]
        maximum = o.sensors[0][ships.SENSOR]['range_m']
        moved = replace(enemy, motion=replace(enemy.motion, position_world_m=replace(enemy.motion.position_world_m,
                        x=own.motion.position_world_m.x, y=own.motion.position_world_m.y+maximum*1.5)))
        world = replace(w, ships=(*w.ships[:-1], moved)); available = b._availability(world)[1]
        with patch.object(o, 'radar_factor', return_value=2.):
            actual = o.plan(world, available, ())
            expected = reference(obs.ObservationRuntime, 'plan')(o, world, available, ())
        self.assertEqual(actual, expected); self.assertTrue(actual.tracks[0, enemy.ship_id].valid)

    def test_success_and_failed_steps_release_phase_entities(self):
        b, key = self.aviation.battle('e1'); self.aviation.command(b, 'launch', [key])
        stage = b.observation.target_stage; refs = []
        def record(*a, **kw):
            value = stage(*a, **kw); refs.append(weakref.ref(value)); return value
        frame = b.target_roster.frame
        with patch.object(b.observation, 'target_stage', side_effect=record):
            with self.assertRaises(RuntimeError):
                b.step(project=lambda *args: (_ for _ in ()).throw(RuntimeError('projection failed')))
            self.assertIs(b.target_roster.frame, frame)
            b.step()
        gc.collect()
        self.assertGreaterEqual(len(refs), 4)
        self.assertTrue(all(ref() is None for ref in refs))


if __name__ == '__main__':unittest.main()
