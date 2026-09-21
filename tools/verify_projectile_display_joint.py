"""D5: matched legacy/stream combat, finite real weapons, three initial layers.

The VLS fires a real salvo at an empty point; inbound shell targets are injected
every two seconds. This isolates display under sustained defense without making
the ships immortal or changing weapon speed, rate, ammunition or damage rules.
Recorded publications can be replayed at wall-clock speed in the real viewport.
"""
import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import gzip
import json
from math import pi
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from time import perf_counter

from backend.high_wilderness_sidecar import tactical_encounter as encounter, tactical_settlement as settlement
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE
from tools import defense_fixture as defense, ew_fixture as ew
from tools.test_missile_defense import MissileDefenseTests
from tools.profile_tactical_fire_control import stats


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')


def build(index, template, scenario, layer):
    designs = (defense.design(index, 'player', True), ew.design(index, 'enemy'))
    records = (defense.record(designs[0], 'player'), ew.record(designs[1], 'enemy', auto=True))
    sides, rows = [], []
    for n, (design, record) in enumerate(zip(designs, records)):
        side = dict(side_id=('side.player' if n == 0 else 'side.enemy'), fleet_id=f'fleet.d5.{n}',
                    flagship_instance_id=record['state']['instance_id'], ships=[])
        member = dict(instance_id=record['state']['instance_id'], revision=record['state']['revision'],
                      deployment=dict(x_m=0., y_m=n*30000., heading_rad=0. if n == 0 else pi))
        side['ships'].append(member); sides.append(side); rows.append((side, member, design, record))
    request = dict(interface=encounter.INTERFACE, encounter_id='encounter.d5', world_id='world.d5',
                   world_revision=0, player_side_id='side.player', sides=sides)
    battle, geometry, _ = encounter.build(request, rows, template, scenario)
    world = battle.session.world
    battle.session._world = replace(world, ships=tuple(replace(s, motion=replace(s.motion, height_layer=layer)) for s in world.ships))
    # Initial test orders: the opposing VLS's salvo goes away from both ships.
    # Real seeker, weather, lifetime, cooldown, stock and departure logic remain.
    key = (1, 'weapon_upper_port')
    battle.missiles.states[key] = replace(battle.missiles.states[key], point=(10000., 30000.), layer=layer)
    for key, state in tuple(battle.missiles.states.items()):
        if key[0] == 0: battle.missiles.states[key] = replace(state, layer=None)
    return battle, geometry


def run(fixture, layer, stream, out, seconds):
    battle, geometry = build(fixture.index, fixture.template, fixture.scenario, layer)
    digest = sha256(); seen = set(); terminal_ids = set(); pending_ids = set()
    costs = {'step': [], 'publish': [], 'read': []}
    wire_bytes = projectile_bytes = peak_bytes = peak = peak_missiles = peak_pending = 0
    retained = terminal_bytes = dropped = 0; shots_by_ship = {}; capture = None
    if stream:
        (out/f'{layer}-geometry.json').write_bytes(encode(geometry))
        capture = gzip.open(out/f'{layer}-stream.jsonl.gz', 'wb')
    with TemporaryDirectory(prefix='gtw-display-d5-') as folder:
        service = RealtimeViewService('backend.display.d5', settlement_dir=folder)
        service._attach(battle, geometry)
        cursor = None
        if stream:
            initial = service.read(service.digest, display=dict(interface=INTERFACE, after_sequence=None))
            cursor = initial['view']['projectile_stream']['sequence']
            capture.write(encode(initial)+b'\n')
        for n in range(seconds*60):
            if n % 120 == 0:
                fixture.incoming(battle, distance=6000 if n % 480 == 0 else 1200, speed=1500, hp=3, layer=layer)
            start = perf_counter(); service.scheduler._stepper(); service.scheduler._committed = battle.session.world
            costs['step'].append(perf_counter()-start)
            world = battle.session.world
            # Authority evidence includes the full missile state, actual ship
            # damage/pose and interception history, not just shot counts.
            digest.update(encode(dict(step=world.fixed_step, projectiles=[asdict(p) for p in battle.projectiles],
                ships=[dict(motion=asdict(s.motion), devices=asdict(s.devices), crew=s.resources.crew) for s in world.ships],
                guns=[asdict(s) for s in battle.states], missiles=[asdict(s) for s in battle.missiles.states.values()],
                pending=[asdict(p) for p in battle.missiles.pending], damage=asdict(battle.damage_state))))
            peak = max(peak, len(battle.projectiles)); peak_missiles = max(peak_missiles, sum(p.missile is not None for p in battle.projectiles))
            peak_pending = max(peak_pending, len(battle.missiles.pending))
            pending_ids.update(p.projectile.id for p in battle.missiles.pending)
            if n % 4 == 3 or battle.ending:
                start = perf_counter(); service.publish(); costs['publish'].append(perf_counter()-start)
                start = perf_counter()
                packet = service.read(service.digest, **(dict(display=dict(interface=INTERFACE, after_sequence=cursor)) if stream else {}))
                costs['read'].append(perf_counter()-start)
                size = len(encode(packet)); wire_bytes += size; peak_bytes = max(peak_bytes, size)
                view = packet['view']; g = view['gunnery']
                projectile_bytes += len(encode([g['projectiles'], view.get('presentation'), view.get('projectile_stream')]))
                if stream:
                    p = view['projectile_stream']; cursor = p['sequence']
                    seen.update(x['id'] for x in p['starts']); terminal_ids.update(x['id'] for x in p['ends'])
                    assert not ({d.projectile.id for d in battle.missiles.pending} & {x['id'] for x in g['projectiles']}), 'VLS appeared before emergence'
                    retained = max(retained, service.projectile_stream.retained_bytes)
                    terminal_bytes = max(terminal_bytes, service.projectile_stream.terminal_bytes)
                    dropped = max(dropped, p['dropped_projectiles'])
                    capture.write(encode(packet)+b'\n')
            if battle.ending: break
        steps = len(costs['step'])
        inventory_hash = sha256(encode([i.snapshot().to_dict() for i in battle.inventory.inventories])).hexdigest()
        shots_by_ship = {str(key): s.shots for key, s in battle.missiles.states.items()}
        shell_shots = sum(s.shots for s in battle.states)
        if not battle.ending: battle.withdraw()
        result = settlement.capture(battle)
        # Epoch is randomly generated per independent battle, outside gameplay.
        result.pop('settlement_id'); result.pop('scene_id')
        settlement_hash = sha256(encode(result)).hexdigest()
        if capture: capture.close()
    row = dict(layer=layer, mode='stream' if stream else 'legacy', simulated_seconds=steps/60,
        authority_sha256=digest.hexdigest(), inventory_sha256=inventory_hash, settlement_sha256=settlement_hash,
        shell_shots=shell_shots, missile_shots=shots_by_ship, peak_projectiles=peak, peak_missiles=peak_missiles,
        peak_pending_vls=peak_pending, vls_departures=len(pending_ids), started=len(seen), ended=len(terminal_ids),
        dropped=dropped, maximum_retained_bytes=retained, maximum_terminal_bytes=terminal_bytes,
        bytes=dict(total=wire_bytes, projectile_total=projectile_bytes, peak=peak_bytes),
        timing={k:dict(calls=len(v), **stats(v)) for k,v in costs.items()})
    print(json.dumps(row), flush=True)
    assert steps == seconds*60, 'Natural ending must not be presented as a sustained run'
    assert shell_shots > 0 and peak_missiles >= 2 and peak_pending > 0
    assert dropped == 0 and peak_bytes < 1024*1024
    return row


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120); parser.add_argument('--layers', nargs='+', default=['upper','cloud','rain'])
    parser.add_argument('--validate-existing', action='store_true', help='Check identity completeness in already recorded publications')
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    if args.validate_existing:
        report = json.loads((args.out/'report.json').read_text(encoding='utf-8'))
        report['recording_checks'] = [validate_recording(args.out, r) for r in report['runs'] if r['mode'] == 'stream']
        (args.out/'report.json').write_bytes(encode(report))
        print(json.dumps(report['recording_checks'])); return
    MissileDefenseTests.setUpClass(); fixture = MissileDefenseTests(); rows = []
    for layer in args.layers:
        pair = [run(fixture, layer, stream, args.out, args.seconds) for stream in (False, True)]
        for key in ('authority_sha256', 'inventory_sha256', 'settlement_sha256'):
            assert pair[0][key] == pair[1][key], (layer, key)
        rows.extend(pair)
    sources = {str(p):sha256(p.read_bytes()).hexdigest() for p in Path('backend/high_wilderness_sidecar').glob('*.py')}
    report = dict(status='PASS', scope=__doc__, machine=platform.platform(), source_hashes=sources, runs=rows,
                  recording_checks=[validate_recording(args.out, r) for r in rows if r['mode'] == 'stream'])
    (args.out/'report.json').write_bytes(encode(report))


def validate_recording(directory, row):
    starts, ends = set(), set(); batches = 0; last = None
    with gzip.open(directory/f"{row['layer']}-stream.jsonl.gz", 'rt', encoding='utf-8') as source:
        for line in source:
            last = json.loads(line)['view']; packet = last['projectile_stream']; batches += 1
            starts.update(p['id'] for p in packet['starts'])
            for p in packet['ends']:
                assert p['id'] in starts and p['end_step'] <= packet['step']
                ends.add(p['id'])
            assert all(p['id'] in starts and p['id'] not in ends for p in last['gunnery']['projectiles'])
    active = {p['id'] for p in last['gunnery']['projectiles']}
    expected = row['shell_shots'] + sum(row['missile_shots'].values()) + int(row['simulated_seconds']/2)
    assert len(starts) == expected, 'A real shot or injected threat has no appearance record'
    assert starts-active == ends, 'A finished flight has no terminal record'
    result = dict(layer=row['layer'], batches=batches, expected_flights=expected, appearances=len(starts),
                  terminals=len(ends), still_active=len(active), missing_appearances=0, missing_terminals=0)
    return result


if __name__ == '__main__': main()
