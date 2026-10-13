def view(self):
    b=self.battle;world=b.session.world;_,available=b._availability(world);ships=[];inputs=_Inputs(self,world,available)
    for n,s in enumerate(world.ships):
        if b._sides[n]!=b._sides[b._direct_index]:continue
        rows=[]
        for (observer,key),track in self.frame.tracks.items():
            if observer!=n:continue
            sources,channels=self.sources(n,key,world,available,inputs=inputs)
            rows.append(dict(id=key,kind=track.target.kind,position_m=track.target.position,
                defense_weapon_ids=[mid for i,mid,_ in track.sources if i==n and (n,mid) in self.integrated
                    and available[n][mid] is None and self.sensor_enabled.get((n,mid),True)] if track.valid else [],
                velocity_mps=track.target.velocity,height_layer=track.target.layer,valid=track.valid,
                heading_rad=track.target.heading, yaw_rate_radps=track.target.yaw,
                altitude_m=track.target.payload.altitude_m if track.target.payload else None,
                vertical_speed_mps=track.target.payload.vertical_velocity_mps if track.target.payload else 0.,
                age_s=max(0,world.fixed_step-track.step)/60,sources=list(sources),
                status='lost' if not track.valid else 'tracked' if sources else 'no_computer',
                radar_source_available=bool(self.sources(n,key,world,available,radar_only=True,inputs=inputs)[0])))
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
