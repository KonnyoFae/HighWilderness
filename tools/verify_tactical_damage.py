"""P2b short real-fire runtime evidence, not an endurance or native UI test."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from backend.high_wilderness_sidecar import simplified_flight as sf, tactical_gunnery as tg
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario


def verify(output):
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/'contracts/web_bridge/fixtures/p2a-gunnery.json').read_text(encoding='utf-8'))
    session = tg.prepare_trial_session(sf.build_sample_session(root, with_command=True), config)
    battle = tg.GunneryBattle(session, build_two_ship_scenario(root), config, damage_enabled=True)
    battle.submit(dict(epoch=session.world.epoch, generation=0, sequence=1, weapon_id='weapon_upper_port',
        kind='target', arguments=dict(ship_id='ship.web.red', module_id='cic')))
    timing, hit_timing = [], []
    for _ in range(900):
        if battle.ending: break
        hits = battle.damage_state.hits
        start = perf_counter_ns(); battle.step(); duration = (perf_counter_ns()-start)/1e6
        timing.append(duration)
        if battle.damage_state.hits > hits: hit_timing.append(duration)
    assert all(s.shots > 0 for s in battle.states)
    assert battle.damage_state.hits > 0
    assert all(s.motion.hull_integrity_fraction < 1 for s in session.world.ships)
    before_end = battle.view()
    battle.withdraw()
    assert battle.ending and not battle.projectiles
    assert all(not i._due for i in battle.inventory.inventories)
    final = battle.view()
    assert not battle.withdraw()
    assert final == battle.view()
    sources = (
        'backend/high_wilderness_sidecar/tactical_damage.py', 'backend/high_wilderness_sidecar/tactical_gunnery.py',
        'backend/high_wilderness_sidecar/simplified_flight.py', 'backend/high_wilderness_sidecar/tactical_inventory.py',
        'backend/high_wilderness_sidecar/tactical_scheduler.py', 'backend/high_wilderness_sidecar/realtime_view.py',
        'apps/desktop/src/tactical/RealtimePanel.tsx', 'apps/desktop/src/tactical/TacticalViewport.tsx',
        'apps/desktop/src/tactical/model.ts', 'apps/desktop/src/tactical/gunnery.ts', 'apps/desktop/src/tactical/realtime.ts',
        'apps/desktop/src-tauri/src/bridge/host.rs', 'contracts/web_bridge/fixtures/p2a-gunnery.json',
        '舰艇数据/标定/阶段I弹丸与损伤技术替身配置.v1.json', '高天荒野舰艇炮弹与甲弹公式.py',
        'tools/test_tactical_damage.py', 'tools/verify_tactical_damage.py', 'tools/verify_damage_view.mjs')
    report = dict(status='PASS', interface='gaotian.p2b-verification/v1',
        timing=dict(scope='900 in-process two-ship fixed steps; both guns enabled, includes real impacts; excludes setup/UI/IPC',
            steps=len(timing), median_ms=median(timing), p95_ms=sorted(timing)[int(len(timing)*.95)],
            max_ms=max(timing), impact_steps=len(hit_timing), impact_max_ms=max(hit_timing), budget_ms=1000/60),
        before_end=before_end, final=final,
        ships=[dict(ship_id=s.ship_id, hull_integrity=s.motion.hull_integrity_fraction,
            modules=[dict(id=m.instance_id, hp=v.durability_points) for m, v in zip(seed.devices.modules, s.devices.modules)],
            armor=battle.damage_state.armor[n], resources=battle.inventory.inventories[n].summary(),
            changes=battle.inventory.inventories[n].changes()) for n, (seed, s) in enumerate(zip(session._seeds, session.world.ships))],
        source_sha256={name: sha256((root/name).read_bytes()).hexdigest() for name in sources},
        not_covered=['persistent atomic settlement store and next battle', 'native WebView visual QA',
            'arbitrary player design deployment', '3D ballistics', 'special ammunition / fire / cargo destruction',
            'armored demo ship (armor branches use separate compiled-kernel test fixtures)'])
    output.mkdir(parents=True, exist_ok=False)
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='PASS', output=str(output/'result.json'), timing=report['timing']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/p2b-damage-runtime'))
    verify(parser.parse_args().output)
