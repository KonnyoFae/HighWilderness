"""D1d owner-bound player input, shared supplies and exact legacy preservation."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from tools import test_tactical_repair as repair_tests
from tools.test_tactical_fire import DEVICE, TARGET
from tools.test_tactical_scheduler import Clock
from tools.test_battle_preparation import ROOT
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import prepared_deployment as deployment
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.preparation_service import SUPPLY_ID


class DamageControlUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repair_tests.TacticalRepairTests.setUpClass()
        cls.f=repair_tests.TacticalRepairTests()

    def scene(self,units=100000,parts=0):
        f=self.f
        record=f.record(health={TARGET:50},units=units,parts=parts)
        next(m for m in record['state']['modules'] if m['module_id']==DEVICE)['operating_mode']='off'
        b,geo=deployment.build([(f.design,record)],record['state']['instance_id'],f.template,f.scenario)
        b.enemy_fire=False
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        clock=Clock();service=RealtimeViewService('backend.d1d',clock=clock,settlement_dir=temp.name)
        service._attach(b,geo)
        self.call(service,'resume')
        return service,clock,b

    def call(self,s,kind,params=None):
        return s.dispatch(dict(method='tactical.realtime.'+kind,params=params or dict(scene_id=s.scheduler.world.epoch)),mode='tactical')

    def command(self,s,kind='enabled',**args):
        return dict(epoch=s.scheduler.world.epoch,generation=s.scheduler.status.generation,
            sequence=s.gunnery.fire.sequence+1,ship_id=s.gunnery.session._direct,module_id=DEVICE,kind=kind,arguments=args)

    def send(self,s,v):
        return self.call(s,'damage_control',dict(scene_id=s.scheduler.world.epoch,input=v))

    def advance(self,s,clock):
        clock.advance(16_666_667);s.tick()

    def test_rpc_activates_domain_then_repairs_and_reports_stocks(self):
        s,clock,b=self.scene()
        v=self.command(s,enabled=True);reply=self.send(s,v)
        device=reply['view']['gunnery']['damage_control']['devices'][0]
        self.assertTrue(device['mode_pending']);self.assertEqual(device['capacity_units'],100000)
        self.assertEqual(self.f.hp(b),50)
        self.advance(s,clock);self.assertAlmostEqual(self.f.hp(b),50.1)
        self.assertEqual(b.fire.controllers[0].status,'repairing_module')
        self.assertFalse(b.fire.pending_modes)
        stock=self.f.quantity(b)
        self.send(s,self.command(s,enabled=False));self.advance(s,clock)
        self.assertEqual(self.f.quantity(b),stock)
        self.assertEqual(b.session.world.ships[0].resources.modes[b._indices[0][DEVICE]],'off')

    def test_lost_reply_exact_retry_paused_no_reenable_or_duplicate(self):
        s,clock,b=self.scene();v=self.command(s,enabled=True)
        self.send(s,v);self.advance(s,clock);stock=self.f.quantity(b)
        self.call(s,'pause');self.send(s,v)
        self.assertEqual(self.f.quantity(b),stock);self.assertFalse(b.fire.pending_modes)
        with self.assertRaises(ps.ContractError):self.send(s,self.command(s,enabled=False))
        self.call(s,'resume')
        altered=dict(v,generation=s.scheduler.status.generation)
        with self.assertRaises(ps.ContractError):self.send(s,altered)
        self.assertTrue(b.fire.controllers[0].enabled)

    def test_invalid_enemy_ignition_and_foreign_targets_leave_everything_unchanged(self):
        s,clock,b=self.scene();v=self.command(s,enabled=True)
        old=b.fire.view()
        for bad in (dict(v,ship_id='ship.web.red'),dict(v,kind='test_ignite',arguments={'intensity_units':100,'duration_steps':100}),
                    dict(v,kind='repair_target',arguments={'module_id':'foreign'}),dict(v,epoch='old'),dict(v,generation=999)):
            with self.assertRaises(ps.ContractError):self.send(s,bad)
            self.assertEqual(b.fire.view(),old);self.assertFalse(b.fire.pending_modes)

    def test_mode_and_spend_rollback_then_successful_retry(self):
        s,clock,b=self.scene();self.send(s,self.command(s,enabled=True));world=b.session.world
        def fail(*args):raise RuntimeError('failed publish')
        with self.assertRaises(RuntimeError):b.step(project=fail)
        self.assertIs(b.session.world,world);self.assertEqual(self.f.quantity(b),100000)
        self.assertEqual(b.fire.pending_modes,{DEVICE:'active'})
        b.step();self.assertEqual(self.f.quantity(b),99900)

    def test_empty_prepares_pause_freezes_and_close_releases_parts(self):
        s,clock,b=self.scene(units=0,parts=2)
        self.send(s,self.command(s,enabled=True));self.advance(s,clock)
        d=b.fire.view()['devices'][0]
        self.assertEqual(d['status'],'preparing');self.assertEqual(d['cargo_costs'][0]['reserved'],2)
        self.call(s,'pause');clock.advance(500_000_000);s.tick()
        self.assertEqual(b.fire.view()['devices'][0]['remaining_preparation_steps'],d['remaining_preparation_steps'])
        self.call(s,'resume');self.send(s,self.command(s,enabled=False))
        d=b.fire.view()['devices'][0]
        self.assertEqual(d['cargo_costs'][0]['reserved'],0)
        self.assertEqual(d['cargo_costs'][0]['available'],2)
        self.assertEqual(self.f.quantity(b),0)

    def test_missing_parts_and_manual_selection(self):
        s,clock,b=self.scene(units=0)
        self.send(s,self.command(s,'repair_target',module_id=TARGET))
        self.send(s,self.command(s,enabled=True));self.advance(s,clock)
        d=b.fire.view()['devices'][0]
        self.assertEqual(d['target_module_id'],TARGET);self.assertEqual(d['status'],'no_engineering_parts')
        self.send(s,self.command(s,'repair_target',module_id=None))
        self.assertIsNone(b.fire.controllers[0].target_module_id)

    def test_old_supply_upgrades_once_old_import_and_mixed_fleet_are_preserved(self):
        with TemporaryDirectory() as temp:
            server=SidecarServer('backend.d1d',settlement_dir=temp)
            service=server.preparation;new_policy=service.policy
            service.policy=json.loads((ROOT/'contracts/web_bridge/fixtures/s1-preparation-policy.v2.json').read_text(encoding='utf-8'))
            source=next(s for s in service.library()['sources'] if '常规有人' in s['name'])
            request=dict(instance_id='instance.legacy',source=dict(kind='resource',value=source['key']))
            legacy=service.import_ship(request)
            draft=service.store.draft('prep.old',['instance.legacy'],SUPPLY_ID)
            draft['ships'][0]['magazines'][0]['quantity']=10
            service.store.commit(draft)
            old=service.store.load_ship('instance.legacy',1)
            service.policy=new_policy;service.provision();service.provision()
            self.assertEqual(service.import_ship(request),legacy)
            self.assertEqual(service.store.load_ship('instance.legacy',1),old)
            for ids in (['instance.legacy'],):
                draft=service.store.draft('prep.legacy.again',ids,SUPPLY_ID)
                result=service.store.commit(draft)
                self.assertEqual(result['supply_after']['ammunition_resources'],990)
                self.assertEqual(next(c['quantity'] for c in result['supply_after']['cargo'] if c['good_id']=='cargo.engineering_parts'),100)
            service.import_ship(dict(request,instance_id='instance.new'))
            draft=service.store.draft('prep.mixed',['instance.legacy','instance.new'],SUPPLY_ID)
            new=next(r for r in draft['ships'] if r['instance_id']=='instance.new')
            self.assertTrue(new['damage_controls']);self.assertFalse(draft['ships'][0]['damage_controls'])
            new['cargo']=[dict(good_id='cargo.engineering_parts',quantity=4)]
            new['damage_controls'][0]['prepare']=True
            result=service.store.commit(draft)
            prepared=next(s['after']['state'] for s in result['ships'] if s['after']['state']['instance_id']=='instance.new')
            self.assertEqual(prepared['damage_controls'][0]['quantity_units'],100000)
            self.assertEqual(prepared['cargo'][0]['quantity'],2)
            self.assertEqual(service.store.commit(draft),result)
            supply=result['supply_after']
            service.provision()
            with service.store.connection() as db:
                saved=service.store._decode(*db.execute('SELECT payload,digest FROM preparation_supplies WHERE id=?',(SUPPLY_ID,)).fetchone())
            self.assertEqual(saved['supply'],supply)


if __name__=='__main__':unittest.main()
