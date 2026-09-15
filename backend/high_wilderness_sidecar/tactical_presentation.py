"""Bounded, disposable flight history, recorded only after committed steps.

This is display data, never a checkpoint, hit resolver or reliable game event.
Finished flights survive several publications so a shot born and hit between
two reads can still be drawn. Active flights retain their launch identity.
"""
from collections import deque

HISTORY_STEPS = 120
MAX_FINISHED_FLIGHTS = 128


class FlightHistory:
    def __init__(self):
        self.active = {}
        self.finished = deque()
        self.dropped = 0

    def record(self, step, projectiles, hits=(), expired=()):
        current = {p.id: p for p in projectiles}
        impacts = {h['projectile_id']: h for h in hits if h['step'] == step}
        terminals = {h['projectile_id']: h['position_m'] for h in expired}
        for identity, flight in self.active.items():
            if identity in current:
                continue
            hit = impacts.get(identity)
            # Only committed collision/expiry data supplies the terminal point.
            self.finished.append(dict(flight, end_step=step,
                end_m=hit['position_m'] if hit else terminals.get(identity,flight['position_m']),
                impact=None if hit is None else dict(ship_id=hit['ship_id'], outcome=hit['outcome'])))
        self.active = {identity: dict(
            self.active.get(identity, dict(id=p.id, ship_id=p.ship_id, born_step=step,
                origin_m=p.position, expires_step=p.expires, height_layer=p.height_layer)),
            position_m=p.position, velocity_mps=p.velocity) for identity, p in current.items()}
        while self.finished and self.finished[0]['end_step'] < step-HISTORY_STEPS:
            self.finished.popleft()
        while len(self.finished) > MAX_FINISHED_FLIGHTS:
            self.finished.popleft()
            self.dropped += 1

    def view(self):
        return dict(interface='gaotian.tactical-presentation/v1alpha1',
            finished_projectiles=list(self.finished), dropped_projectiles=self.dropped)

    def launch(self, identity):
        flight = self.active[identity]
        return {key: flight[key] for key in ('born_step', 'origin_m', 'expires_step')}
