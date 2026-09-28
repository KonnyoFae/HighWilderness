"""AV1 ownership, real inventory transactions and durable preparation receipts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import 高天荒野舰艇人员舱容量 as housing
from backend.high_wilderness_sidecar import aviation_logistics as al, aviation_resources as ar
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps, outfits
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.server import SidecarServer
from tools.test_battle_preparation import fixture, ROOT


def carrier(ship_id='ship.carrier',links=False):
    index=ResourceIndex(ROOT)
    source=next(s for d,s in index.resources.values() if d['id']=='gtw.outfit.aviation.foundation')
    if links:
        source=deepcopy(source)
        next(m for m in source['modules'] if m['id']=='fire_control')['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
        source['modules'].append(dict(id='aviation.datalink',prototype=dict(id='gtw.module.datalink.5d',version=1),placement=dict(kind='hosted',host_instance_id='fire_control')))
    compiled=outfits.document(source,index).compile();_,deployment,_=fixture(index)
    deployment['crew']=[dict(crew_type=k,count=v) for k,v in housing.bounded_crew(compiled.instances,dict(compiled.standard_crew)).items()]
    return bp.compile_design(source,index,deployment,load_current(ROOT),ship_id=ship_id)


class LogisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.design=carrier()

    def setUp(self):
        self.pack=self.design.resources
        self.inv=InventorySession(self.pack,ps.fresh_instance(self.pack,'instance.carrier'))
        self.p=self.inv._definition['aviation']
        self.hangar=next(f['module_id'] for f in self.p['facilities'] if f['kind']=='aircraft_hangar')
        self.catapult=next(f['module_id'] for f in self.p['facilities'] if f['kind']=='aircraft_catapult')
        self.stock={'cargo:'+g['id']:10000 for g in self.inv._definition['goods']}
        for g in ('cargo.engineering_parts',*self.p['recipes']['payload_goods'].values(),*self.p['recipes']['cannon_goods'].values()):
            self.inv.command(epoch=self.inv.epoch,sequence=self.inv.sequence+1,kind='load_cargo',target=g,quantity=500 if g in self.p['recipes']['cannon_goods'].values() else 20)

    def acquire(self,key='f1',pilots=1):
        al.prepare(self.inv,[dict(kind='acquire',model_id='gtw.aircraft.'+key),dict(kind='pilots',module_id=self.hangar,quantity=pilots)],self.stock)
        return self.inv._value['aviation']['manifest']['aircraft'][-1]['id']

    def order(self,pid,kind='prepare',**kwargs):return dict(kind=kind,aircraft_id=pid,module_id=self.hangar,**kwargs)
    def plane(self):return self.inv._value['aviation']['manifest']['aircraft'][0]

    def test_paid_preparation_reserves_pilots_then_boards_without_fuel(self):
        pid=self.acquire();before=self.inv._value['fuel_units']
        al.apply(self.inv,self.order(pid,loadout={'p1':'small_bomb'}))
        s=self.inv._value['aviation'];self.assertEqual(self.plane()['location'],'preparing')
        self.assertEqual(len(s['jobs'][0]['pilot_ids']),1);self.assertEqual(len(s['manifest']['personnel']),1)
        self.inv.snapshot()
        al.advance(self.inv,1800)
        self.assertEqual(self.plane()['location'],'ready');self.assertEqual(len(self.plane()['crew']),1)
        self.assertFalse(self.inv._value['aviation']['manifest']['personnel'])
        self.assertEqual(self.inv._value['fuel_units'],before)
        self.inv.snapshot()

    def test_repair_has_no_berth_reservation_returns_empty_cargo(self):
        pid=self.acquire();self.plane()['condition']='damaged'
        al.apply(self.inv,self.order(pid,'repair'))
        self.assertEqual(self.plane()['location'],'repairing')
        self.assertFalse(self.inv._value['aviation']['jobs'][0]['pilot_ids'])
        al.advance(self.inv,3600)
        self.assertEqual((self.plane()['location'],self.plane()['condition']),('cargo','intact'))
        self.assertFalse(self.inv._value['aviation']['hangar_assignments']);self.inv.snapshot()

    def test_preload_only_ready_is_instant_and_frees_berth(self):
        pid=self.acquire();o=dict(kind='load',aircraft_id=pid,module_id=self.catapult)
        with self.assertRaises(ps.ContractError):al.prepare(self.inv,[o],self.stock)
        al.prepare(self.inv,[self.order(pid,loadout={}),o],self.stock)
        self.assertEqual(self.plane()['location'],'catapult');self.assertFalse(self.inv._value['aviation']['jobs'])
        self.assertEqual(ar.cargo_volume(self.inv._value['aviation'],self.p),0)
        self.inv.snapshot()

    def test_queue_starts_only_on_tick_and_ending_does_not_start_waiting(self):
        pid=self.acquire();second=self.acquire()
        al.prepare(self.inv,[dict(kind='queue',order=self.order(pid,loadout={})),dict(kind='queue',order=self.order(second,loadout={}))],self.stock)
        self.assertFalse(self.inv._value['aviation']['jobs'])
        al.advance(self.inv,1)
        self.assertEqual(len(self.inv._value['aviation']['jobs']),1)
        self.assertEqual(len(self.inv._value['aviation']['queue']),1)
        self.inv.prepare_settlement('ending.test')
        self.assertEqual(self.plane()['location'],'ready')
        self.assertEqual(len(self.inv._value['aviation']['queue']),1)
        self.assertFalse(self.inv._value['aviation']['jobs']);self.inv.snapshot()

    def test_cancel_completes_once_no_ammunition_refund(self):
        pid=self.acquire();al.apply(self.inv,self.order(pid,loadout={'p1':'small_missile'}))
        cargo=deepcopy(self.inv._value['cargo'])
        al.apply(self.inv,dict(kind='cancel',aircraft_id=pid))
        self.assertEqual(self.plane()['location'],'ready');self.assertEqual(self.inv._value['cargo'],cargo)
        with self.assertRaises(ps.ContractError):al.apply(self.inv,dict(kind='cancel',aircraft_id=pid))

    def test_failed_preparation_is_atomic_including_supply_and_identity(self):
        before=self.inv.snapshot();stock=deepcopy(self.stock)
        with self.assertRaises(ps.ContractError):al.prepare(self.inv,[dict(kind='acquire',model_id='gtw.aircraft.f1'),dict(kind='pilots',module_id=self.hangar,quantity=11)],self.stock)
        self.assertEqual(self.inv.snapshot(),before);self.assertEqual(self.stock,stock)

    def test_hangar_damage_precedes_completion_and_keeps_crew_identity(self):
        pid=self.acquire();al.apply(self.inv,self.order(pid,loadout={'p1':'small_bomb'}))
        ids=[p['id'] for p in self.inv._value['aviation']['manifest']['personnel']]
        health=dict(self.inv._health);health[self.hangar]=0
        self.inv.advance(1,health=health);al.advance(self.inv,3600)
        self.assertEqual((self.plane()['location'],self.plane()['condition']),('cargo','damaged'))
        self.assertFalse(self.plane()['loadout']);self.assertFalse(self.inv._value['aviation']['jobs'])
        self.assertEqual([p['id'] for p in self.inv._value['aviation']['manifest']['personnel']],ids)
        self.assertEqual(ar.cargo_volume(self.inv._value['aviation'],self.p),25_000_000)
        self.assertEqual(self.inv._value['aviation']['manifest']['personnel'][0]['housing'],'quarters')
        self.inv.snapshot()

    def test_displaced_pilot_uses_one_cubic_metre_only_if_beds_unavailable(self):
        self.acquire()
        health=dict(self.inv._health);health[self.hangar]=0
        for m in self.pack.seed.resources.modules:
            if m.prototype.reference.id=='gtw.module.aviation.officer_quarters':health[m.id]=0
        self.inv.advance(1,health=health)
        self.assertEqual(ar.cargo_volume(self.inv._value['aviation'],self.p),26_000_000)
        self.assertEqual(self.inv._value['aviation']['manifest']['personnel'][0]['housing'],'temporary_cargo')
        self.inv.snapshot()

    def test_full_ready_berths_allow_repair_and_catapult_releases_a_slot(self):
        for _ in range(5):
            pid=self.acquire();al.prepare(self.inv,[self.order(pid,loadout={})],self.stock)
        extra=self.acquire();a=self.inv._value['aviation']['manifest']['aircraft'][-1];a['condition']='damaged'
        al.apply(self.inv,self.order(extra,'repair'));al.advance(self.inv,3600)
        with self.assertRaises(ps.ContractError):al.apply(self.inv,self.order(extra,loadout={}))
        al.prepare(self.inv,[dict(kind='load',aircraft_id=self.plane()['id'],module_id=self.catapult)],self.stock)
        al.prepare(self.inv,[self.order(extra,loadout={})],self.stock);self.inv.snapshot()

    def test_zero_step_damage_projection_does_not_start_queued_work(self):
        pid=self.acquire();al.prepare(self.inv,[dict(kind='queue',order=self.order(pid,loadout={}))],self.stock)
        health=dict(self.inv._health);health[self.catapult]-=1
        self.inv.advance(0,health=health)
        self.assertFalse(self.inv._value['aviation']['jobs'])
        al.advance(self.inv,1,{self.hangar:0})
        self.assertFalse(self.inv._value['aviation']['jobs'])

    def test_forged_job_duration_and_stock_as_ordinary_cargo_rejected(self):
        with self.assertRaises(ps.ContractError):self.inv.command(epoch=self.inv.epoch,sequence=self.inv.sequence+1,kind='load_cargo',target='supply.aviation.pilot',quantity=1)
        pid=self.acquire();al.apply(self.inv,self.order(pid,loadout={}))
        value=self.inv.snapshot().to_dict();value['aviation']['jobs'][0].update(total_steps=2000)
        with self.assertRaises(ps.ContractError):ps.parse_instance(value,self.pack)

    def test_loaded_catapult_destruction_and_ship_crash(self):
        pid=self.acquire('e1',2)
        al.prepare(self.inv,[self.order(pid,loadout={'p1':'self_defense','p2':'self_defense'})],self.stock)
        al.apply(self.inv,dict(kind='load',aircraft_id=pid,module_id=self.catapult))
        hp=dict(self.inv._health);hp[self.catapult]=0;self.inv.advance(1,health=hp)
        self.assertEqual(self.plane()['condition'],'damaged');self.assertEqual(self.plane()['location'],'cargo')
        self.inv.advance(2,hull_integrity=0)
        self.assertEqual(self.plane()['location'],'salvage')
        self.assertTrue(all(p['housing']=='salvage' for p in self.inv._value['aviation']['manifest']['personnel'] if p['health']!='dead'))
        self.inv.snapshot()

    def test_lift_or_withdrawal_wreck_with_intact_hull_cannot_finish_job(self):
        pid=self.acquire();al.apply(self.inv,self.order(pid,loadout={}))
        al.advance(self.inv,0,crashed=True)
        self.inv.prepare_settlement('ending.wreck')
        self.assertGreater(self.inv._hull_integrity,0)
        self.assertEqual((self.plane()['location'],self.plane()['condition']),('salvage','damaged'))
        self.assertFalse(self.inv._value['aviation']['jobs']);self.inv.snapshot()

    def test_reload_hardpoints_refunds_exact_old_ammunition(self):
        pid=self.acquire();initial={r['good_id']:r['quantity'] for r in self.inv._value['cargo']}
        al.prepare(self.inv,[self.order(pid,loadout={'p1':'small_bomb'}),self.order(pid,loadout={'p1':'small_missile'})],self.stock)
        current={r['good_id']:r['quantity'] for r in self.inv._value['cargo']}
        r=self.p['recipes'];self.assertEqual(current[r['payload_goods']['small_bomb']],initial[r['payload_goods']['small_bomb']])
        self.assertEqual(current[r['cannon_goods'][next(m['cannon_id'] for m in self.p['catalog']['aircraft'] if m['id']=='gtw.aircraft.f1')]],initial[r['cannon_goods'][next(m['cannon_id'] for m in self.p['catalog']['aircraft'] if m['id']=='gtw.aircraft.f1')]]-240)
        self.inv.snapshot()

    def test_snapshot_checkpoint_and_fork_do_not_share_jobs(self):
        pid=self.acquire();al.apply(self.inv,self.order(pid,loadout={}))
        checkpoint=self.inv.checkpoint();restored=InventorySession.restore(self.pack,checkpoint)
        self.assertEqual(restored.snapshot(),self.inv.snapshot())
        child=self.inv.fork();al.advance(child,1800)
        self.assertEqual(self.plane()['location'],'preparing');self.assertEqual(child._value['aviation']['manifest']['aircraft'][0]['location'],'ready')


class DurableTests(unittest.TestCase):
    def test_action_and_commit_retry_survive_restart(self):
        with TemporaryDirectory() as directory:
            server=SidecarServer('backend.aviation',settlement_dir=Path(directory));service=server.preparation
            source=next(s for s in service.library()['sources'] if '航空设施' in s['name'])
            service.import_ship(dict(instance_id='instance.carrier',source=dict(kind='resource',value=source['key'])))
            def call(action,params):return service.dispatch(dict(method='tactical.preparation.'+action,params=params,session_id=None,expected_revision=None))
            packet=call('open',dict(preparation_id='prep.aviation',instance_ids=['instance.carrier']))
            args=dict(operation_id='op.acquire',preparation_id='prep.aviation',revision=0,instance_id='instance.carrier',order=dict(kind='acquire',model_id='gtw.aircraft.f1'))
            draft=call('aviation',args);self.assertEqual(call('aviation',args),draft)
            req=dict(preparation_id='prep.aviation',revision=draft['revision'])
            self.assertTrue(call('preview',req)['can_commit'])
            before=packet['supply'];result=call('commit',req)
            service=SidecarServer('backend.restart',settlement_dir=Path(directory)).preparation
            self.assertEqual(call('commit',req),result);self.assertEqual(call('aviation',args),draft)
            state=call('read',dict(preparation_id='prep.aviation'))['ships'][0]['state']['aviation']
            self.assertEqual(len(state['manifest']['aircraft']),1)
            key='supply.aviation.f1';get=lambda x:next(c['quantity'] for c in x['cargo'] if c['good_id']==key)
            self.assertEqual(get(before)-get(result['supply_after']),1)


if __name__=='__main__':unittest.main()
