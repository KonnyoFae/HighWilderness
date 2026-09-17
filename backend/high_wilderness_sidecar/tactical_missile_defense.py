"""Outer defense planning from sampled threats; no early durability subtraction."""
from dataclasses import replace
from math import hypot
from . import tactical_ballistics as ballistics
from .tactical_point_defense import predict
from .tactical_defense import commitments,policy


def inner_range(b,ship_index,layer,world,available,inventories):
    ship=world.ships[ship_index];ranges=[]
    if not b._can_fire(ship,ship_index):return 0.
    for i,(gun,state) in enumerate(zip(b.guns,b.states)):
        if gun.ship_index!=ship_index or not state.point_defense or not b.point_defense.capable[i] or available[ship_index][gun.module_id]:continue
        if (state.attack_layer or ship.motion.height_layer)!=layer:continue
        if not b.observation.defense_ready(ship_index,gun.module_id,world,available):continue
        inv=inventories[ship_index];row=next(r for r in inv._value['weapons'] if r['module_id']==gun.module_id)
        if not row['ready_rounds'] and not row['reload'] and not any(m['quantity']>0 for m in inv._value['magazines']):continue
        profile=b._gun_flights[i][row['recipe_id'] or state.reload_recipe_id]
        ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
        ranges.append(min(gun.maximum_range,ballistics.reference_range(profile,ratio)))
    return max(ranges,default=0.)


def select(b,n,mid,profile,layer,origin,world,available,inventories,projectiles,frame,contacts,threats,pending):
    options=[];reason='defense_waiting'
    for row in threats:
        if row['observer']!=n or row['height_layer']!=layer:continue
        stamp,measured,_=contacts[n,row['projectile_id']]
        position,velocity=predict(measured,(world.fixed_step-stamp)/60)
        if hypot(*velocity)<policy()['high_speed_mps'] or not measured.flight_profile or measured.flight_profile.caliber_mm<75:continue
        sources,_=b.observation.defense_sources(n,measured.id,world,available,frame,mid)
        if not sources:reason='defense_sensor_unavailable';continue
        endangered=next(i for i,s in enumerate(world.ships) if s.ship_id==row['ship_id'])
        distance=hypot(*(a-c for a,c in zip(position,world.ships[endangered].motion.position_world_m.to_list())))
        if distance<=inner_range(b,endangered,layer,world,available,inventories):reason='defense_inner_circle';continue
        ratio=1. if layer==world.ships[n].motion.height_layer else ballistics.CROSS_LAYER_SPEED
        distance=hypot(*(a-c for a,c in zip(position,origin)))
        if distance>profile.range(layer,ratio):continue
        covered=commitments(b,n,measured.id,[*projectiles,*(d.projectile for d in pending)],world,available,include_pending=False)
        if any(q.missile and q.missile.profile.interceptor for q in covered) or sum(q.interception_damage for q in covered)>=measured.durability:
            reason='defense_covered';continue
        # An optimistic lower bound rejects physically impossible launches;
        # actual turn/acceleration and swept hit remain authoritative.
        if distance/(profile.speed_cap*ratio+hypot(*velocity))>=row['remaining_s']:continue
        options.append((distance,measured.id,position,velocity))
    if not options:return None,reason
    _,pid,position,velocity=min(options)
    return dict(projectile_id=pid,position=position,velocity=velocity),None


def prepare_all(b,world,available,projectiles,environment):
    from .missile_flight import prepare
    result=list(projectiles)
    for i,p in enumerate(result):
        if not p.missile:continue
        n=next(n for n,s in enumerate(world.ships) if s.ship_id==p.ship_id)
        unavailable=tuple(t.id for t in environment.contacts if t.kind=='projectile' and
            any(q.id!=p.id and q.missile and q.missile.profile.interceptor for q in commitments(b,n,t.id,result,world,available))) if p.missile.profile.interceptor else ()
        updated=prepare(p,world,b._sides,replace(environment,unavailable_targets=unavailable))
        result[i]=reservation(updated,world.fixed_step)
    return result


def reservation(projectile,step):
    f=projectile.missile
    if not f or not f.profile.interceptor:return projectile
    target=f.target_id
    if target is None and f.seeker_state in ('midcourse','datalink'):target=f.original_target
    # A short bounded reservation during temporary loss prevents immediate
    # duplicate salvos; a lost or decoy-locked missile no longer covers forever.
    if target is None and f.loss_step is not None and step-f.loss_step<=policy()['reservation_grace_steps']:target=f.original_target
    if type(target) is not int:target=None
    return replace(projectile,interception_target_id=target,
        interception_expected_step=min(projectile.expires,step+policy()['reservation_grace_steps']) if target is not None else None)
