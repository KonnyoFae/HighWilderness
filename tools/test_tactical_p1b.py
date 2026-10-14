"""P1b controller stage boundaries, exact source legality, and failed retries."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from math import nextafter, inf
import unittest
from unittest.mock import patch

from tools import test_tactical_observation as fixture
from tools.profile_tactical_p1b import implementation, validate_reference
from tools.tactical_7c_oracle import snapshot, difference


class CapabilityStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ObservationTests.setUpClass();cls.fixtures=fixture.ObservationTests()
        validate_reference()

    def test_shared_consumers_reduce_work_without_changing_sources_or_ranges(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world;available=b._availability(world)[1]
        frame=o.plan(world,available,(),missiles=(self.fixtures.target(b,powered=True),))
        o.frame=frame;results=[];counts=[]
        for legacy in (True,False):
            count=Counter();rows=[]
            with implementation(legacy,count),o.capability_stage(world,available):
                for _ in range(3):
                    for n,key in frame.tracks:
                        rows.append((o.sources(n,key,world,available,frame),o.sources(n,key,world,available,frame,radar_only=True),
                            o.defense_sources(n,key,world,available,frame),o.ready(n,world,available)))
                    for n,controls in enumerate(b._controllers):
                        p=world.ships[n].motion.position_world_m
                        for mid in controls:
                            limit=b._capabilities[n][mid]['maximum_lock_range_m']
                            for distance in (0.,limit,nextafter(limit,inf)):
                                rows.append(o.controllers(n,world,available,(p.x,p.y+distance)))
            results.append(rows);counts.append(count)
            self.assertIsNone(o._stage_inputs)
        self.assertEqual(*results);self.assertLess(counts[1]['effect'],counts[0]['effect'])

    def test_world_and_availability_identity_guard_and_nested_stage(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world;available=b._availability(world)[1]
        ship=world.ships[0];mid=b._controllers[0][0];index=b._indices[0][mid]
        damaged=list(ship.devices.modules);damaged[index]=replace(damaged[index],durability_points=damaged[index].durability_points*.3)
        variants=[replace(world,ships=(replace(ship,devices=replace(ship.devices,modules=tuple(damaged),revision=ship.devices.revision+1)),*world.ships[1:])),
            replace(world,ships=(replace(ship,resources=replace(ship.resources,staffing=tuple((k,.4) for k,_ in ship.resources.staffing),revision=ship.resources.revision+1)),*world.ships[1:])),
            replace(world,ships=(replace(ship,motion=replace(ship.motion,position_world_m=replace(ship.motion.position_world_m,x=1e6))),*world.ships[1:]))]
        with o.capability_stage(world,available):
            first=o.controllers(0,world,available);outer=o._stage_inputs
            for candidate in variants:
                availability=b._availability(candidate)[1]
                with implementation(True):expected=o.controllers(0,candidate,availability,(0.,0.))
                self.assertEqual(o.controllers(0,candidate,availability,(0.,0.)),expected)
                with o.capability_stage(candidate,availability):
                    self.assertEqual(o.controllers(0,candidate,availability,(0.,0.)),expected)
                self.assertIs(o._stage_inputs,outer)
            for reason in ('destroyed','mode_disabled','power_unavailable','crew_unavailable'):
                changed=deepcopy(available)
                for mid in b._controllers[0]:changed[0][mid]=reason
                self.assertEqual(o.controllers(0,world,changed),((),0))
            self.assertEqual(o.controllers(0,world,available),first)
        self.assertIsNone(o._stage_inputs)

    def test_new_frame_source_mode_link_and_operational_checks_are_not_cached(self):
        b=self.fixtures.battle(True);o=b.observation;world=b.session.world;available=deepcopy(b._availability(world)[1])
        available[0][fixture.SENSOR]='mode_disabled'
        target=self.fixtures.target(b,powered=True);frame=o.plan(world,available,(),missiles=(target,))
        self.assertTrue(frame.tracks[0,target.id].sources)
        with o.capability_stage(world,available):
            self.assertTrue(o.sources(0,target.id,world,available,frame)[0])
            self.assertFalse(o.sources(0,target.id,world,available,frame,radar_only=True)[0])
            lost=replace(frame,tracks={**frame.tracks,(0,target.id):replace(frame.tracks[0,target.id],valid=False)})
            self.assertEqual(o.sources(0,target.id,world,available,lost),((),0))
            o.sensor_enabled={**o.sensor_enabled,(1,fixture.SENSOR):False}
            self.assertFalse(o.sources(0,target.id,world,available,frame)[0])
            o.sensor_enabled={}
            for n in (0,1):
                changed=deepcopy(available)
                for mid in o.links[n]:changed[n][mid]='destroyed'
                self.assertFalse(o.sources(0,target.id,world,changed,frame)[0])
            sender=world.ships[1];life=replace(sender.command.lifecycle,physical_status='exited')
            exited=replace(world,ships=(world.ships[0],replace(sender,command=replace(sender.command,lifecycle=life)),*world.ships[2:]))
            self.assertFalse(o.sources(0,target.id,exited,available,frame)[0])
            self.assertTrue(o.sources(0,target.id,world,available,frame)[0])

    def test_same_step_field_replan_inside_scope_matches_frozen_method(self):
        from backend.high_wilderness_sidecar.tactical_ew import Effect
        b=self.fixtures.battle();o=b.observation;world=b.session.world;available=b._availability(world)[1]
        target=self.fixtures.target(b,powered=True);p=world.ships[0].motion.position_world_m
        effect=Effect('p1b.area',world.ships[0].ship_id,b._sides[0],'chaff','upper',(p.x,p.y),(0.,0.),1e6,1.,0,100)
        with o.capability_stage(world,available):
            rows=[]
            for fields in ((),(effect,),()):
                frame=o.plan(world,available,(),missiles=(target,),occluded=b.ew.sensor_blocker(world,fields))
                candidate=o.sources(0,target.id,world,available,frame)
                with implementation(True):self.assertEqual(candidate,o.sources(0,target.id,world,available,frame))
                rows.append(bool(candidate[0]))
            self.assertEqual(rows,[True,False,True])

    def test_simulation_failure_and_outer_projection_failure_clear_scope(self):
        b=self.fixtures.battle(True);b.step();before=snapshot(b);o=b.observation
        original=b.ew.environment
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            self.assertIsNotNone(o._stage_inputs)
            raise RuntimeError('candidate failed')
        with patch.object(b.ew,'environment',fail),self.assertRaisesRegex(RuntimeError,'candidate failed'):b.step()
        self.assertIsNone(o._stage_inputs);self.assertIsNone(difference(before,snapshot(b)))
        def project(*args):
            self.assertIsNone(o._stage_inputs)
            raise RuntimeError('projection failed')
        with self.assertRaisesRegex(RuntimeError,'projection failed'):b.step(project=project)
        self.assertIsNone(o._stage_inputs);self.assertIsNone(difference(before,snapshot(b)))
        b.step();self.assertIsNone(o._stage_inputs)


if __name__=='__main__':unittest.main()
