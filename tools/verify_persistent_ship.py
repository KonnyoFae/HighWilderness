"""Produce P1a state roundtrip evidence using the actual simplified-flight kernel.

No desktop UI or legal battle settlement is claimed by this technical probe.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from backend.high_wilderness_sidecar import persistent_ship as ps, simplified_flight as sf
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.persistent_ship_fixture import definition, loaded


def verify(output):
    root = Path(__file__).resolve().parents[1]
    sample = sf.build_sample_session(root, with_command=True)
    packs = tuple(ps.compile_resources(s, definition(s)) for s in sample._seeds)
    initial = tuple(loaded(p, 'instance.' + str(i)) for i, p in enumerate(packs))
    def enter(instances):
        return ps.enter_battle(tuple(ps.InstanceBinding(p, i) for p, i in zip(packs, instances)),
            sample._profile, direct_instance_id='instance.0')
    battle = enter(initial)
    assert ps.export_instances(battle) == initial
    session = battle.session
    operations = tuple(DeviceOperation(session.world.epoch, 'ship.web.blue', name, 1, 'damage',
        session._seeds[0].devices.modules[session._device_kernels[0].by_id[name]].maximum_durability_points,
        0, 'opening') for name in ('main_engine_port', 'cargo_hold'))
    session.step(device_operations=operations)
    damaged = ps.export_instances(battle)
    candidate = damaged[0].to_dict()
    candidate['hull_integrity_fraction'] = .72  # explicit contract sample, not a projectile claim
    damaged = (ps.parse_instance(candidate, packs[0]), damaged[1])
    encoded = tuple(ps.dumps(i, p) for i, p in zip(damaged, packs))
    restored = tuple(ps.loads(raw, p, expected_instance_id='instance.' + str(n), expected_revision=0)
        for n, (raw, p) in enumerate(zip(encoded, packs)))
    second = enter(restored)
    for _ in range(60):
        second.session.step()
    assert ps.export_instances(second) == restored
    assert second.session.world.epoch != session.world.epoch
    assert restored[0].to_dict()['cargo'] == initial[0].to_dict()['cargo']
    assert restored[0].to_dict()['magazines'] == initial[0].to_dict()['magazines']
    assert restored[0].to_dict()['weapons'] == initial[0].to_dict()['weapons']
    summary = ps.inventory_summary(restored[0], packs[0])
    assert summary['capacity_cm3'] == 0 and summary['over_capacity']
    sources = ('backend/high_wilderness_sidecar/persistent_ship.py',
        'backend/high_wilderness_sidecar/simplified_flight.py', 'backend/high_wilderness_sidecar/tactical_devices.py',
        'backend/high_wilderness_sidecar/tactical_resources_runtime.py', 'backend/high_wilderness_sidecar/tactical_command_runtime.py',
        'tools/persistent_ship_fixture.py', 'tools/test_persistent_ship.py', 'tools/verify_persistent_ship.py')
    report = dict(interface='gaotian.p1a-state-verification/v1', status='PASS',
        scope='technical-contract-and-flight-entry-only', entry_policy=ps.ENTRY_POLICY,
        evidence=dict(identity_roundtrip=True, resources_roundtrip=True, same_inventory_after_hold_loss=True,
            engine_loss_preserved=True, damaged_hull_preserved=True, new_scene_epoch=True, continued_steps=60),
        damaged_inventory=summary,
        remaining_thrust_units=list(second.session.world.ships[0].propulsion.available_units),
        intact_thrust_units=list(packs[0].seed.contributions.intact_totals_units),
        source_sha256={name: sha256((root / name).read_bytes()).hexdigest() for name in sources},
        not_covered=['weapon firing and damage', 'resource consumption', 'settlement reload completion',
            'product save UI', 'atomic multi-ship settlement', 'dynamic loadout mass', 'latched/disabled redeployment'])
    # All checks complete before emitting a fresh, reviewable evidence directory.
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {}
    for n, (pack, instance, result) in enumerate(zip(packs, initial, encoded)):
        for name, payload in ((f'resources-{n}.json', pack.definition_json),
                              (f'initial-{n}.json', instance.payload_json), (f'restored-{n}.json', result)):
            (output / name).write_text(payload + '\n', encoding='utf-8')
            artifacts[name] = sha256((output / name).read_bytes()).hexdigest()
    report['artifact_sha256'] = artifacts
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/p1a-persistent-state'))
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps(dict(status=report['status'], output=str(args.output / 'result.json')), ensure_ascii=False))
