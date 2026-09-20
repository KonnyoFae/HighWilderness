"""F3 deterministic ordinary-gun search and firing work, staged with the world.

There is one search and one solve request per gun at most. Only fixed steps,
stable gun indices and measured information affect scheduling, never wall time.
Point-defense trajectories and missile guidance remain separate (F4).
"""
from dataclasses import dataclass, replace
from math import asin, atan2, ceil, hypot, pi, sqrt

from . import tactical_ballistics as ballistics, tactical_targeting as targeting
from .tactical_layers import LAYERS

SEARCH_PERIOD = 12
SOLVE_PERIOD = 6
LEASE_STEPS = 12
MIN_BUDGET = 8
MISS_LIMIT_M = .10


@dataclass(frozen=True)
class Shot:
    signature: tuple
    contact: object
    step: int
    due: int
    time: float | None
    inherited: tuple


@dataclass(frozen=True)
class Work:
    search_due: int = 0
    search_since: int | None = None
    solve_since: int | None = None
    shot: Shot | None = None
    rejected: tuple | None = None  # target, retry step
    search_config: tuple | None = None


def next_phase(step, index, period):
    earliest = step + period
    return earliest + (index-earliest) % period


def geometry(gun, ship):
    from .tactical_gunnery import add, rotate
    m = ship.motion; offset = rotate(gun.anchor,m.heading_rad)
    return (add(tuple(m.position_world_m.to_list()),offset),
            add(tuple(m.velocity_world_mps.to_list()),(-m.yaw_rate_radps*offset[1],m.yaw_rate_radps*offset[0])))


def target_motion(battle,state,contact,step):
    from .tactical_gunnery import add, rotate
    elapsed=(step-contact.step)/60
    position=add(contact.position,tuple(v*elapsed for v in contact.velocity))
    velocity=contact.velocity
    if state.target and state.target[1]:
        module=battle._modules[state.target[0]][state.target[1]]
        offset=rotate(module.anchor_m,contact.heading+contact.yaw*elapsed)
        position=add(position,offset)
        velocity=add(velocity,(-contact.yaw*offset[1],contact.yaw*offset[0]))
    return position,velocity


def possible_arc(gun,center,half):
    """Reject only when the entire conservative bearing cone is forbidden."""
    for turn in (-2*pi,0.,2*pi):
        low=max(gun.minimum,center-half+turn); high=min(gun.maximum,center+half+turn)
        if high<low:continue
        if high-low<1e-10:
            from .tactical_gunnery import GunneryBattle
            if not GunneryBattle._hull_blocked(gun,(low+high)/2):return True
            continue
        cursor=low
        blocked=sorted((a*pi/180-gun.rotation+k*2*pi,b*pi/180-gun.rotation+k*2*pi)
                       for a,b in gun.blocked for k in (-2,-1,0,1,2))
        for start,end in blocked:
            if end<cursor:continue
            if start>cursor:return True
            cursor=max(cursor,end)
            if cursor>=high:break
        if cursor<high:return True
    return False


def possible(battle,gun,ship,contact,origin,inherited,flight,ratio,step):
    from .tactical_gunnery import wrap
    elapsed=(step-contact.step)/60
    delta=tuple(p+v*elapsed-o for p,v,o in zip(contact.position,contact.velocity,origin))
    distance=hypot(*delta); movement=hypot(*contact.velocity)*flight.lifetime_steps/60
    maximum=min(gun.maximum_range,ballistics.reference_range(flight,ratio))
    if distance-movement>maximum or distance+movement<gun.minimum_range:return False
    muzzle=flight.muzzle_speed_mps*ratio
    if distance<=movement or hypot(*inherited)>=muzzle:half=pi
    else:half=min(pi,asin(movement/max(distance,1e-12))+asin(hypot(*inherited)/muzzle))
    center=wrap(atan2(delta[0],delta[1])+ship.motion.heading_rad-gun.rotation+contact.bearing_error)
    return possible_arc(gun,center,half)


def candidate(battle,world,frame,contacts,gun,work,layer,origin,inherited,flight,ratio,step,target):
    contact=contacts.get((gun.ship_index,target))
    if contact is None:return None
    track=frame.tracks.get((gun.ship_index,world.ships[target].ship_id))
    target_layer=track.target.layer if track and track.valid else world.ships[target].motion.height_layer
    if target_layer!=layer or (work.rejected and work.rejected[0]==target and step<work.rejected[1]):return None
    # Preserve the existing automatic-acquisition rule: the measured target
    # must already be inside the selected round's reference range. The wider
    # cone below is only a conservative ballistic filter, not new permissions
    # to start attacks against still-out-of-range approaching ships.
    distance=hypot(*(p-o for p,o in zip(contact.position,origin)))
    maximum=min(gun.maximum_range,ballistics.reference_range(flight,ratio))
    if not gun.minimum_range<=distance<=maximum:return None
    return contact if possible(battle,gun,world.ships[gun.ship_index],contact,origin,inherited,flight,ratio,step) else None


def tracking_point(position,velocity,origin,inherited,flight,ratio,previous=None):
    time=previous if previous is not None else min(flight.lifetime_steps/60,
        hypot(*(p-o for p,o in zip(position,origin)))/(flight.muzzle_speed_mps*ratio))
    return tuple(p+(v-own)*time for p,v,own in zip(position,velocity,inherited))


def flight_time(aim,origin,inherited,flight,ratio):
    delta=tuple(p-o for p,o in zip(aim,origin)); distance=hypot(*delta)
    if distance<1e-10:return 0.
    muzzle=flight.muzzle_speed_mps*ratio
    if not flight.drag:return distance/muzzle
    speed=hypot(*(v+d/distance*muzzle for v,d in zip(inherited,delta)))
    return ballistics.time_to_distance(flight,speed,distance)


def reproject(time,position,velocity,origin,inherited,flight,ratio):
    """At most two updates; qualify by actual predicted miss, not cache age alone.

    The candidate world path reaches position+velocity*t at measured flight
    time next_t. Its miss against that same observation is |v|*|next_t-t|.
    A failure requests a full solve and cannot grant permission to fire.
    """
    from .tactical_gunnery import intercept
    if not flight.drag:
        aim=intercept(origin,inherited,position,velocity,flight.muzzle_speed_mps*ratio)
        return (aim,flight_time(aim,origin,inherited,flight,ratio)) if aim else (None,None)
    muzzle=flight.muzzle_speed_mps*ratio
    for _ in range(2):
        delta=tuple(p+v*time-o for p,v,o in zip(position,velocity,origin));distance=hypot(*delta)
        if distance<1e-10:return position,0.
        direction=tuple(d/distance for d in delta)
        along=sum(d*v for d,v in zip(direction,inherited))
        discriminant=muzzle*muzzle-sum(v*v for v in inherited)+along*along
        if discriminant<0:return None,None
        speed=along+sqrt(discriminant)
        if speed<=0:return None,None
        next_t=ballistics.time_to_distance(flight,speed,distance)
        if next_t is None:return None,None
        if hypot(*velocity)*abs(next_t-time)<=MISS_LIMIT_M:
            aim=tuple(o+(d*speed-v)/muzzle*distance for o,d,v in zip(origin,direction,inherited))
            return aim,next_t
        time=next_t
    return None,None


class Runtime:
    def __init__(self):self.work={}
    def begin(self,step):return Plan(step,self.work)
    def commit(self,plan):self.work=plan.work


class Plan:
    def __init__(self,step,work):
        self.step=step;self.work=dict(work);self.aims={};self.reasons={}
        self.counters=dict(searches=0,search_requests=0,search_queue=0,solve_queue=0,
            max_search_wait_steps=0,max_solve_wait_steps=0,reused_solutions=0,tracking_only=0,
            reproject_rejected=0,solution_age_steps=0,refresh_observation=0,refresh_motion=0,
            invalidated=0,solve_budget=0,search_budget=0,precise_requests=0)

    def acquire(self,battle,world,available,inventories,frame,solutions):
        states=list(battle.states); contacts=targeting.search_contacts(battle,world,available,frame)
        pending=[]; budget=max(MIN_BUDGET,ceil(len(battle.guns)/SEARCH_PERIOD))
        self.counters['search_budget']=budget
        for index,(gun,state) in enumerate(zip(battle.guns,states)):
            if state.mode!='auto' or state.target_policy!='automatic' or state.point_defense:
                if index in self.work:self.work[index]=replace(self.work[index],search_since=None,search_due=self.step)
                continue
            ship=world.ships[gun.ship_index];work=self.work.get(index,Work())
            own_side=battle._sides[battle._direct_index]
            layer=state.attack_layer or ship.motion.height_layer
            if (available[gun.ship_index][gun.module_id] is not None or not battle._can_fire(ship,gun.ship_index)
                or not battle.enemy_fire and battle._sides[gun.ship_index]!=own_side
                or abs(LAYERS.index(layer)-LAYERS.index(ship.motion.height_layer))>1):
                states[index]=replace(state,target=None);self.work[index]=Work();continue
            inv=inventories[gun.ship_index];w=next(w for w in inv._value['weapons'] if w['module_id']==gun.module_id)
            flight=battle._gun_flights[index][w['recipe_id'] or state.reload_recipe_id]
            ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
            origin,inherited=geometry(gun,ship)
            config=(gun,flight,ratio,layer,ship.motion.height_layer,state.reload_recipe_id,w['recipe_id'])
            if work.search_config!=config:
                work=replace(work,search_config=config,rejected=None,search_due=min(work.search_due,self.step))
            args=(battle,world,frame,contacts,gun,work,layer,origin,inherited,flight,ratio,self.step)
            if state.target and candidate(*args,state.target[0]) is not None:
                self.work[index]=replace(work,search_since=None);continue
            if state.target:work=replace(work,search_due=min(work.search_due,self.step))
            states[index]=replace(state,target=None)
            if self.step<work.search_due:
                self.work[index]=work;continue
            since=work.search_since if work.search_since is not None else self.step
            work=replace(work,search_since=since);self.work[index]=work
            priority=since+(0 if w['ready_rounds'] and not w['reload'] else SOLVE_PERIOD)
            pending.append((priority,since,index,args))
        self.counters['search_requests']=len(pending)
        self.counters['max_search_wait_steps']=max((self.step-row[1] for row in pending),default=0)
        for _,since,index,args in sorted(pending)[:budget]:
            candidates=[];gun=battle.guns[index];origin=args[7]
            for observer,target in contacts:
                if observer!=gun.ship_index:continue
                contact=candidate(*args,target)
                if contact is not None:
                    distance=hypot(*(p-o for p,o in zip(contact.position,origin)))
                    candidates.append((distance,world.ships[target].ship_id,target))
            target=min(candidates)[2] if candidates else None
            states[index]=replace(states[index],target=(target,None) if target is not None else None)
            self.work[index]=replace(self.work[index],search_since=None,search_due=next_phase(self.step,index,SEARCH_PERIOD))
            self.counters['searches']+=1
            self.counters['max_search_wait_steps']=max(self.counters['max_search_wait_steps'],self.step-since)
        self.counters['search_queue']=max(0,len(pending)-budget)
        return tuple(states),contacts

    def prepare(self,battle,world,available,inventories,states,contacts,frame,solutions):
        pending=[]; budget=max(MIN_BUDGET,ceil(len(battle.guns)/SOLVE_PERIOD))
        self.counters['solve_budget']=budget
        for index,(gun,state) in enumerate(zip(battle.guns,states)):
            work=self.work.get(index,Work());ship=world.ships[gun.ship_index]
            layer=state.attack_layer or ship.motion.height_layer
            contact=contacts.get((gun.ship_index,state.target[0])) if state.target else None
            track=frame.tracks.get((gun.ship_index,world.ships[state.target[0]].ship_id)) if state.target else None
            target_layer=track.target.layer if track and track.valid else world.ships[state.target[0]].motion.height_layer if state.target else None
            if (state.mode!='auto' or state.point_defense or not state.target or contact is None or target_layer!=layer
                or abs(LAYERS.index(layer)-LAYERS.index(ship.motion.height_layer))>1
                or available[gun.ship_index][gun.module_id] is not None or not battle._can_fire(ship,gun.ship_index)):
                self.counters['invalidated']+=work.shot is not None
                self.work[index]=replace(work,shot=None,solve_since=None);continue
            inv=inventories[gun.ship_index];w=next(w for w in inv._value['weapons'] if w['module_id']==gun.module_id)
            flight=battle._gun_flights[index][w['recipe_id'] or state.reload_recipe_id]
            ratio=1. if layer==ship.motion.height_layer else ballistics.CROSS_LAYER_SPEED
            origin,inherited=geometry(gun,ship);position,velocity=target_motion(battle,state,contact,self.step)
            signature=(state.target,gun,flight,ratio,layer,ship.motion.height_layer,state.reload_recipe_id,w['recipe_id'])
            shot=work.shot
            if shot and shot.signature!=signature:
                self.counters['invalidated']+=1;shot=None
            ready=bool(w['ready_rounds'] and not w['reload'] and inv._cooldown[gun.module_id]-self.step<=SOLVE_PERIOD)
            recent_time=shot.time if shot and self.step-shot.step<LEASE_STEPS else None
            aim=tracking_point(position,velocity,origin,inherited,flight,ratio,recent_time)
            self.aims[index]=(aim,False);self.reasons[index]='fire_control_pending'
            measured=replace(contact,step=self.step,position=position,velocity=velocity)
            if ready and not possible(battle,gun,ship,measured,origin,inherited,flight,ratio,self.step):
                self.counters['invalidated']+=shot is not None
                self.reasons[index]='target_unavailable'
                self.work[index]=replace(work,shot=None,solve_since=None)
                if state.target_policy=='automatic':self.reject(index,state.target[0])
                continue
            if not ready:
                self.counters['tracking_only']+=1
                self.work[index]=replace(work,shot=shot,solve_since=None);continue
            refresh=shot is None or self.step>=shot.due
            if shot and shot.contact!=contact:
                refresh=True;self.counters['refresh_observation']+=1
            if shot and hypot(*(a-b for a,b in zip(shot.inherited,inherited)))>2:
                refresh=True;self.counters['refresh_motion']+=1
            if shot and self.step-shot.step<LEASE_STEPS and shot.time is not None:
                projected,time=reproject(shot.time,position,velocity,origin,inherited,flight,ratio)
                if projected is not None:
                    self.aims[index]=(projected,True)
                    shot=replace(shot,time=time)
                    self.counters['reused_solutions']+=1
                    self.counters['solution_age_steps']=max(self.counters['solution_age_steps'],self.step-shot.step)
                else:
                    refresh=True;self.counters['reproject_rejected']+=1
            elif shot and shot.time is None:self.reasons[index]='target_unavailable'
            else:refresh=True
            work=replace(work,shot=shot)
            if refresh:
                since=work.solve_since if work.solve_since is not None else self.step
                work=replace(work,solve_since=since)
                expiry=shot.step+LEASE_STEPS-1 if shot else since
                # Age eventually outranks urgency, even for pre-shot refreshes.
                priority=min(since+(0 if self.step>=inv._cooldown[gun.module_id] else SOLVE_PERIOD),expiry)
                pending.append((priority,since,index,(state,contact,origin,inherited,flight,ratio,signature)))
            else:work=replace(work,solve_since=None)
            self.work[index]=work
        self.counters['precise_requests']=len(pending)
        self.counters['max_solve_wait_steps']=max((self.step-row[1] for row in pending),default=0)
        for _,since,index,args in sorted(pending)[:budget]:
            state,contact,origin,inherited,flight,ratio,signature=args
            aim=solutions.solve(battle,state,contact,self.step,origin,inherited,flight,ratio)
            time=flight_time(aim,origin,inherited,flight,ratio) if aim is not None else None
            self.work[index]=replace(self.work[index],solve_since=None,
                shot=Shot(signature,contact,self.step,next_phase(self.step,index,SOLVE_PERIOD),time,inherited))
            self.counters['max_solve_wait_steps']=max(self.counters['max_solve_wait_steps'],self.step-since)
            if aim is not None:
                self.aims[index]=(aim,True)
            else:
                self.aims[index]=(self.aims[index][0],False)
                self.reasons[index]='target_unavailable'
                if state.target_policy=='automatic':
                    self.work[index]=replace(self.work[index],rejected=(state.target[0],self.step+SEARCH_PERIOD),search_due=self.step+1)
        self.counters['solve_queue']=max(0,len(pending)-budget)

    def reject(self,index,target):
        work=self.work.get(index,Work())
        self.work[index]=replace(work,rejected=(target,self.step+SEARCH_PERIOD),search_due=self.step+1)

    def metrics(self,solutions):return dict(solutions.metrics(),**self.counters)
