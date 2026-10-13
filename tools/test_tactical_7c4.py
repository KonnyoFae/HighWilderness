"""Publication ownership, definition invalidation and the encoded worker boundary."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import server, realtime_view as rv, persistent_ship as ps
from backend.high_wilderness_sidecar import missile_flight, view_cache
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from tools import profile_tactical_7c4 as comparison
from tools.tactical_7b_fixture import ROOT, restore
from tools.tactical_7c_oracle import snapshot
from tools.test_tactical_scheduler import Clock
from 高天荒野Web桥接协议 import encode_message, response_for, validate_message
from 高天荒野舰艇数据契约 import ContractError
from tools.tactical_7c4_live import Recorder, TimedQueue
from queue import Full, Empty


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        comparison.references()
        cls.frozen=json.loads((ROOT/'artifacts/tactical-7b-20261009/mixed-4v4/fixture.json').read_text(encoding='utf-8'))

    def service(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        s=rv.RealtimeViewService('backend.7c.compare',clock=Clock(),settlement_dir=temp.name)
        b,geometry,_=restore(self.frozen,s,'fixed');s._attach(b,geometry)
        return s,b

    def test_complete_view_matches_reference_and_reads_own_their_trees(self):
        s,b=self.service();before=snapshot(b)
        with comparison.implementation(True):old=json.loads(json.dumps(b.view()))
        self.assertEqual(old,json.loads(json.dumps(b.view())))
        packet=s.read();packet['view']['gunnery']['aviation']['ships'][0]['profile']['corrupt']=True
        packet['view']['gunnery']['missiles']['ships'][0]['state']['launchers'].clear()
        packet['view']['static']['ships'].clear()
        again=s.read()
        self.assertNotIn('corrupt',again['view']['gunnery']['aviation']['ships'][0]['profile'])
        self.assertTrue(again['view']['gunnery']['missiles']['ships'][0]['state']['launchers'])
        self.assertTrue(again['view']['static']['ships']);self.assertEqual(before,snapshot(b))

    def test_one_summary_per_ship_and_one_availability_per_publication(self):
        s,b=self.service();calls=[]
        from contextlib import ExitStack
        with ExitStack() as stack:
            availability=stack.enter_context(patch.object(b,'_availability',wraps=b._availability))
            for inv in b.inventory.inventories:calls.append(stack.enter_context(patch.object(inv,'summary',wraps=inv.summary)))
            b.view()
            self.assertEqual(availability.call_count,1)
            self.assertTrue(all(call.call_count==1 for call in calls))

    def test_same_step_inventory_and_damage_changes_are_not_cached(self):
        s,b=self.service();step=b.session.world.fixed_step
        first=b.aviation.view()['ships'][0]
        index=next(n for n,ship in enumerate(b.session.world.ships) if ship.ship_id==first['ship_id'])
        inv=b.inventory.inventories[index];revision=inv._reservation_revision
        inv._value=deepcopy(inv._value);inv._value['cargo'][0]['quantity']+=2
        hold=inv._definition['holds'][0]['module_id'];inv._health={**inv._health,hold:0.}
        current=b.view()['aviation']['ships'][0]
        self.assertNotEqual(first['cargo'],current['cargo'])
        self.assertEqual(current['capacity'],inv.summary());self.assertLess(current['capacity']['capacity_cm3'],first['capacity']['capacity_cm3'])
        self.assertEqual(step,b.session.world.fixed_step);self.assertEqual(revision,inv._reservation_revision)

    def test_definition_versions_replace_cache_and_do_not_rewrite_old_readouts(self):
        s,b=self.service();row=b.aviation.view()['ships'][0];old=row['profile']
        index=next(n for n,ship in enumerate(b.session.world.ships) if ship.ship_id==row['ship_id'])
        inv=b.inventory.inventories[index]
        for version in range(20):
            definition=deepcopy(inv._definition);definition['aviation']['display_revision']=version
            inv._definition=definition;inv.pack=replace(inv.pack,definition_json=ps.encode(definition))
            value=b.aviation.view()['ships'][0]['profile']
            self.assertEqual(value['display_revision'],version)
            value['corrupt']=True
            self.assertNotIn('corrupt',b.aviation.view()['ships'][0]['profile'])
        self.assertNotIn('display_revision',old)
        self.assertLessEqual(len(b.aviation._view_profiles),len(b.session.world.ships))

    def test_missile_model_set_and_flight_profile_version_invalidate(self):
        s,b=self.service();inv=b.inventory.inventories[0];old=b.missiles.view()['ships'][0]['profile']
        key=inv._value['missiles']['launchers'][0]['model_id']
        profiles=dict(missile_flight.profiles());profiles[key]=replace(profiles[key],speed_cap=profiles[key].speed_cap+12.)
        with patch.object(missile_flight,'profiles',return_value=profiles):
            new=b.missiles.view()['ships'][0]['profile']
            self.assertEqual(new['flight_profiles'][key]['speed_cap_mps'],old['flight_profiles'][key]['speed_cap_mps']+12.)
        self.assertEqual(b.missiles.view()['ships'][0]['profile'],old)
        inv._value=deepcopy(inv._value)
        for group in ('launchers','magazines'):
            inv._value['missiles'][group]=[r for r in inv._value['missiles'][group] if r['model_id']!=key]
        # A display-only runtime with no launcher controls lets us test the new
        # model set without pretending this synthetic inventory is a legal order.
        b.missiles.states={}
        self.assertNotIn(key,b.missiles.view()['ships'][0]['profile']['flight_profiles'])
        self.assertLessEqual(len(b.missiles._view_profiles),len(b.session.world.ships))

    def test_failed_publication_keeps_last_snapshot_and_can_retry(self):
        s,b=self.service();before=snapshot(b);old=s.latest
        with patch.object(rv,'MAX_RESPONSE_BYTES',1),self.assertRaises(ContractError):s.read()
        with patch.object(s,'_size',side_effect=ContractError('test','test','test')),self.assertRaises(ContractError):s.publish()
        self.assertIs(s.latest,old);self.assertEqual(before,snapshot(b));s.publish();self.assertEqual(before,snapshot(b))

    def test_negotiated_delta_reset_and_legacy_wire_match_owned_responses(self):
        s,b=self.service();p=s.read(s.digest,display=dict(interface=INTERFACE,after_sequence=None));cursor=p['view']['projectile_stream']['sequence']
        for _ in range(5):s.scheduler._stepper();s.scheduler._committed=b.session.world
        s.publish()
        for after in (None,cursor,0,999999):
            packet=s.read(s.digest,display=dict(interface=INTERFACE,after_sequence=after))
            actual=server.realtime_response(comparison.request(),packet,wire=True)
            expected=encode_message(response_for(comparison.request(),result=packet))
            self.assertEqual(actual,expected)
            self.assertEqual(json.loads(actual)['result'],packet)
        legacy=s.read(s.digest,legacy=True)
        self.assertEqual(json.loads(server.realtime_response(comparison.request(),legacy,wire=True))['result'],legacy)
        self.assertEqual(s.scheduler.world.fixed_step,5)

    def test_worker_execute_wire_preserves_errors_and_new_battle_has_fresh_cache(self):
        s,b=self.service();host=server.SidecarServer.__new__(server.SidecarServer)
        host.handshake_complete=True;host.realtime=s;host.tactical=SimpleNamespace(mode='tactical')
        request=comparison.request();request['params']=dict(scene_id=b.session.world.epoch,known_static_sha256=s.digest,ack_inputs=[],ack_events=0)
        direct=host.execute(request)[0][0];encoded=host.execute(request,wire=True)[0][0]
        self.assertEqual(json.loads(encoded),direct)
        before=snapshot(b);request['params']['ack_inputs']=[999]
        error=host.execute(request,wire=True)[0][0]
        self.assertIs(type(error),dict);self.assertFalse(error['ok']);validate_message(error)
        self.assertEqual(before,snapshot(b))
        other,_=self.service()
        self.assertIsNot(other.gunnery.aviation._view_profiles,b.aviation._view_profiles)
        self.assertIsNot(other.gunnery.missiles._view_profiles,b.missiles._view_profiles)


class WireBoundaryTests(unittest.TestCase):
    def test_wire_is_identical_immutable_and_owned_dict_api_is_unchanged(self):
        value={'text':'雪、\n"\\','nested':[{'value':3}],'none':None}
        expected=encode_message(response_for(comparison.request(),result=value))
        actual=server.realtime_response(comparison.request(),value,wire=True)
        owned=server.realtime_response(comparison.request(),value)
        value['nested'][0]['value']=999
        self.assertEqual(actual,expected);self.assertEqual(owned['result']['nested'][0]['value'],3)
        validate_message(json.loads(actual))

    def test_json_contract_is_not_bypassed_by_encoded_path(self):
        for value in (float('nan'),float('inf'),2**54,'\ud800',(1,2),{1:'bad'}):
            with self.subTest(value=repr(value)),self.assertRaises(ContractError):
                server.realtime_response(comparison.request(),{'bad':value},wire=True)
        deep={};cursor=deep
        for _ in range(70):cursor['x']={};cursor=cursor['x']
        with self.assertRaises(ContractError):server.realtime_response(comparison.request(),deep,wire=True)

    def test_wire_frame_budget_is_still_enforced(self):
        with patch.object(server,'encode_message',side_effect=lambda value:encode_message(value,max_frame_bytes=250)):
            with self.assertRaises(ContractError):server.realtime_response(comparison.request(),{'large':'x'*1000},wire=True)

    def test_failed_cache_build_does_not_replace_previous_valid_entry(self):
        cache={};first=view_cache.definition(cache,0,'v1',lambda:{'a':[1]});saved=cache[0]
        first['a'].append(2)
        self.assertEqual(view_cache.definition(cache,0,'v1',lambda:None),{'a':[1]})
        with self.assertRaises(ValueError):view_cache.definition(cache,0,'v2',lambda:{'a':float('nan')})
        self.assertEqual(cache[0],saved)

    def test_diagnostic_queue_keeps_bounds_order_and_task_accounting(self):
        recorder=Recorder(limit=2);queue=TimedQueue(1,recorder,'input');recorder.active=True
        value={'request':'one'};queue.put_nowait(value)
        with self.assertRaises(Full):queue.put_nowait('two')
        self.assertIs(queue.get_nowait(),value);queue.task_done();queue.join()
        with self.assertRaises(Empty):queue.get_nowait()
        for _ in range(4):queue.put(None);self.assertIsNone(queue.get());queue.task_done()
        row=recorder.report()['input.residence']
        self.assertEqual(row['count'],5);self.assertEqual(row['retained_samples'],2)


if __name__=='__main__':unittest.main()
