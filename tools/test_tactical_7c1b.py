"""7c.1b stage-local reuse: exact reference, immediate invalidation and rollback."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from math import nextafter, inf
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_observation as obs, tactical_ew as ew
from tools import test_tactical_observation as fixture
from tools.profile_tactical_7c1b import implementation, validate_reference
from tools.tactical_7c_oracle import snapshot, difference, plain


class ObservationReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ObservationTests.setUpClass();cls.fixtures=fixture.ObservationTests()
        validate_reference()

    def compare(self,b,world,available,targets=(),effects=()):
        o=b.observation;before=o.frame;rows=[]
        for legacy in (True,False):
            with implementation(legacy):
                frame=o.plan(world,available,(),missiles=targets,occluded=b.ew.sensor_blocker(world,effects))
                sources={(n,key,radar):o.sources(n,key,world,available,frame,radar_only=radar)
                    for n,key in frame.tracks for radar in (False,True)}
                rows.append((frame,sources))
        self.assertEqual(rows[0],rows[1]);self.assertIs(o.frame,before)
        return rows[1][0]

    def test_repeated_same_step_plans_rebuild_positions_layers_devices_and_crew(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world
        target=self.fixtures.target(b,powered=True)
        available=b._availability(world)[1]
        o.frame=self.compare(b,world,available,(target,))
        mid=fixture.SENSOR;index=b._indices[0][mid];ship=world.ships[0]
        damaged=list(ship.devices.modules)
        damaged[index]=replace(damaged[index],durability_points=damaged[index].durability_points*.3)
        variants=[world,
            replace(world,ships=(replace(ship,motion=replace(ship.motion,heading_rad=2.,height_layer='rain')),*world.ships[1:])),
            replace(world,ships=(replace(ship,motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,x=1e6))),*world.ships[1:])),
            replace(world,ships=(replace(ship,devices=replace(ship.devices,modules=tuple(damaged),revision=ship.devices.revision+1)),*world.ships[1:])),
            replace(world,ships=(replace(ship,resources=replace(ship.resources,staffing=tuple((k,.4) for k,_ in ship.resources.staffing),revision=ship.resources.revision+1)),*world.ships[1:]))]
        for candidate in variants:
            with self.subTest(world=candidate.fixed_step,ship=candidate.ships[0].motion.height_layer):
                self.compare(b,candidate,b._availability(candidate)[1],(target,replace(target,id='other',position=(1000.,8000.))))
        for reason in ('mode_disabled','destroyed','power_unavailable','crew_unavailable'):
            changed=deepcopy(available);changed[0][mid]=reason
            self.compare(b,world,changed,(target,))
        self.compare(b,world,available,(target,))

    def test_datalink_range_boundary_and_lost_sample_do_not_use_stale_pairs(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world
        available=deepcopy(b._availability(world)[1]);available[0][fixture.SENSOR]='mode_disabled'
        at=world.ships[0].motion.position_world_m;limit=o.policy['datalink_range_m']
        for distance in (limit,nextafter(limit,inf),limit-1.,limit+1.,limit):
            ship=world.ships[1]
            moved=replace(ship,motion=replace(ship.motion,position_world_m=replace(at,x=at.x+distance)))
            candidate=replace(world,ships=(world.ships[0],moved,*world.ships[2:]))
            target=replace(self.fixtures.target(b,powered=True),position=(at.x+distance,at.y+100.))
            frame=self.compare(b,candidate,available,(target,))
            # Shared sources remain samples from the sending ship.
            self.assertEqual(bool(frame.tracks.get((0,target.id)) and frame.tracks[0,target.id].valid),distance<=limit)
            if (0,target.id) in frame.tracks and frame.tracks[0,target.id].valid:
                self.assertLessEqual(distance,limit)
                self.assertTrue(all(n==1 for n,_,_ in frame.tracks[0,target.id].sources))
            o.frame=frame

    def test_field_closures_rebuild_for_new_effects_at_the_same_step(self):
        b=self.fixtures.battle();world=b.session.world;o=b.observation
        target=self.fixtures.target(b,powered=True);available=b._availability(world)[1]
        p=world.ships[0].motion.position_world_m
        effect=ew.Effect('test.area',world.ships[-1].ship_id,b._sides[-1],'electronic','upper',
            (p.x,p.y),(0.,0.),1e6,1.,world.fixed_step,world.fixed_step+60)
        first=self.compare(b,world,available,(target,));self.assertTrue(first.tracks[0,target.id].valid)
        o.frame=first
        blocked=self.compare(b,world,available,(target,),(effect,));self.assertFalse(blocked.tracks[0,target.id].valid)
        restored=self.compare(b,world,available,(target,));self.assertTrue(restored.tracks[0,target.id].valid)
        friendly=replace(effect,side=b._sides[0]);self.assertTrue(self.compare(b,world,available,(target,),(friendly,)).tracks[0,target.id].valid)

    def test_controller_ranges_remain_per_target_and_recompute_between_calls(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world;available=b._availability(world)[1]
        at=tuple(world.ships[0].motion.position_world_m.to_list())
        for efficiency in (1.,.5,0.,1.):
            with patch.object(o,'effect',return_value=efficiency):
                inputs=obs._Inputs(o,world,available)
                for distance in (0.,1e3,1e6):
                    position=(at[0],at[1]+distance)
                    self.assertEqual(o.controllers(0,world,available,position),o.controllers(0,world,available,position,inputs=inputs))
        changed=deepcopy(available)
        for mid in b._controllers[0]:changed[0][mid]='destroyed'
        self.assertEqual(o.controllers(0,world,changed,inputs=obs._Inputs(o,world,changed)),((),0))

    def test_multiple_controller_ranges_keep_individual_channel_counts(self):
        world=SimpleNamespace(ships=(SimpleNamespace(motion=SimpleNamespace(position_world_m=SimpleNamespace(to_list=lambda:[0.,0.]))),))
        available=({'near':None,'far':None},)
        battle=SimpleNamespace(_controllers=(('near','far'),),_capabilities=({'near':{'maximum_lock_range_m':10.,'simultaneous_channels':4},
            'far':{'maximum_lock_range_m':20.,'simultaneous_channels':6}},))
        runtime=SimpleNamespace(battle=battle,effect=lambda world,n,mid,function:.5 if mid=='far' else 1.)
        inputs=obs._Inputs(runtime,world,available)
        for distance,expected in ((10.,(('near','far'),7)),(nextafter(10.,inf),(('far',),3)),(20.,(('far',),3)),(nextafter(20.,inf),((),0))):
            at=(0.,distance)
            self.assertEqual(inputs.controllers(0,at),expected)
            self.assertEqual(obs.ObservationRuntime.controllers(runtime,0,world,available,at),expected)

    def test_quota_samples_view_and_counts_match_without_retaining_scratch(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world;available=b._availability(world)[1]
        targets=tuple(replace(self.fixtures.target(b,powered=True),id=f'test.{n}',velocity=(0.,-100.)) for n in range(20))
        o.frame=self.compare(b,world,available,targets)
        next_world=replace(world,fixed_step=world.fixed_step+1)
        self.compare(b,next_world,available,tuple(replace(t,velocity=(9000.,0.)) for t in targets))
        original_keys=set(o.__dict__);views=[];counters=[]
        for legacy in (True,False):
            count=Counter()
            with implementation(legacy,count):views.append(o.view())
            counters.append(count)
        self.assertEqual(views[0],views[1]);self.assertEqual(set(o.__dict__),original_keys)
        self.assertLess(counters[1]['effect'],counters[0]['effect'])
        for legacy in (True,False):
            with implementation(legacy):
                viewed=o.view();viewed['ships'][0]['contacts'].clear()
        self.assertEqual(o.view(),views[1])

    def test_failed_step_does_not_commit_scratch_or_candidate_observations(self):
        b=self.fixtures.battle(True);b.step();before=snapshot(b)
        keys=set(b.observation.__dict__);frame=b.observation.frame
        caches=plain((b._availability_key,b._available,b.point_defense.forecasts,b.fire_control.work))
        def fail(*args):raise RuntimeError('7c.1b failed projection')
        with self.assertRaisesRegex(RuntimeError,'7c.1b'):b.step(project=fail)
        self.assertIsNone(difference(before,snapshot(b)));self.assertIs(frame,b.observation.frame)
        self.assertEqual(set(b.observation.__dict__),keys)
        self.assertEqual(caches,plain((b._availability_key,b._available,b.point_defense.forecasts,b.fire_control.work)))
        b.step();self.assertNotEqual(before['world']['step'],snapshot(b)['world']['step'])


if __name__=='__main__':unittest.main()
