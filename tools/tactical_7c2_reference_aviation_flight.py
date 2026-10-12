"""Frozen c0554da functions for 7c.2 differential tooling only."""

def candidates(b,world,inventories,f,key,*,ending=False):
    source=f['owner'];a=plane(inventories[source],key)
    home=next((n for n,i in enumerate(inventories) if i._value['instance_id']==a['home_ship_id']),source)
    order=sorted(range(len(inventories)),key=lambda n:(n!=home,hypot(*(x-y for x,y in zip(world.ships[n].motion.position_world_m.to_list(),f['position']))),n))
    for n in order:
        ship=world.ships[n];inv=inventories[n]
        if b._sides[n]!=b._sides[source] or ship.wreck is not None or ship.command.lifecycle.physical_status not in (('operational','exited') if ending else ('operational',)):continue
        if recovery.receiving(inventories[source],inv,key) is not None:yield n



def finish(b,world,inventories,flights,ending=False):
    """Resolve 6c ship outcomes first; aircraft and crew never enter that lottery."""
    flights=deepcopy(flights)
    for key,f in list(flights.items()):
        departed=world.ships[f['owner']].command.lifecycle.physical_status=='exited'
        if not ending and not departed:continue
        if not any(recovery.recover(inventories,f['owner'],n,key) for n in candidates(b,world,inventories,f,key,ending=True)):
            recovery.salvage(inventories[f['owner']],key,origin=salvage_origin(world,f['position'],f['layer'],'battle_end'))
        del flights[key]
    return flights


