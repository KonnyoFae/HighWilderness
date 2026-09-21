"""Committed missile display samples; never searches, steers or advances combat."""
from collections import deque
from math import atan2, hypot, pi

from .shell_presentation import curve_error, position, WINDOW_STEPS, ERROR_M
from .missile_maneuver import altitude


def wrap(value):
    return (value+pi) % (2*pi)-pi


def sample(p, step):
    f = p.missile
    return (step, *p.position, *p.velocity, 1,
            altitude(p.height_layer) if f.altitude_m is None else f.altitude_m,
            f.vertical_velocity_mps, f.heading, f.pitch_rad)


def state(p, step):
    f = p.missile
    return dict(step=step, height_layer=p.height_layer, phase=f.phase,
                seeker_state=f.seeker_state, target_id=f.target_id,
                maneuver_state=f.maneuver_state, maneuver_reason=f.maneuver_reason,
                maneuver_target_layer=f.return_layer or f.goal_layer,
                vertical_goal=bool(f.return_layer or f.goal_altitude_m is not None))


def simplify(samples, boundaries=()):
    samples = list(samples)
    if not samples:
        return []
    xy = [p[:5] for p in samples]
    z = [(p[0],p[6],0.,p[7],0.) for p in samples]
    result = [samples[0][:5]+(1,)+samples[0][6:]]
    def visit(first, last):
        a,b = samples[first],samples[last]
        if b[0] <= a[0]:
            result.append(b[:5]+(1,)+b[6:]); return
        error = hypot(curve_error(xy[first],xy[last],xy[first:last+1]),
                      curve_error(z[first],z[last],z[first:last+1]))
        angular_error = max((abs(wrap(p[axis]-(a[axis]+wrap(b[axis]-a[axis])*(p[0]-a[0])/(b[0]-a[0]))))
                             for p in samples[first:last+1] for axis in (8,9)),default=0.)
        if error <= ERROR_M and angular_error <= pi/720:
            result.append(b[:5]+(0,)+b[6:])
        elif last == first+1:
            result.append(b[:5]+(1,)+b[6:])
        else:
            middle = (first+last)//2
            visit(first,middle); visit(middle,last)
    # Keep both sides of phase/ownership/guidance changes, including reversals.
    cuts = {0,len(samples)-1}
    for i,p in enumerate(samples):
        if p[0] in boundaries:
            cuts.update((max(0,i-1),i))
    cuts = sorted(cuts)
    for a,b in zip(cuts,cuts[1:]):
        visit(a,b)
    return result


def legacy_projectile(value):
    if not value.get('missile_samples'):
        return value
    samples = value['missile_samples']
    return {**{k:v for k,v in value.items() if k not in ('missile_samples','missile_states','missile_identity')},
            'trajectory':[(step,*position(samples,step)) for step in range(int(samples[0][0]),int(samples[-1][0])+1)]}


class MissileHistory:
    def __init__(self, step, projectiles, identities):
        self.active = {}; self.finished = deque(); self.completed = (); self.published = {}
        self.record(step,projectiles,{}, {})
        for identity,row in self.active.items():
            previous = identities.get(identity)
            if previous:
                row.update({k:previous[k] for k in ('born_step','origin_m','expires_step')})
        self.publish(step)

    @staticmethod
    def _seal(row):
        points = simplify(row['pending'],{s['step'] for s in row['states']})
        if points:
            knots = row['knots']
            if knots and knots[-1][0] == points[0][0]:
                knots.extend(points[1:])
            else:
                knots.extend(points)
            while len(knots)>2 and knots[1][0]<points[-1][0]-WINDOW_STEPS:
                knots.pop(0)
            while len(row['states'])>1 and row['states'][1]['step']<knots[0][0]:
                row['states'].popleft()
            row['pending'] = deque([points[-1]],maxlen=WINDOW_STEPS+1)

    @staticmethod
    def _snapshot(row):
        return {**{k:v for k,v in row.items() if k not in ('knots','pending','states','projectile')},
                'missile_samples':tuple(row['knots']), 'missile_states':tuple(row['states'])}

    def record(self, step, projectiles, impacts, terminals):
        current = {p.id:p for p in projectiles}; completed = []
        for identity in self.active.keys()-current.keys():
            row = self.active.pop(identity); self._seal(row)
            event = impacts.get(identity) or terminals.get(identity)
            endpoint = event['position_m'] if event else row['position_m']
            last = row['knots'][-1]; final = (step,*endpoint,*last[3:5],1,*last[6:])
            layer = row['height_layer']
            if event and 'impact_fraction' in event:
                # Replay only a committed terminal's own interval, using the same
                # cached authoritative segment. No new simulation step is run.
                from .tactical_ballistics import flight_segment, layer_at
                p = row['projectile']; path = flight_segment(p); t = event['impact_fraction']
                _,velocity = path.at(t)
                if hasattr(path,'state'):
                    pos3,vel3 = path.state(path.seconds*t); z,vz = pos3[2],vel3[2]
                else:
                    z,vz = last[6],last[7]
                heading = atan2(velocity[1],velocity[0]) if hypot(*velocity)>1e-9 else last[8]
                final = (step,*endpoint,*velocity,1,z,vz,heading,atan2(vz,hypot(*velocity)))
                layer = event.get('height_layer') or layer_at(p,path,t) or layer
            row['knots'].append(final)
            if layer != row['height_layer']:
                row['states'].append(dict(row['states'][-1],step=step,height_layer=layer))
            hit = impacts.get(identity)
            row.update(end_step=step,end_m=endpoint,height_layer=layer,
                       impact=None if hit is None else dict(ship_id=hit['ship_id'],outcome=hit['outcome']))
            terminal = self._snapshot(row); self.finished.append(terminal); completed.append(terminal)
        for identity,p in current.items():
            if identity not in self.active:
                f = p.missile
                self.active[identity] = dict(id=identity,ship_id=p.ship_id,kind='missile',born_step=step,
                    origin_m=p.position,expires_step=p.expires,maximum_durability=p.maximum_durability,
                    missile_identity=dict(model_id=f.profile.model_id,warhead_id=f.warhead,
                                          interceptor=f.profile.interceptor,born_step=f.born_step),
                    knots=[],pending=deque(maxlen=WINDOW_STEPS+1),states=deque())
            row = self.active[identity]; metadata = state(p,step)
            if not row['states'] or any(row['states'][-1][k]!=v for k,v in metadata.items() if k!='step'):
                row['states'].append(metadata)
            # Bound state changes even when publications are temporarily absent.
            while len(row['states'])>1 and row['states'][1]['step']<step-2*WINDOW_STEPS:
                row['states'].popleft()
            row.update(position_m=p.position,velocity_mps=p.velocity,height_layer=p.height_layer,projectile=p)
            row['pending'].append(sample(p,step))
        while self.finished and (self.finished[0]['end_step']<step-120 or len(self.finished)>128):
            self.finished.popleft()
        self.completed = tuple(completed)

    def publish(self, step):
        for row in self.active.values():
            self._seal(row)
        self.published = {identity:self._snapshot(row) for identity,row in self.active.items()}
