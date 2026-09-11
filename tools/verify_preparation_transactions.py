"""Finite multi-ship preparation/save/restart evidence, without player stores."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture, ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/x1a2-preparation-transactions')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    def write(name,value):
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    index=ResourceIndex(ROOT)
    document,deployment,policy=fixture(index)
    design=bp.compile_design(document,index,deployment,policy,ship_id='ship.preparation.player')
    with TemporaryDirectory() as directory:
        store=PreparationStore(directory,index)
        for key in ('instance.0','instance.1'): store.create_ship(design,key)
        supply=dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.evidence',revision=0,
            ammunition_resources=100,cargo=[dict(good_id='cargo.special_alloy',quantity=30)])
        store.provision_supply(supply,policy['goods'])
        draft=store.draft('preparation.evidence',['instance.0','instance.1'],supply['supply_id'])
        for row in draft['ships']:
            row['magazines'][0]['quantity']=20
            row['cargo']=[dict(good_id='cargo.special_alloy',quantity=2)]
            for w in row['weapons']: w.update(action='preload',recipe_id='recipe.x1a.ordinary',batches=1)
        before=store.draft('preparation.before',['instance.0','instance.1'],supply['supply_id'])
        preview=store.preview(draft)
        assert preview['can_commit']
        assert store.draft('preparation.before',['instance.0','instance.1'],supply['supply_id'])==before
        committed=store.commit(draft)
        assert committed==preview['result']
        reopened=PreparationStore(directory,ResourceIndex(ROOT))
        assert reopened.commit(draft)==committed
        assert reopened.receipt(draft['preparation_id'])==committed
        next_draft=reopened.draft('preparation.next',['instance.0','instance.1'],supply['supply_id'])
        second=reopened.commit(next_draft)
        assert all(row['changes']==[] for row in second['ships'])
        assert all(row['after']['state']['revision']==2 for row in second['ships'])
        assert second['supply_after']['ammunition_resources']==60
        write('preparation-result.json',committed)
        write('keep-existing-result.json',second)
        write('result.json',dict(status='X1A2_SCOPED_PASS',checks=dict(preview_has_no_side_effects=True,
            commit_matches_preview=True,restart_receipt_retry_no_duplicate_cost=True,next_preparation_keeps_loaded_rounds=True),
            ships=[dict(instance_id=r['after']['state']['instance_id'],revision=r['after']['state']['revision'],
                ammunition_resources=sum(m['quantity'] for m in r['after']['state']['magazines']),
                ready_rounds=sum(w['ready_rounds'] for w in r['after']['state']['weapons'])) for r in committed['ships']],
            remaining_supply=committed['supply_after'],not_implemented=['preparation_ui','custom_design_live_battle_entry']))
    print('X1a.2 preparation transaction evidence passed:',args.output/'result.json')


if __name__=='__main__': main()
