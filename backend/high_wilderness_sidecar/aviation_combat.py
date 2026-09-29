"""Aircraft weapons and ephemeral collision adapters, never duplicate airframe ownership."""
from copy import deepcopy
from dataclasses import replace
from math import atan2, cos, sin, hypot, pi, ceil
from . import aviation_flight as flight, aviation_weapons as weapons, aviation_logistics as logistics
from . import aviation_catalog as catalog, persistent_ship as ps
from . import projectile_observation as observed


def config(inv):return inv._definition.get('aviation',{}).get('combat',weapons.policy())


def bodies(b,world,inventories,flights):
    from .tactical_gunnery import Projectile
    return tuple(Projectile(key,world.ships[f['owner']].ship_id,key,tuple(f['position']),tuple(f['position']),tuple(f['velocity']),
        world.fixed_step+3600,None,f['layer'],durability=f['hp'],maximum_durability=f['hp'],
        collision_radius_m=config(inventories[f['owner']])['aircraft_radius_m'][flight.plane(inventories[f['owner']],key)['model_id']],aircraft_body=True)
        for key,f in flights.items() if f['hp']>0)


def apply_hits(flights,events):
    result=deepcopy(flights)
    for row in events:
        if row['projectile_id'] in result:result[row['projectile_id']]['hp']=row['durability_after']
    return result


def environment(b,world,base,flights,inventories):
    from .missile_guidance import Contact
    from . import aviation_ew
    actors=bodies(b,world,inventories,flights)
    contacts=list(base.contacts)
    for body in actors:
        f=flights[body.id];inv=inventories[f['owner']];model=catalog.model(inv._definition['aviation']['catalog'],flight.plane(inv,body.id)['model_id'])
        active=aviation_ew.working(f,model)
        contacts.append(Contact(body.id,b._sides[f['owner']],body.position,body.velocity,body.height_layer,large=False,
            emitting=any(active.values()),jamming=active['jammer'],kind='aircraft',durability=body.durability,payload=observed.sample(body)))
    # Aircraft reports reach missile links only through the receiving ship's live datalink.
    return replace(base,contacts=tuple(contacts))


def known(b,world,available,f,frame=None):
    result={key:t for key,(t,_) in f['contacts'].items()}
    for (n,key),track in (frame or b.observation.frame).tracks.items():
        if (track.valid and b._sides[n]==b._sides[f['owner']] and flight.active(world.ships[n])
            and any(available[n][mid] is None for mid in b.observation.links[n])
            and hypot(*(a-c for a,c in zip(world.ships[n].motion.position_world_m.to_list(),f['position'])))<=b.observation.policy['datalink_range_m']):result.setdefault(key,track.target)
    return result


def aim_for(f,target,speed):
    from .tactical_gunnery import intercept
    return intercept(f['position'],(0.,0.),target.position,target.velocity,max(1.,speed)) or target.position


def approach(f,target,inv):
    """Use only the selected legal sample, including lead for unpowered releases."""
    a=flight.plane(inv,f['id'])
    if not a['loadout'] and not a['cannon_rounds']:return flight.orbit(f,target.position)
    return aim_for(f,target,f['speed'])


def plan(b,world,inventories,flights,projectiles,sequence,env,frame=None):
    from .tactical_gunnery import Projectile,noise,wrap,intercept
    from .tactical_ballistics import FlightProfile
    from .missile_flight import Flight,prepare
    from .tactical_missile_defense import reservation
    flights=deepcopy(flights);events=[];_,available=b._availability(world)
    for key,f in sorted(flights.items()):
        if f['hp']<=0 or f['status']=='recovering':continue
        if b._sides[f['owner']]!=b._sides[b._direct_index] and not b.enemy_fire:continue
        inv=inventories[f['owner']];a=flight.plane(inv,key);cfg=config(inv);model=catalog.model(inv._definition['aviation']['catalog'],a['model_id'])
        tracks=known(b,world,available,f,frame);same=[t for t in tracks.values() if t.layer==f['layer']]
        cooldown=f.setdefault('weapon_cooldowns',{});f.setdefault('shots',0);f.setdefault('rng',sum(key.encode('utf-8')))
        if len(projectiles)>=b.config['max_projectiles']:continue
        point=tuple(f['position']);velocity=tuple(f['velocity']);heading=atan2(velocity[1],velocity[0])
        candidates=[]
        for mount,payload in a['loadout'].items():
            spec=cfg['payloads'][payload]
            if world.fixed_step<cooldown.get('payload',0):break
            for target in same:
                if weapons.category(target.kind) not in spec['target_kinds']:continue
                defense=payload=='self_defense'
                if not defense and (f['return_requested'] or f['task']['layer']!=f['layer'] or
                    f['task']['kind']!='sea_patrol' and target.kind=='ship' or f['task']['kind']!='air_patrol' and target.kind!='ship'):continue
                distance=hypot(*(a-c for a,c in zip(target.position,point)))
                if distance>spec['range_m'] or distance<50:continue
                aim=aim_for(f,target,f['speed'] if spec['motion']!='missile' else spec['speed_mps'])
                angle=atan2(aim[1]-point[1],aim[0]-point[0]);error=abs(wrap(angle-heading))
                cone=pi if defense else pi/3 if spec['motion']=='missile' else .07 if spec['motion']=='guided_bomb' else .025
                if error>cone:continue
                # Existing in-flight interceptors reserve actual remaining damage, not a promised kill.
                reserved=sum(p.interception_damage for p in projectiles if p.interception_target_id==target.id and p.interception_expected_step is not None and world.fixed_step<=p.interception_expected_step)
                if target.durability is not None and reserved>=target.durability:continue
                candidates.append((not defense,distance,str(target.id),mount,payload,target,aim,angle))
        if candidates:
            _,_,_,mount,payload,target,aim,angle=min(candidates,key=lambda row:row[:5]);spec=cfg['payloads'][payload];profile=weapons.profile(payload,cfg)
            powered=spec['motion']=='missile'
            v=tuple(velocity[i]+d*spec['launch_speed_mps'] for i,d in enumerate((cos(angle),sin(angle)))) if powered else velocity
            angular=atan2(v[1],v[0]);sequence+=1
            mf=Flight(profile,'blast',world.fixed_step,angular,tuple(target.position),tuple(target.velocity),target.id)
            p=Projectile(sequence,world.ships[f['owner']].ship_id,key,point,point,v,world.fixed_step+profile.lifetime(),None,f['layer'],
                (profile.model_id,cfg['version']),replace(profile.ballistics(1.),muzzle_speed_mps=hypot(*v),maximum_range_m=spec['range_m']),aimed_ship_id=target.id if target.kind=='ship' else None,
                durability=profile.durability,maximum_durability=profile.durability,collision_radius_m=profile.diameter_mm/2000,missile=mf,
                interception_damage=profile.interception_damage,aircraft_damage=profile.interception_damage,
                interception_radius_m=profile.interception_radius_m,interception_target_id=target.id if profile.interceptor else None,
                interception_expected_step=world.fixed_step+profile.lifetime() if profile.interceptor else None,source_aircraft_id=key)
            if spec['motion']=='bomb':p=replace(p,missile=replace(mf,seeker_state='unguided'))
            else:p=reservation(prepare(p,world,b._sides,env),world.fixed_step)
            projectiles.append(p);w=logistics.Work(inv);del w.plane(key)['loadout'][mount];w.commit()
            cooldown['payload']=world.fixed_step+spec['interval_steps'];f['shots']+=1
            if not powered:f['egress_until']=world.fixed_step+cfg['egress_steps'];f['egress_point']=tuple(point[i]+v[i]*5 for i in range(2))
            events.append(dict(step=world.fixed_step,kind='fired',aircraft_id=key,weapon=payload,target_id=target.id,projectile_id=sequence))
        if model['cannon_id'] and a['cannon_rounds'] and not f['return_requested'] and world.fixed_step>=cooldown.get('cannon',0) and len(projectiles)<b.config['max_projectiles']:
            spec=cfg['cannons'][model['cannon_id']]
            kinds=('ship',) if f['task']['kind']=='sea_patrol' else ('aircraft','projectile') if f['task']['kind']=='air_patrol' else ()
            for target in sorted(same,key=lambda t:(t.id!=f['target_id'],hypot(*(a-c for a,c in zip(t.position,point))),str(t.id))):
                if weapons.category(target.kind) not in kinds or f['layer']!=f['task']['layer']:continue
                if hypot(*(a-c for a,c in zip(target.position,point)))>spec['range_m']:continue
                aim=intercept(point,velocity,target.position,target.velocity,spec['speed_mps'])
                if aim is None:continue
                angle=atan2(aim[1]-point[1],aim[0]-point[0])
                if abs(wrap(angle-heading))>.12:continue
                f['rng'],error=noise(f['rng']);angle+=error*spec['spread_mrad']/1000/f['modifiers']['cannon_accuracy']
                profile=FlightProfile(spec['caliber_mm'],spec['mass_kg'],spec['speed_mps'],1.,360,spec['range_m'])
                v=tuple(velocity[i]+d*spec['speed_mps'] for i,d in enumerate((cos(angle),sin(angle))));sequence+=1
                projectiles.append(Projectile(sequence,world.ships[f['owner']].ship_id,key,point,point,v,world.fixed_step+360,None,f['layer'],
                    ('gtw.aviation.weapon.'+model['cannon_id'],cfg['version']),profile,
                    aimed_ship_id=target.id if target.kind=='ship' else None,interception_damage=spec['air_damage'],aircraft_damage=spec['air_damage'],
                    collision_radius_m=spec['caliber_mm']/2000,source_aircraft_id=key,
                    interception_target_id=target.id if target.kind!='ship' else None,interception_expected_step=world.fixed_step+360 if target.kind!='ship' else None))
                w=logistics.Work(inv);w.plane(key)['cannon_rounds']-=1;w.commit();f['shots']+=1;cooldown['cannon']=world.fixed_step+spec['interval_steps']
                events.append(dict(step=world.fixed_step,kind='fired',aircraft_id=key,weapon=model['cannon_id'],target_id=target.id,projectile_id=sequence));break
    return flights,sequence,tuple(events)
