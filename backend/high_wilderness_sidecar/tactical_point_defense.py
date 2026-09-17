"""Observed incoming threats, shared in-flight commitments and real gun work."""
from dataclasses import replace
from functools import lru_cache
from math import atan2, ceil, cos, hypot, sin, sqrt

from . import tactical_ballistics as ballistics
from .tactical_interception import policy
from .tactical_layers import LAYERS


class Trajectory:
    """Disposable prediction cache; retains authoritative 1/60 s drag steps."""
    def __init__(self,profile,speed):
        self.profile=profile;self.distances=[0.];self.speeds=[speed]

    def at(self,seconds):
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

    def sensor_sources(self,index,position,world,available):
        b=self.battle;origin=world.ships[index].motion.position_world_m.to_list()
        distance=hypot(*(p-o for p,o in zip(position,origin)))
        radars=tuple(k for k in b._radars[index] if not available[index][k] and
            distance<=b._capabilities[index][k]['maximum_instrumented_range_m']*b.crew_efficiency(world,index,k,'sensor.search'))
        controls=tuple(k for k in b._controllers[index] if not available[index][k] and
            distance<=b._capabilities[index][k]['maximum_lock_range_m']*b.crew_efficiency(world,index,k,'fire_control.solution'))
        return radars if controls else (),sum(b._capabilities[index][k]['simultaneous_channels'] for k in controls)

    def observe(self,world,available,projectiles):
        b=self.battle;step=world.fixed_step;contacts={};threats=[];predictions={}
        observers={g.ship_index for i,g in enumerate(b.guns) if self.capable[i] and b.states[i].point_defense
                   and (b.enemy_fire or b._sides[g.ship_index]==b._sides[b._direct_index])
                   and b._can_fire(world.ships[g.ship_index],g.ship_index)}
        for p in projectiles:
            if p.durability is None or p.durability<=0:continue
            for observer in sorted(observers):
                if b._sides[observer]==b.damage.sides.get(p.ship_id):continue
                key=observer,p.id
                sources,_=self.sensor_sources(observer,p.position,world,available)
                if not sources:continue
                old=self.contacts.get(key)
                if old and step-old[0]<self.policy['observation_steps']:
                    stamp,measured,collisions=old
                else:
                    # The current normal radar policy has zero measurement error.
                    # Between samples the firing solution only sees this contact.
                    stamp,measured=step,p
                    identity=b._sides[observer],p.id
                    if identity not in predictions:predictions[identity]=self.predict_collisions(observer,p,world)
                    collisions=predictions[identity]
                contacts[key]=(stamp,measured,collisions)
                for sid,arrival in collisions:
                    endangered=next(s for s in world.ships if s.ship_id==sid)
                    if endangered.motion.height_layer!=p.height_layer or endangered.command.lifecycle.physical_status!='operational':continue
                    remaining=arrival-(step-stamp)/60
                    if remaining>0:threats.append(dict(observer=observer,projectile_id=p.id,ship_id=sid,
                        remaining_s=remaining,durability=p.durability,height_layer=p.height_layer))
        return contacts,tuple(threats)

    def predict_collisions(self,observer,p,world):
        b=self.battle
        seconds=min(self.policy['prediction_seconds'],(p.expires-world.fixed_step)/60)
        if seconds<=0:return ()
        # Predicted world path uses the same drag law; ship orders are not future truth.
        points=[(0.,p.position)];t=0.
        while t<seconds-1e-9:
            t=min(seconds,t+.1);point,_=predict(p,t);points.append((t,point))
        collisions=[]
        for n,ship in enumerate(world.ships):
            if b._sides[n]!=b._sides[observer] or ship.motion.height_layer!=p.height_layer or (
                    ship.motion.hull_integrity_fraction<=0 or ship.command.lifecycle.physical_status!='operational'):continue
            m=ship.motion;path=[];radius=b.damage.radius[n]
            for t,(x,y) in points:
                x-=m.position_world_m.x+m.velocity_world_mps.x*t
                y-=m.position_world_m.y+m.velocity_world_mps.y*t
                angle=-m.heading_rad-m.yaw_rate_radps*t;c,s=cos(angle),sin(angle)
                path.append((t,(c*x-s*y,s*x+c*y)))
            if min(v[0] for _,v in path)>radius or max(v[0] for _,v in path)<-radius or (
                    min(v[1] for _,v in path)>radius or max(v[1] for _,v in path)<-radius):continue
            # Geometry is checked against all decks; only actual future impact
            # will sample its damage deck. An inflated ship circle cannot qualify.
            hit=b.damage.contacts_on_path(n,ship,path)
            if hit:collisions.append((ship.ship_id,min(h[0] for h in hit.values())))
        return tuple(sorted(collisions,key=lambda row:(row[1],row[0])))[:1]

    def choose(self,index,state,world,available,projectiles,contacts,threats,channels,locks,inv):
        from .tactical_gunnery import add,difference,rotate,wrap
        b=self.battle;gun=b.guns[index];ship=world.ships[gun.ship_index];m=ship.motion
        if not self.sensor_sources(gun.ship_index,m.position_world_m.to_list(),world,available)[0]:
            return None,'defense_sensor_unavailable'
        layer=state.attack_layer or m.height_layer
        if abs(LAYERS.index(layer)-LAYERS.index(m.height_layer))>1:return None,'layer_out_of_reach'
        known={p.id:p for p in projectiles}
        candidates=[]
        for row in threats:
            if row['observer']!=gun.ship_index or row['height_layer']!=layer:continue
            p=known.get(row['projectile_id'])
            if not p:continue
            stamp,measured,_=contacts[gun.ship_index,p.id]
            position,velocity=predict(measured,(world.fixed_step-stamp)/60)
            measured=replace(measured,position=position,velocity=velocity)
            distance=hypot(*(v-o for v,o in zip(position,m.position_world_m.to_list())))
            high=measured.flight_profile and measured.flight_profile.caliber_mm>=self.policy['durability_tiers'][0][0] and hypot(*velocity)>=self.policy['high_speed_mps']
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
            reserved=sum(q.interception_damage for q in projectiles if q.interception_target_id==pid
                and q.interception_expected_step is not None and world.fixed_step<=q.interception_expected_step
                and b.damage.sides.get(q.ship_id)==b._sides[gun.ship_index])
            if reserved+1e-8>=known[pid].durability:reason='defense_covered';continue
            sources,capacity=self.sensor_sources(gun.ship_index,p.position,world,available)
            if not sources:reason='defense_sensor_unavailable';continue
            key=gun.ship_index,pid
            if key not in locks and channels.get(gun.ship_index,0)>=capacity:reason='channels_busy';continue
            if not gun.minimum_range<=distance<=maximum:continue
            solution=firing_solution(origin,inherited,p,profile,ratio,min(row['remaining_s'],profile.lifetime_steps/60,(p.expires-world.fixed_step)/60))
            if solution is None:continue
            aim,seconds=solution
            delta=difference(aim,origin);local=rotate(delta,-m.heading_rad)
            desired=wrap(atan2(local[0],local[1])-gun.rotation)
            if not gun.minimum<=desired<=gun.maximum or b._hull_blocked(gun,desired):continue
            if not gun.minimum_range<=hypot(*delta)<=maximum:continue
            if key not in locks:locks.add(key);channels[gun.ship_index]=channels.get(gun.ship_index,0)+1
            return dict(projectile_id=pid,aim=aim,sources=sources,priority=priority,
                expected_step=world.fixed_step+ceil(seconds*60)+1,
                needed_rounds=ceil(max(0.,known[pid].durability-reserved)/self.policy['round_damage'])),None
        return None,reason

    def commit(self,contacts,threats,events):
        self.contacts=contacts;self.threats=threats
        self.recent=(self.recent+events)[-32:];self.hits+=len(events);self.kills+=sum(e['intercepted'] for e in events)

    def view(self):
        world=self.battle.session.world
        return dict(policy=self.policy,hits=self.hits,intercepted=self.kills,recent=self.recent,
            threats=[dict(row,observer_ship_id=world.ships[row['observer']].ship_id) for row in self.threats])
