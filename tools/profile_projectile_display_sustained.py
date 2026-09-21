"""120 simulated seconds, finite real defense equipment, injected test threats.

This is an accelerated deterministic workload, not a real-time FPS acceptance.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from tools import test_missile_defense as fixtures
from tools.profile_tactical_fire_control import stats
from backend.high_wilderness_sidecar import tactical_encounter as encounter
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE


def run(stream):
    geometry=None;original=encounter.build
    def capture(*a,**kw):
        nonlocal geometry
        result=original(*a,**kw);geometry=result[1];return result
    fixture=fixtures.MissileDefenseTests()
    with patch.object(encounter,'build',capture):battle=fixture.battle()
    with TemporaryDirectory(prefix='gtw-d0-sustained-') as directory:
        service=RealtimeViewService('backend.display.sustained',settlement_dir=directory)
        service._attach(battle,geometry)
        cursor=None
        if stream:cursor=service.read(service.digest,display=dict(interface=INTERFACE,after_sequence=None))['view']['projectile_stream']['sequence']
        durations=[];publishes=[];reads=[];wire_bytes=projectile_bytes=0;peak=peak_bytes=0
        retained=terminals=0;digest=sha256();threats=0
        for n in range(7200):
            if n%120==0:
                fixture.incoming(battle,distance=6000 if n%480==0 else 1200,speed=1500,hp=3);threats+=1
            start=perf_counter();service.scheduler._stepper();service.scheduler._committed=battle.session.world;durations.append(perf_counter()-start)
            digest.update(json.dumps([[(p.id,p.position,p.velocity,p.durability) for p in battle.projectiles],
                [s.shots for s in battle.states],[s.shots for s in battle.missiles.states.values()],battle.damage_state.hits],sort_keys=True).encode())
            peak=max(peak,len(battle.projectiles))
            if n%4==0 or battle.ending:
                start=perf_counter();service.publish();publishes.append(perf_counter()-start)
                start=perf_counter();packet=service.read(service.digest,**({'display':dict(interface=INTERFACE,after_sequence=cursor)} if stream else {}));reads.append(perf_counter()-start)
                if stream:cursor=packet['view']['projectile_stream']['sequence']
                size=len(json.dumps(packet,ensure_ascii=False).encode());wire_bytes+=size;peak_bytes=max(peak_bytes,size)
                v=packet['view'];projectile_bytes+=len(json.dumps([v['gunnery']['projectiles'],v.get('presentation'),v.get('projectile_stream')],ensure_ascii=False).encode())
                if service.projectile_stream:
                    retained=max(retained,service.projectile_stream.retained_bytes);terminals=max(terminals,service.projectile_stream.terminal_bytes)
            if battle.ending:break
        return dict(mode='stream' if stream else 'legacy',steps=len(durations),simulated_seconds=len(durations)/60,
            injected_threats=threats,peak_projectiles=peak,shots=sum(s.shots for s in battle.states),
            missile_shots=sum(s.shots for s in battle.missiles.states.values()),hits=battle.damage_state.hits,
            ending=battle.ending,authority_sha256=digest.hexdigest(),
            step=stats(durations),publish=stats(publishes),read=stats(reads),
            bytes=dict(total=wire_bytes,projectile_total=projectile_bytes,peak=peak_bytes),
            maximum_retained_event_bytes=retained,maximum_terminal_bytes=terminals)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    fixtures.MissileDefenseTests.setUpClass();rows=[]
    for stream in (False,True):
        row=run(stream);rows.append(row);print(json.dumps(row),flush=True)
    assert rows[0]['authority_sha256']==rows[1]['authority_sha256']
    assert all(r['steps']==7200 and r['shots']>0 and r['missile_shots']>0 for r in rows)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'report.json').write_text(json.dumps(dict(scope=__doc__,runs=rows),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
