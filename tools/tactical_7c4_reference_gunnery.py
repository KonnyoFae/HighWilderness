def view(self):
    from .tactical_stores_view import project as stores_view
    self._guard()
    weapons = []
    inventory_summaries = tuple(inv.summary() for inv in self.inventory.inventories)
    for gun_index, (gun, state) in enumerate(zip(self.guns, self.states)):
        recipe_id = state.reload_recipe_id
        ship = self.session.world.ships[gun.ship_index]
        inv = self.inventory.inventories[gun.ship_index]
        w = next(w for w in inv._value['weapons'] if w['module_id'] == gun.module_id)
        origin = add(tuple(ship.motion.position_world_m.to_list()), rotate(gun.anchor, ship.motion.heading_rad))
        direction = rotate((sin(state.angle+gun.rotation), cos(state.angle+gun.rotation)), ship.motion.heading_rad)
        layer=state.attack_layer or ship.motion.height_layer
        ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
        flight=self._gun_flights[gun_index][w['recipe_id'] or recipe_id]
        projectile=inv._recipes[w['recipe_id'] or recipe_id]['projectile']
        projectile_key=(projectile['id'],projectile['version'])
        weapons.append(dict(ship_id=ship.ship_id, module_id=gun.module_id, mode=state.mode, target_policy=state.target_policy, angle_rad=state.angle,
            point_defense_capable=bool(self.point_defense and self.point_defense.capable[gun_index]),point_defense=state.point_defense,
            interception_target_id=state.interception_target_id,interception_priority=state.interception_priority,
            interception_needed_rounds=state.interception_needed_rounds,
            incendiary_effect='surface' if is_surface_incendiary(projectile_key) else 'internal' if is_incendiary(projectile_key) else None,
            attack_layer=state.attack_layer, effective_layer=layer,
            ballistics=dict(caliber_mm=flight.caliber_mm,speed_mps=flight.muzzle_speed_mps,effective_speed_mps=flight.muzzle_speed_mps*ratio,
                lifetime_s=flight.lifetime_steps/60,reference_range_m=min(gun.maximum_range,ballistics.reference_range(flight,ratio)),
                speed_retention=ratio,drag=flight.drag,cyclic_rpm=3600/max(1,gun.cooldown_steps)),
            origin_m=origin, direction=direction, aim_point_m=state.aim_point,
            target_ship_id=self.session.world.ships[state.target[0]].ship_id if state.target else None,
            target_module_id=state.target[1] if state.target else None, deck_level=state.deck_level,
            aimed_deck_levels=(self.damage.aim_levels(state.target[0], state.target[1])
                if self.damage and state.mode == 'auto' and state.target and state.target[1] else
                (state.deck_level,) if state.mode == 'manual' and state.deck_level is not None else ()),
            quality=state.quality, quality_reason=state.quality_reason,
            lock_sources=state.lock_sources, status=state.status, shots=state.shots, ready_rounds=w['ready_rounds'],
            reload_steps=max(0, inv._due.get(gun.module_id, inv.fixed_step)-inv.fixed_step),
            cooldown_steps=max(0, inv._cooldown[gun.module_id]-inv.fixed_step),
            ammo_resources=sum(m['quantity'] for m in inv._value['magazines']),
            batch_cost=inv._recipes[recipe_id]['ammo_cost'], batch_rounds=inv._recipes[recipe_id]['rounds'],
            selected_recipe_id=recipe_id, loaded_recipe_id=w['recipe_id'] if w['ready_rounds'] else None,
            loading_recipe_id=w['reload']['recipe_id'] if w['reload'] else None,
            recipe_options=[dict(id=key, ammo_cost=inv._recipes[key]['ammo_cost'], rounds=inv._recipes[key]['rounds'],
                cargo_costs=[dict(c) for c in inv._recipes[key]['cargo_costs']]) for key in inv._weapons[gun.module_id]['recipe_ids']],
            cargo=[dict(good_id=c['good_id'], quantity=c['quantity'], reserved=inventory_summaries[gun.ship_index]['reserved_cargo'].get(c['good_id'],0))
                for c in inv._value['cargo']]))
    return dict(interface='gaotian.gunnery-view/p2a-v1alpha1', command_sequence=self.sequence,
        stores=stores_view(self, inventory_summaries),
        deck_hit_policy=asdict(self.damage.deck_policy) if self.damage else None,
        observation=self.observation.view(), missiles=self.missiles.view(), aviation=self.aviation.view(), electronic_warfare=self.ew.view(), groups=ps.clone(list(self.groups)), weapons=weapons, projectiles=[dict(id=p.id, ship_id=p.ship_id, position_m=p.position,
            previous_m=p.previous, velocity_mps=p.velocity, projectile_type=p.projectile_key[0], height_layer=p.height_layer,
            kind='missile' if p.missile else 'shell',
            missile=None if not p.missile else dict(model_id=p.missile.profile.model_id,warhead_id=p.missile.warhead,
                phase=p.missile.phase,seeker_state=p.missile.seeker_state,target_id=p.missile.target_id,
                **missile_speed_view(p),
                **missile_maneuver_view(p),
                datalink=p.missile.profile.datalink,original_target_id=p.missile.original_target,
                interceptor=p.missile.profile.interceptor,interception_damage=p.interception_damage,
                interception_radius_m=p.interception_radius_m,
                link_sender=p.missile.link_sample.sender if p.missile.link_sample else None,
                age_s=p.missile.age/60,remaining_s=max(0,p.expires-self.session.world.fixed_step)/60),
            durability=p.durability,maximum_durability=p.maximum_durability,interception_target_id=p.interception_target_id) for p in self.projectiles],
        policy_id=self.config['id'], damage_enabled=self.damage is not None, ending=self.ending,
        damage=None if self.damage_state is None else dict(hits=self.damage_state.hits, expired=self.damage_state.expired,
            recent=self.damage_state.recent, magazine_explosions=self.magazines.recent,
            magazine_detonations=self.magazines.count),
        **(dict(damage_control=self.fire.view()) if self.fire.enabled else {}),
        **(dict(personnel=self.personnel.view()) if self.personnel else {}),
        **(dict(point_defense=self.point_defense.view()) if self.point_defense else {}),
        **(dict(fireproof=self.ignition.view()) if self.ignition.enabled else {}),
        **(dict(fuel=[dict(ship_id=s.ship_id,total_units=s.motion.fuel_units,tanks=[dict(t,**{k:inv._fuel_tanks[t['tank_id']][k]
            for k in ('module_id','deck_id','deck_level','capacity_units','maximum_points')})
            for t in inv._value['fuel_tanks']]) for s,inv in zip(self.session.world.ships,self.inventory.inventories) if inv._fuel_tanks])
           if any(i._fuel_tanks for i in self.inventory.inventories) else {}))
