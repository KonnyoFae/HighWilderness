"""Real import/preparation/deployment/save/restart checks in disposable stores."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.high_wilderness_sidecar import persistent_ship as ps
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.tactical_scheduler import ScheduledControl
from tools.test_simplified_flight import command
from tools.test_tactical_scheduler import Clock

ROOT = Path(__file__).resolve().parents[1]


def exercise(document, label, directory):
    path = directory/'design.json'
    path.write_text(ps.encode(document), encoding='utf-8')
    server = SidecarServer('backend.module.check', settlement_dir=directory/'store')
    live, service = server.realtime, server.preparation
    clock = Clock()
    live.clock = clock
    def call(action, **params):
        return service.dispatch(dict(method='tactical.preparation.'+action, params=params, session_id=None, expected_revision=None))
    grant = server.editor.store.bind(str(path), 'open', None, None)
    request = dict(instance_id='instance.module.check', source=dict(kind='file', value=grant['destination_handle']))
    call('import', **request)
    # Exact import retry keeps the one instance.
    call('import', **request)
    assert len(call('library')['ships']) == 1
    packet = call('open', preparation_id='prepare.module', instance_ids=['instance.module.check'])
    draft = packet['draft']
    draft['revision'] = 1
    row = draft['ships'][0]
    for magazine in row['magazines']:
        magazine['quantity'] = 30
    for weapon in row['weapons']:
        weapon.update(action='preload', recipe_id='recipe.x1a.ordinary', batches=1)
    # Cargo is loaded through the real finite supply only if the design has room.
    capacity = packet['ships'][0]['capacity']['capacity_cm3']
    parts = 2 * len(row.get('damage_controls', []))
    if parts and capacity >= parts * 1_000_000:
        row['cargo'] = [dict(good_id='cargo.engineering_parts', quantity=parts)]
        for device in row['damage_controls']:
            device['prepare'] = True
    call('draft', draft=draft, expected_saved_revision=0)
    preview = call('preview', preparation_id='prepare.module', revision=1)
    assert preview['can_commit'], preview['issues']
    receipt = call('commit', preparation_id='prepare.module', revision=1)
    source_hash = ps.canonical_sha256(document)
    try:
        live.deploy_prepared(dict(preparation_id='prepare.module', launch_id='launch.module', direct_instance_id='instance.module.check'))
        battle, scheduler = live.gunnery, live.scheduler
        battle.enemy_fire = False  # Isolation of entry/propulsion/save from enemy balance.
        installed = battle.session._seeds[0].resources.modules
        prototypes = {m.prototype.reference.id.removesuffix('.tactical_no_burn') for m in installed}
        loaded_state = ps.clone(battle.inventory.inventories[0]._value)
        assert loaded_state == receipt['ships'][0]['after']['state']
        scheduler.resume()
        s = scheduler.status
        scheduler.submit(ScheduledControl(s.epoch, s.generation, 1, battle.session.world.ships[0].ship_id,
                                          s.fixed_step, command('yaw.clockwise', yaw=50)))
        for _ in range(120):
            clock.advance(16_666_667)
            scheduler.pump()
            live.publish()
        scheduler.pause()
        assert battle.session.world.fixed_step == 120
        yaw = battle.session.world.ships[0].motion.yaw_rate_radps
        table = battle.session._seeds[0].contributions
        if table.intact_totals_units[5]:
            assert abs(yaw) > 0, 'Installed clockwise thrusters produced no turning'
        battle.withdraw()
        live.publish()
        live.dispatch(dict(method='tactical.realtime.save', params=dict(settlement_id=live._result['settlement_id']), session_id=None, expected_revision=None), mode='tactical')
        after = ps.clone(live._result['ships'][0]['after']['state'])
        assert live._result_saved
        live._prepared_lease.close()
        reopened = SidecarServer('backend.module.reopened', settlement_dir=directory/'store')
        restored = reopened.preparation.dispatch(dict(method='tactical.preparation.open',
            params=dict(preparation_id='prepare.again', instance_ids=['instance.module.check']), session_id=None, expected_revision=None))
        assert restored['ships'][0]['state'] == after
        reopened.preparation.dispatch(dict(method='tactical.preparation.commit', params=dict(preparation_id='prepare.again', revision=0), session_id=None, expected_revision=None))
        try:
            reopened.realtime.deploy_prepared(dict(preparation_id='prepare.again', launch_id='launch.again', direct_instance_id='instance.module.check'))
            assert len(reopened.realtime.gunnery.session._seeds[0].resources.modules) == len(installed)
        finally:
            if reopened.realtime._prepared_lease:
                reopened.realtime._prepared_lease.close()
        assert ps.canonical_sha256(json.loads(path.read_text(encoding='utf-8'))) == source_hash
        return dict(source=label, source_sha256=source_hash, status='PASS', modules=len(installed),
                    prototypes=sorted(prototypes), steps=120, yaw_rate_radps=yaw,
                    prepared_damage_controls=sum(d['quantity_units'] > 0 for d in loaded_state.get('damage_controls', [])),
                    save_restart_redeploy=True)
    finally:
        if live._prepared_lease:
            live._prepared_lease.close()


def main():
    index = ResourceIndex(ROOT)
    documents = [(d['name'], s) for d, s in index.resources.values() if d['kind'] == 'OutfitPlan']
    for path in sorted((ROOT/'artifacts/o3c').glob('*.json')):
        doc = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(doc, dict) and 'outfit' in doc and 'hull_binding' in doc:
            documents.append((str(path.relative_to(ROOT)), doc))
    results = []
    with TemporaryDirectory(prefix='module-battle-check-') as temp:
        for n, (label, doc) in enumerate(documents):
            directory = Path(temp)/str(n)
            directory.mkdir()
            try:
                result = exercise(doc, label, directory)
            except Exception as exc:
                result = dict(source=label, status='FAIL', error=str(exc))
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    expected = {m['id'] for d, s in index.resources.values() if d['kind'] == 'ModulePrototypeCatalog' for m in s['modules']}
    covered = {m for r in results for m in r.get('prototypes', [])}
    passed = all(r['status'] == 'PASS' for r in results) and expected <= covered
    output = ROOT/'artifacts/module-battle-repair/runtime-report.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(status='PASS' if passed else 'FAIL', scope='entry, preparation, scheduled turning, save, restart and redeployment; not full module balance acceptance',
        expected_prototypes=len(expected), covered_prototypes=len(covered), missing_prototypes=sorted(expected-covered), results=results), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
