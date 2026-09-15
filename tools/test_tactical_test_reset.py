from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.tactical_test_reset import reset, TABLES
from backend.high_wilderness_sidecar.prepared_launch_store import BattleLease
from backend.high_wilderness_sidecar import persistent_ship as ps
from tools.test_battle_preparation import fixture


class TacticalTestResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = SidecarServer('backend.resettest', settlement_dir=self.root/'store', recovery_dir=self.root/'editor')
        self.addCleanup(self.close_lease)
        self.params = dict(reset_id='reset.test', scope='all_tactical_test_state')
        document, _, _ = fixture(self.server.editor.index)
        self.design = self.root/'saved-outfit.json'
        self.design.write_text(ps.encode(document), encoding='utf-8')

    def close_lease(self):
        if self.server.realtime._prepared_lease:
            self.server.realtime._prepared_lease.close()

    def call(self, method, params):
        return self.server.preparation.dispatch(dict(method='tactical.preparation.'+method,
            params=params, session_id=None, expected_revision=None))

    def prepare(self):
        grant = self.server.editor.store.bind(str(self.design), 'open', None, None)
        self.call('import', dict(instance_id='instance.reset.ship', source=dict(kind='file', value=grant['destination_handle'])))
        self.call('open', dict(preparation_id='preparation.reset', instance_ids=['instance.reset.ship']))
        self.call('commit', dict(preparation_id='preparation.reset', revision=0))

    def launch(self):
        self.prepare()
        self.server.tactical.mode = 'tactical'
        return self.server.realtime.deploy_prepared(dict(preparation_id='preparation.reset',
            launch_id='launch.reset', direct_instance_id='instance.reset.ship'))

    def assert_empty(self):
        with self.server.preparation.store.connection() as db:
            present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table in present:
                    self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)

    def test_active_battle_clears_and_releases_lease_without_saving(self):
        entry = self.launch(); before_file = self.design.read_bytes(); editor = self.server.editor
        self.server.realtime.scheduler.resume()
        result = reset(self.server, self.params)
        self.assertTrue(result['cleared']); self.assertEqual(result['removed']['ships'], 1)
        self.assertIsNone(self.server.realtime.scheduler); self.assertEqual(self.server.tactical.mode, 'editor')
        self.assertIs(self.server.editor, editor); self.assertEqual(self.design.read_bytes(), before_file)
        self.assert_empty()
        lease = BattleLease(self.server.realtime.store.directory)
        self.assertIsNotNone(lease.file); lease.close()
        with self.assertRaises(ps.ContractError):
            self.server.realtime.dispatch(dict(method='tactical.realtime.read', params=dict(scene_id=entry['status']['epoch'],
                known_static_sha256=None, ack_inputs=[], ack_events=0)), mode='editor')
        self.prepare()
        packet = self.call('read', dict(preparation_id='preparation.reset'))
        self.assertEqual(packet['supply']['ammunition_resources'], 1000)
        self.assertFalse(self.call('library', {})['ships'][0]['blocked'])

    def test_unreadable_pending_rows_do_not_block_forced_clear(self):
        self.launch(); self.server.realtime.gunnery.withdraw(); self.server.realtime.publish()
        with self.server.preparation.store.connection() as db:
            db.execute("INSERT INTO results VALUES ('settlement.corrupt','broken','bad',0)")
        with self.assertRaises(ps.ContractError): self.server.realtime.store.list()
        result = reset(self.server, self.params)
        self.assertEqual(result['removed']['results'], 2)
        self.assert_empty()

    def test_retry_does_not_erase_a_new_battle_even_after_restart(self):
        self.prepare(); receipt = reset(self.server, self.params)
        entry = self.launch(); live = self.server.realtime
        self.assertEqual(reset(self.server, self.params), receipt)
        self.assertIs(self.server.realtime, live); self.assertEqual(live.scheduler.world.epoch, entry['status']['epoch'])
        self.close_lease()
        self.server = SidecarServer('backend.resetretry', settlement_dir=self.root/'store', recovery_dir=self.root/'editor')
        self.assertEqual(reset(self.server, self.params), receipt)
        self.assertEqual(len(self.call('library', {})['ships']), 1)
        reset(self.server, dict(self.params, reset_id='reset.next'))
        self.assert_empty()

    def test_other_window_cannot_reset_a_live_owner(self):
        self.launch(); original = self.server.realtime
        other = SidecarServer('backend.other', settlement_dir=self.root/'store', recovery_dir=self.root/'other-editor')
        with self.assertRaisesRegex(ps.ContractError, '另一个战术窗口'):
            reset(other, self.params)
        self.assertIsNotNone(original.scheduler); self.assertIsNotNone(original._prepared_lease.file)
        with original.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ships').fetchone()[0], 1)

    def test_failed_transaction_preserves_scene_and_all_rows(self):
        self.launch(); original = self.server.realtime
        with original.store.connection() as db:
            db.execute("CREATE TRIGGER reset_failure BEFORE DELETE ON preparations BEGIN SELECT RAISE(ABORT,'injected reset failure'); END")
        with self.assertRaises(ps.ContractError): reset(self.server, self.params)
        self.assertIs(self.server.realtime, original); self.assertIsNotNone(original._prepared_lease.file)
        with original.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ships').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM preparations').fetchone()[0], 1)
            db.execute('DROP TRIGGER reset_failure')
        reset(self.server, self.params)
        self.assert_empty()

    def test_scope_is_explicit(self):
        self.prepare()
        with self.assertRaises(ps.ContractError): reset(self.server, dict(self.params, scope='editor'))
        self.assertEqual(len(self.call('library', {})['ships']), 1)


if __name__ == '__main__': unittest.main()
