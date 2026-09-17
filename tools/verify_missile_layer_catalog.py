"""17-model altitude acceptance with explicit moving observation targets.

These are controlled flight samples, not a claim that every model can hit
every ship after a 5 km climb. Real launcher/warhead coverage remains in
test_missile_catalog; fleet and storage acceptance have separate fixtures.
"""
import argparse
from dataclasses import replace
import json
from math import hypot,isfinite,pi,cos
from pathlib import Path
from backend.high_wilderness_sidecar import missile_flight as mf,missile_guidance as mg,missile_maneuver as mm
from backend.high_wilderness_sidecar.tactical_gunnery import Projectile
from backend.high_wilderness_sidecar.tactical_ballistics import advance_projectile


def body(profile,layer,age,speed):
    f=mf.Flight(profile,'blast',0,0.,(1000.,0.),(0.,0.),'target',age=age,
                phase=profile.phase(age),seeker_state='tracking',target_id='target',ever_locked=True)
    return Projectile(1,'own','launcher',(0.,0.),(0.,0.),(speed,0.),profile.lifetime(),None,layer,
                      (profile.model_id+'.blast',1),profile.ballistics(1.),durability=profile.durability,missile=f)


def run(profile,start,goal,*,coasting=False,low=False):
    age=profile.boost_steps+profile.engine_steps if coasting else profile.boost_steps
    speed=profile.speed_cap if coasting else profile.boost_cap
    p=body(profile,start,age,50. if low else speed)
    if low:p=replace(p,velocity=(50.*cos(pi/6),0.),missile=replace(p.missile,
        pitch_rad=pi/6,vertical_velocity_mps=25.,altitude_m=mm.altitude(start)+10.,maneuver_target_id='target'))
    seen=[start];failed=False;minimum=hypot(*p.velocity,p.missile.vertical_velocity_mps);maximum=minimum
    initial_altitude=mm.altitude(start);start_step=age;deadline=p.expires
    while age<deadline:
        # Always within the narrowest weather-adjusted seeker range and cone.
        # Only observed motion is supplied; no hidden fleet state exists here.
        point=(p.position[0]+1000.,p.position[1])
        target=mg.Contact('target','enemy',point,p.velocity,goal,emitting=True,
                          kind='projectile' if profile.interceptor else 'ship',durability=6.)
        f,aim=mg.update(p.missile,p,age,'own',mg.Environment(contacts=(target,)))
        f=mm.control(f,hypot(*p.velocity,f.vertical_velocity_mps),aim,p.position,p.height_layer)
        old=p;p=advance_projectile(replace(p,missile=f));age+=1;p=replace(p,missile=replace(p.missile,age=age))
        assert p.expires==deadline and p.missile.born_step==0
        speed=hypot(*p.velocity,p.missile.vertical_velocity_mps)
        assert all(isfinite(v) for v in (*p.position,*p.velocity,p.missile.altitude_m,speed))
        assert hypot(*(a-b for a,b in zip(p.position,old.position)),p.missile.altitude_m-(old.missile.altitude_m if old.missile.altitude_m is not None else initial_altitude))<max(speed,maximum,100.)/60+1.
        minimum=min(minimum,speed);maximum=max(maximum,speed)
        if p.height_layer!=seen[-1]:seen.append(p.height_layer)
        failed |= 'target' in p.missile.failed_climb_targets
        if low and failed and p.missile.return_layer is None:break
        if not low and p.height_layer==goal:break
    result=dict(model=profile.model_id,start=start,goal=goal,phase='coast' if coasting else 'powered',low_speed=low,
        layers=seen,elapsed_s=(age-start_step)/60,outcome='returned_after_failure' if low and failed and p.missile.return_layer is None else
        'reached' if p.height_layer==goal else 'climb_failed' if failed else 'lifetime_exhausted',
        minimum_speed_mps=minimum,maximum_speed_mps=maximum,final_altitude_m=p.missile.altitude_m,fixed_lifetime_s=deadline/60)
    if low:
        assert result['outcome']=='returned_after_failure',result
        assert seen==[start] and p.missile.failed_climb_targets==('target',),result
        # Same identity remains blocked after returning; another identity is legal.
        sample=mg.Measurement('target',(p.position[0]+1000,0.),(0.,0.),age,goal)
        blocked=mm.control(replace(p.missile,seeker_state='tracking',last_sample=sample),speed,sample.position,p.position,p.height_layer)
        assert blocked.maneuver_reason=='climb_failed_for_target',result
    else:assert result['outcome']=='reached',result
    return result


def verify():
    rows=[]
    for p in mf.profiles().values():
        for start,goal in [('cloud','upper'),('upper','cloud')]:rows.append(run(p,start,goal))
        rows.append(run(p,'cloud','upper',coasting=True,low=True))
        # Boost cannot steer; a fresh target in another layer is not acquired.
        q=body(p,'cloud',0,p.launch_speed)
        control=mm.control(q.missile,p.launch_speed,(0.,1000.),q.position,q.height_layer)
        assert control.angular_rate==0. and control.pitch_rate==0.
        q=replace(q,missile=replace(q.missile,ever_locked=False,target_id=None,seeker_state='search'))
        c=mg.Contact('new','enemy',(500.,0.),(0.,0.),'upper',emitting=True,kind='projectile' if p.interceptor else 'ship',durability=6.)
        f,_=mg.update(q.missile,q,0,'own',mg.Environment(contacts=(c,)))
        assert f.target_id is None
    assert len(rows)==51
    return dict(status='MISSILE_LAYER_CATALOG_5J4_PASS',models=len(mf.profiles()),samples=rows,
                scope='17 unchanged model profiles; moving observed targets, powered ascent/descent and 50 m/s unpowered failure/return; physical flight at 60 Hz, exact expiry and identity preservation.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    result=verify();args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],models=result['models'],samples=len(result['samples']))))
