"""SCIC installation, historical archives and real preparation/entry/settlement."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, outfits
from backend.high_wilderness_sidecar import persistent_ship as ps, tactical_fleet as fleet
from backend.high_wilderness_sidecar import tactical_encounter as encounter
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import ROOT, fixture
from tools.test_tactical_scheduler import Clock
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_scheduler import DomainBatch
from 高天荒野舰艇数据契约 import ResourceReference


def ref(model):
    return dict(id='gtw.module.'+model, version=1)


def design_document(index, model=None):
    document, deployment, policy = fixture(index)
    if model:
        next(m for m in document['outfit']['modules'] if m['prototype']['id']=='gtw.module.fixture.cic')['prototype']=ref(model)
    return document, deployment, policy


class SCICModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)

    def test_all_models_compile_and_keep_core_role(self):
        for model, capacity, mass, crew in [('scic.basic',3,2000,4),('scic.basic.unmanned',3,2400,0),
                                          ('scic.advanced',8,4000,8),('scic.advanced.unmanned',8,4800,0)]:
            with self.subTest(model=model):
                document, deployment, policy=design_document(self.index,model)
                next(c for c in deployment['crew'] if c['crew_type']=='officer')['count']=4
                design=bp.compile_design(document,self.index,deployment,policy,ship_id='ship.scic')
                core=next(m for m in design.snapshot.outfit.instances if m.prototype.category=='cic')
                p=core.prototype.to_dict()
                self.assertEqual(p['mass_kg'],mass)
                self.assertEqual(sum(c['standard'] for c in p['crew']),crew)
                info=fleet.core_info(design,bp.new_record(design,'instance.scic'))
                self.assertEqual(info['companion_capacity'],capacity)
                self.assertTrue(info['available'])
                restored=bp.restore_design(design.archive(),self.index)
                self.assertEqual(design,restored)

    def test_explicit_replacement_preserves_children_and_legacy_read(self):
        document, deployment, policy=design_document(self.index)
        core=next(m for m in document['outfit']['modules'] if m['prototype']['id']=='gtw.module.fixture.cic')
        next(m for m in document['outfit']['modules'] if m['id']=='remote_core')['id']='legacy.remote'
        source,binding=outfit_documents.unpack(document,self.index)
        editor=outfits.document(source,self.index,binding['hull'])
        editor.compile()  # historical ordinary CIC + remote remains readable
        with self.assertRaises(ps.ContractError):
            editor.place_hosted('new.remote',ref('fixture.remote_core'),core['id'])
        with self.assertRaises(ps.ContractError):editor.rehost('legacy.remote',core['id'])
        before=editor.source_dict()
        outfits.command(editor,'outfit.replace_cic',dict(instance_id=core['id'],prototype=ref('scic.basic.unmanned')),self.index)
        after=editor.source_dict()
        self.assertEqual(after['modules'][-1],before['modules'][-1])
        self.assertEqual(next(m for m in after['modules'] if m['id']==core['id'])['placement'],core['placement'])
        editor.compile()
        # Both remote revisions occupy the same physical berth.
        with self.assertRaises(ps.ContractError):
            outfits.command(editor,'outfit.place_hosted',dict(instance_id='duplicate.remote',
                prototype=dict(id='gtw.module.fixture.remote_core',version=2),host_instance_id=core['id']),self.index)
        editor=outfits.document(after,self.index,binding['hull'])
        with self.assertRaises(ps.ContractError):editor.replace_cic(core['id'],ref('fixture.cic'))
        self.assertEqual(editor.source_dict(),after)
        editor.remove('legacy.remote')
        outfits.command(editor,'outfit.place_hosted',dict(instance_id='new.remote',
            prototype=dict(id='gtw.module.fixture.remote_core',version=2),host_instance_id=core['id']),self.index)
        editor.compile()
        saved=ps.decode(outfit_documents.encode(editor.source_dict(),binding))
        source,reopened=outfit_documents.unpack(saved,self.index)
        self.assertEqual(outfits.document(source,self.index,reopened['hull']).compile(),editor.compile())
        # New SCIC keeps base-deck origin restriction.
        editor.move_grid(core['id'],anchor_half_cell=[2,0])
        with self.assertRaises(ps.ContractError):editor.compile()

    def test_archive_before_scic_catalog_restores_without_upgrade(self):
        old=next(i for i in outfit_documents.catalog_generations(self.index) if not any(d['id']==outfit_documents.SCIC_CATALOG for d,_ in i.resources.values()))
        document,deployment,policy=design_document(old)
        core=next(m for m in document['outfit']['modules'] if m['prototype']['id']=='gtw.module.fixture.cic')
        next(m for m in document['outfit']['modules'] if m['id']=='remote_core')['id']='legacy.remote'
        design=bp.compile_design(document,old,deployment,policy,ship_id='ship.legacy')
        restored=bp.restore_design(design.archive(),self.index)
        self.assertEqual(restored,design)
        r=bp.new_record(restored,'instance.legacy')
        self.assertTrue(fleet.validate([(restored,r)],'instance.legacy')['valid'])
        self.assertEqual(fleet.core_info(restored,r)['companion_capacity'],0)


class SCICQualificationTests(unittest.TestCase):
    def test_capacity_boundaries_flagship_only_and_both_sizes(self):
        def core(cap):return dict(name='core',available=True,companion_capacity=cap)
        for capacity in (0,3,8):
            for count in range(1,11):
                with self.subTest(capacity=capacity,count=count):
                    members={str(i):core(8) for i in range(count)}
                    members['0']=core(capacity)
                    result=fleet.qualification(members,'0')
                    self.assertEqual(result['valid'],count==1 or (capacity>0 and count<=capacity+1))
                    self.assertEqual(result['companion_capacity'],capacity)
        self.assertFalse(fleet.qualification({},None)['valid'])
        self.assertFalse(fleet.qualification({'0':core(8)},'other')['valid'])
        damaged=core(8);damaged['available']=False
        self.assertFalse(fleet.qualification({'0':damaged},'0')['valid'])


class SCICFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.server=SidecarServer('backend.scic',settlement_dir=Path(self.temp.name)/'store')
        self.service=self.server.preparation;self.live=self.server.realtime
        self.clock=Clock();self.live.clock=self.clock
        self.addCleanup(lambda:self.live._prepared_lease and self.live._prepared_lease.close())

    def call(self,action,params):
        return self.service.dispatch(dict(method='tactical.preparation.'+action,params=params,session_id=None,expected_revision=None))

    def import_ship(self,identity,model=None):
        document,_,_=design_document(self.server.editor.index,model)
        path=Path(self.temp.name)/'import.json';path.write_text(ps.encode(document),encoding='utf-8')
        grant=self.server.editor.store.bind(str(path),'open',None,None)
        self.call('import',dict(instance_id=identity,source=dict(kind='file',value=grant['destination_handle'])))

    def layout(self,enemy,player,flagship=None,preparation_id=None):
        v=self.call('scene_read',{})['scene'];revision=v['revision'];v['revision']+=1
        v['preparation_id']=preparation_id
        for side,ids in zip(v['sides'],(enemy,player)):
            side['ships']=[dict(instance_id=key,x_m=i*200,y_m=0,heading_rad=0) for i,key in enumerate(ids)]
            side['flagship_instance_id']=flagship if side['id']=='player' and flagship else ids[0]
        return self.call('scene_save',dict(scene=v,expected_revision=revision))

    def test_invalid_layout_retained_preparation_and_direct_entry_blocked(self):
        for key,model in [('enemy',None),('ordinary',None),('basic','scic.basic'),('advanced','scic.advanced'),('wing1',None),('wing2',None)]:
            self.import_ship('instance.'+key,model)
        player=['instance.'+k for k in ('ordinary','basic','advanced','wing1','wing2')]
        v=self.layout(['instance.enemy'],player,preparation_id='preparation.scic')
        packet=self.call('scene_read',{})
        self.assertFalse(packet['fleets'][1]['valid'])
        self.assertEqual(packet['scene'],v)  # keep editing, never truncate
        draft=self.call('open',dict(preparation_id='preparation.scic',instance_ids=['instance.enemy']+player))
        before=self.call('library',{})
        self.assertFalse(self.call('preview',dict(preparation_id='preparation.scic',revision=0))['can_commit'])
        with self.assertRaises(ps.ContractError):self.call('commit',dict(preparation_id='preparation.scic',revision=0))
        with self.assertRaises(ps.ContractError):self.call('scene_encounter',dict(revision=v['revision'],launch_id='launch.invalid'))
        # Direct ST0 caller cannot bypass UI checks.
        req=dict(interface=encounter.INTERFACE,encounter_id='encounter.invalid',world_id='world.scic',world_revision=0,player_side_id='side.player',sides=[])
        for side in v['sides']:
            req['sides'].append(dict(side_id='side.'+side['id'],fleet_id='fleet.'+side['id'],flagship_instance_id=side['flagship_instance_id'],
                ships=[dict(instance_id=m['instance_id'],revision=1,deployment=dict(x_m=0,y_m=0,heading_rad=0)) for m in side['ships']]))
        with self.assertRaises(ps.ContractError):self.live.deploy_encounter(req)
        self.assertEqual(before,self.call('library',{}))
        v=self.layout(['instance.enemy'],player,flagship='instance.basic',preparation_id='preparation.scic')
        self.assertIn('4 / 3',self.call('scene_read',{})['fleets'][1]['issues'][0])
        v=self.layout(['instance.enemy'],player,flagship='instance.advanced',preparation_id='preparation.scic')
        self.assertTrue(self.call('preview',dict(preparation_id='preparation.scic',revision=0))['can_commit'])
        self.call('commit',dict(preparation_id='preparation.scic',revision=0))
        req=self.call('scene_encounter',dict(revision=v['revision'],launch_id='launch.valid'))
        # Enemy uses exactly the same requirement.
        req['sides'][0]['ships'].append(req['sides'][1]['ships'].pop())
        with self.assertRaises(ps.ContractError):self.live.deploy_encounter(req)

    def test_nine_vs_nine_save_restart_and_reenter(self):
        ids=[f'instance.scic.{i}' for i in range(18)]
        for i,key in enumerate(ids):self.import_ship(key,'scic.advanced.unmanned' if i in (0,9) else None)
        v=self.layout(ids[:9],ids[9:],preparation_id='preparation.full')
        self.call('open',dict(preparation_id='preparation.full',instance_ids=ids))
        self.call('commit',dict(preparation_id='preparation.full',revision=0))
        self.assertTrue(all(f['valid'] for f in self.call('scene_read',{})['fleets']))
        req=self.call('scene_encounter',dict(revision=v['revision'],launch_id='launch.full'))
        view=self.live.deploy_encounter(req)
        self.assertEqual(len(view['view']['ships']),18)
        self.live.scheduler.resume()
        for _ in range(30):self.clock.advance(16_666_667);self.live.scheduler.pump()
        self.live.scheduler.pause()
        self.assertGreater(self.live.gunnery.session.world.fixed_step,0)
        self.live.gunnery.withdraw();self.live.publish()
        saved=self.live.store.save(self.live._result['settlement_id'])
        self.assertEqual(len(saved['result']['ships']),18)
        self.live._result_saved=True;self.live._prepared_lease.close()
        other=SidecarServer('backend.scic.restart',settlement_dir=self.live.store.directory)
        self.addCleanup(lambda:other.realtime._prepared_lease and other.realtime._prepared_lease.close())
        self.assertEqual(other.preparation.library(),self.service.library())
        req['encounter_id']='launch.full.next'
        revisions={s['after']['state']['instance_id']:s['after']['state']['revision'] for s in saved['result']['ships']}
        for side in req['sides']:
            for member in side['ships']:member['revision']=revisions[member['instance_id']]
        self.assertEqual(len(other.realtime.deploy_encounter(req)['view']['ships']),18)

    def test_scic_destroyed_keeps_core_crash_and_saved_damage(self):
        self.import_ship('instance.scic','scic.advanced.unmanned');self.import_ship('instance.enemy')
        v=self.layout(['instance.enemy'],['instance.scic'])
        self.call('open',dict(preparation_id='preparation.damage',instance_ids=['instance.scic','instance.enemy']))
        self.call('commit',dict(preparation_id='preparation.damage',revision=0))
        self.live.deploy_encounter(self.call('scene_encounter',dict(revision=v['revision'],launch_id='launch.damage')))
        battle=self.live.gunnery;session=battle.session
        n=battle._direct_index;ship=session.world.ships[n]
        core_index=session._device_kernels[n].by_id['cic']
        module=ship.devices.modules[core_index]
        op=DeviceOperation(session.world.epoch,ship.ship_id,'cic',module.sequence+1,'damage',module.durability_points,0,'opening')
        self.live.scheduler._domains=lambda world:DomainBatch(device_operations=(op,)) if world.fixed_step==0 else DomainBatch()
        self.live.scheduler.resume();self.clock.advance(16_666_667);self.live.scheduler.pump();self.live.scheduler.pause()
        self.assertEqual(session.world.ships[n].devices.modules[core_index].durability_points,0)
        self.assertIsNotNone(session.world.ships[n].wreck)
        self.live.publish()
        if self.live._result is None:battle.withdraw();self.live.publish()
        saved=self.live.store.save(self.live._result['settlement_id'])['result']
        state=next(s['after']['state'] for s in saved['ships'] if s['after']['state']['instance_id']=='instance.scic')
        self.assertEqual(next(m for m in state['modules'] if m['module_id']=='cic')['durability_points'],0)
        self.assertNotEqual(state['service']['status'],'available')

    def test_fully_unmanned_scic_uses_new_remote_core_in_battle(self):
        document=ps.clone(next(s for d,s in self.server.editor.index.resources.values()
            if d['kind']=='OutfitPlan' and d['id'].endswith('unmanned_flagship')))
        catalog=outfits.module_catalog(self.server.editor.index)
        for module in document['modules']:
            category=catalog.module(ResourceReference.parse(module['prototype'],'$.prototype')).category
            if category=='cic':module['prototype']=ref('scic.basic.unmanned')
            if category=='remote_core':module['prototype']=dict(id='gtw.module.fixture.remote_core',version=2)
        path=Path(self.temp.name)/'unmanned.json';path.write_text(ps.encode(document),encoding='utf-8')
        grant=self.server.editor.store.bind(str(path),'open',None,None)
        self.call('import',dict(instance_id='instance.remote',source=dict(kind='file',value=grant['destination_handle'])))
        self.import_ship('instance.enemy')
        v=self.layout(['instance.enemy'],['instance.remote'])
        self.call('open',dict(preparation_id='preparation.remote',instance_ids=['instance.remote','instance.enemy']))
        self.call('commit',dict(preparation_id='preparation.remote',revision=0))
        self.live.deploy_encounter(self.call('scene_encounter',dict(revision=v['revision'],launch_id='launch.remote')))
        battle=self.live.gunnery;ship=battle.session.world.ships[battle._direct_index]
        self.assertTrue(ship.authority_allowed)
        state=battle.inventory.prepared.bindings[battle._direct_index].instance.to_dict()
        with self.service.store.connection() as db:
            raw=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',('instance.remote',)).fetchone()
            deployment=self.service.store._decode(*raw)['deployment']
        self.assertEqual(deployment['control_mode'],'remote_core')
        self.assertEqual(sum(c['count'] for c in state['crew']),0)


if __name__=='__main__':unittest.main()
