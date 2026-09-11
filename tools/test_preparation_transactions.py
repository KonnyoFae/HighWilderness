from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import preparation_transactions as pt
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.tactical_settlement import SettlementStore, RESULT_INTERFACE
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture, ROOT


class PreparationTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT)
        cls.document,cls.deployment,cls.policy=fixture(cls.index)
        cls.design=bp.compile_design(cls.document,cls.index,cls.deployment,cls.policy,ship_id='ship.preparation.player')

    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.store=pt.PreparationStore(self.directory,self.index)
        self.records=[self.store.create_ship(self.design,'instance.'+str(n)) for n in range(2)]
        self.supply=dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.finite',revision=0,
            ammunition_resources=100,cargo=[dict(good_id='cargo.special_alloy',quantity=30)])
        self.store.provision_supply(self.supply,self.policy['goods'])

    def draft(self,key='preparation.test'):
        return self.store.draft(key,['instance.0','instance.1'],'supply.finite')

    def loaded(self,draft=None):
        d=draft or self.draft()
        for row in d['ships']:
            row['magazines'][0]['quantity']=20
            row['cargo']=[dict(good_id='cargo.special_alloy',quantity=2)]
            for w in row['weapons']:
                w.update(action='preload',recipe_id='recipe.x1a.ordinary',batches=1)
        return d

    def current(self):
        return self.draft('preparation.current')

    def test_preview_is_pure_and_commit_matches_all_ship_preview(self):
        before=self.current(); draft=self.loaded()
        preview=self.store.preview(draft)
        self.assertTrue(preview['can_commit'])
        self.assertEqual(self.current(),before)
        result=self.store.commit(draft)
        self.assertEqual(result,preview['result'])
        self.assertEqual(result['supply_after']['ammunition_resources'],60)
        self.assertEqual(result['supply_after']['cargo'][0]['quantity'],26)
        for row in result['ships']:
            state=row['after']['state']
            self.assertEqual(state['revision'],1)
            self.assertEqual(state['magazines'][0]['quantity'],20-5*len(state['weapons']))
            self.assertTrue(all(w['ready_rounds']==1 and w['reload'] is None for w in state['weapons']))

    def test_all_supply_shortages_reported_and_no_partial_commit(self):
        draft=self.loaded(); before=self.current()
        for row in draft['ships']:
            row['magazines'][0]['quantity']=80
            row['cargo'][0]['quantity']=20
        preview=self.store.preview(draft)
        shortage={r['target']:r['missing'] for r in preview['issues'] if r['instance_id'] is None and 'missing' in r}
        self.assertEqual(shortage,{'ammunition':60,'cargo:cargo.special_alloy':10})
        self.assertIsNone(preview['result'])
        with self.assertRaises(ps.ContractError): self.store.commit(draft)
        self.assertEqual(self.current(),before)

    def test_ship_shortage_multiple_weapons_does_not_partially_load(self):
        draft=self.loaded(); draft['ships'][0]['magazines'][0]['quantity']=0
        self.assertFalse(self.store.preview(draft)['can_commit'])
        with self.assertRaises(ps.ContractError): self.store.commit(draft)
        self.assertEqual(self.current()['ships'][1]['magazines'][0]['quantity'],0)

    def test_unload_before_load_permits_order_independent_ship_transfer(self):
        first=self.draft('preparation.initial'); first['ships'][1]['magazines'][0]['quantity']=100
        self.store.commit(first)
        transfer=self.draft('preparation.transfer')
        transfer['ships'][0]['magazines'][0]['quantity']=100
        transfer['ships'][1]['magazines'][0]['quantity']=0
        result=self.store.commit(transfer)
        self.assertEqual(result['supply_after']['ammunition_resources'],0)
        self.assertEqual(result['ships'][0]['after']['state']['magazines'][0]['quantity'],100)

    def test_lost_receipt_retry_after_restart_no_duplicate_cost_or_revision(self):
        draft=self.loaded(); first=self.store.commit(draft)
        reopened=pt.PreparationStore(self.directory,ResourceIndex(ROOT))
        self.assertEqual(reopened.commit(draft),first)
        self.assertEqual(reopened.receipt(draft['preparation_id']),first)
        self.assertEqual(self.current()['ships'][0]['revision'],1)
        changed=ps.clone(draft); changed['revision']+=1
        with self.assertRaises(ps.ContractError): reopened.commit(changed)

    def test_new_preparation_preserves_ready_rounds_and_old_receipt_is_read_only(self):
        original=self.loaded(); a=self.store.commit(original)
        keep=self.draft('preparation.next'); b=self.store.commit(keep)
        for row in b['ships']:
            self.assertEqual(row['before']['state']['weapons'],row['after']['state']['weapons'])
            self.assertEqual(row['changes'],[])
        self.assertEqual(self.store.commit(original),a)
        self.assertEqual(self.current()['ships'][0]['revision'],2)

    def test_stale_draft_cannot_replace_new_stock(self):
        stale=self.loaded(self.draft('preparation.stale'))
        self.store.commit(self.loaded())
        with self.assertRaises(ps.ContractError): self.store.commit(stale)

    def test_disk_failure_after_ship_writes_rolls_back_stock_and_receipt(self):
        before=self.current()
        with patch.object(self.store,'_write_supply',side_effect=OSError('injected storage failure')):
            with self.assertRaises(ps.ContractError): self.store.commit(self.loaded())
        self.assertEqual(self.current(),before)
        with self.assertRaises(ps.ContractError): self.store.receipt('preparation.test')
        self.assertEqual(self.store.commit(self.loaded())['supply_after']['ammunition_resources'],60)

    def test_process_exit_mid_transaction_rolls_back_all_ships(self):
        draft=self.loaded(); (self.directory/'draft.json').write_text(ps.encode(draft),encoding='utf-8')
        code='''
import os,sys
from pathlib import Path
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar import persistent_ship as ps
class Crash(PreparationStore):
 def _write_ship(self,db,record):
  super()._write_ship(db,record)
  os._exit(19)
directory=Path(sys.argv[1])
Crash(directory,ResourceIndex(Path.cwd())).commit(ps.decode((directory/'draft.json').read_text(encoding='utf-8')))
'''
        child=subprocess.run([sys.executable,'-X','utf8','-c',code,str(self.directory)],cwd=ROOT,capture_output=True,timeout=30)
        self.assertEqual(child.returncode,19,child.stderr.decode('utf-8'))
        self.assertEqual(self.current()['ships'][0]['revision'],0)
        self.assertEqual(self.current()['ships'][1]['revision'],0)
        self.assertEqual(self.store.commit(draft)['supply_after']['ammunition_resources'],60)

    def test_supply_reinitialization_and_existing_ship_recreation_cannot_refill(self):
        self.store.commit(self.loaded())
        with self.assertRaises(ps.ContractError): self.store.provision_supply(self.supply,self.policy['goods'])
        existing=self.store.create_ship(self.design,'instance.0')
        self.assertEqual(existing['state']['revision'],1)
        self.assertTrue(existing['state']['weapons'][0]['ready_rounds']>0)

    def alter(self,record,edit):
        value=ps.clone(record); edit(value['state'])
        with self.store.connection() as db: self.store._write_ship(db,bp.validate_record(value,self.design))

    def test_destroyed_hold_overcapacity_keep_unload_allowed_new_load_rejected(self):
        def damage(s):
            next(m for m in s['modules'] if m['module_id']=='custom.cargo')['durability_points']=0
            s['cargo']=[dict(good_id='cargo.special_alloy',quantity=50)]
        self.alter(self.records[0],damage)
        keep=self.draft(); self.assertTrue(self.store.preview(keep)['can_commit'])
        result=self.store.commit(keep)
        self.assertTrue(result['ships'][0]['capacity_after']['over_capacity'])
        add=self.draft('preparation.add'); add['ships'][0]['cargo'][0]['quantity']=51
        self.assertFalse(self.store.preview(add)['can_commit'])
        remove=self.draft('preparation.unload'); remove['ships'][0]['cargo'][0]['quantity']=40
        result=self.store.commit(remove)
        self.assertEqual(result['supply_after']['cargo'][0]['quantity'],40)
        self.assertTrue(result['ships'][0]['capacity_after']['over_capacity'])

    def test_destroyed_weapon_or_magazine_refuses_preloading_and_keeps_stock(self):
        for category in ('weapons','magazines'):
            with self.subTest(category=category):
                target=self.records[0]['state'][category][0]['module_id']
                def damage(s): next(m for m in s['modules'] if m['module_id']==target).__setitem__('durability_points',0)
                self.alter(self.records[0],damage)
                self.assertFalse(self.store.preview(self.loaded())['can_commit'])
                self.alter(self.records[0],lambda s:None)

    def test_active_claim_and_pending_settlement_block_preparation(self):
        before=self.draft()
        self.store.claim_battle('scene.test',[dict(instance_id='instance.0',revision=0)])
        with self.assertRaises(ps.ContractError): self.store.commit(before)
        # Matching P3 save releases the claim atomically and advances same ship rows.
        record=self.records[0]; after=ps.clone(record); after['state']['revision']=1
        result=dict(interface=RESULT_INTERFACE,settlement_id='settlement.scene.test',scene_id='scene.test',reason='withdrawal',
            fixed_step=1,removed_projectiles=0,ships=[dict(before=record,after=after,capacity_before={},capacity_after={},changes=[],module_names={})])
        base=SettlementStore(self.directory); base.stage(result)
        with self.assertRaises(ps.ContractError): self.store.draft('preparation.blocked',['instance.0'],'supply.finite')
        base.save(result['settlement_id'])
        self.assertEqual(self.current()['ships'][0]['revision'],1)

    def test_corrupt_supply_does_not_load_or_commit(self):
        draft=self.loaded()
        with self.store.connection() as db:
            db.execute("UPDATE preparation_supplies SET payload='{}'")
        with self.assertRaises(ps.ContractError): self.store.commit(draft)

    def test_different_ships_cannot_concurrently_spend_same_supply_revision(self):
        a=self.store.draft('preparation.a',['instance.0'],'supply.finite')
        b=self.store.draft('preparation.b',['instance.1'],'supply.finite')
        for draft in (a,b): draft['ships'][0]['magazines'][0]['quantity']=80
        def submit(draft):
            try: return self.store.commit(draft)
            except ps.ContractError: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(submit,(a,b)))
        self.assertEqual(sum(r is not None for r in results),1)
        winner=next(r for r in results if r is not None)
        self.assertEqual(winner['supply_after']['ammunition_resources'],20)

    def test_malformed_ship_selection_fails_as_contract_error(self):
        for ids in ([{}],[True],[],['instance.0','instance.0']):
            with self.subTest(ids=ids), self.assertRaises(ps.ContractError):
                self.store.draft('preparation.test',ids,'supply.finite')

    def batch_inventory(self, *, special=False):
        definition=self.design.resources.definition()
        for w in definition['weapons']: w['ready_capacity']=100
        recipe=definition['recipes'][0]; recipe.update(rounds=20,ammo_cost=3)
        if special: recipe['cargo_costs']=[dict(good_id='cargo.special_alloy',quantity=2)]
        pack=ps.compile_resources(self.design.resources.seed,definition)
        state=ps.fresh_instance(pack,'instance.batch').to_dict()
        state['magazines'][0]['quantity']=30
        state['cargo']=[dict(good_id='cargo.special_alloy',quantity=10)]
        state['weapons'][0].update(recipe_id=recipe['id'],ready_rounds=11,cooldown_steps=19)
        return InventorySession(pack,ps.parse_instance(state,pack))

    def test_whole_batches_and_partial_belt_preserve_cooldown(self):
        inv=self.batch_inventory(); w=inv.snapshot().to_dict()['weapons'][0]
        inv.prepare_reload(w['module_id'],w['recipe_id'],3)
        after=inv.snapshot().to_dict()
        self.assertEqual(after['weapons'][0]['ready_rounds'],71)
        self.assertEqual(after['magazines'][0]['quantity'],21)
        self.assertEqual(after['weapons'][0]['cooldown_steps'],19)
        before=inv.snapshot()
        with self.assertRaises(ps.ContractError): inv.prepare_reload(w['module_id'],w['recipe_id'],2)
        self.assertEqual(inv.snapshot(),before)

    def test_discard_no_refund_special_materials_and_failure_atomic(self):
        inv=self.batch_inventory(special=True); w=inv.snapshot().to_dict()['weapons'][0]
        before=inv.snapshot()
        with self.assertRaises(ps.ContractError): inv.prepare_reload(w['module_id'],w['recipe_id'],10,discard=True)
        self.assertEqual(inv.snapshot(),before)
        inv.prepare_reload(w['module_id'],w['recipe_id'],2,discard=True)
        after=inv.snapshot().to_dict()
        self.assertEqual(after['weapons'][0]['ready_rounds'],40)
        self.assertEqual(after['magazines'][0]['quantity'],24)
        self.assertEqual(after['cargo'][0]['quantity'],6)
        self.assertIn(dict(resource='ready:'+w['module_id'],reason='discard',delta=-11),inv.changes())

    def test_live_inventory_cannot_use_offline_preload(self):
        inv=self.batch_inventory(); w=inv.snapshot().to_dict()['weapons'][0]
        class Flight: _executing=False
        inv._flight_session=Flight()
        with self.assertRaises(ps.ContractError): inv.prepare_reload(w['module_id'],w['recipe_id'],1)


if __name__=='__main__': unittest.main()
