"""AV2 fixed-step flight. Plans are private until the whole battle tick commits."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from math import atan2, hypot, sin, cos, pi
import json
from . import aviation_catalog as ac, aviation_logistics as al, aviation_recovery as recovery
from . import aviation_manifest as am, persistent_ship as ps
from .tactical_layers import LAYERS
from .tactical_observation import Target, Track, can_observe, allocate
from .missile_guidance import blocked_channels


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-aviation-flight.av2.json').read_text(encoding='utf-8'))


def modifiers(crew):
    return {key:(min if key in ('radar_signature','infrared_signature') else max)(p['modifiers'].get(key,1.) for p in crew) for key in am.MODIFIERS}


def origin(b,world,n,mid):
    from .tactical_gunnery import add,rotate
    m=b._modules[n][mid];motion=world.ships[n].motion
    return add(tuple(motion.position_world_m.to_list()),rotate(m.anchor_m,motion.heading_rad)), m.rotation_deg*pi/180-motion.heading_rad


def plane(inv,key):
    return next(a for a in inv._value['aviation']['manifest']['aircraft'] if a['id']==key)


def active(ship):
    return ship.wreck is None and ship.command.lifecycle.physical_status=='operational'


def launch(b,world,inventories,flights,n,keys,group):
    _,available=b._availability(world);w=al.Work(inventories[n])
    for key in keys:
        a=w.plane(key);mid=a['module_id']
        ps.need(a['location']=='catapult' and not any(j['aircraft_id']==key for j in w.s['jobs']), '$.aircraft_ids', '飞机尚未在弹射器完成装载')
        ps.need(available[n][mid] is None and b.crew_efficiency(world,n,mid,'aviation.launch')>0, '$.catapult', '弹射器当前无法工作')
        model=ac.model(w.p['catalog'],a['model_id']);spec=w.specs[mid]
        at,heading=origin(b,world,n,mid)
        task=deepcopy(w.s.get('departure_tasks',{}).get(key,dict(kind='observe',layer=world.ships[n].motion.height_layer,point_m=[at[0]+sin(heading)*1500,at[1]+cos(heading)*1500])))
        speed=spec['launch_speed_mps'];mods=modifiers(a['crew'])
        flights[key]=dict(id=key,owner=n,group_id=group,task=task,position=at,heading=heading,speed=speed,
            velocity=(sin(heading)*speed,cos(heading)*speed),layer=world.ships[n].motion.height_layer,
            layer_progress=0,layer_goal=None,status='patrolling',target_id=None,contacts={},sample_step=-100000,
            hp=model['durability_points'],modifiers=mods,had_payload=bool(a['loadout']),return_requested=False,receiver=None,recovery_progress=0,step=world.fixed_step)
        a.update(location='airborne',ship_id=None,module_id=None)
        w.s['hangar_assignments'].pop(key)
    w.commit()


def targets(flights,inventories,sides):
    from .projectile_observation import Sample,altitude
    rows=[]
    for key,f in flights.items():
        if f['hp']<=0:continue
        a=plane(inventories[f['owner']],key);model=ac.model(inventories[f['owner']]._definition['aviation']['catalog'],a['model_id'])
        rows.append(Target(key,'aircraft',sides[f['owner']],tuple(f['position']),tuple(f['velocity']),f['layer'],
            large=False,powered=True,heading=f['heading'],durability=f['hp'],
            payload=Sample(key,tuple(f['position']),tuple(f['velocity']),f['layer'],f.get('step',0)+3600,None,f['hp'],altitude(f['layer'])),
            radar_signature=model['radar_signature_m2']*f['modifiers']['radar_signature'],
            infrared_signature=model['infrared_signature']*f['modifiers']['infrared_signature']))
    return tuple(rows)


def sample(b,world,f,model,all_targets,effects):
    if world.fixed_step-f['sample_step']<policy()['sample_steps']:return
    sensors=[]
    for channel in ('radar','infrared'):
        r=model[channel+'_range_m']*f['modifiers'][channel+'_detection']
        sensors.append(dict(channel=channel,range_m=r,ship_range_m=r,coasting_range_m=r,
            range_efficiency=1.,weather=policy()['weather_'+channel]))
    seen={};sources={}
    for t in all_targets:
        if t.side==b._sides[f['owner']]:continue
        blocked=blocked_channels(effects,f['position'],f['layer'],t.position,t.layer)
        for spec in sensors:
            channel=spec['channel']
            if can_observe(spec,f['position'],f['layer'],t,('chaff' if channel=='radar' else 'thermal') in blocked,
                    ship_radar_factor=b.observation.radar_factor(t,f['position']) if t.kind=='ship' and channel=='radar' else 1.):
                seen[t.id]=t;sources.setdefault(t.id,[]).append(channel)
    priorities={t.id:(0 if t.id==f['target_id'] else 1,hypot(t.position[0]-f['position'][0],t.position[1]-f['position'][1])) for t in seen.values()}
    ids,_=allocate(list(seen.values()),tuple(f['contacts']),policy()['tracking_capacity'],priorities)
    f['contacts']={key:(seen[key],tuple(sources[key])) for key in ids};f['sample_step']=world.fixed_step


def candidates(b,world,inventories,f,key,*,ending=False):
    source=f['owner'];a=plane(inventories[source],key)
    home=next((n for n,i in enumerate(inventories) if i._value['instance_id']==a['home_ship_id']),source)
    order=sorted(range(len(inventories)),key=lambda n:(n!=home,hypot(*(x-y for x,y in zip(world.ships[n].motion.position_world_m.to_list(),f['position']))),n))
    for n in order:
        ship=world.ships[n];inv=inventories[n]
        if b._sides[n]!=b._sides[source] or ship.wreck is not None or ship.command.lifecycle.physical_status not in (('operational','exited') if ending else ('operational',)):continue
        if recovery.receiving(inventories[source],inv,key) is not None:yield n


def set_location(inventories,f,key,location):
    inv=inventories[f['owner']]
    if plane(inv,key)['location']==location:return
    w=al.Work(inv);w.plane(key)['location']=location;w.commit()


def move(f,aim,model):
    # The incoming interval uses the last committed velocity, shared with swept collision.
    f['position']=tuple(p+v/60 for p,v in zip(f['position'],f['velocity']))
    delta=(aim[0]-f['position'][0],aim[1]-f['position'][1])
    angle=atan2(*delta);error=(angle-f['heading']+pi)%(2*pi)-pi
    turn=model['turn_rate_deg_s']*pi/180*f['modifiers']['agility']/60
    f['heading']=(f['heading']+max(-turn,min(turn,error)))%(2*pi)
    maximum=model['speed_mps']*f['modifiers']['speed']
    if f['status']=='returning':maximum=min(maximum,max(15.,hypot(*delta)*.25))
    acceleration=policy()['acceleration_mps2']/60
    f['speed']+=max(-acceleration,min(acceleration,maximum-f['speed']))
    f['velocity']=(sin(f['heading'])*f['speed'],cos(f['heading'])*f['speed'])


def change_layer(f,layer,model):
    if layer!=f['layer_goal']:f['layer_progress']=0;f['layer_goal']=layer
    if layer==f['layer']:f['layer_progress']=0;return
    f['layer_progress']+=1
    if f['layer_progress']>=model['layer_change_steps']:
        i=LAYERS.index(f['layer']);j=LAYERS.index(layer)
        f['layer']=LAYERS[i+(1 if j>i else -1)];f['layer_progress']=0


def orbit(f,point):
    dx,dy=f['position'][0]-point[0],f['position'][1]-point[1]
    if hypot(dx,dy)>policy()['patrol_radius_m']*1.5:return point
    angle=atan2(dx,dy)+pi/3;r=policy()['patrol_radius_m']
    return (point[0]+sin(angle)*r,point[1]+cos(angle)*r)


def advance(b,world,inventories,flights,projectiles,effects):
    if not flights:return {}
    flights=deepcopy(flights);all_targets=b.observation.targets(world,projectiles)+targets(flights,inventories,b._sides)
    _,available=b._availability(world);busy=set()
    for key,f in sorted(list(flights.items()),key=lambda row:(row[1]['recovery_progress']==0,row[0])):
        f['step']=world.fixed_step
        inv=inventories[f['owner']];a=plane(inv,key);model=ac.model(inv._definition['aviation']['catalog'],a['model_id'])
        if f['hp']<=0:
            recovery.salvage(inv,key,destroyed=True);del flights[key];continue
        if f['hp']<model['durability_points'] or a['condition']=='damaged':
            if a['condition']!='damaged':
                w=al.Work(inv);w.plane(key)['condition']='damaged';w.commit()
            f['return_requested']=True
        if (not a['loadout'] and (model['required_payload'] or model['hardpoints'] and f.get('had_payload'))) or not active(world.ships[f['owner']]):f['return_requested']=True
        if model['cannon_id'] and not a['cannon_rounds'] and not a['loadout']:f['return_requested']=True
        sample(b,world,f,model,all_targets,effects)
        if f['return_requested']:
            f['target_id']=None
            eligible=list(candidates(b,world,inventories,f,key))
            if not eligible:
                recovery.salvage(inv,key);del flights[key];continue
            selected=None
            for n in eligible:
                for spec in inventories[n]._definition['aviation']['facilities']:
                    mid=spec['module_id']
                    if spec['kind']=='aircraft_arrester' and spec['maximum_berth_slots']>=model['berth_slots'] and available[n][mid] is None and (n,mid) not in busy:
                        selected=(n,mid,spec);break
                if selected:break
            if selected:
                n,mid,spec=selected;at,_=origin(b,world,n,mid);layer=world.ships[n].motion.height_layer
                identity=(n,mid)
                if f['receiver']!=identity:f['recovery_progress']=0
                f['receiver']=identity;f['status']='returning';set_location(inventories,f,key,'returning')
                change_layer(f,layer,model)
                # Capture the short moving deck window once; the cable owns the aircraft until unloading completes.
                if f['layer']==layer and (f['recovery_progress']>0 or hypot(*(x-y for x,y in zip(at,f['position'])))<=spec['capture_radius_m']+f['speed']/60):
                    busy.add(identity);f['recovery_progress']+=1;f['position']=at;f['velocity']=(0.,0.);f['status']='recovering'
                    if f['recovery_progress']>=spec['recovery_steps'] and recovery.recover(inventories,f['owner'],n,key):del flights[key]
                    continue
                aim=at
            else:
                f.update(status='waiting_recovery',receiver=None,recovery_progress=0)
                set_location(inventories,f,key,'waiting_recovery')
                n=eligible[0];change_layer(f,world.ships[n].motion.height_layer,model)
                aim=orbit(f,world.ships[n].motion.position_world_m.to_list())
        else:
            f['status']='patrolling';set_location(inventories,f,key,'airborne');f['receiver']=None
            task=f['task'];change_layer(f,task['layer'],model)
            contacts={k:t for k,(t,_) in f['contacts'].items()}
            # Fleet tracks remain legal samples. A tower alone never grants intelligence.
            linked=[n for n in range(len(world.ships)) if active(world.ships[n]) and b._sides[n]==b._sides[f['owner']] and
                   any(available[n][mid] is None for mid in b.observation.links[n]) and
                   hypot(*(x-y for x,y in zip(world.ships[n].motion.position_world_m.to_list(),f['position'])))<=b.observation.policy['datalink_range_m']]
            if linked:
                for (n,k),track in b.observation.frame.tracks.items():
                    if track.valid and n in linked:contacts.setdefault(k,track.target)
            kinds=('ship',) if task['kind']=='sea_patrol' else ('aircraft','missile') if task['kind']=='air_patrol' else ()
            valid=[t for t in contacts.values() if t.kind in kinds and t.layer==task['layer']==f['layer'] and hypot(*(x-y for x,y in zip(t.position,task['point_m'])))<=policy()['maximum_pursuit_m']]
            target=min(valid,key=lambda t:(t.id!=f['target_id'],hypot(*(x-y for x,y in zip(t.position,f['position']))),str(t.id)),default=None)
            f['target_id']=target.id if target else None
            if target:f['status']='pursuing'
            from .aviation_combat import approach
            aim=approach(f,target,inv) if target else orbit(f,task['point_m'])
            if world.fixed_step<f.get('egress_until',0):aim=f['egress_point']
        move(f,aim,model)
    return flights


def finish(b,world,inventories,flights,ending=False):
    """Resolve 6c ship outcomes first; aircraft and crew never enter that lottery."""
    flights=deepcopy(flights)
    for key,f in list(flights.items()):
        departed=world.ships[f['owner']].command.lifecycle.physical_status=='exited'
        if not ending and not departed:continue
        if not any(recovery.recover(inventories,f['owner'],n,key) for n in candidates(b,world,inventories,f,key,ending=True)):
            recovery.salvage(inventories[f['owner']],key)
        del flights[key]
    return flights


def share(b,world,available,frame,flights):
    """Airborne reports need a live receiving datalink; never act as onboard illumination."""
    from dataclasses import replace
    tracks=dict(frame.tracks)
    for f in flights.values():
        for n,s in enumerate(world.ships):
            if not active(s) or b._sides[n]!=b._sides[f['owner']]:continue
            mid=next((mid for mid in b.observation.links[n] if available[n][mid] is None),None)
            if mid is None or hypot(*(x-y for x,y in zip(s.motion.position_world_m.to_list(),f['position'])))>b.observation.policy['datalink_range_m']:continue
            for key,(target,channels) in f['contacts'].items():
                old=tracks.get((n,key));source=tuple((n,mid,'aircraft_'+c) for c in channels)
                if old and old.valid:
                    if old.step>f['sample_step']:continue
                    source=tuple(sorted(set(old.sources+source)))
                tracks[n,key]=Track(target,f['sample_step'],source)
    return replace(frame,tracks=tracks)
