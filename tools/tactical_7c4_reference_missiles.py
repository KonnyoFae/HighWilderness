def view(self):
    from .missile_presentation import profile as present_profile
    b=self.battle;available=b._availability(b.session.world)[1]
    ships=[]
    for i,(ship,inv) in enumerate(zip(b.session.world.ships,b.inventory.inventories)):
        profile=inv._definition.get('missiles')
        if not profile or not(profile['launchers'] or profile['magazines']):continue
        model_ids={r['model_id'] for group in ('launchers','magazines') for r in inv._value['missiles'][group]}
        ships.append(dict(ship_id=ship.ship_id,profile=present_profile(profile,model_ids),state=ps.clone(inv._value['missiles']),
            module_names={s['module_id']:b._modules[i][s['module_id']].prototype.name for group in ('launchers','magazines') for s in profile[group]},
            blocked={s['module_id']:[available[i][s['module_id']]] if available[i][s['module_id']] else []
                     for group in ('launchers','magazines') for s in profile[group]},
            cargo=ps.clone(inv._value['cargo']),over_capacity=inv.summary()['over_capacity'],
            launchers=[dict(module_id=mid,target_id=s.target,point_m=s.point,attack_layer=s.layer or ship.motion.height_layer,
                active_target_id=s.active_target,active_attack_layer=s.active_layer,automatic_layer=s.layer is None,
                interceptor=bool((p:=flight.profiles().get(next(r['model_id'] for r in inv._value['missiles']['launchers'] if r['module_id']==mid))) and p.interceptor),
                target_kinds=list(p.target_kinds or (('aircraft','projectile') if p.interceptor else ('ship',))) if p else [],
                integrated_fire_control=(i,mid) in b.observation.integrated,
                fire_arc=ps.clone(self.fire_arcs[i,mid]),
                angle_rad=s.angle,aim_point_m=s.aim,fire_requested=s.fire_requested,shots=s.shots,status=s.status,
                supported=next(r['model_id'] for r in inv._value['missiles']['launchers'] if r['module_id']==mid) in flight.profiles(),
                maximum_range_m=flight.profiles()[r['model_id']].range(1. if (s.active_layer or s.layer or ship.motion.height_layer)==ship.motion.height_layer else CROSS_LAYER_SPEED)
                    if (r:=next(r for r in inv._value['missiles']['launchers'] if r['module_id']==mid))['model_id'] in flight.profiles() else 0.)
                for (n,mid),s in self.states.items() if n==i]))
    return dict(command_sequence=self.sequence,flight_available=True,supported_model_ids=list(flight.profiles()),ships=ships,
                pending=[dict(projectile_id=d.projectile.id,ship_id=d.projectile.ship_id,weapon_id=d.projectile.weapon_id,
                              remaining_steps=max(0,d.due_step-b.session.world.fixed_step),height_layer=d.projectile.height_layer) for d in self.pending],recent=self.recent)
