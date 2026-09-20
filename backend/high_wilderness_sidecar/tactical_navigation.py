"""6b fleet intent -> actuator commands. No position/velocity teleportation.

Orders and steering caches commit only after the enclosing physics transaction.
Enemy navigation reads its own observation frame, never hidden target motion.
"""
from dataclasses import dataclass, replace
from math import atan2, pi, sqrt
from 高天荒野舰艇定向推进控制桥 import directional_control
from 高天荒野舰艇推进通道合同 import ChannelPropulsionCommand, DIRECTIONAL_CHANNELS
from 高天荒野舰艇战术机动求解器 import Vec2, body_to_world, world_to_body, wrap_angle, calculate_tactical_drag
from 高天荒野舰艇推进安全判定器 import TELEGRAPH_NOTCH_PERCENT, THRUST_OUTPUT_STAGES_PERCENT
from . import persistent_ship as ps

REFRESH_STEPS = 6
MAX_WAYPOINTS = 32
ARRIVAL_METRES = 25.


@dataclass(frozen=True)
class Order:
    kind: str = 'formation'
    points: tuple = ()
    speed: float = 100.
    heading: float | None = None
    target: str | None = None
    distance: float = 3000.
    status: str = 'following'


def steer(ship, seed, destination, velocity, heading, limit):
    """Damped velocity/heading servo; governors still own actual safe output."""
    m=ship.motion
    error=destination-m.position_world_m
    correction=error*(1/8.)
    if correction.length>limit:correction=correction*(limit/correction.length)
    desired=velocity+correction
    if desired.length>limit and limit>0:desired=desired*(limit/desired.length)
    acceleration=(desired-m.velocity_world_mps)*(1/3.)
    force=world_to_body(acceleration*seed.model.runtime.current_mass_kg
                        -calculate_tactical_drag(seed.model,m).force_world_n,m.heading_rad)
    caps=[u/seed.contributions.unit_denominator for u in ship.propulsion.available_units]
    stages=TELEGRAPH_NOTCH_PERCENT
    commands=[]
    for value,pos,neg in ((force.y,0,1),(force.x,3,2)):
        d=pos if value>=0 else neg
        pct=min(100.,abs(value)/caps[d]*100) if caps[d]>0 else 0
        notch=min(stages,key=lambda row:abs(row[1]-pct))[0]
        commands.append(ChannelPropulsionCommand(DIRECTIONAL_CHANNELS[d],notch,None))
    angle=wrap_angle(heading-m.heading_rad)
    inertia=seed.model.runtime.current_inertia_kg_m2
    max_alpha=max(caps[4:])*seed.model.tuning.turn_scale/inertia
    # Braking distance + actuator response lead prevents full-wheel oscillation.
    desired_rate=(1 if angle>=0 else -1)*min(.08,sqrt(max(0,2*max_alpha*abs(angle))))
    if abs(angle)<.003:desired_rate=0.
    alpha=(desired_rate-m.yaw_rate_radps)/3.
    d=4 if alpha>=0 else 5
    pct=min(100.,abs(alpha)*inertia/(caps[d]*seed.model.tuning.turn_scale)*100) if caps[d]>0 else 0
    yaw=min(THRUST_OUTPUT_STAGES_PERCENT,key=lambda value:abs(value-pct))
    commands.append(ChannelPropulsionCommand(DIRECTIONAL_CHANNELS[d],None,yaw))
    return directional_control(commands)


class Navigation:
    def __init__(self,battle,scenario):
        self.battle=battle;self.sequence=0;self.last=None
        self.orders={};self.controls={};self.cache={};self.withdrawals={}
        self.flags={};self.members={};self.offsets={};self.flag_by_ship={};self.enemy_flags=set()
        request=scenario.manifest.get('encounter')
        if request:
            mapping={r['instance_id']:r['ship_id'] for r in scenario.manifest['instance_mapping']}
            world=battle.session.world;by_id={s.ship_id:s for s in world.ships}
            for side in request['sides']:
                flag=mapping[side['flagship_instance_id']];self.flags[side['side_id']]=flag
                if side['side_id']!=request['player_side_id']:self.enemy_flags.add(flag)
                members=tuple(mapping[m['instance_id']] for m in side['ships']);self.members[flag]=members
                anchor=by_id[flag].motion
                for key in members:
                    self.flag_by_ship[key]=flag
                    self.offsets[key]=world_to_body(by_id[key].motion.position_world_m-anchor.position_world_m,anchor.heading_rad)
                    if key!=world.ships[battle._direct_index].ship_id:
                        self.orders[key]=Order(kind='hold',points=(tuple(anchor.position_world_m.to_list()),),heading=anchor.heading_rad,status='holding') if key==flag else Order()

    def submit(self,value):
        b=self.battle;b._guard();v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id kind arguments','$.navigation')
        ps.need(v['epoch']==b.session.world.epoch,'$.epoch','航行指令来自旧战场')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        if v['sequence']==self.sequence:
            ps.need(v==self.last,'$.sequence','航行指令重试内容不一致');return False
        ps.need(v['sequence']==self.sequence+1,'$.sequence','航行指令序号不连续')
        key=v['ship_id'];world=b.session.world
        n=next((i for i,s in enumerate(world.ships) if s.ship_id==key),None)
        ps.need(n is not None and b._sides[n]==b._sides[b._direct_index] and key in self.flag_by_ship,'$.ship_id','只能指挥本方编队成员')
        ps.need(b.ending is None and b.session.can_navigate(world.ships[n],True),'$.ship_id','该舰当前无法执行航行命令')
        kind,args=v['kind'],v['arguments'];ship=world.ships[n]
        if kind in ('withdraw','cancel_withdraw'):
            ps.obj(args,'','$.arguments');flag=self.flag_by_ship[key]
            if kind=='withdraw':
                enemy=next(s for s in world.ships if s.ship_id==next(f for f in self.members if f!=flag))
                anchor=next(s for s in world.ships if s.ship_id==flag)
                delta=anchor.motion.position_world_m-enemy.motion.position_world_m
                direction=delta*(1/delta.length) if delta.length>1 else body_to_world(Vec2(0,1),anchor.motion.heading_rad)
                self.withdrawals[flag]=tuple(direction.to_list())
            else:self.withdrawals.pop(flag,None)
            self.controls={} if kind=='withdraw' else {flag:directional_control()}
        else:
            ps.need(key!=b.session._direct,'$.ship_id','旗舰使用独立操纵区；整队撤离通过舰队命令执行')
            ps.need(self.flag_by_ship[key] not in self.withdrawals,'$.kind','请先取消整队撤离机动')
            if kind=='move':
                ps.obj(args,'point_m append speed_mps heading_deg','$.arguments')
                ps.need(type(args['point_m']) is list and len(args['point_m'])==2,'$.point','需要二维目标地点')
                for x in args['point_m']:ps.number(x,'$.point',-1_000_000,1_000_000)
                ps.need(type(args['append']) is bool,'$.append','追加路径须为开关')
                ps.number(args['speed_mps'],'$.speed',1,5000)
                if args['heading_deg'] is not None:ps.number(args['heading_deg'],'$.heading',-180,180)
                old=self.orders.get(key,Order());points=old.points if args['append'] and old.kind=='move' else ()
                ps.need(len(points)<MAX_WAYPOINTS,'$.points','路径最多保留 32 个航点')
                order=Order('move',(*points,tuple(args['point_m'])),args['speed_mps'],None if args['heading_deg'] is None else args['heading_deg']*pi/180,status='moving')
            elif kind=='attack':
                ps.obj(args,'target_id distance_m speed_mps','$.arguments')
                ps.identifier(args['target_id'],'$.target_id')
                track=b.observation.frame.tracks.get((n,args['target_id']))
                ps.need(track is not None and track.valid and track.target.kind=='ship' and track.target.side!=b._sides[n],
                        '$.target_id','只能攻击本舰有效观测中的敌舰')
                ps.number(args['distance_m'],'$.distance_m',100,50000)
                ps.number(args['speed_mps'],'$.speed_mps',1,5000)
                order=Order('attack',speed=args['speed_mps'],target=args['target_id'],distance=args['distance_m'],status='attacking')
            elif kind in ('hold','return'):
                ps.obj(args,'','$.arguments')
                order=Order('hold',(tuple(ship.motion.position_world_m.to_list()),),heading=ship.motion.heading_rad,status='holding') if kind=='hold' else Order(status='returning')
            else:
                ps.need(False,'$.kind','不支持的航行指令')
            self.orders={**self.orders,key:order};self.controls.pop(key,None)
        self.sequence,self.last=v['sequence'],v
        return True

    def sustainable_speed(self,n,ship,cache):
        seed=self.battle.session._seeds[n]
        rk=self.battle.session._resource_kernels[n]
        efficiencies=(rk.engine_efficiencies(ship.resources) if rk else None) or (1.,)*len(seed.contributions.engines)
        key=(ship.ship_id,ship.propulsion.available_units,tuple(any(e.blocked) for e in ship.propulsion.engines),efficiencies,ship.motion.height_layer,ship.motion.hull_integrity_fraction)
        if cache.get(ship.ship_id,())[0:1]==(key,):return cache[ship.ship_id][1]
        thrust=sum(e.contribution_units[0]*eff for e,slot,eff in zip(seed.contributions.engines,ship.propulsion.engines,efficiencies) if not any(slot.blocked))/seed.contributions.unit_denominator
        low,high=0.,5000.
        if thrust>0:
            wind=seed.model.environment.layer(ship.motion.height_layer).wind_world_mps
            for _ in range(20):
                mid=(low+high)/2
                m=replace(ship.motion,heading_rad=0.,velocity_world_mps=wind+Vec2(0,mid))
                if calculate_tactical_drag(seed.model,m).breakdown.drag_force_n<=thrust:low=mid
                else:high=mid
        cache[ship.ship_id]=(key,low);return low

    def plan(self,world,*,cancel_flag=None):
        b=self.battle;by_id={s.ship_id:s for s in world.ships};orders=dict(self.orders);controls=dict(self.controls);cache=dict(self.cache)
        withdrawals=dict(self.withdrawals)
        if cancel_flag is not None:
            withdrawals.pop(cancel_flag,None)
            controls={k:v for k,v in controls.items() if self.flag_by_ship[k]!=cancel_flag}
        # Basic opposing flagship navigation uses only its own measured tracks.
        for n,ship in enumerate(world.ships):
            if ship.ship_id not in self.enemy_flags or not b.enemy_fire or ship.ship_id in withdrawals:continue
            old=orders[ship.ship_id]
            tracks=[t for (i,_),t in b.observation.frame.tracks.items() if i==n and t.valid and t.target.kind=='ship' and t.target.side!=b._sides[n]]
            target=next((t for t in tracks if t.target.id==old.target),None)
            if target is None and tracks:target=min(tracks,key=lambda t:((Vec2(*t.target.position)-ship.motion.position_world_m).length,t.target.id))
            if target:
                order=Order('attack',target=target.target.id,status='attacking')
            elif old.kind=='attack':
                order=Order('hold',(tuple(ship.motion.position_world_m.to_list()),),heading=ship.motion.heading_rad,status='holding')
            else:order=old
            if (order.kind,order.target)!=(old.kind,old.target):controls.pop(ship.ship_id,None)
            orders[ship.ship_id]=order
        retreat={}
        for flag in withdrawals:
            speeds=[self.sustainable_speed(n,s,cache) for n,s in enumerate(world.ships)
                    if s.ship_id in self.members[flag] and b.session.can_navigate(s,True)]
            retreat[flag]=min((v for v in speeds if v>.01),default=0.)
        for n,ship in enumerate(world.ships):
            key=ship.ship_id;flag=self.flag_by_ship.get(key)
            if key not in orders and flag not in withdrawals:continue
            if not b.session.can_navigate(ship,True):controls[key]=directional_control();continue
            if orders.get(key,Order()).kind=='attack':
                track=b.observation.frame.tracks.get((n,orders[key].target))
                if track is None or not track.valid:
                    orders[key]=Order(status='returning');controls.pop(key,None)
            if world.fixed_step%REFRESH_STEPS and key in controls:continue
            seed=b.session._seeds[n];order=orders.get(key,Order());anchor=by_id[flag]
            velocity=Vec2();heading=ship.motion.heading_rad;limit=order.speed
            if flag in withdrawals:
                direction=Vec2(*withdrawals[flag]);limit=retreat[flag];heading=atan2(-direction.x,direction.y)
                # Parallel retreat, same requested ground velocity. Disabled ships
                # wait for repairs; they do not reduce the moving fleet to zero.
                destination=ship.motion.position_world_m
                velocity=direction*limit
            elif order.kind=='hold':
                destination=Vec2(*order.points[0]);heading=order.heading
            elif order.kind=='attack':
                t=b.observation.frame.tracks[(n,order.target)]
                position=Vec2(*t.target.position)+Vec2(*t.target.velocity)*max(0,(world.fixed_step-t.step)/60)
                away=ship.motion.position_world_m-position
                away=away*(1/away.length) if away.length>1 else body_to_world(Vec2(0,-1),ship.motion.heading_rad)
                destination=position+away*order.distance
                velocity=Vec2(*t.target.velocity);heading=atan2(away.x,-away.y)
            elif order.kind=='move':
                points=order.points
                if (Vec2(*points[0])-ship.motion.position_world_m).length<=ARRIVAL_METRES:
                    points=points[1:]
                    if not points:order=Order(status='returning')
                    else:order=replace(order,points=points)
                    orders[key]=order
                if order.kind=='move':
                    destination=Vec2(*order.points[0]);delta=destination-ship.motion.position_world_m
                    heading=order.heading if order.heading is not None else atan2(-delta.x,delta.y)
            if flag not in withdrawals and order.kind=='formation':
                if not b.session.can_navigate(anchor,True):
                    order=Order('hold',(tuple(ship.motion.position_world_m.to_list()),),heading=ship.motion.heading_rad,status='flagship_unavailable')
                    orders[key]=order;destination=ship.motion.position_world_m
                else:
                    offset=body_to_world(self.offsets[key],anchor.motion.heading_rad)
                    destination=anchor.motion.position_world_m+offset
                    velocity=anchor.motion.velocity_world_mps+Vec2(-offset.y,offset.x)*anchor.motion.yaw_rate_radps
                    heading=anchor.motion.heading_rad;limit=max(100.,velocity.length+50.)
                    orders[key]=replace(order,status='following' if (destination-ship.motion.position_world_m).length<=ARRIVAL_METRES else 'returning')
            controls[key]=steer(ship,seed,destination,velocity,heading,limit)
        return orders,controls,cache,retreat,withdrawals

    def commit(self,plan):
        self.orders,self.controls,self.cache,self.retreat_speeds,self.withdrawals=plan

    def gun_states(self, orders, withdrawals):
        b=self.battle;result=[]
        for gun,state in zip(b.guns,b.states):
            key=b.session.world.ships[gun.ship_index].ship_id
            order=orders.get(key)
            if not state.point_defense:
                if order and order.kind=='attack' and self.flag_by_ship[key] not in withdrawals:
                    target=next(i for i,s in enumerate(b.session.world.ships) if s.ship_id==order.target)
                    track=b.observation.frame.tracks[(gun.ship_index,order.target)]
                    state=replace(state,mode='auto',target_policy='assigned',target=(target,None),manual_point=None,attack_layer=track.target.layer,navigation_override=True)
                elif state.navigation_override:
                    state=replace(state,target_policy='automatic',target=None,attack_layer=None,navigation_override=False)
            result.append(state)
        return result

    def view(self):
        flag=self.flag_by_ship.get(self.battle.session._direct)
        members=self.members.get(flag,())
        return dict(command_sequence=self.sequence,last=self.last,ships=[dict(ship_id=key,kind=o.kind,status=o.status,
            points=[list(p) for p in o.points],speed_mps=o.speed,heading_deg=None if o.heading is None else o.heading*180/pi,
            target_id=o.target,distance_m=o.distance) for key,o in self.orders.items() if key in members],
            withdrawals=[dict(flagship_id=k,speed_mps=getattr(self,'retreat_speeds',{}).get(k,0.),members=list(self.members[k]))
                         for k in self.withdrawals if k==flag])
