"""7a deterministic first contact along a fixed, translated two-fleet layout.

Split at sensor arc edges and the existing one-degree RCS knots. Within each
piece range**4 is linear in bearing; range**4 - distance**4 has one maximum.
Thus a narrow contact window is found without assuming global monotonicity or
sampling the whole 50 km interval on an arbitrary grid. No simulation is run.
"""
from dataclasses import replace
from math import atan2, cos, sin, pi, hypot, nextafter
from types import SimpleNamespace
from . import persistent_ship as ps, prepared_deployment as deployment
from .tactical_sensor_state import SensorState
from .tactical_observation import ObservationRuntime, observation_range, tracking_cost
from .tactical_layers import LAYERS
from 高天荒野舰艇水平射界 import interval_blocks_bearing
from 高天荒野舰艇统一战术场景 import TacticalSceneShipBinding

POLICY = 'gaotian.contact-start/7a-v1'
PRECISION_M = .001


def input_digest(rows):
    """Layout/damage/crew/resources/prototypes, independent of preview time/epoch."""
    flags={side['side_id']:member['deployment'] for side,member,_,_ in rows
        if member['instance_id']==side['flagship_instance_id']}
    return ps.canonical_sha256(dict(policy=POLICY, ships=[dict(
        side_id=side['side_id'], fleet_id=side['fleet_id'], flagship=side['flagship_instance_id'],
        member=dict(member,deployment=dict(x_m=float(round(member['deployment']['x_m']-flags[side['side_id']]['x_m'],8)),
            y_m=float(round(member['deployment']['y_m']-flags[side['side_id']]['y_m'],8)),heading_rad=member['deployment']['heading_rad'])),
        design=design.snapshot.source_sha256, record=record)
        for side, member, design, record in rows]))


class ContactState(SensorState):
    def __init__(self, rows, profile):
        seeds, bindings, latches = [], [], []
        for side, member, design, record in rows:
            pose=member['deployment']
            seed,_=deployment.load_ship(design,record,x=pose['x_m'],y=pose['y_m'],heading=pose['heading_rad'])
            seeds.append(seed)
            bindings.append(TacticalSceneShipBinding(record['ship_id'],design.snapshot,design.sortie,
                side_id=side['side_id'],fleet_id=side['fleet_id']))
            latches.append((record['ship_id'],tuple(record['state']['engine_latches'])))
        self.session=ps.sf.SimplifiedFlightSession(tuple(seeds),profile,direct_ship_id=bindings[0].ship_id,
            initial_resource_latches=tuple(latches))
        self._modules=[{m.id:m for m in seed.resources.modules} for seed in seeds]
        self._indices=[{m.instance_id:n for n,m in enumerate(seed.devices.modules)} for seed in seeds]
        self._sides=[s['side_id'] for s,_,_,_ in rows]
        self._capabilities=[{k:m.prototype.capability.to_dict() for k,m in modules.items()} for modules in self._modules]
        self._controllers=[tuple(k for k,m in modules.items() if m.prototype.category=='fire_control'
            and 'solution' in self._capabilities[n][k]['supported_requirements']) for n,modules in enumerate(self._modules)]
        self._availability_key,self._available=None,None
        self.guns=self.states=()
        self.observation=ObservationRuntime(self,SimpleNamespace(bindings=bindings))


def _rotate(p, angle):
    c,s=cos(angle),sin(angle)
    return p[0]*c-p[1]*s,p[0]*s+p[1]*c


def pair_distance(observation, n, mid, ship, target, effective, direction, limit):
    """Furthest valid separation for one sensor/target, or None (1 mm precision)."""
    arc=observation.arcs[n,mid]
    offset=_rotate(arc['origin_m'],ship.motion.heading_rad)
    origin=tuple(a+b for a,b in zip(ship.motion.position_world_m.to_list(),offset))
    dx,dy=target.position[0]-origin[0],target.position[1]-origin[1]
    cuts={1.,float(limit)}
    # Direction discontinuity/coincident origin and the minimum physical range.
    zero=-dy/direction
    if 1.<zero<limit:cuts.add(zero)
    angles=[v*pi/180-ship.motion.heading_rad for a,b in arc['blocked_intervals_deg']
        for v in (a,b,a-1e-8,b+1e-8)]
    if effective['channel']=='radar':
        angles.extend(v*pi/180-target.heading-pi for v in range(360))
    for angle in angles:
        sy,cy=sin(angle),cos(angle)
        if abs(sy)<1e-12:continue
        y=dx*cy/sy;d=(y-dy)/direction
        if 1.<d<limit and dx*sy+y*cy>0:cuts.add(d)
    def sample(d):return replace(target,position=(target.position[0],target.position[1]+direction*d))
    def radius(d):
        t=sample(d)
        factor=observation.radar_factor(t,origin) if effective['channel']=='radar' else 1.
        return observation_range(effective,t,ship_radar_factor=factor)
    def visible(d):return observation.visible(n,mid,ship,sample(d),effective,None)
    def blocked(d):
        local=_rotate((dx,dy+direction*d),-ship.motion.heading_rad)
        return hypot(*local)>1e-9 and interval_blocks_bearing(arc['blocked_intervals_deg'],atan2(local[0],local[1])*180/pi)
    points=sorted(cuts)
    for a,b in reversed(tuple(zip(points,points[1:]))):
        if visible(b):return b
        if blocked((a+b)/2):
            # Blocked intervals are inclusive; neighboring open pieces handle edges.
            continue
        lo,hi=sorted((dy+direction*a,dy+direction*b))
        r0,r1=radius(a),radius(b)
        minimum=hypot(dx,min(max(0.,lo),hi))
        if minimum>max(r0,r1)+1e-8:continue
        t0,t1=atan2(dx,dy+direction*a),atan2(dx,dy+direction*b)
        angle=atan2(sin(t1-t0),cos(t1-t0))
        slope=(r1**4-r0**4)/angle if abs(angle)>1e-12 else 0.
        # Derivative has the sign of -(4*y*(dx²+y²)² + slope*dx).
        for _ in range(60):
            y=(lo+hi)/2
            if 4*y*(dx*dx+y*y)**2+slope*dx>0:hi=y
            else:lo=y
        peak=min(b,max(a,((lo+hi)/2-dy)/direction))
        inset=min(.00001,(b-a)/4)
        candidates=(peak,peak+inset,peak-inset,(a+b)/2,a+inset,nextafter(a,b),a)
        peak=next((d for d in candidates if a<=d<=b and visible(d)),None)
        if peak is None:continue
        lower,upper=peak,b
        while upper-lower>PRECISION_M:
            middle=(lower+upper)/2
            if visible(middle):lower=middle
            else:upper=middle
        return lower
    return 1. if visible(1.) else None


def translate(world, sides, distance):
    # side.enemy above and side.player below, as in the existing preparation layout.
    return replace(world,ships=tuple(replace(s,motion=replace(s.motion,position_world_m=replace(
        s.motion.position_world_m,y=s.motion.position_world_m.y+distance/2*(1 if side=='side.enemy' else -1))))
        for s,side in zip(world.ships,sides)))


def solve(state, defender_flag):
    world=state.session.world;obs=state.observation
    flag=next(s for s in world.ships if s.ship_id==defender_flag)
    threshold=50000. if flag.motion.height_layer=='upper' else 25000.
    _,available=state._availability(world)
    targets=obs.targets(world,())
    best=None
    for n,ship in enumerate(world.ships):
        if ship.command.lifecycle.physical_status!='operational':continue
        for mid,spec in obs.sensors[n].items():
            if available[n][mid] or not obs.sensor_enabled.get((n,mid),True):continue
            capacity=int(spec['tracking_capacity']*obs.effect(world,n,mid,'sensor.track'))
            effective=dict(spec,range_efficiency=obs.effect(world,n,mid,'sensor.search'))
            for target in targets:
                if target.side==state._sides[n] or tracking_cost(target)>capacity:continue
                if abs(LAYERS.index(ship.motion.height_layer)-LAYERS.index(target.layer))>1:continue
                direction=1 if target.side=='side.enemy' else -1
                distance=pair_distance(obs,n,mid,ship,target,effective,direction,threshold)
                if distance is not None and (best is None or distance>best):best=distance
                if best==threshold:break
            if best==threshold:break
        if best==threshold:break
    if best is None:return dict(status='no_contact',distance_m=None,threshold_m=threshold,policy=POLICY)
    proposed=translate(world,state._sides,best)
    frame=obs.plan(proposed,available,())
    ps.need(any(t.valid for t in frame.local.values()),'$.contact_start','自动距离未形成有效观测，请重新计算')
    return dict(status='ready',distance_m=best,threshold_m=threshold,policy=POLICY)


def preview(service, rows):
    digest=input_digest(rows)
    cached=getattr(service,'_contact_cache',None)
    if cached and cached[0]==digest:return ps.clone(cached[1])
    if not hasattr(service,'_contact_profile'):
        service._contact_profile=ps.sf.build_sample_session(service.editor.root,with_command=True)._profile
    state=ContactState(rows,service._contact_profile)
    defender=next(record['ship_id'] for side,member,_,record in rows
        if side['side_id']=='side.player' and member['instance_id']==side['flagship_instance_id'])
    result=solve(state,defender)
    result=dict(result,input_sha256=digest)
    service._contact_cache=(digest,result)
    return ps.clone(result)


def initialize(battle):
    """An initial observation only: no step, firing, resource or EW advancement."""
    world=battle.session.world
    _,available=battle._availability(world)
    battle.observation.frame=battle.observation.plan(world,available,(),
        occluded=battle.ew.sensor_blocker(world,()))
