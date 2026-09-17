"""Swept contact between finite projectiles; no proximity detonation."""
from dataclasses import replace
from functools import lru_cache
from math import ceil, floor, sqrt
from pathlib import Path
import json

from .tactical_ballistics import flight_segment


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-point-defense.4.json').read_text(encoding='utf-8'))


def properties(profile):
    hp = next((hp for caliber,hp in reversed(policy()['durability_tiers']) if profile.caliber_mm>=caliber),None)
    return dict(durability=hp,maximum_durability=hp,collision_radius_m=profile.caliber_mm/2000,
                interception_damage=policy()['round_damage'] if profile.caliber_mm==30 else 0.)


def contact_fraction(a, b):
    """Earliest relative contact, with 10 micrometre drag-chord tolerance."""
    radius = a.collision_radius_m+b.collision_radius_m
    if radius <= 0:return None
    fa,fb = flight_segment(a),flight_segment(b)
    ae,_ = fa.at(1);be,_ = fb.at(1)
    if any(max(min(a.position[k],ae[k]),min(b.position[k],be[k])) >
           min(max(a.position[k],ae[k]),max(b.position[k],be[k]))+radius for k in range(2)):
        return None
    tolerance = min(1e-5,radius*1e-3)
    curvature = fa.k*fa.speed**2+fb.k*fb.speed**2
    count = max(1,ceil(sqrt(curvature*fa.seconds**2/(8*tolerance))))
    previous = tuple(x-y for x,y in zip(a.position,b.position))
    for n in range(1,count+1):
        pa,_ = fa.at(n/count);pb,_ = fb.at(n/count)
        current = tuple(x-y for x,y in zip(pa,pb))
        v = tuple(x-y for x,y in zip(current,previous))
        aa = sum(x*x for x in v);bb = 2*sum(x*y for x,y in zip(previous,v))
        cc = sum(x*x for x in previous)-(radius+tolerance)**2
        if cc <= 0:return (n-1)/count
        disc = bb*bb-4*aa*cc
        if aa>1e-20 and disc>=0:
            t = (-bb-sqrt(disc))/(2*aa)
            if 0<=t<=1:return (n-1+t)/count
        previous = current
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
    def buckets(p):
        end,_=flight_segment(p).at(1)
        lower=[floor((min(a,b)-p.collision_radius_m)/128) for a,b in zip(p.position,end)]
        upper=[floor((max(a,b)+p.collision_radius_m)/128) for a,b in zip(p.position,end)]
        if (upper[0]-lower[0]+1)*(upper[1]-lower[1]+1)>256:return None
        return ((p.height_layer,x,y) for x in range(lower[0],upper[0]+1) for y in range(lower[1],upper[1]+1))
    grid={};global_targets=set()
    for target in targets:
        cells=buckets(target)
        if cells is None:global_targets.add(target.id)
        else:
            for key in cells:grid.setdefault(key,set()).add(target.id)
    pending = []
    for shot in rounds:
        cells=buckets(shot)
        candidates={p.id for p in targets} if cells is None else global_targets.union(*(grid.get(key,set()) for key in cells))
        for identity in sorted(candidates):
            target=active[identity]
            if shot.id==target.id or sides.get(shot.ship_id)==sides.get(target.ship_id) or shot.height_layer!=target.height_layer:
                continue
            t = contact_fraction(shot,target)
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
            height_layer=target.height_layer,durability_before=target.durability,
            durability_after=after,intercepted=after<=0))
    return active,removed,tuple(events)
