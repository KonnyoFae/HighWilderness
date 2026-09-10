"""Short P2a deterministic runtime probe; no native UI or real impact claim."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from backend.high_wilderness_sidecar import simplified_flight as sf, tactical_gunnery as tg
from backend.high_wilderness_sidecar.tactical_scenario import build_two_ship_scenario
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation


def verify(output):
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/'contracts/web_bridge/fixtures/p2a-gunnery.json').read_text(encoding='utf-8'))
    session = tg.prepare_trial_session(sf.build_sample_session(root, with_command=True), config)
    battle = tg.GunneryBattle(session, build_two_ship_scenario(root), config)
    def command(kind, **arguments):
        battle.submit(dict(epoch=session.world.epoch, generation=0, sequence=battle.sequence+1,
            weapon_id='weapon_upper_port', kind=kind, arguments=arguments))
    command('target', ship_id='ship.web.red', module_id='cic')
    timing, checkpoints = [], {}
    for n in range(360):
        start = perf_counter_ns()
        battle.step()
        timing.append((perf_counter_ns()-start)/1000000)
        if n in (59, 119, 359):
            checkpoints[str(n+1)] = battle.view()
    assert checkpoints['60']['weapons'][0]['quality'] == 'degraded'
    assert checkpoints['120']['weapons'][0]['quality'] == 'normal'
    assert checkpoints['360']['weapons'][0]['shots'] >= 3
    hp = next(m.maximum_durability_points for m in session._seeds[0].devices.modules if m.instance_id == 'sensor_upper_starboard')
    battle.step(device_operations=(DeviceOperation(session.world.epoch, session._direct, 'sensor_upper_starboard',
        1, 'damage', hp, session.world.fixed_step, 'opening'),))
    degraded = battle.view()
    assert degraded['weapons'][0]['quality'] == 'degraded'
    shots = degraded['weapons'][0]['shots']
    for _ in range(240):
        battle.step()
    assert battle.states[0].shots > shots
    command('mode', mode='manual')
    command('aim', point=[-5, 300])
    for _ in range(180):
        battle.step()
    before = battle.states[0].shots
    command('fire', point=[-5, 300]); battle.step()
    assert battle.states[0].shots == before+1
    for _ in range(150):
        battle.step()
    assert battle.states[0].shots == before+1
    final = battle.view()
    sources = ('backend/high_wilderness_sidecar/tactical_gunnery.py',
        'backend/high_wilderness_sidecar/tactical_inventory.py', 'backend/high_wilderness_sidecar/realtime_view.py',
        'backend/high_wilderness_sidecar/tactical_scheduler.py', 'apps/desktop/src/tactical/RealtimePanel.tsx',
        'apps/desktop/src/tactical/TacticalViewport.tsx', 'apps/desktop/src/tactical/viewport.ts',
        'apps/desktop/src/tactical/gunnery.ts', 'apps/desktop/src/tactical/model.ts', 'apps/desktop/src/editor/OutfitPanel.tsx',
        'apps/desktop/src-tauri/src/bridge/host.rs', 'contracts/web_bridge/fixtures/p2a-gunnery.json',
        'tools/test_tactical_gunnery.py', 'tools/verify_tactical_gunnery.py', 'tools/verify_gunnery_view.mjs')
    report = dict(status='PASS', interface='gaotian.p2a-verification/v1',
        evidence=dict(auto_module_target=True, degraded_then_normal=True, radar_loss_degrades=True,
            degraded_still_fires=True, manual_single_shot=True, batches_consume_resources=True),
        timing=dict(scope='360 in-process two-ship steps with one firing gun; excludes setup, UI and IPC',
            steps=len(timing), total_ms=sum(timing), median_ms=median(timing), p95_ms=sorted(timing)[int(len(timing)*.95)],
            max_ms=max(timing), fixed_step_budget_ms=1000/60),
        checkpoints=checkpoints, immediately_after_test_radar_damage=degraded, final=final,
        source_sha256={name: sha256((root/name).read_bytes()).hexdigest() for name in sources},
        not_covered=['projectile hits/armor/damage', 'full battle checkpoint', 'legal settlement/persistent store',
            'native WebView visual QA', 'arbitrary player design deployment', 'special ammunition effects'])
    output.mkdir(parents=True, exist_ok=False)
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='PASS', output=str(output/'result.json'), timing=report['timing']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/p2a-gunnery-runtime'))
    verify(parser.parse_args().output)
