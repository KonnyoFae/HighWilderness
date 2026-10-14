# Frozen ad3870c DamageKernel.advance; exact original method for P1c comparison.
def advance(self, before, world, projectiles, state, decoys=(), aircraft=()):
    if not projectiles:
        return (), replace(state,fuel_damage=(),ignition_attempts=(),expired_flights=(),module_impacts=(),interceptions=()) if state.fuel_damage or state.ignition_attempts or state.expired_flights or state.module_impacts or state.interceptions else state, ImpactBatch()
    armor = list(state.armor)
    survivors, events, damages, hull = [], [], {}, {}
    expired = 0
    tank_damage = {}
    attempts = []
    module_impacts = []
    terminals, pending = [], []
    for p in sorted(projectiles, key=lambda p: p.id):
        if world.fixed_step > p.expires:
            expired += 1
            terminals.append(dict(projectile_id=p.id, position_m=p.position, impact_fraction=0.))
            continue
        flight = flight_segment(p)
        end, _ = flight.at(1)
        hits = []
        if p.missile:
            from .tactical_interception import contact_fraction
            from .tactical_gunnery import Projectile
            for decoy in decoys:
                if decoy.side==self.sides.get(p.ship_id) or world.fixed_step>=decoy.expires:continue
                body=Projectile(-1,decoy.ship_id,'decoy',decoy.position,decoy.position,decoy.velocity,decoy.expires,
                                None,decoy.layer,collision_radius_m=1.)
                t=contact_fraction(p,body)
                if t is not None:hits.append((t,-1,2,decoy.id))
        selections = {choice.ship_id: choice for choice in p.deck_selections}
        for i, (old, ship) in enumerate(zip(before.ships, world.ships)):
            boundary=ship_layer_boundary(old.motion,ship.motion,flight.seconds)
            def same_layer(t):
                return p.height_layer is None or layer_at(p,flight,t)==ship_layer_at(old.motion,ship.motion,t,flight.seconds)
            breaks=sorted({0.,1.,boundary,*layer_breaks(flight)})
            if not any(same_layer((a+b)/2) for a,b in zip(breaks,breaks[1:])) and not same_layer(1.):
                continue
            if ship.ship_id == p.ship_id or ship.command.lifecycle.physical_status == 'exited' or ship.motion.hull_integrity_fraction <= 0:
                continue
            if p.ship_id in self.sides and self.sides[p.ship_id] == self.sides[ship.ship_id]:
                continue
            radius = self.radius[i]+flight.curvature*flight.seconds**2/8
            lo = tuple(min(a, b)-radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
            hi = tuple(max(a, b)+radius for a, b in zip(old.motion.position_world_m.to_list(), ship.motion.position_world_m.to_list()))
            if _segment_aabb_entry_fraction(p.position, end, lo, hi) is None:
                continue
            path = local_path(flight, old.motion, ship.motion,breaks)
            choice = selections.get(ship.ship_id)
            level = choice.level if choice else p.deck_level
            contacts={}
            for a,b in zip(path,path[1:]):
                if not same_layer((a[0]+b[0])/2) and not same_layer(b[0]):continue
                for deck,hit in self.contacts_on_path(i,ship,(a,b),level,same_layer).items():
                    if deck not in contacts or hit<contacts[deck]:contacts[deck]=hit
            if world.fixed_step == p.expires:
                contacts = {deck: hit for deck, hit in contacts.items() if hit[0] < 1-1e-10}
            if not contacts:
                continue
            if level is None:
                choice = self.choose_deck(i, ship, old, flight, min(contacts.values()), p)
                selections[ship.ship_id] = choice
                p = replace(p, deck_selections=tuple(selections[k] for k in sorted(selections)))
                level = choice.level
            if level in contacts:
                hits.append(contacts[level])
        # TTL is a deadline: the final interval exists, but its endpoint is
        # already expired. A collision strictly before it can still resolve.
        if world.fixed_step == p.expires:
            hits = [hit for hit in hits if hit[0] < 1-1e-10]
        if not hits:
            if world.fixed_step == p.expires:
                expired += 1
                terminals.append(dict(projectile_id=p.id, position_m=end, impact_fraction=1.))
            else:
                survivors.append(advance_projectile(p,flight))
            continue
        pending.append((min(hits),p,flight))
    from .tactical_interception import resolve as intercept_projectiles
    collision_rounds=tuple(replace(p,aircraft_damage=p.aircraft_damage or p.interception_damage or self.profiles[p.projectile_key].damage.surface_module_damage_points)
        if aircraft and p.projectile_key in self.profiles else p for p in projectiles)
    updated,removed,interceptions = intercept_projectiles((*collision_rounds,*aircraft),self.sides,
        {p.id:hit[0] for hit,p,_ in pending},world.fixed_step)
    survivors = [replace(p,durability=updated[p.id].durability) for p in survivors if p.id not in removed]
    terminals = [row for row in terminals if row['projectile_id'] not in removed]
    expired -= sum(p.id in removed and p.expires==world.fixed_step for p in projectiles
                   if not any(item[1].id==p.id for item in pending))
    for (t,i,kind,n),p,flight in sorted(pending,key=lambda item:(item[0][0],item[1].id)):
        if p.id in removed:continue
        if kind==2:
            terminals.append(dict(projectile_id=p.id,position_m=flight.at(t)[0],impact_fraction=t,decoy_id=n))
            continue
        profile = self.profiles[p.projectile_key]
        incendiary=is_incendiary(p.projectile_key) or bool(p.missile and p.missile.warhead=='incendiary')
        surface_incendiary=is_surface_incendiary(p.projectile_key) or bool(p.missile and p.missile.warhead=='incendiary')
        fire_scale=((4 if p.missile.profile.diameter_mm>=100 else 2)*p.missile.profile.warhead_scale) if p.missile else 1
        ship, old = world.ships[i], before.ships[i]
        level = self.edges[i][n].key[1] if kind == 0 else self.cells[i][n].level
        selection = next((choice for choice in p.deck_selections if choice.ship_id == ship.ship_id), None)
        position, impact_velocity = flight.at(t)
        center, heading = pose_at(old.motion, ship.motion, t)
        point = rotate((position[0]-center[0],position[1]-center[1]),-heading)
        target_velocity = lerp(old.motion.velocity_world_mps.to_list(),ship.motion.velocity_world_mps.to_list(),t)
        yaw = old.motion.yaw_rate_radps+(ship.motion.yaw_rate_radps-old.motion.yaw_rate_radps)*t
        velocity = relative_impact_velocity_xy(impact_velocity, target_velocity,
            yaw, (position[0]-center[0], position[1]-center[1]))
        relative = rotate(velocity, -heading)
        horizontal_speed = hypot(*relative)
        vertical_speed = flight.vertical_speed(t) if hasattr(flight,'vertical_speed') else 0.
        speed = hypot(horizontal_speed,vertical_speed)
        direction = (relative[0]/horizontal_speed, relative[1]/horizontal_speed) if horizontal_speed > 1e-9 else (0., 0.)
        damage = profile.damage
        ids, amount, outcome, energy = [], damage.surface_module_damage_points, 'module', 0.
        armor_before = armor_after = None
        armor_profile = None
        fuel_ids=[]
        internal_ids=set()
        internal_points={}
        if kind == 1:
            ids = [self.cells[i][n].module_id]
        else:
            edge = self.edges[i][n]
            armor_before = armor[i][n]
            result = resolve_armor_impact(profile.penetration,
                ArmorState(edge.protection, edge.thickness_mm, armor_before), speed,
                impact_incidence(relative,edge,vertical_speed),
                ricochet_roll=((p.id*2654435761+world.fixed_step*12345) & 0xffffffff)/0xffffffff)
            outcome, energy = result.outcome.value, result.residual_energy_ratio
            if edge.flare_angle_deg:
                armor_profile = dict(deck_id=edge.key[0], region_id=edge.key[2], edge_index=edge.key[3],
                    thickness_mm=edge.thickness_mm, flare_angle_deg=edge.flare_angle_deg,
                    tilt_cosine=edge.tilt_cosine, impact_angle_deg=result.impact_angle_deg,
                    effective_angle_deg=result.effective_angle_deg,
                    required_penetration_mm=result.required_penetration_mm,
                    available_penetration_mm=result.available_penetration_mm, active=armor_before > 0)
            armor_after = max(0., armor_before-result.armor_damage_formula_points*damage.armor_damage_to_local_durability_proxy)
            if armor_after != armor_before:
                values = list(armor[i]); values[n] = armor_after; armor[i] = tuple(values)
            if result.outcome == ImpactOutcome.PENETRATED:
                ray_end = point[0]+direction[0]*damage.internal_effect_range_m, point[1]+direction[1]*damage.internal_effect_range_m
                crossed = []
                for cell in self.cells[i]:
                    if cell.level != level or ship.devices.modules[self.indices[i][cell.module_id]].durability_points <= 0:
                        continue
                    entry = _segment_aabb_entry_fraction(point, ray_end, *cell.bounds)
                    if entry is not None:
                        crossed.append((entry, cell.module_id, cell.center, cell.exposed))
                crossed.sort()
                if crossed:
                    first = crossed[0][2]
                    ids = [k for _, k, c, exposed in crossed if profile.penetration.aftereffect == Aftereffect.KINETIC_RAY
                        or hypot(c[0]-first[0], c[1]-first[1]) <= damage.internal_effect_radius_m]
                    if p.missile and p.missile.warhead=='blast':
                        ids=[c.module_id for c in self.cells[i] if c.level==level and hypot(c.center[0]-first[0],c.center[1]-first[1])<=damage.internal_effect_radius_m]
                if incendiary:
                    internal_ids={k for _,k,_,exposed in crossed if not exposed} & set(ids)
                    for _,key,center,exposed in crossed:
                        if not exposed and key in internal_ids:
                            internal_points.setdefault(key,center)
                amount = damage.internal_module_damage_points*energy
                from .tactical_fuel import crosses_polygon
                fuel_ids=[key for key,tank_level,polys in self.fuel_areas[i] if tank_level==level
                          and any(crosses_polygon(point,ray_end,poly) for poly in polys)]
                for key in fuel_ids:
                    tank_damage[i,key]=tank_damage.get((i,key),0)+amount
                hull[i] = min(1., hull.get(i, 0.)+self.profile_hull_factors[p.projectile_key][i]*energy)
            else:
                ids = [c.module_id for c in self.cells[i] if c.exposed and c.level == level
                    and hypot(c.center[0]-point[0], c.center[1]-point[1]) <= damage.surface_effect_radius_m]
        if kind==1 and p.missile and p.missile.warhead=='blast':
            ids.extend(c.module_id for c in self.cells[i] if c.exposed and c.level==level and hypot(c.center[0]-point[0],c.center[1]-point[1])<=damage.surface_effect_radius_m)
        ids = sorted(set(ids))
        for k in ids:
            damages[i, k] = damages.get((i, k), 0.)+amount
            if amount > 0: module_impacts.append((i, k, amount, level))
        if incendiary and energy > 0:
            attempts.extend(Attempt(p.id,p.ship_id,i,k,level,
                internal_points[k] if surface_incendiary else None,intensity_scale=fire_scale) for k in sorted(internal_ids))
        if surface_incendiary:
            attempts.append(Attempt(p.id,p.ship_id,i,self.cells[i][n].module_id if kind==1 else None,level,point,True,fire_scale))
        events.append(dict(projectile_id=p.id, step=world.fixed_step, source_ship_id=p.ship_id, ship_id=ship.ship_id,
            impact_fraction=t, relative_speed_mps=speed, projectile_speed_mps=hypot(*impact_velocity,vertical_speed),
            projectile_type=p.projectile_key[0], projectile_version=p.projectile_key[1],
            position_m=position, deck_level=level, height_layer=layer_at(p,flight,t) or ship_layer_at(old.motion,ship.motion,t,flight.seconds), outcome=outcome, module_ids=ids,
            module_damage=amount if ids else 0., armor_before=armor_before, armor_after=armor_after,
            residual_energy_ratio=energy,
            **(dict(armor_profile=armor_profile) if armor_profile else {}),
            **(dict(deck_selection=dict(policy=self.deck_policy.id, preferred_levels=selection.preferred_levels,
                probabilities=[dict(deck_level=deck, probability=weight/sum(w for _,w in selection.weights))
                    for deck,weight in selection.weights], sample=selection.roll)) if selection else {}),
            **(dict(fuel_tank_ids=fuel_ids) if fuel_ids else {})))
    operations = tuple(DeviceOperation(world.epoch, world.ships[i].ship_id, k,
        world.ships[i].devices.modules[self.indices[i][k]].sequence+1, 'damage', amount, world.fixed_step, 'closing')
        for (i, k), amount in sorted(damages.items()) if amount > 0)
    result = DamageState(tuple(armor), (state.recent+tuple(events))[-32:], state.hits+len(events), state.expired+expired,
                         tuple((i,k,a) for (i,k),a in sorted(tank_damage.items()) if a>0), tuple(attempts), tuple(terminals), tuple(module_impacts),interceptions)
    return tuple(survivors), result, ImpactBatch(operations, tuple((world.ships[i].ship_id, v) for i, v in sorted(hull.items())))
