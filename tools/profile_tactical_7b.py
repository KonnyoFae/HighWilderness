"""7b fixed-input baseline, distinct from real-time and final battle acceptance."""
import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from tools.tactical_7b_fixture import ROOT,CASES,freeze,restore,commands,write
from tools.profile_tactical_fire_control import stats
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import aviation_flight as af,aviation_logistics as al,aviation_combat as ac,aviation_ew as ae
from backend.high_wilderness_sidecar import tactical_fire_control as fc,tactical_contact_start as contact,persistent_ship as ps
from backend.high_wilderness_sidecar.projectile_stream import INTERFACE as STREAM


def fingerprint():
    paths=sorted([*ROOT.glob('*.py'),*(ROOT/'backend').rglob('*.py'),*(ROOT/'apps/desktop/src').rglob('*'),*(ROOT/'contracts/web_bridge/fixtures').glob('*.json'),*(ROOT/'舰艇数据').rglob('*.json'),
        *(ROOT/'tools').glob('*7b*')])
    hashes={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
    return dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        worktree_status=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True),files=hashes,
        digest=ps.canonical_sha256(hashes),python=sys.version,platform=platform.platform())


def counts(b):
    manifests=[i._value.get('aviation',{}).get('manifest',{}) for i in b.inventory.inventories]
    sensors=0
    for key,f in b.aviation.flights.items():
        if f['hp']<=0 or f['status']=='recovering':continue
        inv=b.inventory.inventories[f['owner']];model=af.ac.model(inv._definition['aviation']['catalog'],af.plane(inv,key)['model_id'])
        sensors+=int(model['infrared_range_m']>0)+int(model['radar_range_m']>0 and ae.working(f,model)['radar'])
    return dict(ships=len(b.session.world.ships),guns=len(b.guns),projectiles=len(b.projectiles),missiles=sum(p.missile is not None for p in b.projectiles),
        airborne=len(b.aviation.flights),aircraft_sensors=sensors,
        jammers=len(ae.areas(b,b.session.world,b.inventory.inventories,b.aviation.flights)),
        jobs=sum(len(i._value.get('aviation',{}).get('jobs',[])) for i in b.inventory.inventories),
        queued=sum(len(i._value.get('aviation',{}).get('queue',[])) for i in b.inventory.inventories),
        aircraft=sum(len(m.get('aircraft',[])) for m in manifests),pilots=sum(len(m.get('personnel',[]))+sum(len(a['crew']) for a in m.get('aircraft',[])) for m in manifests),
        local_tracks=len(b.observation.frame.local),shared_tracks=len(b.observation.frame.tracks),
        shots=sum(s.shots for s in b.states),missile_shots=sum(s.shots for s in b.missiles.states.values()),
        hits=b.damage_state.hits,interceptions=b.point_defense.kills)


def digest_state(b):
    return ps.canonical_sha256(dict(step=b.session.world.fixed_step,motion=[asdict(s.motion) for s in b.session.world.ships],
        inventories=[i._value for i in b.inventory.inventories],guns=[asdict(s) for s in b.states],
        projectiles=[(p.id,p.ship_id,p.weapon_id,p.position,p.velocity,p.durability,p.expires) for p in b.projectiles],
        aircraft=[(k,f['position'],f['velocity'],f['layer'],f['hp'],f['status'],f['task']) for k,f in sorted(b.aviation.flights.items())]))


def instrument(stack,b,parts):
    def wrap(owner,name,label):
        original=getattr(owner,name)
        def timed(*a,**kw):
            start=perf_counter()
            try:return original(*a,**kw)
            finally:parts[label]=parts.get(label,0.)+perf_counter()-start
        stack.enter_context(patch.object(owner,name,timed))
    for owner,name,label in [(b.observation,'plan','observation'),(b.point_defense,'observe','defense_observe'),(b.point_defense,'choose','defense_choose'),
        (fc.Plan,'acquire','gun_acquire'),(fc.Plan,'prepare','gun_solutions'),(b.damage,'advance','collision_damage'),
        (b.missiles,'advance','missile_logistics'),(b.missiles,'plan','missile_control'),
        (af,'advance','aircraft_flight_sensing'),(af,'share','aircraft_datalink'),(al,'advance','aviation_logistics'),(ac,'plan','aviation_weapons'),(ae,'refresh','aviation_ew')]:wrap(owner,name,label)


def entry_measure(rows,profile):
    from copy import deepcopy
    local=[];origins={s['side_id']:m['deployment'] for s,m,_,_ in rows if s['flagship_instance_id']==m['instance_id']}
    for s,m,d,r in rows:
        m=deepcopy(m);o=origins[s['side_id']]
        m['deployment']['x_m']-=o['x_m'];m['deployment']['y_m']-=o['y_m'];local.append((s,m,d,r))
    start=perf_counter();state=contact.ContactState(local,profile);build_s=perf_counter()-start
    defender=next(r['ship_id'] for s,m,d,r in local if s['side_id']=='side.player' and s['flagship_instance_id']==m['instance_id'])
    samples=[];results=[]
    for _ in range(3):
        start=perf_counter();results.append(contact.solve(state,defender));samples.append(perf_counter()-start)
    assert all(r==results[0] for r in results)
    original=contact.pair_distance;calls=[0]
    def count(*a,**kw):calls[0]+=1;return original(*a,**kw)
    with patch.object(contact,'pair_distance',count):assert contact.solve(state,defender)==results[0]
    return dict(state_build_s=build_s,solve=stats(samples),pair_calls=calls[0],result=results[0],cold_state_plus_first_s=build_s+samples[0])


def run(frozen,out,mode,steps,*,metered=False,frames=False):
    with TemporaryDirectory(prefix='hw-7b-measure-') as directory:
        service=RealtimeViewService('backend.7b.measure',settlement_dir=directory)
        start=perf_counter();b,geometry,rows=restore(frozen,service,mode);build_s=perf_counter()-start
        service._attach(b,geometry);packet=service.read(service.digest,display=dict(interface=STREAM,after_sequence=None));cursor=packet['view']['projectile_stream']['sequence']
        samples=[];pub=[];read=[];wire=[];parts={};component={};peak=counts(b);milestones={};tape=[];history=[];first=packet
        aviation_events={};workloads=[]
        if any(t.valid for t in b.observation.frame.local.values()):milestones['any_contact']=0
        if any(t.valid and b._sides[n]==b._sides[b._direct_index] for (n,_),t in b.observation.frame.tracks.items()):milestones['player_contact']=0
        initial_identities={p['id'] for i in b.inventory.inventories for p in i._value.get('aviation',{}).get('manifest',{}).get('personnel',[])}|{p['id'] for i in b.inventory.inventories for a in i._value.get('aviation',{}).get('manifest',{}).get('aircraft',[]) for p in a['crew']}
        with ExitStack() as stack:
            if metered:instrument(stack,b,parts)
            for step in range(steps):
                commands(b,frozen);parts.clear();start=perf_counter();service.scheduler._stepper();samples.append(perf_counter()-start);service.scheduler._committed=b.session.world
                for k,v in parts.items():component.setdefault(k,[]).append(v)
                c=counts(b);peak={k:max(peak[k],v) for k,v in c.items()}
                for event in b.aviation.recent:aviation_events[ps.canonical_sha256(event)]=event
                for key,n in c.items():
                    if key in ('shots','missile_shots','hits','interceptions','airborne') and n:milestones.setdefault(key,b.session.world.fixed_step)
                if any(t.valid for t in b.observation.frame.local.values()):milestones.setdefault('any_contact',b.session.world.fixed_step)
                if any(t.valid and b._sides[n]==b._sides[b._direct_index] for (n,_),t in b.observation.frame.tracks.items()):milestones.setdefault('player_contact',b.session.world.fixed_step)
                tape.append(digest_state(b))
                if step in (0,1,599,602,899):
                    fleet=[]
                    for inv in b.inventory.inventories:
                        av=inv._value.get('aviation',{});spec=inv._definition.get('aviation',{})
                        if not spec.get('facilities'):continue
                        planes=av['manifest']['aircraft']
                        fleet.append(dict(instance_id=inv._value['instance_id'],locations=dict(Counter(a['location'] for a in planes)),
                            used_ready_slots=sum(af.ac.model(spec['catalog'],a['model_id'])['berth_slots'] for a in planes if a['location'] in ('ready','preparing')),
                            jobs=dict(Counter(j['kind'] for j in av['jobs'])),queue=len(av['queue']),
                            facilities=spec['facilities'],personnel=av['manifest']['personnel']))
                    workloads.append(dict(step=step+1,ships=fleet))
                if step%4==0 or b.ending:
                    start=perf_counter();service.publish();pub.append(perf_counter()-start)
                    start=perf_counter();packet=service.read(service.digest,display=dict(interface=STREAM,after_sequence=cursor));read.append(perf_counter()-start)
                    cursor=packet['view']['projectile_stream']['sequence'];wire.append(len(json.dumps(packet,ensure_ascii=False).encode('utf-8')))
                    if frames:history.append(packet)
                if b.ending:break
        pilots=[p['id'] for i in b.inventory.inventories for p in i._value.get('aviation',{}).get('manifest',{}).get('personnel',[])]
        pilots.extend(p['id'] for i in b.inventory.inventories for a in i._value.get('aviation',{}).get('manifest',{}).get('aircraft',[]) for p in a['crew'])
        assert len(set(pilots))==len(pilots) and set(pilots)==initial_identities
        report=dict(case=frozen['case'],mode=mode,metered=metered,fixture_sha256=ps.canonical_sha256(frozen),build_s=build_s,steps=len(samples),simulated_s=len(samples)/60,
            step=stats(samples),steady_after_30=stats(samples[30:]),publish=stats(pub),read=stats(read),
            estimated_service_ms_per_sim_second=(sum(samples)+sum(pub)+sum(read))/(len(samples)/60)*1000,
            parts_inclusive={k:stats(v) for k,v in component.items()},parts_note='Only steps invoking each part; inclusive, potentially nested. Do not sum. Separate metered run.',
            wire=dict(samples=len(wire),total_bytes=sum(wire),max_bytes=max(wire,default=0)),peak=peak,last=counts(b),milestones_steps=milestones,
            ending=b.ending,authority_tape_sha256=ps.canonical_sha256(tape),aircrew_conserved=True,
            aviation_events=list(aviation_events.values()),aviation_workloads=workloads,
            gun_states=[dict(ship_id=b.session.world.ships[g.ship_index].ship_id,module_id=g.module_id,state=asdict(s)) for g,s in zip(b.guns,b.states)],
            retained=dict(display_bytes=service.projectile_stream.retained_bytes,terminal_bytes=service.projectile_stream.terminal_bytes),
            scope='Offline fixed-step sample, not wall-clock realtime, natural full battle, native GPU or maximum possible aircraft load.')
        if mode=='automatic':report['contact_measurement']=entry_measure(rows,b.session._profile)
        name=mode+('-metered' if metered else '')
        write(out/(name+'.json'),report)
        if frames:
            write(out/(name+'-frames.json'),dict(geometry=service.geometry,initial=first,packets=history))
        write(out/(name+'-tape.json'),tape)
        print(json.dumps({k:report[k] for k in ('case','mode','metered','steps','step','publish','read','peak','milestones_steps','ending')},ensure_ascii=False),flush=True)
        return report


def main():
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,required=True);a.add_argument('--case',choices=CASES,required=True)
    a.add_argument('--mode',choices=('fixed','automatic'),default='fixed');a.add_argument('--steps',type=int);a.add_argument('--metered',action='store_true');a.add_argument('--frames',action='store_true')
    args=a.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((args.out/'fixture.json').read_text(encoding='utf-8')) if (args.out/'fixture.json').exists() else freeze(args.out,args.case)
    assert frozen['case']==args.case
    write(args.out/(args.mode+('-metered' if args.metered else '')+'-source.json'),fingerprint())
    run(frozen,args.out,args.mode,args.steps or frozen['protocol'][args.mode+'_steps'],metered=args.metered,frames=args.frames)


if __name__=='__main__':main()
