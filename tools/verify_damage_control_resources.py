"""Write D1a finite-resource evidence to a fresh directory, never player saves."""
import argparse
import json
from pathlib import Path

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar import damage_control_resources as dc
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture, ROOT


def verify(directory):
    directory.mkdir(parents=True, exist_ok=False)
    index = ResourceIndex(ROOT)
    document, deployment, _ = fixture(index)
    policy = json.loads((ROOT/'contracts/web_bridge/fixtures/d1-preparation-policy.v3.json').read_text(encoding='utf-8'))
    design = bp.compile_design(document, index, deployment, policy, ship_id='ship.d1.evidence')
    store = PreparationStore(directory/'store', index)
    record = store.create_ship(design, 'instance.d1.evidence')
    supply = dict(interface=bp.SUPPLY_INTERFACE, supply_id='supply.d1.evidence', revision=0,
        ammunition_resources=0, cargo=[dict(good_id=dc.PARTS, quantity=10)])
    store.provision_supply(supply, policy['goods'])
    draft = store.draft('preparation.d1.evidence', ['instance.d1.evidence'], supply['supply_id'])
    draft['ships'][0]['cargo'] = [dict(good_id=dc.PARTS, quantity=6)]
    draft['ships'][0]['damage_controls'][0]['prepare'] = True
    preview = store.preview(draft)
    assert preview['can_commit']
    receipt = store.commit(draft)
    assert receipt == preview['result']
    reopened = PreparationStore(directory/'store', index)
    assert reopened.commit(draft) == receipt
    saved = reopened.load_ship('instance.d1.evidence', 1)
    inv = InventorySession(design.resources, ps.parse_instance(saved['state'], design.resources))
    target = inv._value['damage_controls'][0]['module_id']
    def command(kind, **args):
        inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind=kind, target=target, **args)
    command('consume_damage_control', quantity=25000)
    partial = inv.snapshot().to_dict()
    command('consume_damage_control', quantity=75000)
    command('start_damage_control_preparation')
    reservation = inv.summary()
    for step in range(1, 300): inv.advance(step)
    waiting = inv.snapshot().to_dict()
    inv.advance(300)
    complete = inv.snapshot().to_dict()
    inv.prepare_settlement('settlement.d1.evidence')
    assert not inv.prepare_settlement('settlement.d1.evidence')
    final = inv.snapshot().to_dict()
    assert complete == final
    assert final['damage_controls'][0]['quantity_units'] == 100000
    assert final['cargo'][0]['quantity'] == 2
    assert partial['damage_controls'][0]['quantity_units'] == 75000
    assert waiting['damage_controls'][0]['preparation']['remaining_steps'] == 1
    evidence = dict(status='D1A_RESOURCE_SCOPED_PASS', resource_units_per_point=dc.UNITS_PER_POINT,
        scope='Resource primitives only; no firefighting or repair effect asserted',
        definition=design.resources.definition(), initial=record['state'], preparation_receipt=receipt,
        partial_store=partial, reservation=reservation, before_deadline=waiting, final=final,
        battle_resource_changes=inv.changes())
    (directory/'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=evidence['status'], output=str(directory/'result.json')), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT/'artifacts/d1a-damage-control-resources-v1')
    verify(parser.parse_args().out)
