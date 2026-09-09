from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from queue import Queue
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService, MAX_RESPONSE_BYTES
from backend.high_wilderness_sidecar.tactical import render_static
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario, SCENARIO_ID
from backend.high_wilderness_sidecar.tactical_scheduler import ScheduledControl
from backend.high_wilderness_sidecar.tactical_scheduler import DomainBatch
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.test_tactical_scheduler import Clock
from tools.test_simplified_flight import command
from 高天荒野舰艇数据契约 import ContractError

ROOT = Path(__file__).resolve().parents[1]


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(ROOT, with_command=True)
        cls.scenario = build_two_ship_scenario(ROOT)

    def service(self):
        clock = Clock(); s = RealtimeViewService('backend.viewtest', clock=clock)
        factory = lambda *_args, **_kw: sf.SimplifiedFlightSession(self.sample._seeds,self.sample._profile,direct_ship_id=self.sample._direct)
        with patch('backend.high_wilderness_sidecar.realtime_view.build_sample_session', factory), \
             patch('backend.high_wilderness_sidecar.realtime_view.build_two_ship_scenario', return_value=self.scenario):
            self.call(s, 'create', dict(scenario_id=SCENARIO_ID))
        return s, clock

    def call(self, s, method, params=None, mode='tactical'):
        if params is None: params = dict(scene_id=s.scheduler.world.epoch)
        return s.dispatch(dict(method='tactical.realtime.'+method, params=params), mode=mode)

    def read(self, s, ack_inputs=None, ack_events=0):
        return self.call(s, 'read', dict(scene_id=s.scheduler.world.epoch, known_static_sha256=s.digest,
            ack_inputs=ack_inputs or [], ack_events=ack_events))

    def test_cached_snapshot_frequency_reads_never_advance_authority(self):
        s, clock = self.service(); self.call(s,'resume')
        initial = s.latest
        clock.advance(20_000_000)
        for _ in range(10): self.read(s)
        self.assertEqual(s.scheduler.world.fixed_step, 0)
        s.tick(); self.assertEqual(s.scheduler.world.fixed_step, 1)
        self.assertIs(s.latest, initial)
        clock.advance(50_000_000); s.tick()
        self.assertEqual(s.latest['fixed_step'],4)
        response = self.read(s)
        self.assertIsNone(response['view']['static'])
        response['view']['ships'][0]['position_m'][0] = 999999
        self.assertNotEqual(s.latest['ships'][0]['position_m'][0],999999)
        self.assertLess(len(json.dumps(s.read()).encode('utf-8')),MAX_RESPONSE_BYTES)

    def test_lease_pause_cancels_pending_and_does_not_resume_on_read(self):
        s,clock=self.service(); self.call(s,'resume')
        q=s.scheduler; r=ScheduledControl(q.world.epoch,0,1,self.sample._direct,6,command())
        self.call(s,'control',dict(scene_id=q.world.epoch,input=r.to_dict()))
        clock.advance(2_100_000_000); s.tick()
        self.assertFalse(s.running)
        self.assertEqual(q.query(q.world.epoch,1).receipt.reason,'disconnected')
        self.read(s)
        self.assertFalse(s.running)

    def test_projection_failure_keeps_commit_and_rebuilds_without_replay(self):
        s,clock=self.service(); self.call(s,'resume')
        old=s.latest; clock.advance(70_000_000)
        with patch.object(s,'publish',side_effect=RuntimeError('display')): s.tick()
        self.assertIs(s.latest,old); self.assertFalse(s.running)
        self.assertEqual(s.scheduler.world.fixed_step,4)
        current=s.scheduler.world
        response=self.read(s)
        self.assertEqual(response['view']['fixed_step'],4)
        self.assertIs(s.scheduler.world,current); self.assertIsNone(response['error'])

    def test_receipts_events_repeated_read_and_explicit_ack(self):
        s,clock=self.service(); self.call(s,'resume'); q=s.scheduler
        r=ScheduledControl(q.world.epoch,0,1,self.sample._direct,0,command())
        self.call(s,'control',dict(scene_id=q.world.epoch,input=r.to_dict()))
        clock.advance(70_000_000); s.tick()
        a,b=self.read(s),self.read(s)
        self.assertEqual(a,json.loads(json.dumps(a)))
        self.assertEqual(a['events'],b['events']); self.assertTrue(a['events'])
        self.assertEqual(a['receipts'][0]['status'],'executed')
        next_view=self.read(s,[1],a['events'][-1]['sequence'])
        self.assertEqual(next_view['receipts'],[]); self.assertEqual(next_view['events'],[])
        self.assertEqual(self.read(s,[1],a['events'][-1]['sequence'])['receipts'],[])

    def test_invalid_ack_batch_does_not_release_existing_results(self):
        s,clock=self.service(); self.call(s,'resume'); q=s.scheduler
        q.submit(ScheduledControl(q.world.epoch,0,1,self.sample._direct,0,command()))
        clock.advance(70_000_000); s.tick(); a=self.read(s)
        with self.assertRaises(ContractError): self.read(s,[1,2],a['events'][-1]['sequence'])
        self.assertEqual(q.query(q.world.epoch,1).status,'executed')
        self.assertEqual(q.status.acknowledged_event_sequence,0)

    def test_mode_pause_and_foreign_scope(self):
        s,clock=self.service(); self.call(s,'resume')
        clock.advance(40_000_000); s.tick(); s.pause('mode_exit')
        self.assertTrue(s.latest['paused'])
        self.assertEqual(s.latest['fixed_step'],s.scheduler.world.fixed_step)
        with self.assertRaises(ContractError): self.call(s,'resume',mode='editor')
        with self.assertRaises(ContractError): self.call(s,'pause',dict(scene_id='foreign'))
        self.call(s,'close'); self.assertIsNone(s.scheduler)

    def test_actual_cic_loss_is_visible_and_disables_controls(self):
        s,clock=self.service(); q=s.scheduler
        hp=next(m.maximum_durability_points for m in self.sample._seeds[0].devices.modules if m.instance_id=='cic')
        q._domains=lambda world: DomainBatch(device_operations=(DeviceOperation(world.epoch,self.sample._direct,
            'cic',1,'damage',hp,0,'opening'),)) if world.fixed_step==0 else DomainBatch()
        self.call(s,'resume'); clock.advance(70_000_000); s.tick()
        response=self.read(s)
        self.assertFalse(response['available']); self.assertEqual(response['loss_reason'],'direct_ship_falling')
        self.assertEqual(response['view']['ships'][0]['physical_status'],'falling')
        self.assertTrue(all(e['actual']==0 for e in response['engines']))

    def test_lost_create_close_replies_can_be_reconciled_without_reset(self):
        s,clock=self.service(); q=s.scheduler
        again=self.call(s,'create',dict(scenario_id=SCENARIO_ID))
        self.assertEqual(again['status']['epoch'],q.world.epoch)
        self.assertIs(s.scheduler,q)
        params=dict(scene_id=q.world.epoch)
        self.call(s,'close',params)
        self.assertEqual(self.call(s,'close',params),dict(closed=True))


class WorkerTests(unittest.TestCase):
    def test_real_stdio_worker_runs_without_reads_and_mode_switch_pauses(self):
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3btest'],
            cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        outputs=Queue()
        def collect():
            for line in process.stdout: outputs.put(json.loads(line))
        reader=Thread(target=collect,daemon=True); reader.start(); seq=0
        def request(method,params):
            nonlocal seq
            seq+=1; request_id=f'req.{seq}'
            value=dict(interface='gaotian.web-bridge/v1alpha1',kind='request',backend_instance_id='backend.e3btest',
                request_id=request_id,session_id=None,expected_revision=None,method=method,params=params)
            process.stdin.write(json.dumps(value)+'\n'); process.stdin.flush()
            while True:
                response=outputs.get(timeout=20)
                if response.get('request_id')==request_id:
                    self.assertTrue(response['ok'],response)
                    return response['result']
        try:
            request('system.hello',dict(client_name='e3btest',client_version='1',supported_interfaces=['gaotian.web-bridge/v1alpha1'],required_capabilities=['tactical.realtime.create']))
            request('tactical.set_mode',dict(mode='tactical'))
            created=request('tactical.realtime.create',dict(scenario_id=SCENARIO_ID)); scene=created['status']['epoch']
            request('tactical.realtime.resume',dict(scene_id=scene))
            sleep(.3)
            paused=request('tactical.realtime.pause',dict(scene_id=scene))
            self.assertGreater(paused['status']['fixed_step'],5)
            before=paused['status']['fixed_step']; sleep(.1)
            read=lambda: request('tactical.realtime.read',dict(scene_id=scene,known_static_sha256=created['view']['static_sha256'],ack_inputs=[],ack_events=0))
            self.assertEqual(read()['status']['fixed_step'],before)
            request('tactical.realtime.resume',dict(scene_id=scene))
            request('tactical.set_mode',dict(mode='editor'))
            a=read(); sleep(.1); b=read()
            self.assertFalse(a['status']['running']); self.assertEqual(a['status']['fixed_step'],b['status']['fixed_step'])
            request('system.shutdown',dict(reason='user_exit'))
            self.assertEqual(process.wait(timeout=10),0)
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
            process.stdin.close(); process.stdout.close(); process.stderr.close()


if __name__=='__main__': unittest.main()
