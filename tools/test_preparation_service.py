from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import persistent_ship as ps
from tools.test_battle_preparation import fixture


class PreparationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.server=SidecarServer('backend.preptest',settlement_dir=self.directory/'store')
        self.service=self.server.preparation
        self.source=next(s for s in self.call('library',{})['sources'] if '常规有人' in s['name'])

    def call(self,action,params):
        return self.service.dispatch(dict(method='tactical.preparation.'+action,params=params,session_id=None,expected_revision=None))

    def open(self):
        self.call('import',dict(instance_id='instance.ui',source=dict(kind='resource',value=self.source['key'])))
        return self.call('open',dict(preparation_id='preparation.ui',instance_ids=['instance.ui']))

    def test_import_saved_file_lost_response_retries_without_second_instance(self):
        document,_,_=fixture(self.server.editor.index)
        path=self.directory/'自建栖装.json';path.write_text(ps.encode(document),encoding='utf-8')
        grant=self.server.editor.store.bind(str(path),'open',None,None)
        params=dict(instance_id='instance.file',source=dict(kind='file',value=grant['destination_handle']))
        first=self.call('import',params)
        reopened=SidecarServer('backend.restarted',settlement_dir=self.directory/'store')
        self.service=reopened.preparation
        self.assertEqual(self.call('import',params),first)
        self.assertEqual(len(self.call('library',{})['ships']),1)
        changed=ps.clone(params);changed['source']['value']='file.other'
        with self.assertRaises(ps.ContractError):self.call('import',changed)

    def test_file_changed_after_selection_rejected(self):
        document,_,_=fixture(self.server.editor.index)
        path=self.directory/'outfit.json';path.write_text(ps.encode(document),encoding='utf-8')
        grant=self.server.editor.store.bind(str(path),'open',None,None)
        document['outfit']['name']='changed';path.write_text(ps.encode(document),encoding='utf-8')
        with self.assertRaises(ps.ContractError):
            self.call('import',dict(instance_id='instance.file',source=dict(kind='file',value=grant['destination_handle'])))
        self.assertEqual(self.call('library',{})['ships'],[])

    def test_draft_overwrite_restart_stale_revision_and_commit_retry(self):
        packet=self.open();draft=packet['draft']
        draft['revision']=1;draft['ships'][0]['magazines'][0]['quantity']=20
        first=self.call('draft',dict(draft=draft,expected_saved_revision=0))
        self.assertEqual(self.call('draft',dict(draft=draft,expected_saved_revision=0)),first)
        self.assertEqual(len(self.call('library',{})['drafts']),1)
        self.service=SidecarServer('backend.restarted',settlement_dir=self.directory/'store').preparation
        restored=self.call('read',dict(preparation_id='preparation.ui'))
        self.assertEqual(restored['draft'],draft)
        self.assertEqual(restored['ships'][0]['state']['magazines'][0]['quantity'],0)
        args=dict(preparation_id='preparation.ui',revision=1)
        self.assertTrue(self.call('preview',args)['can_commit'])
        result=self.call('commit',args)
        self.assertEqual(self.call('commit',args),result)
        self.assertEqual(self.call('read',dict(preparation_id='preparation.ui'))['receipt'],result)
        with self.assertRaises(ps.ContractError):self.call('commit',dict(preparation_id='preparation.ui',revision=0))

    def test_stale_draft_retained_without_overwriting_state(self):
        self.open()
        self.call('open',dict(preparation_id='preparation.other',instance_ids=['instance.ui']))
        self.call('commit',dict(preparation_id='preparation.other',revision=0))
        packet=self.call('read',dict(preparation_id='preparation.ui'))
        self.assertIsNotNone(packet['stale_error'])
        self.assertEqual(packet['draft']['revision'],0)

    def test_discard_draft_or_receipt_entry_never_undoes_inventory(self):
        packet=self.open();draft=packet['draft'];draft['revision']=1;draft['ships'][0]['magazines'][0]['quantity']=10
        self.call('draft',dict(draft=draft,expected_saved_revision=0))
        self.call('commit',dict(preparation_id='preparation.ui',revision=1))
        self.call('discard',dict(preparation_id='preparation.ui',revision=1))
        self.assertEqual(self.call('library',{})['drafts'],[])
        packet=self.call('open',dict(preparation_id='preparation.new',instance_ids=['instance.ui']))
        self.assertEqual(packet['ships'][0]['state']['magazines'][0]['quantity'],10)
        self.assertEqual(packet['supply']['ammunition_resources'],990)

    def test_open_retry_cannot_reset_draft_and_raw_policy_not_accepted(self):
        packet=self.open();draft=packet['draft'];draft['revision']=1
        self.call('draft',dict(draft=draft,expected_saved_revision=0))
        retry=self.call('open',dict(preparation_id='preparation.ui',instance_ids=['instance.ui']))
        self.assertEqual(retry['draft']['revision'],1)
        with self.assertRaises(ps.ContractError):self.call('import',dict(instance_id='instance.other',source=dict(kind='resource',value=self.source['key']),policy={}))


if __name__=='__main__':unittest.main()
