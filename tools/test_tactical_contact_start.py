"""7a contact windows, actual prepared state, first frame and persistent reentry."""
import unittest
from math import pi, sin, cos, atan2, sqrt
from types import SimpleNamespace, MethodType
from dataclasses import replace
from random import Random
from backend.high_wilderness_sidecar import tactical_contact_start as contact, tactical_test_scene as scene
from backend.high_wilderness_sidecar import tactical_observation as obs, tactical_encounter as encounter
from backend.high_wilderness_sidecar import persistent_ship as ps
from backend.high_wilderness_sidecar.server import SidecarServer
from 高天荒野舰艇战术机动求解器 import Vec2
from 高天荒野舰艇RCS缓存 import RCSDirectionSample
from tools import test_prepared_deployment as prepared


class ContactGeometryTests(unittest.TestCase):
    def fixture(self,*,x=0,y=0,range_m=12000,channel='infrared',blocked=(),heading=0,values=None):
        ship=SimpleNamespace(motion=SimpleNamespace(position_world_m=Vec2(0,0),height_layer='upper',heading_rad=heading))
        target=obs.Target('enemy','ship','side.enemy',(x,y),(0,0),'upper')
        spec=dict(channel=channel,range_m=range_m,ship_range_m=range_m,coasting_range_m=0,weather=[1,.3,.5],range_efficiency=1.)
        runtime=SimpleNamespace(arcs={(0,'sensor'):dict(origin_m=(0,0),blocked_intervals_deg=blocked)},
            policy={'ship_radar_signature':{'reference_rcs_m2':1}},ship_signatures={'enemy':(
                SimpleNamespace(directions=tuple(RCSDirectionSample(n,0,v,0) for n,v in enumerate(values or [1.]*360))),1.,0.)})
        runtime.visible=MethodType(obs.ObservationRuntime.visible,runtime)
        runtime.radar_factor=MethodType(obs.ObservationRuntime.radar_factor,runtime)
        return runtime,ship,target,spec

    def solve(self,fixture,limit=50000,direction=1):
        r,s,t,e=fixture
        return contact.pair_distance(r,0,'sensor',s,t,e,direction,limit)

    def test_range_cap_lateral_offset_and_front_ship(self):
        self.assertAlmostEqual(self.solve(self.fixture()),12000,delta=.001)
        self.assertEqual(self.solve(self.fixture(range_m=80000),25000),25000)
        self.assertAlmostEqual(self.solve(self.fixture(x=3000,y=-700)),sqrt(12000**2-3000**2)+700,delta=.001)
        self.assertAlmostEqual(self.solve(self.fixture(y=800),direction=-1),12800,delta=.001)

    def test_disconnected_and_narrow_visible_intervals(self):
        # Both outer endpoints invisible, but a finite contact island exists.
        self.assertAlmostEqual(self.solve(self.fixture(x=50,y=-1000,range_m=100)),1000+sqrt(7500),delta=.001)
        bearing=atan2(500,16000)*180/pi
        f=self.fixture(x=500,range_m=50000,blocked=((0,bearing-.00001),(bearing+.00001,360)))
        result=self.solve(f)
        self.assertIsNotNone(result);self.assertAlmostEqual(result,16000,delta=.2)
        self.assertIsNone(self.solve(self.fixture(blocked=((0,360),))))
        self.assertIsNone(self.solve(self.fixture(x=12001)))

    def test_weather_damage_and_heading_match_actual_visibility(self):
        r,s,t,e=self.fixture(heading=pi,blocked=((0,180),))
        self.assertIsNone(self.solve((r,s,t,e)))
        r,s,t,e=self.fixture();e['range_efficiency']=.5;t=replace(t,layer='cloud')
        self.assertAlmostEqual(self.solve((r,s,t,e)),1800,delta=.001)
        self.assertFalse(r.visible(0,'sensor',s,replace(t,layer='rain'),e,None))

    def test_rcs_direction_windows_agree_with_independent_dense_reference(self):
        rng=Random(714)
        for case in range(12):
            values=[rng.uniform(.02,6) for _ in range(360)]
            f=self.fixture(x=rng.uniform(-6000,6000),y=rng.uniform(-18000,5000),range_m=7000,
                channel='radar',heading=rng.uniform(-pi,pi),values=values,
                blocked=((20,100),(170,220)))
            r,s,t,e=f;result=self.solve(f,25000)
            visible=lambda d:r.visible(0,'sensor',s,replace(t,position=(t.position[0],t.position[1]+d)),e,None)
            reference=max((d for d in range(1,25001,10) if visible(d)),default=None)
            with self.subTest(case=case):
                if reference is not None:self.assertIsNotNone(result);self.assertGreaterEqual(result+.001,reference)
                if result is not None:
                    self.assertTrue(visible(result));self.assertLessEqual(result,25000)
                    self.assertFalse(any(visible(d) for d in range(int(result)+2,25001,10)))


class ContactEntryTests(unittest.TestCase):
    setUp=prepared.PreparedDeploymentTests.setUp
    close_lease=prepared.PreparedDeploymentTests.close_lease
    call=prepared.PreparedDeploymentTests.call
    prepare=prepared.PreparedDeploymentTests.prepare

    def configured(self):
        self.prepare(2)
        v=self.call('scene_read',{})['scene'];v['revision']+=1
        for side,index in zip(v['sides'],(1,0)):
            key=f'instance.custom.{index}'
            side.update(flagship_instance_id=key,ships=[dict(instance_id=key,x_m=10.123456789,y_m=30.23456789,
                heading_rad=pi if index else 0.)])
        self.call('scene_save',dict(scene=v,expected_revision=0))
        return self.call('scene_read',{})

    def request(self,packet,identity='encounter.contact.first'):
        return self.call('scene_encounter',dict(revision=packet['scene']['revision'],launch_id=identity,
            contact_input_sha256=packet['contact_start'].get('input_sha256')))

    def test_real_initial_contact_is_read_only_and_survives_save_restart(self):
        p=self.configured();self.assertEqual(p['contact_start']['status'],'ready')
        before=ps.clone(p['ships']);req=self.request(p)
        self.assertEqual(req,self.request(p));self.assertEqual(req['interface'],encounter.CONTACT_INTERFACE)
        view=self.live.deploy_encounter(req);b=self.live.gunnery
        self.assertEqual(self.live.deploy_encounter(req),view)
        self.assertEqual(b.session.world.fixed_step,0);self.assertIsNone(b.ending)
        self.assertTrue(any(t.valid for t in b.observation.frame.local.values()))
        self.assertEqual(b.observation.frame.step,0)
        self.assertTrue(all(g.shots==0 for g in b.states));self.assertFalse(b.projectiles)
        self.assertEqual([i.instance.to_dict() for i in b.inventory.prepared.bindings],
            [s['state'] for s in before])
        b.withdraw();self.live.publish();saved=self.live.store.save(self.live._result['settlement_id']);self.live._result_saved=True
        self.assertEqual(len(saved['result']['ships']),2)
        self.close_lease()
        other=SidecarServer('backend.contact.restart',settlement_dir=self.live.store.directory)
        self.addCleanup(lambda:other.realtime._prepared_lease and other.realtime._prepared_lease.close())
        after=scene.packet(other.preparation)
        self.assertEqual(after['scene'],p['scene']);self.assertEqual(after['contact_start']['distance_m'],p['contact_start']['distance_m'])
        req2=scene.encounter(other.preparation,after['scene']['revision'],'encounter.contact.second',after['contact_start']['input_sha256'])
        other.realtime.deploy_encounter(req2)
        self.assertEqual([i.instance.to_dict() for i in other.realtime.gunnery.inventory.prepared.bindings],
            [s['after']['state'] for s in saved['result']['ships']])

    def change_sensor(self,side_index,mode):
        key=f'instance.custom.{side_index}'
        with self.service.store.connection() as db:
            raw=db.execute('SELECT payload,digest FROM ships WHERE id=?',(key,)).fetchone()
            record=self.service.store._decode(*raw)
            for m in record['state']['modules']:
                if 'sensor' in m['module_id']:m['operating_mode']=mode
            payload,digest=self.service.store._encoded(record)
            db.execute('UPDATE ships SET payload=?,digest=? WHERE id=?',(payload,digest,key))

    def test_one_sided_detection_no_contact_and_stale_preview(self):
        p=self.configured();self.change_sensor(0,'off')
        with self.assertRaises(ps.ContractError):self.request(p)
        p=self.call('scene_read',{});self.assertEqual(p['contact_start']['status'],'ready')
        req=self.request(p);self.live.deploy_encounter(req)
        frame=self.live.gunnery.observation.frame
        self.assertTrue(frame.local);self.assertTrue(all(n==0 for n,_ in frame.local))
        own=self.live.gunnery.observation.view()['ships'];self.assertTrue(all(not s['contacts'] for s in own))
        self.live.gunnery.withdraw();self.live.publish();self.live.store.save(self.live._result['settlement_id']);self.live._result_saved=True
        self.change_sensor(1,'off');p=self.call('scene_read',{})
        self.assertEqual(p['contact_start']['status'],'no_contact')
        with self.assertRaises(ps.ContractError):self.request(p,'encounter.contact.none')

    def test_old_manual_scene_and_forged_start_are_rejected_without_claims(self):
        p=self.configured();req=self.request(p)
        bad=ps.clone(req);bad['contact_start']['input_sha256']='0'*64
        with self.assertRaises(ps.ContractError):self.live.deploy_encounter(bad)
        bad=ps.clone(req);bad['contact_start']['distance_m']=100000
        with self.assertRaises(ps.ContractError):encounter.parse(bad)
        v=p['scene'];v.pop('distance_mode');v.update(interface=scene.INTERFACE,revision=2,distance_m=4567)
        self.call('scene_save',dict(scene=v,expected_revision=1))
        self.assertEqual(self.call('scene_read',{})['scene'],v)
        manual=self.call('scene_encounter',dict(revision=2,launch_id='encounter.manual.legacy'))
        self.assertNotIn('contact_start',manual);self.assertEqual(manual['interface'],encounter.DISTANCE_INTERFACE)
        self.live.deploy_encounter(manual)
        self.assertEqual(self.live.gunnery.disengagement.view()['distance_m'],4567)


if __name__=='__main__':unittest.main()
