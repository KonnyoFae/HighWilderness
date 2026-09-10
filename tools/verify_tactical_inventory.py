"""P1b technical transactions in the actual flight/device loop, no gun/UI claim."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from backend.high_wilderness_sidecar import persistent_ship as ps, simplified_flight as sf, tactical_inventory as ti
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from tools.persistent_ship_fixture import definition, loaded


def verify(output):
    root = Path(__file__).resolve().parents[1]
    sample = sf.build_sample_session(root, with_command=True)
    packs = tuple(ps.compile_resources(s, definition(s)) for s in sample._seeds)
    initial = tuple(loaded(p, 'instance.' + str(n)) for n, p in enumerate(packs))
    def enter(instances):
        return ti.enter_battle(tuple(ps.InstanceBinding(p, i) for p, i in zip(packs, instances)),
                              sample._profile, direct_instance_id='instance.0')
    battle = enter(initial)
    commands = []
    for n, inventory in enumerate(battle.inventories):
        commands.extend(((n, dict(epoch=inventory.epoch, sequence=1, kind='discharge',
            target='weapon_upper_port', quantity=1, cooldown_steps=180)),
            (n, dict(epoch=inventory.epoch, sequence=2, kind='start_reload',
            target='weapon_upper_port', recipe_id='recipe.special'))))
    battle.step(inventory_commands=commands)
    for _ in range(39):
        battle.step()
    active = tuple(i.checkpoint() for i in battle.inventories)
    restored = tuple(ti.InventorySession.restore(p, payload) for p, payload in zip(packs, active))
    assert tuple(i.checkpoint() for i in restored) == active
    session = battle.prepared.session
    damage = []
    for n, name in ((0, 'cargo_hold'), (1, 'weapon_upper_port')):
        hp = next(m.maximum_durability_points for m in packs[n].seed.devices.modules if m.instance_id == name)
        damage.append(DeviceOperation(session.world.epoch, session.world.ships[n].ship_id, name,
            1, 'damage', hp, session.world.fixed_step, 'opening'))
    battle.step(device_operations=tuple(damage))
    assert battle.inventories[0].summary()['over_capacity']
    assert battle.inventories[0].summary()['reserved_cargo']['cargo.special_alloy'] == 1
    assert battle.inventories[1].summary()['reserved_cargo']['cargo.special_alloy'] == 0
    battle.prepare_settlement('result.p1b.technical')
    settled_checkpoints = tuple(i.checkpoint() for i in battle.inventories)
    battle.prepare_settlement('result.p1b.technical')
    assert tuple(i.checkpoint() for i in battle.inventories) == settled_checkpoints
    for p, payload in zip(packs, settled_checkpoints):
        restored_inventory = ti.InventorySession.restore(p, payload)
        assert not restored_inventory.prepare_settlement('result.p1b.technical')
        assert restored_inventory.checkpoint() == payload
    settled = battle.export_instances()
    blue, red = (i.to_dict() for i in settled)
    assert blue['magazines'][0]['quantity'] == 35 and blue['weapons'][0]['ready_rounds'] == 1
    assert red['magazines'][0]['quantity'] == 40 and red['weapons'][0]['ready_rounds'] == 0
    assert blue['weapons'][0]['cooldown_steps'] == 140
    assert all(w['reload'] is None for v in (blue, red) for w in v['weapons'])
    second = enter(settled)
    assert second.prepared.session.world.epoch != session.world.epoch
    assert second.export_instances() == settled
    for _ in range(60):
        second.step()
    after = second.export_instances()
    assert after[0].to_dict()['weapons'][0]['cooldown_steps'] == 80
    assert after[0].to_dict()['cargo'] == blue['cargo']
    assert after[0].to_dict()['magazines'] == blue['magazines']
    assert all(s.model == p.seed.model for s, p in zip(second.prepared.session._seeds, packs))
    sources = ('backend/high_wilderness_sidecar/tactical_inventory.py',
        'backend/high_wilderness_sidecar/persistent_ship.py', 'backend/high_wilderness_sidecar/simplified_flight.py',
        'backend/high_wilderness_sidecar/tactical_devices.py', 'tools/persistent_ship_fixture.py',
        'tools/test_tactical_inventory.py', 'tools/verify_tactical_inventory.py')
    report = dict(interface='gaotian.p1b-inventory-verification/v1', status='PASS',
        scope='inventory-transactions-and-flight-commit-only',
        evidence=dict(active_domain_checkpoint_roundtrip=True, hold_loss_preserves_reservation=True,
            weapon_loss_releases_reservation=True, settlement_completes_only_active_valid_reload=True,
            settlement_retry_after_domain_restore_is_noop=True, overcapacity_redeployment=True,
            cooldown_preserved=True, static_flight_model_preserved=True, next_scene_steps=60),
        summaries=[i.summary() for i in battle.inventories], changes=[i.changes() for i in battle.inventories],
        source_sha256={name: sha256((root/name).read_bytes()).hexdigest() for name in sources},
        not_covered=['gun aiming/firing/projectiles', 'real projectile damage', 'legal battle-end eligibility',
            'durable multi-ship commit', 'product inventory/save UI', 'full battle checkpoint with inventory',
            'resource-driven mass/inertia', 'special ammunition effects'])
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {}
    for n, pack in enumerate(packs):
        for name, payload in ((f'resources-{n}.json', pack.definition_json),
                (f'initial-{n}.json', initial[n].payload_json),
                (f'active-inventory-{n}.json', active[n]),
                (f'settled-inventory-{n}.json', settled_checkpoints[n]),
                (f'settled-ship-{n}.json', settled[n].payload_json),
                (f'next-scene-{n}.json', after[n].payload_json)):
            (output/name).write_text(payload+'\n', encoding='utf-8')
            artifacts[name] = sha256((output/name).read_bytes()).hexdigest()
    report['artifact_sha256'] = artifacts
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/p1b-inventory'))
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps(dict(status=report['status'], output=str(args.output/'result.json')), ensure_ascii=False))
