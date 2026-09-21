from copy import deepcopy
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from backend.high_wilderness_sidecar.projectile_stream import ProjectileStream, INTERFACE, MAX_PUBLICATIONS
from backend.high_wilderness_sidecar.tactical_presentation import FlightHistory
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical import render_static
from tools import test_tactical_damage as damage_fixtures, test_tactical_gunnery as gun_fixtures


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.history=FlightHistory();self.stream=ProjectileStream()
        self.p=Projectile(1,'ship.blue','gun',(0,0),(0,0),(5000,0),1200)

    def step(self,step,projectiles,hits=()):
        self.history.record(step,projectiles,hits)
        self.stream.record(step,self.history.completed)

    def publish(self,step):self.stream.publish({'fixed_step':step},self.history)

    def test_short_flight_between_publications_and_repeated_reads(self):
        self.step(0,[]);self.publish(0);cursor=self.stream.sequence
        self.step(1,[self.p]);self.step(2,[],[dict(projectile_id=1,step=2,position_m=(50,0),ship_id='enemy',outcome='module')]);self.publish(4)
        packet=self.stream.read(cursor)
        self.assertFalse(packet['reset']);self.assertEqual(len(packet['starts']),1)
        self.assertEqual(packet['paths'],[[1,[(1,0,0),(2,50,0)]]])
        self.assertEqual(packet['ends'][0]['end_m'],(50,0))
        self.assertEqual(packet,self.stream.read(cursor))
        self.assertEqual(self.stream.read(packet['sequence'])['ends'],[])

    def test_no_future_data_leaks_when_authority_runs_between_publications(self):
        self.step(0,[self.p]);self.publish(0);old=deepcopy(self.stream.read(None))
        self.step(1,[replace(self.p,position=(80,0))]);self.step(2,[])
        self.assertEqual(self.stream.read(None),old)
        self.publish(4);delta=self.stream.read(old['sequence'])
        self.assertEqual(delta['starts'],[]);self.assertEqual(len(delta['ends']),1)

    def test_terminal_burst_is_not_limited_by_legacy_128_records(self):
        self.step(0,[]);self.publish(0)
        self.step(1,[replace(self.p,id=i) for i in range(300)])
        self.step(2,[]);self.publish(4)
        packet=self.stream.read(1)
        self.assertEqual(len(packet['starts']),300);self.assertEqual(len(packet['ends']),300)
        self.assertEqual(packet['dropped_projectiles'],0)
        self.assertEqual(len(self.history.finished),128)

    def test_missing_window_future_cursor_and_large_step_gap_resynchronize(self):
        for n in range(MAX_PUBLICATIONS+4):
            self.step(n,[replace(self.p,position=(n*80,0))]);self.publish(n)
        self.assertEqual(len(self.stream.frames),MAX_PUBLICATIONS)
        self.assertTrue(self.stream.read(1)['reset'])
        self.assertTrue(self.stream.read(9999)['reset'])
        old=self.stream.sequence
        self.step(120,[replace(self.p,position=(9600,0))]);self.publish(120)
        self.assertTrue(self.stream.read(old)['reset'])
        self.assertEqual(self.stream.read(None)['starts'][0]['born_step'],0)

    def test_curve_samples_keep_new_interval_only_and_match_recorded_samples(self):
        self.step(0,[self.p]);self.publish(0)
        for n in range(1,9):
            self.step(n,[replace(self.p,position=(n*70.,n*n*.4))])
            if n%4==0:self.publish(n)
        latest=self.stream.read(2)
        self.assertEqual(latest['starts'],[])
        path=latest['paths'][0][1];self.assertEqual(path[0][0],4)
        for n in range(4,9):
            a,b=next((a,b) for a,b in zip(path,path[1:]) if a[0]<=n<=b[0]);f=(n-a[0])/(b[0]-a[0])
            self.assertLessEqual(abs(a[2]+f*(b[2]-a[2])-n*n*.4),.0500001)

    def test_byte_limits_are_bounded_and_request_full_recovery(self):
        with patch('backend.high_wilderness_sidecar.projectile_stream.MAX_HISTORY_BYTES',512):
            self.step(0,[]);self.publish(0)
            self.step(1,[replace(self.p,id=i) for i in range(30)]);self.publish(1)
            self.step(2,[]);self.publish(2)
            self.assertLessEqual(self.stream.retained_bytes,512)
            self.assertLessEqual(self.stream.terminal_bytes,512)
            self.assertTrue(self.stream.read(1)['reset'])
            self.assertGreater(self.stream.read(None)['dropped_projectiles'],0)


class LiveStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):gun_fixtures.GunneryTests.setUpClass()

    def test_real_swept_hit_commit_failure_and_read_protocol(self):
        battle=damage_fixtures.DamageTests().battle();projectile=damage_fixtures.DamageTests().shell(battle,(-30,-50),(30,-50))
        with TemporaryDirectory() as folder:
            service=RealtimeViewService('backend.stream',settlement_dir=folder)
            service._attach(battle,render_static(gun_fixtures.GunneryTests.scenario))
            def read(after):
                return service.dispatch(dict(method='tactical.realtime.read',params=dict(scene_id=battle.session.world.epoch,
                    known_static_sha256=service.digest,ack_inputs=[],ack_events=0,
                    display=dict(interface=INTERFACE,after_sequence=after))),mode='tactical')
            initial=read(None);cursor=initial['view']['projectile_stream']['sequence']
            self.assertNotIn('trajectory',initial['view']['gunnery']['projectiles'][0])
            with patch.object(battle,'step',side_effect=RuntimeError('not committed')):
                with self.assertRaises(RuntimeError):service.scheduler._stepper()
            self.assertEqual(read(cursor)['view']['projectile_stream']['sequence'],cursor)
            service.scheduler._stepper();service.scheduler._committed=battle.session.world;service.publish()
            value=read(cursor);terminal=value['view']['projectile_stream']['ends'][0]
            self.assertEqual(terminal['id'],projectile.id)
            self.assertEqual(terminal['end_m'],list(battle.damage_state.recent[0]['position_m']))
            self.assertEqual(value,read(cursor))
            before=service.scheduler.world
            with self.assertRaises(ValueError):read(True)
            self.assertIs(service.scheduler.world,before)
            self.assertEqual(service.read(legacy=True)['view']['presentation']['finished_projectiles'][0]['id'],projectile.id)


if __name__=='__main__':unittest.main()
