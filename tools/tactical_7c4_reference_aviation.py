def view(self):
    b=self.battle;ships=[];own=b._sides[b._direct_index];flights=[];known={}
    for n,inv in enumerate(b.inventory.inventories):
        if 'aviation' not in inv._definition or b._sides[n]!=b._sides[b._direct_index]:continue
        p=inv._definition['aviation']
        if not p['facilities'] and not inv._value['aviation']['manifest']['aircraft']:continue
        ships.append(dict(ship_id=b.session.world.ships[n].ship_id,profile=ps.clone(p),state=ps.clone(inv._value['aviation']),
            module_names={m.id:m.prototype.name for m in inv.pack.seed.resources.modules},
            cargo=ps.clone(inv._value['cargo']),capacity=inv.summary()))
    for key,f in self.flights.items():
        if b._sides[f['owner']]!=own:continue
        a=flight.plane(b.inventory.inventories[f['owner']],key)
        model=aviation_ew.model_for(b.inventory.inventories[f['owner']],key)
        flights.append(dict(id=key,model_id=a['model_id'],home_ship_id=b.session.world.ships[f['owner']].ship_id,
            emissions=f.get('emissions',aviation_ew.defaults(model)),emission_capabilities=aviation_ew.defaults(model),jammer_radius_m=model['jammer_radius_m'],
            group_id=f['group_id'],task=ps.clone(f['task']),position_m=f['position'],velocity_mps=f['velocity'],heading_rad=f['heading'],
            height_layer=f['layer'],layer_goal=f['layer_goal'],layer_progress=f['layer_progress'],status=f['status'],hp=f['hp'],
            target_id=f['target_id'],shots=f.get('shots',0),loadout=ps.clone(a['loadout']),cannon_rounds=a['cannon_rounds'],receiver_ship_id=b.session.world.ships[f['receiver'][0]].ship_id if f['receiver'] else None,
            contacts=[dict(id=t.id,kind=t.kind,position_m=t.position,height_layer=t.layer,channels=list(channels)) for t,channels in f['contacts'].values()]))
        for t,_ in f['contacts'].values():
            if t.kind=='aircraft':known[t.id]=t
    for (n,key),track in b.observation.frame.tracks.items():
        if b._sides[n]==own and track.valid and track.target.kind=='aircraft':known[key]=track.target
    owned={a['id'] for n,inv in enumerate(b.inventory.inventories) if b._sides[n]==own for a in inv._value.get('aviation',{}).get('manifest',{}).get('aircraft',[])}
    return dict(command_sequence=self.sequence,ships=ships,flights=flights,recent=[e for e in self.recent if e['aircraft_id'] in owned],
        jamming_areas=[] if b.ending else [dict(id=a.id,source_id=a.source_id,position_m=a.position,height_layer=a.layer,radius_m=a.radius) for a in aviation_ew.areas(b,b.session.world,b.inventory.inventories,self.flights) if a.side==own],
        command_available=self.command_available(b.session.world,own),
        contacts=[] if b.ending else [dict(id=t.id,position_m=t.position,heading_rad=t.heading,height_layer=t.layer) for t in known.values()])
