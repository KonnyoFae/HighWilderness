"""Sampled seekers and memory. Steering never receives an unseen target object.

Truth enters only through contact(); memory and data-link paths use immutable
measurements. All deadlines use battle steps, independent of rendering.
"""
from dataclasses import dataclass, replace
from math import atan2, hypot, sin, cos, pi, sqrt
from .tactical_layers import LAYERS


@dataclass(frozen=True)
class Contact:
    id: str
    side: str
    position: tuple
    velocity: tuple
    layer: str
    large: bool = True
    emitting: bool = False
    decoy: bool = False
    signal: float = 1.
    kind: str = 'ship'
    durability: float | None = None
    payload: object = None


@dataclass(frozen=True)
class Measurement:
    id: str
    position: tuple
    velocity: tuple
    step: int
    layer: str
    sender: str | None = None
    sender_position: tuple | None = None


@dataclass(frozen=True)
class Environment:
    contacts: tuple = ()
    areas: tuple = ()
    links: tuple = ()  # (own side, Measurement); all sources validated by runtime
    retargets: tuple = ()  # (projectile id, target id)
    decoy_takeover_ratio: float = 1.5
    datalink_range_m: float = 100000.
    threat_check: object = None
    unavailable_targets: tuple = ()


def wrap(a):return (a+pi)%(2*pi)-pi


def segment_circle(a,b,center,radius):
    delta=tuple(y-x for x,y in zip(a,b));length=sum(x*x for x in delta)
    t=max(0.,min(1.,sum((c-x)*d for c,x,d in zip(center,a,delta))/length)) if length else 0.
    return sum((x+d*t-c)**2 for x,d,c in zip(a,delta,center))<=radius*radius


def blocked_channels(areas,origin,layer,target,target_layer):
    # Only endpoint layers participate: a cloud is not projected through every
    # altitude. Regions affect both sides and also block sensors inside them.
    return frozenset(a.kind for a in areas if a.layer in (layer,target_layer)
                     and a.kind in ('chaff','thermal') and segment_circle(origin,target,a.position,a.radius))


def contact(f,position,layer,own_side,t,environment,step):
    if t.side==own_side:return None
    if f.profile.interceptor:
        if not t.decoy and (t.kind!='projectile' or t.durability is None or t.durability<=0):return None
        if t.id in environment.unavailable_targets and t.id not in (f.original_target,f.target_id):return None
    elif t.kind=='projectile':return None
    # First acquisition and new targets remain in the immutable attack layer.
    if t.layer!=layer and not (f.ever_locked and t.id in (f.original_target,f.target_id)):return None
    distance=hypot(*(a-b for a,b in zip(t.position,position)));p=f.profile
    if distance>p.seeker_range*p.weather[LAYERS.index(t.layer)]:return None
    if abs(wrap(atan2(t.position[1]-position[1],t.position[0]-position[0])-f.heading))>p.seeker_half_cone:return None
    blocked=blocked_channels(environment.areas,position,layer,t.position,t.layer)
    if p.seeker in ('radar','anti_radiation') and 'chaff' in blocked:return None
    if p.seeker=='infrared' and 'thermal' in blocked:return None
    if p.seeker=='composite' and {'chaff','thermal'}<=blocked:return None
    if p.seeker=='anti_radiation' and not t.emitting:return None
    if p.interceptor and not t.decoy and environment.threat_check and not environment.threat_check(own_side,t):return None
    # A sample is copied only after every visibility gate above succeeds.
    return Measurement(t.id,t.position,t.velocity,step,t.layer),distance,t.signal/max(1.,distance)**2,t


def prediction(sample,step,*,velocity=True):
    return tuple(a+v*max(0,step-sample.step)/60 for a,v in zip(sample.position,sample.velocity)) if velocity else sample.position


def choose(f,candidates,environment):
    if not candidates:return None
    by_id={r[0].id:r for r in candidates}
    current=by_id.get(f.target_id) if f.seeker_state in ('tracking','acquiring') else None
    original=by_id.get(f.original_target)
    if current:
        # A deployed decoy is a competing signal, never an unconditional spell.
        decoys=[r for r in candidates if r[3].decoy and r[0].id!=current[0].id
                and r[2]>current[2]*environment.decoy_takeover_ratio]
        return max(decoys,key=lambda r:(r[2],r[0].id)) if decoys else current
    if (f.ever_locked or f.profile.interceptor) and original:return original
    preferred=sorted(candidates,key=lambda r:(not r[3].large,r[1],str(r[0].id)))[0]
    decoys=[r for r in candidates if r[3].decoy and r[2]>preferred[2]*environment.decoy_takeover_ratio]
    return max(decoys,key=lambda r:(r[2],r[0].id)) if decoys else preferred


def update(f,projectile,step,side,environment):
    p=f.profile;position=projectile.position
    requested=dict(environment.retargets).get(projectile.id)
    identity=requested or f.original_target
    links=[s for s_side,s in environment.links if s_side==side and s.id==identity
           and s.layer==projectile.height_layer and 0<=step-s.step<=p.datalink_valid_steps
           and (s.sender_position is None or hypot(*(a-b for a,b in zip(position,s.sender_position)))<=environment.datalink_range_m)] if p.datalink else []
    link=max(links,key=lambda s:(s.step,s.sender or '')) if links else None
    if requested and link:
        f=replace(f,original_target=requested,target_id=None,ever_locked=False,last_sample=None,
                  loss_step=None,search_step=None,acquire_step=None,seeker_state='midcourse')
    f=replace(f,link_sample=link)
    launch=Measurement(identity or '',f.launch_point,f.launch_velocity,f.born_step,projectile.height_layer)
    aim=prediction(link or launch,step)
    if f.seeker_state=='midcourse':
        if hypot(*(a-b for a,b in zip(aim,position)))>p.seeker_range*p.weather[LAYERS.index(projectile.height_layer)]:return f,aim
        f=replace(f,seeker_state='search')
    samples=[s for t in environment.contacts if (s:=contact(f,position,projectile.height_layer,side,t,environment,step))]
    chosen=choose(f,samples,environment)
    if chosen:
        sample,distance,_,_=chosen
        start=f.acquire_step if f.target_id==sample.id and f.acquire_step is not None else step
        state='tracking' if step-start>=p.confirm_steps else 'acquiring'
        lead=min(max(0,projectile.expires-step)/60,distance/max(1.,hypot(*projectile.velocity)))
        if p.interceptor:
            delta=tuple(a-b for a,b in zip(sample.position,position));velocity=sample.velocity
            a=sum(v*v for v in velocity)-hypot(*projectile.velocity)**2
            b=2*sum(d*v for d,v in zip(delta,velocity));c=distance**2
            disc=b*b-4*a*c
            roots=([-c/b] if abs(a)<1e-8 and b<0 else [(-b-sqrt(disc))/(2*a),(-b+sqrt(disc))/(2*a)] if abs(a)>=1e-8 and disc>=0 else [])
            lead=min([t for t in roots if t>=0],default=0.)
            lead=min(lead,max(0,projectile.expires-step)/60)
        aim=tuple(a+v*lead for a,v in zip(sample.position,sample.velocity))
        return replace(f,seeker_state=state,target_id=sample.id,original_target=f.original_target or sample.id,
                       ever_locked=True,last_sample=sample,acquire_step=start,loss_step=None,search_step=None),aim
    lost=f.loss_step if f.loss_step is not None else step
    f=replace(f,target_id=None,acquire_step=None,loss_step=lost)
    if link:return replace(f,seeker_state='datalink',last_sample=link,loss_step=step,search_step=None),prediction(link,step)
    if f.last_sample is None:
        # Initial failed searches continue toward the launch reference. Advanced
        # seekers start a pattern after reaching that region or search timeout.
        if p.lost_behavior=='memory_search' and (hypot(*(a-b for a,b in zip(aim,position)))<p.search_radius_m or step-lost>=p.memory_steps):
            f=replace(f,search_center=aim)
        else:return replace(f,seeker_state='search'),aim
    elif p.lost_behavior=='straight':return replace(f,seeker_state='lost'),None
    elif f.last_sample:
        aim=prediction(f.last_sample,step,velocity=p.memory_extrapolate)
        if step-lost<p.memory_steps:return replace(f,seeker_state='memory'),aim
        # Freeze the predicted area once; hidden later manoeuvres cannot move it.
        f=replace(f,search_center=f.search_center if f.search_step is not None else aim)
    start=f.search_step if f.search_step is not None else step
    # Actual figure-eight waypoints; steering/overload consumes time and speed.
    angle=2*pi*(step-start)/p.search_period_steps+pi/4
    center=f.search_center or aim;r=p.search_radius_m
    offset=(r*sin(angle),r*.5*sin(2*angle))
    axis=f.heading if f.search_step is None else f.search_axis
    point=(center[0]+offset[0]*cos(axis)-offset[1]*sin(axis),center[1]+offset[0]*sin(axis)+offset[1]*cos(axis))
    return replace(f,seeker_state='rescan',search_step=start,search_axis=axis,search_center=center),point
