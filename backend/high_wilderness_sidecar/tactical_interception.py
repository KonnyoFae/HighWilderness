"""Swept finite contacts; interceptor activation radius is separate from body size."""
from dataclasses import replace
from functools import lru_cache
from math import ceil, floor, sqrt
from pathlib import Path
import json

from .tactical_ballistics import flight_segment,layer_at,layer_breaks


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-point-defense.4.json').read_text(encoding='utf-8'))


def properties(profile):
    hp = next((hp for caliber,hp in reversed(policy()['durability_tiers']) if profile.caliber_mm>=caliber),None)
    return dict(durability=hp,maximum_durability=hp,collision_radius_m=profile.caliber_mm/2000,
                interception_damage=policy()['round_damage'] if profile.caliber_mm==30 else 0.)


def contact_fraction(a, b, *, activation_radius=0.):
    """Earliest relative contact, with 10 micrometre drag-chord tolerance."""
    radius = max(a.collision_radius_m,activation_radius)+b.collision_radius_m
    if radius <= 0:return None
    fa,fb = flight_segment(a),flight_segment(b)
    ae,_ = fa.at(1);be,_ = fb.at(1)
    curve_margin=(fa.curvature+fb.curvature)*fa.seconds**2/8
    if any(max(min(a.position[k],ae[k]),min(b.position[k],be[k])) >
           min(max(a.position[k],ae[k]),max(b.position[k],be[k]))+radius+curve_margin for k in range(2)):
        return None
    tolerance = min(1e-5,radius*1e-3)
    curvature = fa.curvature+fb.curvature
    count = max(1,ceil(sqrt(curvature*fa.seconds**2/(8*tolerance))))
    times=sorted({*(n/count for n in range(count+1)),*layer_breaks(fa),*layer_breaks(fb)})
    for start,end in zip(times,times[1:]):
        if layer_at(a,fa,(start+end)/2)!=layer_at(b,fb,(start+end)/2):continue
        pa,_=fa.at(start);pb,_=fb.at(start)
        previous=tuple(x-y for x,y in zip(pa,pb))
        pa,_ = fa.at(end);pb,_ = fb.at(end)
        current = tuple(x-y for x,y in zip(pa,pb))
        v = tuple(x-y for x,y in zip(current,previous))
        aa = sum(x*x for x in v);bb = 2*sum(x*y for x,y in zip(previous,v))
        cc = sum(x*x for x in previous)-(radius+tolerance)**2
        if cc <= 0:return start
        disc = bb*bb-4*aa*cc
        if aa>1e-20 and disc>=0:
            t = (-bb-sqrt(disc))/(2*aa)
            if 0<=t<=1:
                hit=start+(end-start)*t
                if layer_at(a,fa,hit)==layer_at(b,fb,hit):return hit
    if layer_at(a,fa,1.)==layer_at(b,fb,1.) and sum((x-y)**2 for x,y in zip(ae,be))<=(radius+tolerance)**2:return 1.
    return None


def resolve(projectiles, sides, deadlines, step):
    """Only contacts strictly before ship impact/TTL can intercept.

    Returns staged projectiles and events. No committed battle state is changed.
    The firing round is consumed on contact; a surviving target retains motion.
    """
    active = {p.id:p for p in projectiles if step<=p.expires}
    targets = [p for p in active.values() if p.durability is not None and p.durability>0]
    rounds = [p for p in active.values() if p.interception_damage>0]
    if not targets or not rounds:return active,set(),()
    def buckets(p,activation_radius=0.):
        path=flight_segment(p);end,_=path.at(1)
        radius=max(p.collision_radius_m,activation_radius)+path.curvature*path.seconds**2/8
        lower=[floor((min(a,b)-radius)/128) for a,b in zip(p.position,end)]
        upper=[floor((max(a,b)+radius)/128) for a,b in zip(p.position,end)]
        if (upper[0]-lower[0]+1)*(upper[1]-lower[1]+1)>256:return None
        layers={p.height_layer,*(v for _,v in getattr(path,'transitions',()))}
        return ((layer,x,y) for layer in layers for x in range(lower[0],upper[0]+1) for y in range(lower[1],upper[1]+1))
    grid={};global_targets=set()
    for target in targets:
        cells=buckets(target)
        if cells is None:global_targets.add(target.id)
        else:
            for key in cells:grid.setdefault(key,set()).add(target.id)
    pending = []
    for shot in rounds:
        cells=buckets(shot,shot.interception_radius_m)
        candidates={p.id for p in targets} if cells is None else global_targets.union(*(grid.get(key,set()) for key in cells))
        for identity in sorted(candidates):
            target=active[identity]
            if shot.id==target.id or sides.get(shot.ship_id)==sides.get(target.ship_id):
                continue
            t = contact_fraction(shot,target,activation_radius=shot.interception_radius_m)
            limit = min(deadlines.get(shot.id,float('inf')),deadlines.get(target.id,float('inf')),
                        1. if step==shot.expires or step==target.expires else 1.+1e-8)
            if t is not None and t < limit-1e-10:
                pending.append((t,target.id,shot.id))
    removed,events = set(),[]
    for t,tid,sid in sorted(pending):
        if tid in removed or sid in removed:continue
        target,shot = active[tid],active[sid]
        after = max(0.,target.durability-shot.interception_damage)
        active[tid] = replace(target,durability=after)
        removed.add(sid)
        if after<=0:removed.add(tid)
        point,_ = flight_segment(target).at(t)
        events.append(dict(step=step,impact_fraction=t,projectile_id=tid,round_id=sid,
            source_ship_id=shot.ship_id,weapon_id=shot.weapon_id,position_m=point,
            height_layer=layer_at(target,flight_segment(target),t),durability_before=target.durability,
            durability_after=after,intercepted=after<=0))
    return active,removed,tuple(events)
