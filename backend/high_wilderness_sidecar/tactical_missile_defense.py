"""Outer defense planning from sampled threats; no early durability subtraction."""
from dataclasses import replace
from math import hypot
from . import tactical_ballistics as ballistics
from .tactical_defense import commitments,policy
from . import projectile_observation as observed
from .tactical_layers import LAYERS


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
        if row['observer']!=n:continue
        stamp,measured,_=contacts[n,row['projectile_id']]
        measured=observed.extrapolate(measured,(world.fixed_step-stamp)/60)
        position,velocity=measured.position,measured.velocity
        launch_layer=layer or measured.height_layer
        if measured.height_layer!=launch_layer or abs(LAYERS.index(launch_layer)-LAYERS.index(world.ships[n].motion.height_layer))>1:continue
        aircraft=row.get('target_kind')=='aircraft'
        from .aviation_weapons import can_target
        if not can_target(profile,'aircraft' if aircraft else 'projectile'):continue
        if not aircraft and (observed.total_speed(measured)<policy()['high_speed_mps'] or not measured.flight_profile or measured.flight_profile.caliber_mm<75):continue
        sources,_=b.observation.defense_sources(n,measured.id,world,available,frame,mid)
        if not sources:reason='defense_sensor_unavailable';continue
        endangered=next(i for i,s in enumerate(world.ships) if s.ship_id==row['ship_id'])
        distance=hypot(*(a-c for a,c in zip(position,world.ships[endangered].motion.position_world_m.to_list())))
        if not aircraft and distance<=inner_range(b,endangered,row.get('impact_layer',launch_layer),world,available,inventories):reason='defense_inner_circle';continue
        ratio=1. if launch_layer==world.ships[n].motion.height_layer else ballistics.CROSS_LAYER_SPEED
        distance=hypot(*(a-c for a,c in zip(position,origin)))
        if distance>profile.range(ratio):continue
        covered=commitments(b,n,measured.id,[*projectiles,*(d.projectile for d in pending)],world,available,include_pending=False,frame=frame)
        if any(q.missile and q.missile.profile.interceptor for q in covered) or sum(q.interception_damage for q in covered)>=measured.durability:
            reason='defense_covered';continue
        # An optimistic lower bound rejects physically impossible launches;
        # actual turn/acceleration and swept hit remain authoritative.
        if distance/(profile.speed_cap*ratio+hypot(*velocity))>=row['remaining_s']:continue
        options.append((distance,measured.id,position,velocity,launch_layer))
    if not options:return None,reason
    _,pid,position,velocity,launch_layer=min(options,key=lambda row:(row[0],(0,row[1]) if type(row[1]) is int else (1,row[1])))
    return dict(projectile_id=pid,position=position,velocity=velocity,layer=launch_layer),None


class _ReservationInputs:
    """One prepare phase's interceptor index, updated in original round order.

    Only the target buckets changed by a prepared round lose their cached
    observation-gated commitments. Pending launches remain in their original
    positions after active rounds; nothing survives this candidate phase.
    """
    def __init__(self,b,world,available,projectiles,frame):
        self.b=b;self.world=world;self.available=available;self.frame=frame
        self.buckets={};self.targets={};self.covered={}
        for i,p in enumerate((*projectiles,*(d.projectile for d in b.missiles.pending))):self.replace(i,p)

    def replace(self,index,p):
        old=self.targets.pop(index,None)
        if old is not None:
            del self.buckets[old][index]
            if not self.buckets[old]:del self.buckets[old]
            self.covered.pop(old,None)
        target=p.interception_target_id
        if (target is not None and p.missile and p.missile.profile.interceptor and p.interception_damage>0
            and p.interception_expected_step is not None and self.world.fixed_step<=min(p.expires,p.interception_expected_step)):
            self.targets[index]=target;self.buckets.setdefault(target,{})[index]=p
            self.covered.pop(target,None)

    def occupied(self,observer,target,own_id):
        rows=self.buckets.get(target,{})
        if not any(p.id!=own_id for p in rows.values()):return False
        by_observer=self.covered.setdefault(target,{})
        if observer not in by_observer:
            by_observer[observer]=commitments(self.b,observer,target,tuple(rows[i] for i in sorted(rows)),
                self.world,self.available,include_pending=False,frame=self.frame)
        return any(p.id!=own_id for p in by_observer[observer])


def prepare_all(b,world,available,projectiles,environment,frame=None):
    from .missile_flight import prepare
    from .aviation_weapons import can_target
    result=list(projectiles)
    if not any(p.missile for p in result):return result
    sources={s.ship_id:n for n,s in enumerate(world.ships)}
    reservations=_ReservationInputs(b,world,available,result,frame)
    targets=tuple(t for t in environment.contacts if t.kind=='projectile')
    contacts={}
    for i,p in enumerate(result):
        if not p.missile:continue
        n=sources[p.ship_id];profile=p.missile.profile;side=b._sides[n]
        unavailable=tuple(t.id for t in targets if reservations.occupied(n,t.id,p.id)) if profile.interceptor else ()
        # These gates cannot depend on missile pose or seeker memory. Keep
        # original order and decoys; range, cone, layers, fields and threat
        # validation still run on each remaining contact for each missile.
        key=side,profile.interceptor,tuple(profile.target_kinds),profile.seeker=='anti_radiation'
        if key not in contacts:
            contacts[key]=tuple(t for t in environment.contacts if t.side!=side
                and (t.decoy or can_target(profile,t.kind))
                and (not profile.interceptor or t.decoy or t.durability is not None and t.durability>0)
                and (profile.seeker!='anti_radiation' or t.emitting))
        updated=prepare(p,world,b._sides,replace(environment,contacts=contacts[key],unavailable_targets=unavailable))
        result[i]=reservation(updated,world.fixed_step)
        reservations.replace(i,result[i])
    return result


def reservation(projectile,step):
    f=projectile.missile
    if not f or not f.profile.interceptor:return projectile
    target=f.target_id
    if target is None and f.seeker_state in ('midcourse','datalink'):target=f.original_target
    # A short bounded reservation during temporary loss prevents immediate
    # duplicate salvos; a lost or decoy-locked missile no longer covers forever.
    if target is None and f.loss_step is not None and step-f.loss_step<=policy()['reservation_grace_steps']:target=f.original_target
    if type(target) not in (str,int):target=None
    from .tactical_defense import reachable
    measurement=f.last_sample if f.last_sample and f.last_sample.id==target else f.link_sample
    if target is not None and measurement and measurement.id==target and not reachable(projectile,measurement,step):target=None
    return replace(projectile,interception_target_id=target,
        interception_expected_step=min(projectile.expires,step+policy()['reservation_grace_steps']) if target is not None else None)
