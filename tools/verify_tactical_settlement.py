"""P3 real two-battle result evidence plus focused atomic-store/crash tests."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter_ns
import unittest

from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import tactical_settlement as st


def verify(output):
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=False)
    service = RealtimeViewService('backend.p3verify', settlement_dir=output/'.local/store')
    battle, scenario, geometry = service._template()
    battle.submit(dict(epoch=battle.session.world.epoch, generation=0, sequence=1, weapon_id='weapon_upper_port',
        kind='target', arguments=dict(ship_id='ship.web.red', module_id='cic')))
    times = []
    for _ in range(450):
        begin = perf_counter_ns(); battle.step(); times.append((perf_counter_ns()-begin)/1e6)
    battle.withdraw()
    first = st.capture(battle)
    service.store.stage(first)
    # New connection owner represents recovery of the pending result after exit.
    restarted = st.SettlementStore(output/'.local/store')
    assert not restarted.read(first['settlement_id'])['saved']
    saved = restarted.save(first['settlement_id'])
    assert restarted.save(first['settlement_id']) == saved
    blue = first['ships'][0]['after']['state']
    assert blue['hull_integrity_fraction'] < 1
    template, scenario, _ = service._template()
    next_battle = st.redeploy(restarted.load_ship(blue['instance_id'], 1), template, scenario)
    assert next_battle.session.world.ships[0].motion.hull_integrity_fraction == blue['hull_integrity_fraction']
    assert next_battle.inventory.inventories[0]._value['magazines'] == blue['magazines']
    for _ in range(120): next_battle.step()
    next_battle.withdraw()
    second = st.capture(next_battle); restarted.stage(second); restarted.save(second['settlement_id'])
    assert restarted.load_ship(blue['instance_id'], 2)['state']['hull_integrity_fraction'] == blue['hull_integrity_fraction']
    suite = unittest.defaultTestLoader.loadTestsFromNames(['tools.'+name for name in (
        'test_tactical_gunnery', 'test_tactical_inventory', 'test_persistent_ship', 'test_tactical_scheduler',
        'test_realtime_view', 'test_tactical_checkpoint', 'test_tactical_devices', 'test_tactical_resources_runtime',
        'test_tactical_command_runtime', 'test_simplified_flight', 'test_tactical_damage', 'test_tactical_settlement')])
    tested = unittest.TextTestRunner(verbosity=1).run(suite)
    assert tested.wasSuccessful()
    sources = [
        'backend/high_wilderness_sidecar/tactical_settlement.py', 'backend/high_wilderness_sidecar/persistent_ship.py',
        'backend/high_wilderness_sidecar/tactical_inventory.py', 'backend/high_wilderness_sidecar/tactical_gunnery.py',
        'backend/high_wilderness_sidecar/simplified_flight.py', 'backend/high_wilderness_sidecar/tactical_damage.py',
        'backend/high_wilderness_sidecar/realtime_view.py', 'backend/high_wilderness_sidecar/server.py',
        'backend/high_wilderness_sidecar/__main__.py', 'apps/desktop/src/tactical/SettlementPanel.tsx',
        'apps/desktop/src/tactical/settlement.ts', 'apps/desktop/src/tactical/settlement.test.ts',
        'apps/desktop/src/tactical/RealtimePanel.tsx', 'apps/desktop/src/tactical/realtime.ts',
        'apps/desktop/src/tactical/model.ts', 'apps/desktop/src/styles.css', 'apps/desktop/src-tauri/src/bridge/host.rs',
        'tools/test_tactical_settlement.py', 'tools/verify_tactical_settlement.py', 'tools/verify_settlement_view.mjs']
    report = dict(status='PASS', interface='gaotian.p3-verification/v1', tests_run=tested.testsRun,
        battle_a=first, battle_b=second, library=restarted.list(),
        timing=dict(scope='450 in-process combat steps; excludes entry, storage, UI and IPC',
            median_ms=sorted(times)[len(times)//2], p95_ms=sorted(times)[int(len(times)*.95)], max_ms=max(times)),
        source_sha256={name: sha256((root/name).read_bytes()).hexdigest() for name in sources},
        not_covered=['arbitrary player design deployment', 'in-progress battle checkpoint', 'native WebView visual QA',
            'special ammunition effects', 'cargo destruction', 'strategy mode'])
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='PASS', tests=tested.testsRun, output=str(output/'result.json'), timing=report['timing']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/p3-settlement-runtime'))
    verify(parser.parse_args().output)
