"""Bounded, disposable flight history, recorded only after committed steps.

This is display data, never a checkpoint, hit resolver or reliable game event.
Finished flights survive several publications so a shot born and hit between
two reads can still be drawn. Active flights retain their launch identity.
"""
from collections import deque

HISTORY_STEPS = 120
MAX_FINISHED_FLIGHTS = 128
PATH_STEPS = 32  # covers interpolation delay and bounded publication jitter
PATH_ERROR_M = .05  # display-only deviation from the committed time samples


def display_path(points):
    """Time-aware simplification: keep turns AND changes of speed.

    Geometric collinearity alone would erase acceleration along a straight path.
    Retain the original endpoint tuples and never change simulation history.
    """
    if len(points) <= 2:return list(points)
    keep={0,len(points)-1};pending=[(0,len(points)-1)]
    while pending:
        first,last=pending.pop();a,b=points[first],points[last]
        if last-first<=1:continue
        if b[0]<=a[0]:
            keep.update(range(first,last+1));continue
        worst=PATH_ERROR_M**2;split=None
        for i in range(first+1,last):
            p=points[i];f=(p[0]-a[0])/(b[0]-a[0])
            error=(p[1]-(a[1]+f*(b[1]-a[1])))**2+(p[2]-(a[2]+f*(b[2]-a[2])))**2
            if error>worst:worst=error;split=i
        if split is not None:
            keep.add(split);pending.extend(((first,split),(split,last)))
    return [points[i] for i in sorted(keep)]


class FlightHistory:
    def __init__(self):
        self.active = {}
        self.finished = deque()
        self.dropped = 0
        self.completed = ()
        self.shells = None
        self.missiles = None

    def enable_missile_stream(self, step, projectiles):
        from .missile_display import MissileHistory
        if self.missiles is None:
            self.missiles = MissileHistory(step,[p for p in projectiles if getattr(p,'missile',None)],self.active)
            self.active = {k:p for k,p in self.active.items() if p['kind']!='missile'}

    def enable_shell_stream(self, step):
        from .shell_presentation import ShellHistory
        if self.shells is not None:
            return
        self.shells = ShellHistory(step, [p for p in self.active.values() if p['kind']=='shell'],
                                  [p for p in self.finished if p['kind']=='shell'])
        self.active = {k:p for k,p in self.active.items() if p['kind']!='shell'}
        self.finished = deque(p for p in self.finished if p['kind']!='shell')

    def publish_shells(self, step):
        if self.shells is not None:
            self.shells.publish(step)
        if self.missiles is not None:
            self.missiles.publish(step)

    def active_flights(self):
        return (*self.active.values(), *(() if self.shells is None else self.shells.published.values()),
                *(() if self.missiles is None else self.missiles.published.values()))

    def finished_flights(self):
        return (*self.finished, *(() if self.shells is None else self.shells.finished),
                *(() if self.missiles is None else self.missiles.finished))

    def release_finished(self):
        """Once transferred, the stream owns the sole bounded terminal window."""
        self.finished.clear()
        for history in (self.shells,self.missiles):
            if history is not None:history.finished.clear()
        self.completed = ()

    def record(self, step, projectiles, hits=(), expired=()):
        completed = []
        current = {p.id: p for p in projectiles}
        impacts = {h['projectile_id']: h for h in hits if h['step'] == step}
        terminals = {h['projectile_id']: h['position_m'] for h in expired}
        if self.missiles is not None:
            self.missiles.record(step,[p for p in current.values() if getattr(p,'missile',None)],impacts,
                                 {h['projectile_id']:h for h in expired})
            completed.extend(self.missiles.completed)
            current = {identity:p for identity,p in current.items() if not getattr(p,'missile',None)}
        if self.shells is not None:
            self.shells.record(step, [p for p in current.values() if not getattr(p,'missile',None)], impacts, terminals)
            completed.extend(self.shells.completed)
            current = {identity:p for identity,p in current.items() if getattr(p,'missile',None)}
        for identity, flight in self.active.items():
            if identity in current:
                continue
            hit = impacts.get(identity)
            # Only committed collision/expiry data supplies the terminal point.
            endpoint=hit['position_m'] if hit else terminals.get(identity,flight['position_m'])
            terminal = dict(flight, end_step=step,
                end_m=endpoint,trajectory=[*flight['trajectory'][-PATH_STEPS:],(step,*endpoint)],
                impact=None if hit is None else dict(ship_id=hit['ship_id'], outcome=hit['outcome']))
            self.finished.append(terminal); completed.append(terminal)
        self.active = {identity: dict(
            self.active.get(identity, dict(id=p.id, ship_id=p.ship_id, born_step=step,
                origin_m=p.position, expires_step=p.expires, height_layer=p.height_layer)),
            position_m=p.position, velocity_mps=p.velocity,kind='missile' if getattr(p,'missile',None) else 'shell',
            maximum_durability=p.maximum_durability,
            trajectory=[*self.active.get(identity,{}).get('trajectory',())[-PATH_STEPS:],(step,*p.position)]) for identity, p in current.items()}
        while self.finished and self.finished[0]['end_step'] < step-HISTORY_STEPS:
            self.finished.popleft()
        while len(self.finished) > MAX_FINISHED_FLIGHTS:
            self.finished.popleft()
            self.dropped += 1
        self.completed = tuple(completed)

    def view(self):
        return dict(interface='gaotian.tactical-presentation/v1alpha1',
            finished_projectiles=[dict(p,trajectory=display_path(p['trajectory'])) if 'trajectory' in p else dict(p)
                                  for p in self.finished_flights()], dropped_projectiles=self.dropped)

    def launch(self, identity):
        if self.missiles is not None and identity in self.missiles.published:
            flight = self.missiles.published[identity]
            return {key:flight[key] for key in ('born_step','origin_m','expires_step','missile_identity','missile_samples','missile_states')}
        if self.shells is not None and identity in self.shells.published:
            flight = self.shells.published[identity]
            return {key:flight[key] for key in ('born_step', 'origin_m', 'expires_step', 'shell_samples')}
        flight = self.active[identity]
        return {**{key: flight[key] for key in ('born_step', 'origin_m', 'expires_step')},
            'trajectory':display_path(flight['trajectory'])}
