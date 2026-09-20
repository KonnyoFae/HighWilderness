from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import unittest
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import persistent_ship as ps
from backend.high_wilderness_sidecar import lift_reserve, outfit_documents
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar.tactical_scheduler import DomainBatch
from tools.test_battle_preparation import fixture
from tools.test_tactical_scheduler import Clock


class PreparedDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.server=SidecarServer('backend.launchtest',settlement_dir=Path(self.temp.name)/'store')
        self.addCleanup(self.close_lease)
        self.service=self.server.preparation;self.live=self.server.realtime
        self.clock=Clock();self.live.clock=self.clock

    def close_lease(self):
        if self.live._prepared_lease:self.live._prepared_lease.close()

    def call(self,action,params):
        return self.service.dispatch(dict(method='tactical.preparation.'+action,params=params,session_id=None,expected_revision=None))

    def prepare(self,count=1):
        document,_,_=fixture(self.server.editor.index)
        file=Path(self.temp.name)/'custom.json';file.write_text(ps.encode(document),encoding='utf-8')
        for i in range(count):
            # A multi-ship fixture explicitly refits its flagship. Solo fixtures
            # retain ordinary CIC so compatibility remains exercised.
            imported=ps.clone(document)
            if count > 1 and i == 0:
                next(m for m in imported['outfit']['modules'] if m['prototype']['id']=='gtw.module.fixture.cic')['prototype']=dict(id='gtw.module.scic.advanced',version=1)
            file.write_text(ps.encode(imported),encoding='utf-8')
            grant=self.server.editor.store.bind(str(file),'open',None,None)
            self.call('import',dict(instance_id=f'instance.custom.{i}',source=dict(kind='file',value=grant['destination_handle'])))
        packet=self.call('open',dict(preparation_id='preparation.launch',instance_ids=[f'instance.custom.{i}' for i in range(count)]))
        draft=packet['draft'];draft['revision']=1
        for row in draft['ships']:
            row['magazines'][0]['quantity']=30
            row['cargo']=[dict(good_id='cargo.special_alloy',quantity=2)]
            for w in row['weapons']:w.update(action='preload',recipe_id='recipe.x1a.ordinary',batches=1)
        self.call('draft',dict(draft=draft,expected_saved_revision=0))
        return self.call('commit',dict(preparation_id=draft['preparation_id'],revision=1))

    def launch(self,**kwargs):
        return self.live.deploy_prepared(dict(preparation_id='preparation.launch',launch_id='launch.test',direct_instance_id='instance.custom.0',**kwargs))

    def test_real_design_entry_reload_and_settlement(self):
        receipt=self.prepare(2);view=self.launch();battle=self.live.gunnery
        self.assertEqual(len(view['view']['static']['ships']),3)
        self.assertEqual(view,self.launch())
        self.assertEqual(view['view']['static']['ships'][0]['derived_snapshot_sha256'],receipt['ships'][0]['after']['design_sha256'])
        self.assertTrue(any(m['id']=='custom.cargo' for m in view['view']['static']['ships'][0]['modules']))
        self.assertEqual([i._value['cargo'] for i in battle.inventory.inventories[:2]],[[dict(good_id='cargo.special_alloy',quantity=2)]]*2)
        battle.submit(dict(epoch=battle.session.world.epoch,generation=0,sequence=1,weapon_id='weapon_upper_port',kind='target',arguments=dict(ship_id='ship.web.red',module_id=None)))
        self.live.scheduler.resume()
        for _ in range(600):self.clock.advance(16_666_667);self.live.scheduler.pump()
        self.assertGreater(battle.states[0].shots,1)
        self.assertGreater(battle.damage_state.hits,0)
        self.assertGreater(battle.states[1].shots,0)
        self.assertLess(battle.session.world.ships[0].motion.hull_integrity_fraction,1)
        self.live.scheduler.pause()
        battle.withdraw();self.live.publish()
        result=self.live._result
        saved=self.live.store.save(result['settlement_id'])
        self.assertTrue(saved['saved'])
        self.assertEqual(saved,self.live.store.save(result['settlement_id']))
        self.assertEqual(result['ships'][0]['before'],receipt['ships'][0]['after'])
        self.assertTrue(all(not s['blocked'] for s in self.call('library',{})['ships']))
        self.live._result_saved=True
        new=self.call('open',dict(preparation_id='preparation.next',instance_ids=['instance.custom.0','instance.custom.1']))
        self.assertEqual([s['state'] for s in new['ships']],[r['after']['state'] for r in result['ships'][:2]])
        self.call('commit',dict(preparation_id='preparation.next',revision=0))
        view=self.live.deploy_prepared(dict(preparation_id='preparation.next',launch_id='launch.next',direct_instance_id='instance.custom.0'))
        self.assertEqual(view['view']['ships'][0]['hull_integrity'],result['ships'][0]['after']['state']['hull_integrity_fraction'])
        self.assertEqual(self.live.gunnery.entry_armor[:2],battle.damage_state.armor[:2])
        self.assertTrue(all(w['target_ship_id'] is None and w['shots']==0 for w in view['view']['gunnery']['weapons']))
        self.assertEqual(self.live.gunnery.inventory.inventories[0]._value['cargo'],result['ships'][0]['after']['state']['cargo'])

    def test_stale_receipt_and_unended_close_rejected(self):
        self.prepare();view=self.launch()
        with self.assertRaises(ps.ContractError):self.live.dispatch(dict(method='tactical.realtime.close',params=dict(scene_id=view['status']['epoch']),session_id=None,expected_revision=None),mode='tactical')
        self.live.gunnery.withdraw();self.live.publish();self.live.store.save(self.live._result['settlement_id']);self.live._result_saved=True
        with self.assertRaises(ps.ContractError):self.live.deploy_prepared(dict(preparation_id='preparation.launch',launch_id='launch.stale',direct_instance_id='instance.custom.0'))

    def test_lift_design_preparation_combat_and_damage_agree(self):
        self.prepare()
        packet = self.call('read', dict(preparation_id='preparation.launch'))
        prepared = packet['ships'][0]['lift_reserve']
        document, _, _ = fixture(self.server.editor.index)
        source, binding = outfit_documents.unpack(document, self.server.editor.index)
        preview = self.server.editor.preview(source, binding)
        self.assertEqual(prepared, preview['model']['derived']['lift_reserve'])
        with self.service.store.connection() as db:
            ships, _, _ = self.service.store._draft_inputs(db, packet['draft'])
        design, record = ships[0]
        view = self.launch()
        self.assertEqual(prepared, view['view']['ships'][0]['lift_reserve'])
        unloaded = ps.clone(record)
        unloaded['state']['cargo'] = []
        self.assertEqual(lift_reserve.prepared(design, unloaded), prepared)
        session = self.live.gunnery.session
        seed = session._seeds[0]
        tank = next(m for m in seed.resources.modules if m.prototype.category == 'lift_fuel_tank')
        index = session._device_kernels[0].by_id[tank.id]
        for remaining in (tank.prototype.durability_points * .01, 0):
            module = session.world.ships[0].devices.modules[index]
            step = session.world.fixed_step
            op = DeviceOperation(session.world.epoch, session._direct, tank.id,
                module.sequence + 1, 'damage', module.durability_points - remaining, step, 'opening')
            scheduler = self.live.scheduler
            scheduler._domains = lambda world: DomainBatch(device_operations=(op,)) if world.fixed_step == step else DomainBatch()
            scheduler.resume(); self.clock.advance(16_666_667); scheduler.pump(); scheduler.pause()
            self.live.publish()
            actual = self.live.latest['ships'][0]['lift_reserve']
            next(m for m in record['state']['modules'] if m['module_id'] == tank.id)['durability_points'] = remaining
            record['state']['fuel_tanks'] = ps.clone(self.live.gunnery.inventory.inventories[0]._value['fuel_tanks'])
            record['state']['fuel_units'] = session.world.ships[0].motion.fuel_units
            if self.live._result is not None:
                record = ps.clone(self.live._result['ships'][0]['after'])
            self.assertEqual(actual, lift_reserve.prepared(design, record))
            self.assertEqual(actual['dry_mass_kg'], prepared['dry_mass_kg'])
            if remaining:
                self.assertEqual(actual, prepared)  # binary output, not linear HP
            else:
                self.assertLess(actual['available_force_n'], prepared['available_force_n'])
                self.assertLess(actual['reserve_fraction'], 0)

    def test_preparation_mode_recovers_pending_result_without_deployment(self):
        self.prepare(); self.launch(); self.live.gunnery.withdraw(); self.live.publish(); self.close_lease()
        other = SidecarServer('backend.recovery', settlement_dir=self.live.store.directory)
        def request(method, params):
            return other.realtime.dispatch(dict(method='tactical.realtime.' + method, params=params), mode='editor')
        pending = request('settlements', {})['results']
        identity = next(r['settlement_id'] for r in pending if not r['saved'])
        self.assertFalse(request('settlement', dict(settlement_id=identity))['saved'])
        saved = request('save', dict(settlement_id=identity))
        self.assertTrue(saved['saved'])
        self.assertEqual(saved, request('save', dict(settlement_id=identity)))
        self.assertFalse(other.preparation.library()['ships'][0]['blocked'])
        with self.assertRaises(ps.ContractError): request('deploy', dict(instance_id='instance.custom.0', revision=1, launch_id='launch.invalid'))

    def test_pending_settlement_survives_owner_exit_and_blocks_preparation(self):
        self.prepare();self.launch();self.live.gunnery.withdraw();self.live.publish();identity=self.live._result['settlement_id'];self.close_lease()
        other=SidecarServer('backend.restarted',settlement_dir=self.live.store.directory)
        self.assertTrue(other.preparation.library()['ships'][0]['blocked'])
        other.realtime.store.save(identity)
        self.assertFalse(other.preparation.library()['ships'][0]['blocked'])

    def test_no_ammunition_and_damaged_hold_overcapacity_can_enter(self):
        receipt=self.prepare();record=ps.clone(receipt['ships'][0]['after'])
        record['state']['revision']+=1
        record['state']['magazines'][0]['quantity']=0
        record['state']['weapons'][0].update(ready_rounds=0,recipe_id=None)
        next(m for m in record['state']['modules'] if m['module_id']=='custom.cargo')['durability_points']=0
        with self.service.store.connection() as db:self.service.store._write_ship(db,record)
        packet=self.call('open',dict(preparation_id='preparation.empty',instance_ids=['instance.custom.0']))
        self.assertTrue(packet['ships'][0]['capacity']['over_capacity'])
        self.call('commit',dict(preparation_id='preparation.empty',revision=0))
        view=self.live.deploy_prepared(dict(preparation_id='preparation.empty',launch_id='launch.empty',direct_instance_id='instance.custom.0'))
        self.assertEqual(view['view']['gunnery']['weapons'][0]['ready_rounds'],0)
        self.assertEqual(view['view']['gunnery']['weapons'][0]['ammo_resources'],0)
        self.assertTrue(self.live.gunnery.inventory.inventories[0].summary()['over_capacity'])

    def test_failed_attach_releases_claim_and_exact_retry(self):
        self.prepare()
        with patch.object(self.live,'_attach',side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):self.launch()
        self.assertFalse(self.call('library',{})['ships'][0]['blocked'])
        self.launch()

    def test_other_backend_cannot_release_live_claim(self):
        self.prepare();self.launch()
        other=SidecarServer('backend.other',settlement_dir=self.live.store.directory)
        self.assertTrue(other.preparation.library()['ships'][0]['blocked'])
        with self.assertRaises(ps.ContractError):other.realtime.deploy_prepared(dict(preparation_id='preparation.launch',launch_id='launch.other',direct_instance_id='instance.custom.0'))

    def test_interrupted_entry_recovers_saved_preparation_without_refund(self):
        receipt=self.prepare();self.launch();self.close_lease()
        other=SidecarServer('backend.restarted',settlement_dir=self.live.store.directory)
        library=other.preparation.library()
        self.assertFalse(library['ships'][0]['blocked']);self.assertEqual(library['interrupted_battles'],1)
        self.assertEqual(other.preparation.store.load_ship('instance.custom.0',1),receipt['ships'][0]['after'])


if __name__=='__main__':unittest.main()
