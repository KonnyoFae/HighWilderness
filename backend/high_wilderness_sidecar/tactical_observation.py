"""5d sampled observations. Only this boundary reads enemy truth for sensors.

Plans are published after the enclosing atomic battle step succeeds. Shared
tracks preserve the originating sample; they never remeasure at the receiver.
"""
from dataclasses import dataclass, replace, field
from math import hypot, atan2, degrees
from pathlib import Path
import json

from 高天荒野舰艇数据契约 import canonical_sha256
from 高天荒野舰艇水平射界 import sensor_arc, interval_blocks_bearing
from . import persistent_ship as ps
from .tactical_layers import LAYERS


@dataclass(frozen=True)
class Target:
    id: str
    kind: str
    side: str
    position: tuple
    velocity: tuple
    layer: str
    large: bool = True
    powered: bool = False
    heading: float = 0.
    yaw: float = 0.
    durability: float | None = None
    payload: object = None
    threat: bool = False


@dataclass(frozen=True)
class Track:
    target: Target
    step: int
    sources: tuple  # (source ship index, module id, channel)
    valid: bool = True


@dataclass(frozen=True)
class Frame:
    tracks: dict
    assignments: dict
    devices: tuple
    step: int
    locks: dict = field(default_factory=dict)
    local: dict = field(default_factory=dict)


def tracking_cost(target, high_speed=1000.):
    return (1 if target.large else 2)*(4 if hypot(*target.velocity)>=high_speed else 1)


def can_observe(sensor, own_position, own_layer, target, blocked=False):
    if blocked or abs(LAYERS.index(own_layer)-LAYERS.index(target.layer))>1:return False
    maximum=sensor['range_m']
    if target.kind=='ship':maximum=min(maximum,sensor['ship_range_m'])
    elif not target.powered:maximum=min(maximum,sensor['coasting_range_m'])
    maximum*=sensor['weather'][LAYERS.index(target.layer)]*sensor['range_efficiency']
    return hypot(*(a-b for a,b in zip(own_position,target.position)))<=maximum


def allocate(candidates, previous, capacity, priorities):
    """Retain valid existing tracks; on shrink retain important existing first.

    New high priority contacts do not preempt existing tracks. Invalid or
    out-of-range tracks release their occupancy at this same boundary.
    """
    costs={t.id:tracking_cost(t) for t in candidates}
    retained=set(previous)&costs.keys()
    order=sorted(retained,key=lambda k:(priorities[k],str(k)))+sorted(costs.keys()-retained,key=lambda k:(priorities[k],str(k)))
    result=[];used=0
    for key in order:
        if used+costs[key]<=capacity:result.append(key);used+=costs[key]
    return tuple(result),used


class ObservationRuntime:
    def __init__(self,battle,scenario):
        self.battle=battle
        self.policy=json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-observation.5d.json').read_text(encoding='utf-8'))
        profiles={(r['prototype']['id'],r['prototype']['version']):r for r in self.policy['sensor_profiles']}
        self.sensors=[];self.links=[];self.arcs={};self.integrated={};self.sensor_enabled={}
        from .tactical_defense import integrated
        for n,modules in enumerate(battle._modules):
            sensors={};links=[]
            for mid,m in modules.items():
                cap=m.prototype.capability.to_dict()
                if m.prototype.category=='datalink':links.append(mid)
                own=integrated(m)
                if own:
                    self.integrated[n,mid]=own
                    self.arcs[n,mid]=sensor_arc(scenario.bindings[n].snapshot.hull,m)
                    sensors[mid]={k:own[k] for k in ('channel','range_m','tracking_capacity','coasting_range_m','ship_range_m','weather')}
                    sensors[mid]['legacy']=False
                    continue
                if m.prototype.category!='sensor' or cap['sensor_channel'] not in ('radar','infrared'):continue
                profile=profiles.get((m.prototype.reference.id,m.prototype.reference.version))
                if profile:ps.need(canonical_sha256(m.prototype)==profile['prototype_sha256'],'$.sensor','传感器配置与原型不符')
                # Explicit legacy adapter: unchanged archived geometry and range,
                # but all compatible radars now feed the same observation system.
                legacy=not profile
                self.arcs[n,mid]=sensor_arc(scenario.bindings[n].snapshot.hull,m)
                sensors[mid]=dict(channel=cap['sensor_channel'],range_m=cap['maximum_instrumented_range_m'],
                    tracking_capacity=profile['tracking_capacity'] if profile else 24,
                    coasting_range_m=profile['coasting_range_m'] if profile else cap['maximum_instrumented_range_m'],
                    ship_range_m=profile['ship_range_m'] if profile else cap['maximum_instrumented_range_m'],
                    weather=profile['weather'] if profile else [1.,.8,.4],legacy=legacy)
            self.sensors.append(sensors);self.links.append(tuple(links))
        self.frame=Frame({}, {}, (), -1)
        self.sequence=0;self.last=None;self.locks={};self.pending_modes={}

    def visible(self,n,mid,ship,target,effective,occluded):
        from .tactical_gunnery import rotate, add
        arc=self.arcs[n,mid];position=tuple(ship.motion.position_world_m.to_list())
        origin=add(position,rotate(arc['origin_m'],ship.motion.heading_rad))
        if not can_observe(effective,origin,ship.motion.height_layer,target):return False
        delta=tuple(a-b for a,b in zip(target.position,origin))
        if hypot(*delta)>1e-9:
            local=rotate(delta,-ship.motion.heading_rad)
            if interval_blocks_bearing(arc['blocked_intervals_deg'],degrees(atan2(local[0],local[1]))):return False
        return not (occluded and occluded(n,mid,target))

    def effect(self,world,n,mid,function):
        b=self.battle;m=b._modules[n][mid]
        if (n,mid) in self.integrated:function=self.integrated[n,mid]['damage_function']
        hp=world.ships[n].devices.modules[b._indices[n][mid]].durability_points
        response=next((r for r in m.prototype.damage_responses if r.function_id==function),None)
        return (response.output_fraction(hp/m.prototype.durability_points) if response else 1.)*b.crew_efficiency(world,n,mid,function)

    def targets(self,world,projectiles,missiles=()):
        b=self.battle;result=[]
        for n,s in enumerate(world.ships):
            if s.command.lifecycle.physical_status!='operational' or s.motion.hull_integrity_fraction<=0:continue
            m=s.motion
            result.append(Target(s.ship_id,'ship',b._sides[n],tuple(m.position_world_m.to_list()),
                tuple(m.velocity_world_mps.to_list()),m.height_layer,heading=m.heading_rad,yaw=m.yaw_rate_radps))
        for p in projectiles:
            if p.durability is None or p.durability<=0:continue
            side=b.damage.sides[p.ship_id] if b.damage else b._sides[next(n for n,s in enumerate(world.ships) if s.ship_id==p.ship_id)]
            # Priority hint only. Actual interception still predicts swept hull
            # collision from the measured sample in PointDefense.
            threat=False
            for n,s in enumerate(world.ships):
                if b._sides[n]==side or s.motion.height_layer!=p.height_layer:continue
                delta=tuple(a-c for a,c in zip(s.motion.position_world_m.to_list(),p.position))
                relative=tuple(a-c for a,c in zip(p.velocity,s.motion.velocity_world_mps.to_list()))
                speed2=sum(v*v for v in relative)
                time=sum(a*v for a,v in zip(delta,relative))/speed2 if speed2 else -1
                if 0<=time<=(p.expires-world.fixed_step)/60 and hypot(*(a-v*time for a,v in zip(delta,relative)))<100:threat=True;break
            result.append(Target(p.id,'missile' if getattr(p,'missile',None) else 'shell',side,p.position,p.velocity,p.height_layer,
                powered=bool(getattr(p,'missile',None) and p.missile.phase!='coast'),
                large=bool(p.flight_profile and p.flight_profile.caliber_mm>=75),durability=p.durability,payload=p,threat=threat))
        # 5e supplies immutable Target missile samples with powered/coasting
        # state. No fake missile objects are added by 5d.
        result.extend(missiles)
        return tuple(result)

    def plan(self,world,available,projectiles,*,missiles=(),occluded=None):
        b=self.battle;step=world.fixed_step;targets=self.targets(world,projectiles,missiles)
        assignments={};local={};devices=[]
        for n,s in enumerate(world.ships):
            assigned=set()
            ordered=sorted(self.sensors[n],key=lambda mid:((n,mid) not in self.integrated,not bool(self.frame.assignments.get((n,mid))),mid))
            for mid in ordered:
                spec=self.sensors[n][mid];reason=available[n][mid]
                own=(n,mid) in self.integrated
                if own and not self.sensor_enabled.get((n,mid),True):reason=reason or 'sensor_disabled'
                if s.command.lifecycle.physical_status!='operational':reason=reason or 'control_unavailable'
                capacity=int(spec['tracking_capacity']*self.effect(world,n,mid,'sensor.track')) if not reason else 0
                effective=dict(spec,range_efficiency=self.effect(world,n,mid,'sensor.search'))
                candidates=[t for t in targets if t.side!=b._sides[n] and (own or t.id not in assigned) and not reason
                    and self.visible(n,mid,s,t,effective,occluded)]
                priorities={t.id:(0 if self.locks.get(n)==t.id else 1 if t.threat else 2,
                    hypot(*(a-c for a,c in zip(t.position,s.motion.position_world_m.to_list())))) for t in candidates}
                own_target=next((v.interception_target_id for g,v in zip(b.guns,b.states) if g.ship_index==n and g.module_id==mid),None)
                launcher=getattr(b,'missiles',None)
                if launcher and (n,mid) in launcher.states:own_target=launcher.states[n,mid].active_target
                if own:
                    priorities={t.id:(-1 if t.id==own_target else 0 if t.threat else 2,priorities[t.id][1]) for t in candidates}
                previous=() if own and any(t.threat or t.id==own_target for t in candidates) else self.frame.assignments.get((n,mid),())
                ids,used=allocate(candidates,previous,capacity,priorities)
                assignments[n,mid]=ids;assigned.update(ids)
                by_id={t.id:t for t in candidates}
                for key in ids:
                    old=self.frame.local.get((n,key));sample=by_id[key]
                    source=(n,mid,spec['channel'])
                    if old and old.valid and source in old.sources and step-old.step<self.policy['sample_steps']:
                        track=replace(old,sources=(source,))
                    else:track=Track(sample,step,(source,))
                    existing=local.get((n,key))
                    if existing:
                        latest=track if track.step>=existing.step else existing
                        track=replace(latest,sources=tuple(sorted(set(existing.sources+track.sources))))
                    local[n,key]=track
                devices.append(dict(ship_index=n,module_id=mid,capacity=capacity,used=used,tracked=len(ids),
                    waiting=len(candidates)-len(ids),reason=reason,channel=spec['channel'],range_m=spec['range_m'],
                    coasting_range_m=spec['coasting_range_m'],
                    integrated=own,sensor_enabled=self.sensor_enabled.get((n,mid),True),
                    blocked_angle_deg=sum(b-a for a,b in self.arcs[n,mid]['blocked_intervals_deg'])))
        tracks=dict(local)
        linked=[n for n,s in enumerate(world.ships) if s.command.lifecycle.physical_status=='operational'
            and any(available[n][mid] is None for mid in self.links[n]) and self.controllers(n,world,available)[1]>0]
        for receiver in linked:
            for (sender,key),track in local.items():
                if sender==receiver or sender not in linked or b._sides[sender]!=b._sides[receiver]:continue
                if hypot(*(a-c for a,c in zip(world.ships[sender].motion.position_world_m.to_list(),world.ships[receiver].motion.position_world_m.to_list())))>self.policy['datalink_range_m']:continue
                old=tracks.get((receiver,key))
                latest=track if old is None or track.step>old.step else old
                tracks[receiver,key]=replace(latest,sources=tuple(sorted(set(track.sources+(old.sources if old else ())))))
        for key,track in self.frame.tracks.items():
            if key not in tracks and step-track.step<=self.policy['memory_steps']:
                tracks[key]=replace(track,valid=False,sources=())
        frame=Frame(tracks,assignments,tuple(devices),step,local=local)
        locks={}
        for n,key in self.locks.items():
            if key is None:continue
            sources,_=self.sources(n,key,world,available,frame)
            previous=self.frame.locks.get(n)
            start=previous['start'] if previous and previous['target']==key and previous['status']!='lost' else step
            status='lost' if not sources else 'locked' if step-start>=b.config['lock_acquisition_steps'] else 'acquiring'
            locks[n]=dict(target=key,start=start,status=status)
        return replace(frame,locks=locks)

    def controllers(self,n,world,available,position=None):
        b=self.battle;controls=[];channels=0
        for mid in b._controllers[n]:
            if available[n][mid]:continue
            cap=b._capabilities[n][mid];eff=self.effect(world,n,mid,'fire_control.solution')
            if position is not None and hypot(*(a-c for a,c in zip(position,world.ships[n].motion.position_world_m.to_list())))>cap['maximum_lock_range_m']:continue
            controls.append(mid);channels+=int(cap['simultaneous_channels']*eff)
        return tuple(controls),channels

    def sources(self,n,key,world,available,frame=None,*,radar_only=False):
        track=(frame or self.frame).tracks.get((n,key))
        if not track or not track.valid:return (),0
        _,channels=self.controllers(n,world,available,track.target.position)
        sources=tuple(mid if i==n else f'{world.ships[i].ship_id}/{mid}' for i,mid,channel in track.sources
            if available[i][mid] is None and world.ships[i].command.lifecycle.physical_status=='operational'
            and self.sensor_enabled.get((i,mid),True)
            and (i==n or self.controllers(i,world,available)[1] and
                any(available[n][link] is None for link in self.links[n]) and any(available[i][link] is None for link in self.links[i]))
            and (not radar_only or channel=='radar' and i==n))
        return sources if channels else (),channels

    def defense_sources(self,n,key,world,available,frame=None,weapon_id=None):
        frame=frame or self.frame;track=frame.tracks.get((n,key))
        if track and track.valid:
            own=tuple(mid for i,mid,_ in track.sources if i==n and (n,mid) in self.integrated
                and (weapon_id is None or mid==weapon_id) and available[n][mid] is None and self.sensor_enabled.get((n,mid),True))
            if own:return own,1
        return self.sources(n,key,world,available,frame)

    def defense_ready(self,n,mid,world,available):
        return ((n,mid) in self.integrated and available[n][mid] is None and self.sensor_enabled.get((n,mid),True)) or self.ready(n,world,available)

    def contact(self,n,key,world,quality,frame=None):
        track=(frame or self.frame).tracks.get((n,key))
        if not track or not track.valid:return None
        t=track.target
        target=next(i for i,s in enumerate(world.ships) if s.ship_id==key)
        return self.battle._measure(n,target,world,quality,t,track.step)

    def ready(self,n,world,available):
        if not self.controllers(n,world,available)[1]:return False
        if any(available[n][mid] is None and self.sensor_enabled.get((n,mid),True) for mid in self.sensors[n]):return True
        if not any(available[n][mid] is None for mid in self.links[n]):return False
        return any(self.battle._sides[i]==self.battle._sides[n] and
            any(available[i][mid] is None for mid in self.links[i]) and
            any(available[i][mid] is None and self.sensor_enabled.get((i,mid),True) for mid in self.sensors[i]) and self.controllers(i,world,available)[1] and
            hypot(*(a-c for a,c in zip(world.ships[i].motion.position_world_m.to_list(),world.ships[n].motion.position_world_m.to_list())))<=self.policy['datalink_range_m']
            for i in range(len(world.ships)))

    def submit(self,value):
        b=self.battle;b._guard();v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id kind target value','$.fire_control_input')
        if v==self.last:return False
        ps.need(v['epoch']==b.session.world.epoch and b.ending is None,'$.epoch','场景已结束或过期')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        ps.identifier(v['ship_id'],'$.ship_id')
        ps.need(v['target'] is None or type(v['target']) in (str,int),'$.target','目标身份格式错误')
        ps.need(v['sequence']==self.sequence+1,'$.sequence','指令序号不连续')
        n=next((n for n,s in enumerate(b.session.world.ships) if s.ship_id==v['ship_id']),None)
        ps.need(n is not None and b._sides[n]==b._sides[b._direct_index],'$.ship_id','只能操作己方设备')
        ps.need(b._can_fire(b.session.world.ships[n],n),'$.ship_id','所选舰艇已失去指挥权限')
        if v['kind']=='sensor_mode':
            ps.need((n,v['target']) in self.integrated and v['value'] in ('active','off'),'$.target','请选择进阶武器的内置传感器')
            self.sensor_enabled={**self.sensor_enabled,(n,v['target']):v['value']=='active'}
        elif v['kind']=='mode':
            ps.need(type(v['target']) is str and v['target'] in (*self.sensors[n],*b._controllers[n],*self.links[n]) and v['value'] in ('active','off'),'$.target','未知设备或工作状态')
            self.pending_modes[v['ship_id'],v['target']]=v['value']
        elif v['kind']=='lock':
            ps.need(v['value'] is None,'$.value','锁定不接受附加数据')
            if v['target'] is not None:
                t=self.frame.tracks.get((n,v['target']))
                ps.need(t is not None and t.valid,'$.target','目标没有有效观测')
                _,available=b._availability(b.session.world)
                ps.need(bool(self.sources(n,v['target'],b.session.world,available)[0]),'$.target','缺少可用指挥机')
            self.locks[n]=v['target']
        else:ps.need(False,'$.kind','未知火控指令')
        self.last=v;self.sequence=v['sequence'];return True

    def mode_operations(self,existing):
        from .tactical_resources_runtime import ResourceOperation
        world=self.battle.session.world
        sequences={s.ship_id:max([s.resources.sequence]+[o.sequence for o in existing if o.ship_id==s.ship_id]) for s in world.ships}
        result=[]
        for (ship_id,mid),mode in sorted(self.pending_modes.items()):
            sequences[ship_id]+=1
            result.append(ResourceOperation(world.epoch,ship_id,sequences[ship_id],'mode',mid,mode,world.fixed_step,'opening'))
        return tuple(result)

    def view(self):
        b=self.battle;world=b.session.world;_,available=b._availability(world);ships=[]
        for n,s in enumerate(world.ships):
            if b._sides[n]!=b._sides[b._direct_index]:continue
            rows=[]
            for (observer,key),track in self.frame.tracks.items():
                if observer!=n:continue
                sources,channels=self.sources(n,key,world,available)
                rows.append(dict(id=key,kind=track.target.kind,position_m=track.target.position,
                    defense_weapon_ids=[mid for i,mid,_ in track.sources if i==n and (n,mid) in self.integrated
                        and available[n][mid] is None and self.sensor_enabled.get((n,mid),True)] if track.valid else [],
                    velocity_mps=track.target.velocity,height_layer=track.target.layer,valid=track.valid,
                    age_s=max(0,world.fixed_step-track.step)/60,sources=list(sources),
                    status='lost' if not track.valid else 'tracked' if sources else 'no_computer',
                    radar_source_available=bool(self.sources(n,key,world,available,radar_only=True)[0])))
            stats={d['module_id']:d for d in self.frame.devices if d['ship_index']==n}
            devices=[]
            for mid in (*self.sensors[n],*b._controllers[n],*self.links[n]):
                m=b._modules[n][mid];di=b._indices[n][mid]
                devices.append(dict(stats.get(mid,{}),module_id=mid,name=m.prototype.name,
                    kind=m.prototype.category,mode=s.resources.modes[di],pending_mode=self.pending_modes.get((s.ship_id,mid)),
                    reason=available[n][mid] or stats.get(mid,{}).get('reason'),host_id=m.host_instance_id,
                    durability=s.devices.modules[di].durability_points,maximum_durability=m.prototype.durability_points))
            ships.append(dict(ship_id=s.ship_id,locked_target_id=self.locks.get(n),lock_status=self.frame.locks.get(n,{}).get('status'),contacts=rows,devices=devices))
        return dict(command_sequence=self.sequence,ships=ships,sample_interval_s=self.policy['sample_steps']/60,
            memory_s=self.policy['memory_steps']/60)
