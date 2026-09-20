"""Transactional logistics, launcher orders and immutable VLS departures."""
from dataclasses import dataclass, replace
from math import atan2, sin, cos, hypot, pi, ceil
from 高天荒野舰艇水平射界 import horizontal_fire_arc, interval_blocks_bearing
from . import persistent_ship as ps, missile_logistics as logistics
from . import missile_flight as flight
from .tactical_layers import LAYERS
from .tactical_ballistics import CROSS_LAYER_SPEED


@dataclass(frozen=True)
class Launcher:
    target: str | int | None = None
    point: tuple | None = None
    layer: str | None = None
    angle: float = 0.
    fire_requested: bool = False
    shots: int = 0
    status: str = 'no_target'
    aim: tuple | None = None
    active_target: str | int | None = None
    active_layer: str | None = None


@dataclass(frozen=True)
class Departure:
    due_step: int
    projectile: object


def launcher_fire_arc(gun, kind):
    """Static display sectors from the exact geometry used by launch checks.

    Bearings are clockwise from the ship's local bow. Hull tangency blocks;
    traverse limits retain the runtime's inclusive endpoints. VLS bypasses both.
    """
    result=dict(launcher_kind=kind,origin_local_m=list(gun.anchor),rotation_rad=gun.rotation,
                boundary_policy='hull_blocked',sectors=[])
    if kind=='vls':
        result['sectors']=[dict(start_deg=0.,end_deg=360.,kind='clear')]
        return result
    points={0.,360.,*((gun.rotation+a)*180/pi%360 for a in (gun.minimum,gun.maximum))}
    points.update(a for interval in gun.blocked for a in interval)
    ordered=sorted(points)
    for start,end in zip(ordered,ordered[1:]):
        bearing=(start+end)/2
        relative=(bearing*pi/180-gun.rotation+pi)%(2*pi)-pi
        status='out_of_arc' if not gun.minimum<=relative<=gun.maximum else 'hull_blocked' if interval_blocks_bearing(gun.blocked,bearing) else 'clear'
        if result['sectors'] and result['sectors'][-1]['kind']==status:
            result['sectors'][-1]['end_deg']=end
        else:result['sectors'].append(dict(start_deg=start,end_deg=end,kind=status))
    return result


class MissileRuntime:
    def __init__(self,battle,scenario):
        self.battle=battle;self.sequence=0;self.last=None
        self.states={};self.geometry={};self.fire_arcs={};self.pending=();self.recent=();self.retargets={}
        from .tactical_gunnery import Gun,RAD
        for n,inv in enumerate(battle.inventory.inventories):
            for spec in inv._definition.get('missiles',{}).get('launchers',()):
                mid=spec['module_id'];m=battle._modules[n][mid];turret=spec['turret']
                blocked=() if spec['launcher_kind']=='vls' else tuple(tuple(r) for r in
                    horizontal_fire_arc(scenario.bindings[n].snapshot.hull,m.anchor_m,m.base_deck_level)['blocked_intervals_deg'])
                self.geometry[n,mid]=Gun(n,mid,m.anchor_m,m.rotation_deg*pi/180,turret['minimum_mdeg']*RAD,
                    turret['maximum_mdeg']*RAD,turret['slew_mdeg_per_s']*RAD/60,
                    blocked,0.,50000.,spec['shot_interval_steps'])
                self.fire_arcs[n,mid]=launcher_fire_arc(self.geometry[n,mid],spec['launcher_kind'])
                self.states[n,mid]=Launcher()
            for model in inv._definition.get('missiles',{}).get('models',()):
                p=flight.profiles().get(model['id'])
                if p:ps.need((p.diameter_mm,p.mass_kg,p.durability)==(model['diameter_mm'],model['mass_g']/1000,model['durability_points']),
                             '$.missile_flight','飞行配置与导弹型号不匹配')

    def submit(self,value):
        b=self.battle;b._guard()
        v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id order','$.missile_command')
        ps.need(v['epoch']==b.session.world.epoch,'$.epoch','导弹命令已过期')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        if v==self.last:return False
        ps.need(v['sequence']==self.sequence+1 and not b.ending,'$.sequence','导弹命令序号不连续或交战已结束')
        index=next((i for i,s in enumerate(b.session.world.ships) if s.ship_id==v['ship_id']),None)
        ps.need(index is not None and b._sides[index]==b._sides[b._direct_index],'$.ship_id','只能指挥己方舰艇')
        order=v['order'];ps.need(type(order) is dict,'$.order','需要导弹命令')
        ps.need(type(order.get('kind')) is str,'$.order.kind','需要明确导弹命令类型')
        fields={'target':' target_id','point':' point_m','attack_layer':' layer','fire':'','clear':''}
        if order['kind']=='retarget':
            ps.need(b._can_fire(b.session.world.ships[index],index),'$.ship_id','舰艇不能接受数据链更新指令')
            ps.obj(order,'kind projectile_id target_id','$.order')
            ps.integer(order['projectile_id'],'$.projectile_id',1)
            ps.need(type(order['target_id']) in (str,int),'$.target_id','需要目标身份')
            projectile=next((p for p in b.projectiles if p.id==order['projectile_id']),None)
            ps.need(projectile is not None and projectile.missile and projectile.missile.profile.datalink,'$.projectile_id','此在途导弹不支持数据链改攻')
            source=next(i for i,s in enumerate(b.session.world.ships) if s.ship_id==projectile.ship_id)
            ps.need(b._sides[source]==b._sides[index],'$.projectile_id','只能更新己方导弹')
            world=b.session.world;available=b._availability(world)[1]
            ps.need(any(available[index][mid] is None for mid in b.observation.links[index]),'$.ship_id','所选舰艇没有可用数据链')
            track=b.observation.frame.tracks.get((index,order['target_id']))
            appropriate=track and ((track.target.kind in ('shell','missile') and track.target.durability is not None) if projectile.missile.profile.interceptor else track.target.kind=='ship')
            ps.need(track and track.valid and appropriate
                    and b.observation.sources(index,order['target_id'],world,available)[0],'$.target_id','需要对应敌方目标的有效火控观测')
            ps.need(hypot(*(a-c for a,c in zip(projectile.position,world.ships[index].motion.position_world_m.to_list())))<=b.observation.policy['datalink_range_m'],
                    '$.projectile_id','导弹超出数据链通信范围')
            self.retargets={**self.retargets,projectile.id:order['target_id']}
        elif order.get('kind') in fields:
            kind=order['kind'];ps.obj(order,'module_id kind'+fields[kind],'$.order')
            ps.identifier(order['module_id'],'$.module_id')
            key=(index,order['module_id']);ps.need(key in self.states,'$.module_id','找不到发射器')
            state=self.states[key]
            if kind=='target':
                identity=order['target_id']
                row=next(r for r in b.inventory.inventories[index]._value['missiles']['launchers'] if r['module_id']==key[1])
                profile=flight.profiles().get(row['model_id']);interceptor=bool(profile and profile.interceptor)
                track=b.observation.frame.tracks.get((index,identity)) if type(identity) in (str,int) else None
                ps.need(track and track.valid and ((track.target.kind in ('shell','missile') and track.target.durability is not None)
                        if interceptor else track.target.kind=='ship'),'$.target_id','拦截弹需要有耐久的敌方弹体，反舰弹需要敌舰')
                sources=b.observation.defense_sources(index,identity,b.session.world,b._availability(b.session.world)[1],weapon_id=key[1]) if interceptor else b.observation.sources(index,identity,b.session.world,b._availability(b.session.world)[1])
                ps.need(sources[0],
                        '$.target_id','目标当前没有有效火控观测')
                state=replace(state,target=identity,point=None,fire_requested=False)
            elif kind=='point':
                point=order['point_m'];ps.need(type(point) is list and len(point)==2,'$.point_m','需要二维发射地点')
                for x in point:ps.number(x,'$.point_m',-1e7,1e7)
                state=replace(state,point=tuple(point),target=None,fire_requested=False)
            elif kind=='attack_layer':
                row=next(r for r in b.inventory.inventories[index]._value['missiles']['launchers'] if r['module_id']==key[1])
                profile=flight.profiles().get(row['model_id'])
                automatic=order['layer'] is None and profile and profile.interceptor
                ps.need(automatic or order['layer'] in LAYERS,'$.layer','未知作用层')
                ps.need(automatic or abs(LAYERS.index(order['layer'])-LAYERS.index(b.session.world.ships[index].motion.height_layer))<=1,'$.layer','只能发射至本层或相邻层')
                state=replace(state,layer=order['layer'],fire_requested=False)
            elif kind=='fire':
                row=next(r for r in b.inventory.inventories[index]._value['missiles']['launchers'] if r['module_id']==key[1])
                ps.need(row['model_id'] in flight.profiles(),'$.model_id','此型号尚未接通飞行，请使用本阶段代表型号')
                ps.need(state.target is not None or state.point is not None,'$.target','请先指定目标或地点')
                state=replace(state,fire_requested=True)
            else:state=replace(state,target=None,point=None,fire_requested=False,aim=None)
            self.states={**self.states,key:state}
        else:
            inv=b.inventory.inventories[index].fork()
            logistics.apply(inv,order)
            candidates=list(b.inventory.inventories);candidates[index]=inv
            b.inventory.inventories=tuple(candidates)
        self.sequence,self.last=v['sequence'],v
        return True

    def advance(self,world,inventories,available):
        b=self.battle
        for i,inv in enumerate(inventories):
            profile=inv._definition.get('missiles')
            if not profile:continue
            rates={s['module_id']:0. if available[i][s['module_id']] else
                   b.crew_efficiency(world,i,s['module_id'],'weapon.reload' if group=='launchers' else 'ammunition.feed')
                   for group in ('launchers','magazines') for s in profile[group]}
            logistics.advance(inv,1,rates)

    def plan(self,world,inventories,available,projectiles,sequence,frame,ending,environment=None,defense_contacts=None,defense_threats=None,navigation_orders=None):
        """Only candidate inventories are mutated. Caller commits this result."""
        from .tactical_gunnery import Projectile,rotate,add,wrap
        from .tactical_missile_defense import select,reservation
        b=self.battle;step=world.fixed_step;states={};pending=[];events=[]
        if ending:return self.states,(),sequence,()
        if defense_contacts is None:
            defense_contacts,defense_threats=b.point_defense.observe(world,available,projectiles,frame) if b.point_defense else ({},())
        for departure in self.pending:
            if departure.due_step<=step:
                projectiles.append(reservation(flight.prepare(departure.projectile,world,b._sides,environment),step))
                events.append(dict(kind='emerged',step=step,projectile_id=departure.projectile.id,ship_id=departure.projectile.ship_id,weapon_id=departure.projectile.weapon_id))
            else:pending.append(departure)
        # Departed missiles do not consult a destroyed launcher or a new order.
        for key,state in self.states.items():
            n,mid=key;inv=inventories[n];row=next(r for r in inv._value['missiles']['launchers'] if r['module_id']==mid)
            spec=next(s for s in inv._definition['missiles']['launchers'] if s['module_id']==mid)
            p=flight.profiles().get(row['model_id']);gun=self.geometry[key];ship=world.ships[n];motion=ship.motion
            layer=state.layer or motion.height_layer;ratio=1. if layer==motion.height_layer else CROSS_LAYER_SPEED
            status=available[n][mid]
            if not b._can_fire(ship,n) or (b._sides[n]!=b._sides[b._direct_index] and not b.enemy_fire):status=status or 'control_unavailable'
            if not p:states[key]=replace(state,status='model_unavailable',fire_requested=False);continue
            origin=add(tuple(motion.position_world_m.to_list()),rotate(gun.anchor,motion.heading_rad))
            aim=state.point;velocity=(0.,0.);target=state.target
            order=(navigation_orders or {}).get(ship.ship_id)
            if order and order.kind=='attack' and row['auto_fire'] and not p.interceptor:
                target=order.target;aim=None
                track=frame.tracks.get((n,target))
                if track and track.valid:
                    layer=track.target.layer;ratio=1. if layer==motion.height_layer else CROSS_LAYER_SPEED
            if abs(LAYERS.index(layer)-LAYERS.index(motion.height_layer))>1:status=status or 'layer_out_of_reach'
            sources_for=lambda identity:b.observation.defense_sources(n,identity,world,available,frame,mid) if p.interceptor else b.observation.sources(n,identity,world,available,frame)
            if p.interceptor and row['auto_fire'] and not state.fire_requested and not status:
                rows=defense_threats if state.target is None else tuple(r for r in defense_threats if r['projectile_id']==state.target)
                choice,reason=select(b,n,mid,p,state.layer,origin,world,available,inventories,projectiles,frame,defense_contacts,rows,pending)
                if choice:
                    target=choice['projectile_id'];layer=choice['layer']
                    ratio=1. if layer==motion.height_layer else CROSS_LAYER_SPEED
                else:target=None;aim=None;status=reason
            elif target is None and aim is None and row['auto_fire'] and not status:
                candidates=[t for (i,_),t in frame.tracks.items() if i==n and t.valid and t.target.kind=='ship' and t.target.layer==layer
                            and b.observation.sources(n,t.target.id,world,available,frame)[0]
                            and hypot(*(a-c for a,c in zip(t.target.position,origin)))<=p.range(ratio)]
                if candidates:target=min(candidates,key=lambda t:(not t.target.large,hypot(*(a-c for a,c in zip(t.target.position,origin))),t.target.id)).target.id
            if target is not None:
                track=frame.tracks.get((n,target))
                appropriate=track and ((track.target.kind in ('shell','missile') and track.target.durability is not None) if p.interceptor else track.target.kind=='ship')
                if track and track.valid and appropriate and sources_for(target)[0]:
                    aim=tuple(a+v*(step-track.step)/60 for a,v in zip(track.target.position,track.target.velocity));velocity=track.target.velocity
                    from .projectile_observation import extrapolate
                    target_layer=extrapolate(track.target.payload,max(0,step-track.step)/60).height_layer if track.target.payload else track.target.layer
                    if p.interceptor and state.layer is None:
                        layer=target_layer;ratio=1. if layer==motion.height_layer else CROSS_LAYER_SPEED
                        if abs(LAYERS.index(layer)-LAYERS.index(motion.height_layer))>1:status=status or 'layer_out_of_reach'
                    if target_layer!=layer:status=status or 'target_other_layer'
                else:status=status or 'target_unavailable';aim=None
            if p.interceptor and not b.observation.defense_ready(n,mid,world,available):status=status or 'defense_sensor_unavailable'
            angle=state.angle
            if aim is not None:
                delta=tuple(a-c for a,c in zip(aim,origin));local=rotate(delta,-motion.heading_rad)
                desired=wrap(atan2(local[0],local[1])-gun.rotation)
                if spec['launcher_kind']=='vls':angle=desired
                else:
                    limit=gun.slew*b.crew_efficiency(world,n,mid,'weapon.aim')
                    if not status:angle+=max(-limit,min(limit,max(gun.minimum,min(gun.maximum,desired))-angle))
                    status=status or ('out_of_arc' if not gun.minimum<=desired<=gun.maximum else 'hull_blocked' if b._hull_blocked(gun,angle)
                                     else 'traversing' if abs(wrap(desired-angle))>b.config['aligned_tolerance_mdeg']*pi/180000 else None)
                if hypot(*delta)>p.range(ratio):status=status or 'out_of_range'
            else:status=status or 'no_target'
            # A replenishment job owns a different round. Multi-round launchers
            # may fire their remaining ready rounds at the configured interval.
            # Unloading / changing warheads still locks the whole launcher.
            if (row['job'] and (row['job']['kind'] not in ('load_raw','load_ready') or not row['ready'])) or row['unload_remaining'] or any(u['warhead_id']!=row['warhead_id'] for u in row['ready']):status=status or 'reloading'
            if row['cooldown_steps']:status=status or 'cooldown'
            if not row['ready']:status=status or 'no_ammunition'
            # Pending VLS events reserve projectile capacity at departure.
            if len(projectiles)+len(pending)>=b.config['max_projectiles']:status=status or 'projectile_limit'
            shots=state.shots;requested=state.fire_requested
            if not status and (requested or row['auto_fire']):
                direction=rotate((sin(angle+gun.rotation),cos(angle+gun.rotation)),motion.heading_rad)
                muzzle=add(origin,tuple(d*3 for d in direction));unit=row['ready'][0]
                work=logistics.Work(inv);loaded=work.launchers[mid];loaded['ready'].pop(0)
                loaded['cooldown_steps']=ceil(spec['shot_interval_steps']/max(1e-8,b.crew_efficiency(world,n,mid,'weapon.fire')))
                # Replace the consumed round, preferring complete stock as usual.
                loaded['load_remaining']=max(loaded['load_remaining'],work.cap(loaded)-len(loaded['ready'])-int(loaded['job'] is not None))
                work.commit('missile_fired')
                sequence+=1;due=step+spec['launch_delay_steps'];shots+=1;requested=False
                launch_point=tuple(a+v*spec['launch_delay_steps']/60 for a,v in zip(aim,velocity))
                f=flight.Flight(p,unit['warhead_id'],due,atan2(direction[1],direction[0]),launch_point,velocity,target,ratio)
                projectile=Projectile(sequence,ship.ship_id,mid,muzzle,muzzle,tuple(d*p.launch_speed*ratio for d in direction),due+p.lifetime(),
                    None,layer,(p.model_id+'.'+unit['warhead_id'],1),p.ballistics(ratio),aimed_ship_id=target if not p.interceptor else None,
                    durability=p.durability,maximum_durability=p.durability,collision_radius_m=p.diameter_mm/2000,missile=f,
                    interception_damage=p.interception_damage,interception_radius_m=p.interception_radius_m,
                    interception_target_id=target if p.interceptor and type(target) is int else None,
                    interception_expected_step=due+p.lifetime() if p.interceptor and type(target) is int else None)
                if due>step:pending.append(Departure(due,projectile));status='departed'
                else:projectiles.append(reservation(flight.prepare(projectile,world,b._sides,environment),step));status='fired'
                events.append(dict(kind='fired',step=step,projectile_id=sequence,ship_id=ship.ship_id,weapon_id=mid,model_id=p.model_id,
                                   warhead_id=unit['warhead_id'],height_layer=layer,emergence_step=due,
                                   position_m=muzzle,direction=direction,effect='missile_vls_launch' if due>step else 'missile_turret_launch'))
            states[key]=replace(state,angle=angle,status=status or 'ready',aim=aim,shots=shots,fire_requested=requested,active_target=target,active_layer=layer)
        return states,tuple(pending),sequence,tuple(events)

    def commit(self,plan,ending=False):
        self.states,self.pending,_,events=plan
        self.retargets={}
        self.recent=(self.recent+events)[-40:]
        if ending:self.clear()

    def clear(self):
        self.pending=()
        self.retargets={}
        self.states={k:replace(s,fire_requested=False,status='battle_finished') for k,s in self.states.items()}

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
