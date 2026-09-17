"""5c real catalog, preparation, in-progress stores and exact-cost recovery."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import missile_resources as mr, missile_logistics as ml, preparation_transactions as tx
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from tools.test_battle_preparation import ROOT, fixture

LAUNCHER='weapon_upper_port';MAG='ammunition_magazine';MODEL='gtw.missile.5c.small.rocket.active_radar'


def document(index, *, launcher='gtw.module.launcher.5c.small.rocket.active_radar'):
    doc,dep,_=fixture(index)
    for m in doc['outfit']['modules']:
        if m['id']==LAUNCHER:m['prototype']=dict(id=launcher,version=1)
        if m['id']==MAG:m['prototype']=dict(id='gtw.module.magazine.5c.missile',version=1)
    doc['outfit']['name']='导弹组装与装填测试舰'
    return doc,dep


class MissileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.doc,cls.dep=document(cls.index)
        cls.design=bp.compile_design(cls.doc,cls.index,cls.dep,load_current(ROOT),ship_id='ship.5c')
        cls.profile=cls.design.resources.definition()['missiles']

    def inv(self):return InventorySession(self.design.resources,ps.fresh_instance(self.design.resources,'instance.5c'))
    def stock(self):return {'cargo:'+g['id']:100000 for g in self.design.resources.definition()['goods']}
    def order(self,kind,mid=LAUNCHER,**kw):return dict(module_id=mid,kind=kind,**kw)
    def prepare(self,inv,*orders):return ml.prepare(inv,orders,self.stock())
    def row(self,inv,group='launchers'):return inv._value['missiles'][group][0]
    def load_cargo(self,inv):
        for key,n in mr.recipe(self.profile,MODEL,'blast').items():
            inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_cargo',target=key,quantity=n*20)

    def test_catalog_all_combinations_and_exact_binding(self):
        p=load_current(ROOT)
        self.assertEqual(len(p['missiles']['models']),17)
        self.assertEqual(len([m for m in p['modules'] if m['binding'].get('kind')=='missile_launcher']),11)
        self.assertEqual(bp.restore_design(self.design.archive(),self.index),self.design)
        self.assertFalse(self.design.resources.definition()['weapons'])
        self.assertFalse(self.design.resources.definition()['magazines'])

    def test_pre_missile_saved_design_keeps_exact_policy_and_fingerprints(self):
        import json
        from backend.high_wilderness_sidecar import outfit_documents
        previous=outfit_documents.catalog_generations(self.index)[-2]
        doc,dep,_=fixture(previous)
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/h5d-preparation-policy.v10.json').read_text(encoding='utf-8'))
        design=bp.compile_design(doc,previous,dep,policy,ship_id='ship.legacy')
        restored=bp.restore_design(design.archive(),self.index)
        self.assertEqual(restored,design)
        self.assertNotIn('missiles',bp.new_record(restored,'instance.legacy')['state'])

    def test_vls_all_models_and_medium_capacity(self):
        doc,dep=document(self.index,launcher='gtw.module.launcher.5c.vls')
        next(m for m in doc['outfit']['modules'] if m['id']==LAUNCHER)['placement']['deck_id']='deck.0'
        design=bp.compile_design(doc,self.index,dep,load_current(ROOT),ship_id='ship.vls')
        for model in design.resources.definition()['missiles']['models']:
            inv=InventorySession(design.resources,ps.fresh_instance(design.resources,'instance.vls'))
            stock=self.stock()
            ml.prepare(inv,[self.order('model',model_id=model['id']),self.order('load')],stock)
            row=self.row(inv)
            self.assertEqual(len(row['ready']),16 if model['size']=='small' else 4)
            self.assertTrue(all(u['model_id']==model['id'] for u in row['ready']))
            with self.assertRaises(ps.ContractError):ml.apply(inv,self.order('model',model_id=MODEL))
            inv.snapshot()

    def test_parallel_assembly_then_ready_transfer_no_double_charge(self):
        inv=self.inv();stock=self.stock();before=stock.copy()
        elapsed=ml.prepare(inv,[self.order('assemble',MAG,quantity=5),self.order('load')],stock)
        model=self.profile['models'][0]
        self.assertEqual(elapsed,model['assembly_steps']+4*model['ready_reload_steps'])
        self.assertEqual(len(self.row(inv)['ready']),4);self.assertEqual(len(self.row(inv,'magazines')['stock']),1)
        for key,n in mr.recipe(self.profile,MODEL,'blast').items():self.assertEqual(before['cargo:'+key]-stock['cargo:'+key],5*n)
        self.assertEqual(InventorySession.restore(inv.pack,inv.checkpoint()).snapshot(),inv.snapshot())

    def test_raw_fallback_unload_exact_refund(self):
        inv=self.inv();self.prepare(inv,self.order('load'))
        self.assertTrue(all(u['source']=='raw' for u in self.row(inv)['ready']))
        self.prepare(inv,self.order('unload'))
        self.assertEqual(self.row(inv)['ready'],[])
        self.assertEqual({c['good_id']:c['quantity'] for c in inv._value['cargo']},{k:v*4 for k,v in mr.recipe(self.profile,MODEL,'blast').items()})

    def test_in_progress_cancel_refund_and_no_free_round(self):
        inv=self.inv();self.load_cargo(inv);before=ps.clone(inv._value['cargo'])
        ml.apply(inv,self.order('assemble',MAG,quantity=5));ml.advance(inv,0)
        self.assertEqual(len(self.row(inv,'magazines')['jobs']),5)
        ml.advance(inv,100);ml.apply(inv,self.order('cancel',MAG))
        self.assertEqual(inv._value['cargo'],before);self.assertEqual(self.row(inv,'magazines')['stock'],[])
        ml.apply(inv,self.order('load'));ml.advance(inv,0);ml.advance(inv,100);ml.apply(inv,self.order('cancel'))
        self.assertEqual(inv._value['cargo'],before);self.assertIsNone(self.row(inv)['job'])

    def test_loaded_rounds_return_to_library_and_fifo_full_recovery(self):
        inv=self.inv();self.prepare(inv,self.order('assemble',MAG,quantity=5),self.order('load'),self.order('assemble',MAG,quantity=19))
        old=min(u['serial'] for u in self.row(inv,'magazines')['stock'])
        self.prepare(inv,self.order('unload'))
        self.assertEqual(len(self.row(inv,'magazines')['stock']),20)
        self.assertNotIn(old,[u['serial'] for u in self.row(inv,'magazines')['stock']])
        self.assertEqual({c['good_id']:c['quantity'] for c in inv._value['cargo']},{k:v*4 for k,v in mr.recipe(self.profile,MODEL,'blast').items()})
        inv.snapshot()

    def test_swap_loaded_library_rounds_recycles_original_warhead(self):
        inv=self.inv();stock=self.stock()
        ml.prepare(inv,[self.order('assemble',MAG,quantity=4),self.order('load'),self.order('warhead',warhead_id='incendiary')],stock)
        self.assertTrue(all(u['warhead_id']=='incendiary' for u in self.row(inv)['ready']))
        self.assertEqual(next(c['quantity'] for c in inv._value['cargo'] if c['good_id']=='cargo.high_explosive'),8)
        self.assertFalse(self.row(inv,'magazines')['stock'])
        self.assertFalse(self.row(inv,'magazines')['jobs'])

    def test_paused_work_and_in_progress_settlement_preserved(self):
        inv=self.inv();self.load_cargo(inv);ml.apply(inv,self.order('assemble',MAG,quantity=5));ml.advance(inv,0)
        before=ps.clone(self.row(inv,'magazines'))
        ml.advance(inv,600,{MAG:0,LAUNCHER:0});self.assertEqual(before,self.row(inv,'magazines'))
        ml.advance(inv,100);progress=ps.clone(self.row(inv,'magazines'))
        inv.prepare_settlement('settlement.5c');self.assertEqual(progress,self.row(inv,'magazines'))
        self.assertEqual(InventorySession.restore(inv.pack,inv.checkpoint()).snapshot(),inv.snapshot())

    def test_destroyed_stock_work_no_refund_and_fork_rollback(self):
        inv=self.inv();self.load_cargo(inv);ml.apply(inv,self.order('assemble',MAG,quantity=5));ml.advance(inv,0)
        amount=mr.explosives(self.profile,self.row(inv,'magazines'))
        self.assertEqual(amount,40)
        baseline=inv.snapshot();candidate=inv.fork();candidate._health=dict(candidate._health,**{MAG:0})
        ml.advance(candidate,100000)
        self.assertEqual(inv.snapshot(),baseline)
        self.assertFalse(self.row(candidate,'magazines')['jobs']);self.assertFalse(self.row(candidate,'magazines')['stock'])
        self.assertEqual(candidate._value['cargo'],inv._value['cargo'])

    def test_duplicate_serial_and_invalid_capacity_rejected(self):
        inv=self.inv();self.prepare(inv,self.order('assemble',MAG,quantity=1))
        v=inv.snapshot().to_dict();v['missiles']['magazines'][0]['stock']*=2
        with self.assertRaises(ps.ContractError):ps.parse_instance(v,inv.pack)
        v=inv.snapshot().to_dict();v['missiles']['magazines'][0]['model_id']='bogus'
        with self.assertRaises(ps.ContractError):ps.parse_instance(v,inv.pack)

    def test_preparation_insufficient_supply_rolls_back_whole_fleet(self):
        record=bp.new_record(self.design,'instance.5c')
        supply=dict(interface=bp.fuel.SUPPLY_INTERFACE,supply_id='supply.5c',revision=0,ammunition_resources=1000,fuel_units=1000,
                    cargo=[dict(good_id=g['id'],quantity=0) for g in self.design.resources.definition()['goods']])
        draft=bp.new_draft('preparation.5c',[(self.design,record)],supply)
        draft['ships'][0]['missile_orders']=[self.order('assemble',MAG,quantity=5)]
        old=ps.clone((record,supply,draft))
        result=tx.evaluate(draft,[(self.design,record)],supply)
        self.assertFalse(result['can_commit']);self.assertIsNone(result['result'])
        self.assertEqual(ps.clone((record,supply,draft)),old)

    def battle(self,inv):
        from backend.high_wilderness_sidecar import prepared_deployment as deploy
        from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
        template,scenario,_=RealtimeViewService('backend.5c')._template()
        record=bp.new_record(self.design,'instance.5c');record['state']=inv.snapshot().to_dict()
        result=deploy.build([(self.design,record)],'instance.5c',template,scenario)[0]
        result.enemy_fire=False
        return result

    def test_real_battle_order_retry_progress_pause_and_settlement(self):
        from backend.high_wilderness_sidecar import tactical_settlement as settlement
        inv=self.inv();self.load_cargo(inv);b=self.battle(inv)
        order=dict(epoch=b.session.world.epoch,generation=0,sequence=1,ship_id='ship.5c',order=self.order('assemble',MAG,quantity=5))
        self.assertTrue(b.missiles.submit(order));self.assertFalse(b.missiles.submit(order))
        b.step();row=self.row(b.inventory.inventories[0],'magazines')
        self.assertEqual(len(row['jobs']),5)
        total=row['jobs'][0]['remaining_work_steps']
        for _ in range(10):b.step()
        self.assertLess(self.row(b.inventory.inventories[0],'magazines')['jobs'][0]['remaining_work_steps'],total)
        b.withdraw();result=settlement.capture(b)
        after=result['ships'][0]['after'];self.assertEqual(len(after['state']['missiles']['magazines'][0]['jobs']),5)
        bp.validate_record(after,self.design)
        self.assertEqual(b.view()['missiles']['command_sequence'],1)

    def test_real_library_destruction_beats_work_completion_and_is_not_refunded(self):
        from tools.test_tactical_fire import TacticalFireTests
        inv=self.inv();self.load_cargo(inv);ml.apply(inv,self.order('assemble',MAG,quantity=5));ml.advance(inv,0)
        for job in self.row(inv,'magazines')['jobs']:job['remaining_work_steps']=1.
        b=self.battle(inv);before=ps.clone(b.inventory.inventories[0]._value['cargo'])
        b.step(device_operations=(TacticalFireTests().operation(b,MAG,100),))
        event,=b.magazines.recent
        self.assertEqual(event['store_kind'],'missile');self.assertEqual(event['ammunition_resources'],40)
        row=self.row(b.inventory.inventories[0],'magazines')
        self.assertFalse(row['stock']);self.assertFalse(row['jobs'])
        self.assertEqual(b.inventory.inventories[0]._value['cargo'],before)
        b.step();self.assertEqual(b.magazines.count,1)

    def test_material_return_over_capacity_kept_but_fresh_loading_rejected(self):
        inv=self.inv();self.prepare(inv,self.order('load'))
        # Real cargo hold loss can shrink capacity to zero; refunds remain owned.
        inv._health={k:(0 if k=='custom.cargo' else v) for k,v in inv._health.items()}
        self.prepare(inv,self.order('unload'))
        self.assertTrue(inv.summary()['over_capacity'])
        with self.assertRaises(ps.ContractError):inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='load_cargo',target='cargo.engineering_parts',quantity=1)

    def test_cancel_completed_round_transfer_returns_same_identity(self):
        inv=self.inv();self.prepare(inv,self.order('assemble',MAG,quantity=1))
        serial=self.row(inv,'magazines')['stock'][0]['serial']
        ml.apply(inv,self.order('load'));ml.advance(inv,0)
        self.assertFalse(self.row(inv,'magazines')['stock']);self.assertEqual(self.row(inv)['job']['kind'],'load_ready')
        ml.apply(inv,self.order('cancel'))
        self.assertEqual(self.row(inv,'magazines')['stock'][0]['serial'],serial)
        self.assertFalse(inv._value['cargo']);self.assertIsNone(self.row(inv)['job'])


if __name__=='__main__':unittest.main()
