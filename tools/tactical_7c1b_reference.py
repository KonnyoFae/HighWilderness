# Frozen c0554da methods for test-only execution with production module globals.
# Loaded by profile_tactical_7c1b; production never imports this file.

def visible(self,n,mid,ship,target,effective,occluded):
    from .tactical_gunnery import rotate, add
    arc=self.arcs[n,mid];position=tuple(ship.motion.position_world_m.to_list())
    origin=add(position,rotate(arc['origin_m'],ship.motion.heading_rad))
    factor=self.radar_factor(target,origin) if effective['channel']=='radar' and target.kind=='ship' else 1.
    if not can_observe(effective,origin,ship.motion.height_layer,target,ship_radar_factor=factor):return False
    delta=tuple(a-b for a,b in zip(target.position,origin))
    if hypot(*delta)>1e-9:
        local=rotate(delta,-ship.motion.heading_rad)
        if interval_blocks_bearing(arc['blocked_intervals_deg'],degrees(atan2(local[0],local[1]))):return False
    return not (occluded and occluded(n,mid,target))


def plan(self,world,available,projectiles,*,missiles=(),occluded=None,extra_targets=()):
    b=self.battle;step=world.fixed_step;targets=self.targets(world,projectiles,missiles)+tuple(extra_targets)
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
                heading_rad=track.target.heading, yaw_rate_radps=track.target.yaw,
                altitude_m=track.target.payload.altitude_m if track.target.payload else None,
                vertical_speed_mps=track.target.payload.vertical_velocity_mps if track.target.payload else 0.,
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

def sensor_blocker(self,world,effects):
    b=self.battle
    def occluded(n,mid,t):
        from .tactical_gunnery import rotate,add
        ship=world.ships[n];arc=b.observation.arcs[n,mid]
        origin=add(tuple(ship.motion.position_world_m.to_list()),rotate(arc['origin_m'],ship.motion.heading_rad))
        channel='chaff' if b.observation.sensors[n][mid]['channel']=='radar' else 'thermal'
        return channel in blocked_channels(effects,origin,ship.motion.height_layer,t.position,t.layer,observer_side=b._sides[n])
    return occluded

