from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools import tactical_testbench as tb
from backend.high_wilderness_sidecar.server import SidecarServer
from tools.test_tactical_scheduler import Clock


class TestBenchTests(unittest.TestCase):
    def test_all_samples_compile_prepare_and_enter_real_battle(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for scenario in tb.SCENARIOS:
                with self.subTest(scenario=scenario):
                    run = tb.create_run(root, scenario)
                    path = tb.run_path(root, run['id'])
                    server = SidecarServer('backend.benchtest', settlement_dir=path/'store', recovery_dir=path/'recovery')
                    service, live = server.preparation, server.realtime
                    clock = Clock(); live.clock = clock
                    def call(name, **params):
                        return service.dispatch(dict(method='tactical.preparation.'+name, params=params, session_id=None, expected_revision=None))
                    key = 'instance.testbench.player'
                    packet = call('open', preparation_id='prepare.bench', instance_ids=[key])
                    self.assertTrue(call('preview', preparation_id='prepare.bench', revision=packet['draft']['revision'])['can_commit'])
                    call('commit', preparation_id='prepare.bench', revision=packet['draft']['revision'])
                    try:
                        live.dispatch(dict(method='tactical.realtime.deploy_prepared', params=dict(preparation_id='prepare.bench', launch_id='launch.bench', direct_instance_id=key), session_id=None, expected_revision=None), mode='tactical')
                        live.scheduler.resume()
                        for _ in range(3):
                            clock.advance(16_666_667); live.scheduler.pump()
                        live.scheduler.pause()
                        live.gunnery.withdraw(); live.publish()
                        live.dispatch(dict(method='tactical.realtime.save', params=dict(settlement_id=live._result['settlement_id']), session_id=None, expected_revision=None), mode='tactical')
                        self.assertTrue(live._result_saved)
                    finally:
                        if live._prepared_lease: live._prepared_lease.close()
                    reopened = SidecarServer('backend.benchrestart', settlement_dir=path/'store')
                    saved_ship = next(s for s in reopened.preparation.library()['ships'] if s['instance_id'] == key)
                    self.assertGreater(reopened.realtime.store.load_ship(key, saved_ship['revision'])['state']['revision'], 1)
                    self.assertEqual(tb.read_run(root, run['id']), run)
            self.assertEqual(len(tb.list_runs(root)), len(tb.SCENARIOS))

    def test_invalid_paths_and_notes_cannot_escape_or_recreate_a_run(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for key in ['../player', 'run-x', 'C:/player']:
                with self.assertRaises(ValueError): tb.run_path(root, key)
            with self.assertRaises(ValueError): tb.create_run(root, 'unknown')
            run = tb.create_run(root, 'roundtrip')
            tb.add_note(root, run['id'], '测试问题一')
            tb.add_note(root, run['id'], '测试问题二')
            self.assertEqual(len((tb.run_path(root,run['id'])/'notes.jsonl').read_text(encoding='utf-8').splitlines()), 2)
            before = (tb.run_path(root,run['id'])/'entry.json').read_bytes()
            tb.read_run(root,run['id']);tb.list_runs(root)
            self.assertEqual(before,(tb.run_path(root,run['id'])/'entry.json').read_bytes())


if __name__ == '__main__': unittest.main()
