"""Reproducible X1a.1 boundary evidence, not a UI or combat acceptance run."""
import argparse
import json
from pathlib import Path

from backend.high_wilderness_sidecar import battle_preparation as bp, persistent_ship as ps
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.storage import read_json
from 高天荒野舰艇数据契约 import canonical_sha256
from tools.test_battle_preparation import fixture, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/x1a1-preparation-contract')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    index = ResourceIndex(ROOT)
    document, deployment, policy = fixture(index)
    write('saved-outfit.json', document)
    saved, _ = read_json(args.output/'saved-outfit.json')
    design = bp.compile_design(saved,index,deployment,policy,ship_id='ship.preparation.player')
    write('design-archive.json',design.archive())
    restored = bp.restore_design(read_json(args.output/'design-archive.json')[0],index)
    assert restored == design
    record = bp.new_record(design,'instance.preparation.player')
    assert not record['state']['cargo'] and all(m['quantity']==0 for m in record['state']['magazines'])
    damaged = ps.clone(record)
    damaged['state']['hull_integrity_fraction'] = .62
    damaged['state']['revision'] = 7
    damaged['state']['cargo'] = [dict(good_id='cargo.special_alloy',quantity=50)]
    next(m for m in damaged['state']['modules'] if m['module_id']=='custom.cargo')['durability_points'] = 0
    supply = dict(interface=bp.SUPPLY_INTERFACE,supply_id='supply.test.finite',revision=1,
        ammunition_resources=100,cargo=[dict(good_id='cargo.special_alloy',quantity=20)])
    original = canonical_sha256(damaged)
    draft = bp.new_draft('preparation.evidence',[(design,damaged)],supply)
    write('preparation-draft.json',draft)
    bp.validate_draft(read_json(args.output/'preparation-draft.json')[0],[(restored,damaged)],supply)
    assert canonical_sha256(damaged)==original
    summary = ps.inventory_summary(ps.parse_instance(damaged['state'],design.resources),design.resources)
    assert summary['over_capacity'] and summary['capacity_cm3']==0
    write('result.json',dict(status='X1A1_SCOPED_PASS',checks=dict(saved_editor_document_compiled=True,
        archived_design_restored=True,empty_new_inventory=True,damaged_record_unchanged=True,
        overcapacity_preserved=True,draft_roundtrip=True),snapshot_sha256=design.snapshot.source_sha256,
        resources_sha256=design.resources.source_sha256,inventory_summary=summary,
        not_implemented=['supply_and_preload_commit','preparation_ui','custom_design_live_battle_route'],
        prototype_policy=policy['id'],swap_policy=bp.SWAP_POLICY))
    print('X1a.1 boundary evidence passed:',args.output/'result.json')


if __name__ == '__main__':
    main()
