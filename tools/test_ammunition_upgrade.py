"""Existing preparations keep their stock/damage while changing the ammo scale."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents
from backend.high_wilderness_sidecar.preparation_ammunition_upgrade import upgrade
from backend.high_wilderness_sidecar.preparation_service import SUPPLY_ID
from tools.test_ammunition_scale import caliber_document,ROOT


class AmmunitionUpgradeTests(unittest.TestCase):
    def setup_store(self,directory):
        service=SidecarServer('ammunition.upgrade.test',settlement_dir=directory).preparation
        service.provision();index=service.store.index
        old=outfit_documents.catalog_generations(index)[-6]
        doc,dep=caliber_document(old)
        policy=json.loads((ROOT/'contracts/web_bridge/fixtures/ammunition-preparation-policy.v12.json').read_text(encoding='utf-8'))
        design=bp.compile_design(doc,old,dep,policy,ship_id='ship.ammo.upgrade')
        record=service.store.create_ship(design,'instance.ammo.upgrade')
        record['state']['hull_integrity_fraction']=.8
        record['state']['modules'][0]['durability_points']/=2
        record['armor'][0]['durability']/=2
        next(w for w in record['state']['weapons'] if w['module_id']=='gun.partner').update(recipe_id='recipe.3a.50mm.ordinary',ready_rounds=10)
        record['state']['magazines'][0]['quantity']=7
        bp.validate_record(record,design)
        with service.store.connection() as db:service.setup(db);service.store._write_ship(db,record)
        draft=service.store.draft('preparation.ammo.upgrade',['instance.ammo.upgrade'],SUPPLY_ID)
        next(w for w in draft['ships'][0]['weapons'] if w['module_id']=='gun.partner').update(action='top_up',recipe_id='recipe.3a.50mm.ordinary',batches=0)
        service.save_draft(draft,-1)
        return service,design,record,draft

    def test_preview_apply_backup_rebase_and_repeat_preserve_state(self):
        with TemporaryDirectory() as directory:
            service,design,before,draft=self.setup_store(directory);store=service.store
            preview=upgrade(store,ROOT)
            self.assertFalse(preview['applied']);self.assertEqual(preview['drafts'],[draft['preparation_id']])
            backup=Path(directory)/'before.json'
            report=upgrade(store,ROOT,apply=True,backup_path=backup)
            self.assertTrue(report['applied']);self.assertTrue(backup.exists())
            with store.connection() as db:
                rows,_,_=store._inputs(db,['instance.ammo.upgrade'],SUPPLY_ID)
                new_design,after=rows[0]
            self.assertEqual(new_design.archive()['policy']['version'],13)
            self.assertEqual(after['armor'],before['armor'])
            self.assertEqual({k:v for k,v in after['state'].items() if k not in ('resources_sha256','revision')},
                             {k:v for k,v in before['state'].items() if k not in ('resources_sha256','revision')})
            self.assertEqual(json.loads(backup.read_text(encoding='utf-8'))['ships'][0]['record'],before)
            rebased=service.read_draft(draft['preparation_id'])
            self.assertEqual(rebased['ships'][0]['weapons'],draft['ships'][0]['weapons'])
            result=store.preview(rebased);self.assertTrue(result['can_commit'],result['issues'])
            filled=result['result']['ships'][0]['after']['state']
            self.assertEqual(next(w['ready_rounds'] for w in filled['weapons'] if w['module_id']=='gun.partner'),30)
            self.assertEqual(filled['magazines'][0]['quantity'],6)
            self.assertFalse(upgrade(store,ROOT,apply=True,backup_path=backup)['applied'])
            with store.connection() as db:self.assertEqual(store._inputs(db,['instance.ammo.upgrade'],SUPPLY_ID)[0][0][1],after)

    def test_battle_claim_blocks_every_write_and_backup(self):
        with TemporaryDirectory() as directory:
            service,design,before,_=self.setup_store(directory);store=service.store
            with store.connection() as db:db.execute('INSERT INTO battle_instance_claims VALUES (?,?)',('instance.ammo.upgrade','battle.claimed'))
            backup=Path(directory)/'blocked.json'
            with self.assertRaises(bp.ps.ContractError):upgrade(store,ROOT,apply=True,backup_path=backup)
            self.assertFalse(backup.exists())
            with store.connection() as db:
                raw=db.execute('SELECT payload,digest FROM ships WHERE id=?',('instance.ammo.upgrade',)).fetchone()
                self.assertEqual(store._decode(*raw),before)


if __name__=='__main__':unittest.main()
