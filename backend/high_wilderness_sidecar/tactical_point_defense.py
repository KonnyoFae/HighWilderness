"""Observed incoming threats, shared in-flight commitments and real gun work."""
from functools import lru_cache
from math import atan2, ceil, hypot, sqrt

from . import tactical_ballistics as ballistics
from .tactical_interception import policy
from .tactical_layers import LAYERS
from . import projectile_observation as observed


class Trajectory:
    """Disposable prediction cache; retains authoritative 1/60 s drag steps."""
    def __init__(self,profile,speed):
        self.profile=profile;self.distances=[0.];self.speeds=[speed]

    def at(self,seconds):
        if self.profile is None or not self.profile.drag:
            return max(0.,seconds)*self.speeds[0],self.speeds[0]
        n=int(max(0.,seconds)*60)
        while len(self.speeds)<=n:
            speed=self.speeds[-1]
            k=ballistics.step_coefficient(self.profile,speed,1/60) if self.profile else 0.
            distance,speed=ballistics.scalar(speed,1/60,k)
            self.distances.append(self.distances[-1]+distance);self.speeds.append(speed)
        dt=max(0.,seconds-n/60);speed=self.speeds[n]
        k=ballistics.step_coefficient(self.profile,speed,dt) if self.profile else 0.
        distance,speed=ballistics.scalar(speed,dt,k)
        return self.distances[n]+distance,speed


@lru_cache(maxsize=256)
def trajectory(profile,speed):return Trajectory(profile,speed)


def predict(projectile,seconds):
    initial=hypot(*projectile.velocity)
    if initial<=1e-12:return projectile.position,projectile.velocity
    distance,speed=trajectory(projectile.flight_profile,initial).at(seconds)
    return (tuple(p+v/initial*distance for p,v in zip(projectile.position,projectile.velocity)),
            tuple(v/initial*speed for v in projectile.velocity))


def travel(profile,speed,seconds):
    return trajectory(profile,speed).at(seconds)[0]


def firing_solution(origin,inherited,target,profile,ratio,limit):
    """Match both timed drag trajectories, including the 3 m muzzle offset."""
    muzzle=profile.muzzle_speed_mps*ratio
    def residual(t):
        position,_=predict(target,t)
        delta=tuple(p-o+v*3/muzzle for p,o,v in zip(position,origin,inherited))
        distance=hypot(*delta)
        if distance<1e-10:return -1.,None
        direction=tuple(v/distance for v in delta)
        along=sum(d*v for d,v in zip(direction,inherited))
        disc=muzzle*muzzle-sum(v*v for v in inherited)+along*along
        if disc<0:return float('inf'),None
        speed=along+sqrt(disc)
        if speed<=0:return float('inf'),None
        barrel=tuple((d*speed-v)/muzzle for d,v in zip(direction,inherited))
        return distance-travel(profile,speed,t)-3*speed/muzzle,tuple(o+d*distance for o,d in zip(origin,barrel))
    lo=0.;hi=0.
    # A short scan also finds inbound targets whose path crosses the muzzle.
    while hi<limit:
        hi=min(limit,hi+.1)
        if residual(hi)[0]<=0:
            for _ in range(21):
                mid=(lo+hi)/2
                if residual(mid)[0]>0:lo=mid
                else:hi=mid
            aim=residual(hi)[1]
            return (aim,hi) if aim else None
        lo=hi
    return None


class PointDefense:
    def __init__(self,battle):
        self.battle=battle;self.policy=policy()
        self.capable=tuple(all(p.caliber_mm==30 for p in flights.values()) for flights in battle._gun_flights)
        self.contacts={};self.threats=();self.recent=();self.hits=0;self.kills=0
        self.forecasts={};self.prediction_metrics={}

    def observe(self,world,available,projectiles,frame=None,*,prediction=None):
        b=self.battle;step=world.fixed_step;contacts={};threats=[]
        prediction=prediction or self.begin(world)
        observers={g.ship_index for i,g in enumerate(b.guns) if self.capable[i] and b.states[i].point_defense
                   and (b.enemy_fire or b._sides[g.ship_index]==b._sides[b._direct_index])
                   and b._can_fire(world.ships[g.ship_index],g.ship_index)}
        observers.update(n for n,mid in b.missiles.states if b._can_fire(world.ships[n],n)
            and (b.enemy_fire or b._sides[n]==b._sides[b._direct_index]))
        for p in projectiles:
            if p.durability is None or p.durability<=0:continue
            for observer in sorted(observers):
                if b._sides[observer]==b.damage.sides.get(p.ship_id):continue
                key=observer,p.id
                sources,_=b.observation.defense_sources(observer,p.id,world,available,frame)
                if not sources:continue
                track=(frame or b.observation.frame).tracks[key]
                # Only the frozen sensor sample reaches prediction; the live
                # projectile above is used solely to prune destroyed identities.
                stamp,measured=track.step,track.target.payload
                age=max(0,step-stamp)/60
                current=observed.extrapolate(measured,age)
                collisions=tuple((sid,t+age) for sid,t in self.predict_collisions(
                    observer,measured,world,prediction=prediction,stamp=stamp))
                contacts[key]=(stamp,measured,collisions)
                for sid,arrival in collisions:
                    endangered=next(s for s in world.ships if s.ship_id==sid)
                    if endangered.command.lifecycle.physical_status!='operational':continue
                    remaining=arrival-(step-stamp)/60
                    if remaining>0:threats.append(dict(observer=observer,projectile_id=p.id,ship_id=sid,
                        remaining_s=remaining,durability=measured.durability,height_layer=current.height_layer,
                        impact_layer=endangered.motion.height_layer))
        return contacts,tuple(threats)

    def begin(self,world):
        from .tactical_defense_prediction import Plan
        return Plan(self.battle,world,self.forecasts)

    def predict_collisions(self,observer,p,world,*,prediction=None,stamp=None):
        return (prediction or self.begin(world)).collisions(observer,p,stamp)

    def choose(self,index,state,world,available,projectiles,contacts,threats,channels,locks,inv,frame=None):
        from .tactical_gunnery import add,difference,rotate,wrap
        b=self.battle;gun=b.guns[index];ship=world.ships[gun.ship_index];m=ship.motion
        if not b.observation.defense_ready(gun.ship_index,gun.module_id,world,available):
            return None,'defense_sensor_unavailable'
        layer=state.attack_layer or m.height_layer
        if abs(LAYERS.index(layer)-LAYERS.index(m.height_layer))>1:return None,'layer_out_of_reach'
        known={p.id:p for p in projectiles}
        candidates=[]
        for row in threats:
            if row['observer']!=gun.ship_index:continue
            p=known.get(row['projectile_id'])
            if not p:continue
            stamp,measured,_=contacts[gun.ship_index,p.id]
            measured=observed.extrapolate(measured,(world.fixed_step-stamp)/60)
            position,velocity=measured.position,measured.velocity
            distance=hypot(*(v-o for v,o in zip(position,m.position_world_m.to_list())))
            high=measured.flight_profile and measured.flight_profile.caliber_mm>=self.policy['durability_tiers'][0][0] and observed.total_speed(measured)>=self.policy['high_speed_mps']
            priority=0 if high else 1 if row['ship_id']==ship.ship_id else 2
            candidates.append((priority,distance,p.id,row,measured))
        if not candidates:return None,'defense_waiting'
        offset=rotate(gun.anchor,m.heading_rad);origin=add(tuple(m.position_world_m.to_list()),offset)
        inherited=add(tuple(m.velocity_world_mps.to_list()),(-m.yaw_rate_radps*offset[1],m.yaw_rate_radps*offset[0]))
        w=next(w for w in inv._value['weapons'] if w['module_id']==gun.module_id)
        profile=b._gun_flights[index][w['recipe_id'] or state.reload_recipe_id]
        ratio=1. if layer==m.height_layer else ballistics.CROSS_LAYER_SPEED
        maximum=min(gun.maximum_range,ballistics.reference_range(profile,ratio))
        reason='defense_waiting'
        for priority,distance,pid,row,p in sorted(candidates,key=lambda v:v[:3]):
            from .tactical_defense import commitments
            reserved=sum(q.interception_damage for q in commitments(b,gun.ship_index,pid,projectiles,world,available,frame=frame))
            if reserved+1e-8>=p.durability:reason='defense_covered';continue
            sources,capacity=b.observation.defense_sources(gun.ship_index,pid,world,available,frame,gun.module_id)
            if not sources:reason='defense_sensor_unavailable';continue
            independent=(gun.ship_index,gun.module_id) in b.observation.integrated and gun.module_id in sources
            key=(gun.ship_index,gun.module_id) if independent else gun.ship_index
            lock_key=key,pid
            if lock_key not in locks and channels.get(key,0)>=capacity:reason='channels_busy';continue
            if not gun.minimum_range<=distance<=maximum:continue
            solution=firing_solution(origin,inherited,p,profile,ratio,min(row['remaining_s'],profile.lifetime_steps/60,(p.expires-world.fixed_step)/60))
            if solution is None:continue
            aim,seconds=solution
            if observed.layer_at(p,seconds)!=layer:continue
            delta=difference(aim,origin);local=rotate(delta,-m.heading_rad)
            desired=wrap(atan2(local[0],local[1])-gun.rotation)
            if not gun.minimum<=desired<=gun.maximum or b._hull_blocked(gun,desired):continue
            if not gun.minimum_range<=hypot(*delta)<=maximum:continue
            if lock_key not in locks:locks.add(lock_key);channels[key]=channels.get(key,0)+1
            return dict(projectile_id=pid,aim=aim,sources=sources,priority=priority,
                expected_step=world.fixed_step+ceil(seconds*60)+1,
                needed_rounds=ceil(max(0.,p.durability-reserved)/self.policy['round_damage'])),None
        return None,reason

    def commit(self,contacts,threats,events,*,prediction=None):
        self.contacts=contacts;self.threats=threats
        if prediction is not None:
            self.forecasts=prediction.values;self.prediction_metrics=dict(prediction.metrics)
        self.recent=(self.recent+events)[-32:];self.hits+=len(events);self.kills+=sum(e['intercepted'] for e in events)

    def view(self):
        world=self.battle.session.world
        return dict(policy=self.policy,hits=self.hits,intercepted=self.kills,recent=self.recent,
            threats=[dict(row,observer_ship_id=world.ships[row['observer']].ship_id) for row in self.threats])
