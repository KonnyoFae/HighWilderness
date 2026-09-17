"""Continuous altitude, shared turn authority and one authoritative swept path.

Height is positive upwards (rain=0). Guided intervals prescribe bounded yaw
and pitch rates; tangential gravity is integrated analytically. At an unpowered
climb failure the remainder is a ballistic fall, retaining all three velocity
components. Eight-point Gaussian integration projects that same path to XY.
"""
from dataclasses import replace
from math import atan2, cos, sin, hypot, pi, sqrt, ceil
from .tactical_layers import LAYERS, SEGMENT_METRES

G = 9.8
DT = 1/60
NODES = ((.0198550717512319,.0506142681451881),(.101666761293187,.111190517226687),
         (.237233795041836,.156853322938944),(.408282678752175,.181341891689181),
         (.591717321247825,.181341891689181),(.762766204958164,.156853322938944),
         (.898333238706813,.111190517226687),(.980144928248768,.0506142681451881))


def altitude(layer):return (2-LAYERS.index(layer))*SEGMENT_METRES
def clamp(v,a,b):return max(a,min(b,v))
def wrap(a):return (a+pi)%(2*pi)-pi


def control(f, speed, aim, position, layer):
    p=f.profile;phase=p.phase(f.age)
    sample=f.last_sample if f.seeker_state in ('tracking','acquiring','memory','rescan','datalink') else None
    if f.seeker_state=='midcourse':sample=f.link_sample
    goal=sample.layer if sample else None
    identity=sample.id if sample else f.maneuver_target_id
    reason=None
    if goal and altitude(goal)>altitude(layer) and identity in f.failed_climb_targets:
        goal=layer;reason='climb_failed_for_target'
    if f.return_layer:
        goal=f.return_layer;reason='unpowered_climb_failed'
    z=altitude(layer) if f.altitude_m is None else f.altitude_m
    settled=layer if f.altitude_m is None else f.settled_layer
    if goal and goal!=layer:settled=None
    goal_z=altitude(goal) if goal else None
    if goal==settled and not f.return_layer:goal_z=None
    # Last-seen straight flight levels at its actual height, never at an anchor.
    desired_pitch=0.
    authority=p.max_g*G*min(1.,speed/p.low_speed_reference)**2
    if goal_z is not None and abs(goal_z-z)>1e-6:
        dz=goal_z-z
        distance=hypot(aim[0]-position[0],aim[1]-position[1]) if aim else 0.
        # Reserve horizontal distance for the final bounded pull-out. Aiming
        # the entire descent straight at the XY intercept would start leveling
        # too late and pass above it, losing even a stationary target's cone.
        leveling_lead=speed*speed*sin(p.maximum_pitch_rad)/max(authority,1e-9)
        desired_pitch=clamp(atan2(dz,max(1.,distance-leveling_lead)), -p.maximum_pitch_rad,p.maximum_pitch_rad)
        # Do not approach the layer asymptotically when the target keeps moving
        # away in XY. A small terminal crossing is followed by bounded leveling.
        desired_pitch=(1 if dz>0 else -1)*max(abs(desired_pitch),min(p.maximum_pitch_rad,pi/360))
        # Arrive with a shallow pitch, without resetting height or velocity.
        braking=sqrt(2*max(authority,1e-9)*abs(dz))/max(speed,1e-9)
        desired_pitch=clamp(desired_pitch,-braking,braking)
    yaw=pitch=0.
    if phase!='boost' and speed>1e-6:
        yaw=wrap(atan2(aim[1]-position[1],aim[0]-position[0])-f.heading)/DT if aim else 0.
        pitch=(desired_pitch-f.pitch_rad)/DT if not f.return_layer else 0.
        # Heading change near vertical contributes less actual angular motion.
        required=hypot(yaw*cos(f.pitch_rad),pitch)
        scale=min(1.,authority/(speed*required)) if required else 1.
        yaw*=scale;pitch*=scale
    loss=speed*hypot(yaw*cos(f.pitch_rad),pitch)
    thrust=(p.boost_acceleration if phase=='boost' else p.engine_acceleration if phase=='powered' else 0.)*f.ratio
    cap=(p.boost_cap if phase=='boost' else p.speed_cap)*f.ratio
    # The cap limits engine work, not gravity's contribution or existing energy.
    acceleration=max(-speed/DT,min(thrust-loss,max(0.,cap-speed)/DT))
    state='returning' if f.return_layer else 'climbing' if goal_z is not None and goal_z-z>.1 else 'diving' if goal_z is not None and goal_z-z<-.1 else 'leveling' if abs(f.pitch_rad)>1e-4 else 'level'
    if phase=='boost':state='boost';goal=None;goal_z=None
    return replace(f,phase=phase,altitude_m=z,angular_rate=yaw,pitch_rate=pitch,
                   acceleration=acceleration,goal_layer=goal,goal_altitude_m=goal_z,
                   maneuver_target_id=identity,maneuver_state=state,maneuver_reason=reason,settled_layer=settled)


class Trajectory:
    """Read-only interval shared by movement, layer boundaries and contacts."""
    k=0.

    def __init__(self, projectile, seconds=DT):
        self.projectile=projectile;self.f=projectile.missile
        self.origin=projectile.position;self.velocity=projectile.velocity;self.seconds=seconds
        self.speed=hypot(*projectile.velocity,self.f.vertical_velocity_mps)
        self.z=altitude(projectile.height_layer) if self.f.altitude_m is None else self.f.altitude_m
        self.failure=None
        self.fall_start=0. if self.f.return_layer else None
        if self.fall_start is None and self.f.phase=='coast':
            # A threshold crossing is an event inside the step, not a reset at
            # its end. Include leveling after loss of guidance in this rule.
            def failing(t):
                s,pitch,_=self.kinematics(t)
                return pitch>0 and s<=self.f.profile.minimum_climb_speed_mps
            if failing(0.):self.failure=0.
            elif failing(seconds):
                lo,hi=0.,seconds
                for _ in range(35):
                    mid=(lo+hi)/2
                    if failing(mid):hi=mid
                    else:lo=mid
                self.failure=hi
            if self.failure is not None:self.fall_start=self.failure
        self.fall_state=self.guided(self.fall_start) if self.fall_start is not None else None
        # Boundaries include every adjacent anchor reached during this interval.
        self.transitions=[]
        layer=projectile.height_layer
        last_t=0.;last_z=self.z
        for n in range(1,9):
            t=seconds*n/8;z=self.state(t)[0][2]
            direction=1 if z>last_z else -1
            index=LAYERS.index(layer)-direction
            while 0<=index<len(LAYERS):
                anchor=altitude(LAYERS[index])
                if not (last_z<anchor<=z if direction>0 else z<=anchor<last_z):break
                lo,hi=last_t,t
                for _ in range(35):
                    mid=(lo+hi)/2;zm=self.state(mid)[0][2]
                    if (zm<anchor)==(direction>0):lo=mid
                    else:hi=mid
                layer=LAYERS[index];self.transitions.append((hi/seconds,layer));index-=direction
            last_t=t;last_z=z

    def kinematics(self,t):
        f=self.f;pitch=f.pitch_rad+f.pitch_rate*t
        # Stable sinc form of integral sin(pitch + pitch_rate*t).
        half=f.pitch_rate*t/2
        gravity=-G*t*sin(f.pitch_rad+half)*(sin(half)/half if abs(half)>1e-8 else 1.)
        return self.speed+f.acceleration*t+gravity,pitch,f.heading+f.angular_rate*t

    def guided(self,t):
        delta=[0.,0.,0.]
        for node,weight in NODES:
            s,pitch,yaw=self.kinematics(t*node)
            for i,v in enumerate((s*cos(pitch)*cos(yaw),s*cos(pitch)*sin(yaw),s*sin(pitch))):delta[i]+=t*weight*v
        s,pitch,yaw=self.kinematics(t)
        return ((self.origin[0]+delta[0],self.origin[1]+delta[1],self.z+delta[2]),
                (s*cos(pitch)*cos(yaw),s*cos(pitch)*sin(yaw),s*sin(pitch)))

    def state(self,t):
        if self.fall_start is not None and t>=self.fall_start:
            pos,v=self.fall_state;dt=t-self.fall_start
            if abs(self.f.angular_rate)>1e-12 and dt>0:
                # During the forced return, gravity owns vertical curvature;
                # remaining aerodynamic authority can still steer horizontally.
                # Turn losses decelerate along the full velocity, once only.
                omega=self.f.angular_rate
                def derivative(s):
                    vx,vy,vz=s[3:];speed=hypot(vx,vy,vz)
                    drag=abs(omega)*hypot(vx,vy)/speed if speed>1e-9 else 0.
                    return (vx,vy,vz,-omega*vy-drag*vx,omega*vx-drag*vy,-G-drag*vz)
                def add(a,b,h):return tuple(x+h*y for x,y in zip(a,b))
                state=(*pos,*v);count=max(1,ceil(dt*240));h=dt/count
                for _ in range(count):
                    a=derivative(state);b=derivative(add(state,a,h/2));c=derivative(add(state,b,h/2));d=derivative(add(state,c,h))
                    state=tuple(x+h*(aa+2*bb+2*cc+dd)/6 for x,aa,bb,cc,dd in zip(state,a,b,c,d))
                return state[:3],state[3:]
            return ((pos[0]+v[0]*dt,pos[1]+v[1]*dt,pos[2]+v[2]*dt-.5*G*dt*dt),(v[0],v[1],v[2]-G*dt))
        return self.guided(t)

    @property
    def maximum_speed(self):return self.speed+(abs(self.f.acceleration)+G)*self.seconds
    @property
    def curvature(self):return abs(self.f.acceleration)+G+self.maximum_speed*(abs(self.f.angular_rate)+abs(self.f.pitch_rate))
    def at(self,fraction):
        p,v=self.state(self.seconds*fraction)
        return p[:2],v[:2]
    def vertical_speed(self,fraction):return self.state(self.seconds*fraction)[1][2]
    def layer_at(self,fraction):
        layer=self.projectile.height_layer
        for t,value in self.transitions:
            if fraction+1e-12<t:break
            layer=value
        return layer

    def arrival(self):
        p=self.projectile;f=self.f;pos,v=self.state(self.seconds)
        layer=self.layer_at(1.)
        failed=f.failed_climb_targets
        returning=f.return_layer
        if self.failure is not None:
            returning=self.layer_at(self.failure/self.seconds)
            if f.maneuver_target_id is not None and f.maneuver_target_id not in failed:failed=(*failed,f.maneuver_target_id)
        if returning and pos[2]<=altitude(returning)+1e-8 and v[2]<=0:returning=None
        settled=f.settled_layer
        if layer!=p.height_layer:settled=layer
        if f.goal_layer==layer and (self.z-altitude(layer))*(pos[2]-altitude(layer))<=0:settled=layer
        horizontal=hypot(*v[:2])
        updated=replace(f,altitude_m=pos[2],vertical_velocity_mps=v[2],
                        pitch_rad=atan2(v[2],horizontal),heading=atan2(v[1],v[0]) if horizontal>1e-9 else f.heading,
                        return_layer=returning,failed_climb_targets=failed,settled_layer=settled)
        return replace(p,previous=p.position,position=pos[:2],velocity=v[:2],height_layer=layer,missile=updated)


def view(p):
    f=p.missile;z=altitude(p.height_layer) if f.altitude_m is None else f.altitude_m
    target=f.return_layer or f.goal_layer
    return dict(altitude_m=z,pitch_deg=f.pitch_rad*180/pi,maneuver_state=f.maneuver_state,
                maneuver_reason=f.maneuver_reason,maneuver_target_layer=target,
                vertical_remaining_m=abs(altitude(target)-z) if target and (f.return_layer or f.goal_altitude_m is not None) else None,
                failed_climb_targets=f.failed_climb_targets)
