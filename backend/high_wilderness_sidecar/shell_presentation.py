"""Display-only shell curves. No ballistic integration or future extrapolation.

Raw committed samples exist only until publication. Curves are checked against
the committed piecewise-linear path with a Bezier convex-hull error bound.
"""
from collections import deque
from math import hypot, sqrt

ERROR_M = .05
WINDOW_STEPS = 32
STEP_S = 1 / 60


def tangents(a, b):
    duration = (b[0] - a[0]) * STEP_S
    result = []
    for axis in (1, 2):
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            result.append((0., 0.)); continue
        x = max(0., a[axis + 2] * duration / delta)
        y = max(0., b[axis + 2] * duration / delta)
        scale = min(1., 3 / max(3., sqrt(x*x + y*y)))
        result.append((x * scale * delta, y * scale * delta))
    return result


def value_and_derivative(a, b, t, slopes):
    values, derivatives = [], []
    for axis, (m0, m1) in zip((1, 2), slopes):
        delta = b[axis] - a[axis]
        c2, c3 = 3*delta - 2*m0 - m1, -2*delta + m0 + m1
        values.append(a[axis] + t*(m0 + t*(c2 + t*c3)))
        derivatives.append(m0 + t*(2*c2 + t*3*c3))
    return values, derivatives


def position(samples, step):
    if step <= samples[0][0]:
        return samples[0][1:3]
    for a, b in zip(samples, samples[1:]):
        if step <= b[0]:
            t = (step-a[0])/(b[0]-a[0])
            if b[5] == 1:
                return tuple(a[j]+t*(b[j]-a[j]) for j in (1, 2))
            return value_and_derivative(a, b, t, tangents(a, b))[0]
    return samples[-1][1:3]


def legacy_projectile(value):
    """Only legacy/control reads pay for the old trajectory shape."""
    if not value.get('shell_samples'):
        return value
    samples = value['shell_samples']
    return {**{k:v for k,v in value.items() if k != 'shell_samples'},
            'trajectory':[(step, *position(samples, step)) for step in range(int(samples[0][0]), int(samples[-1][0])+1)]}


def curve_error(a, b, raw):
    """Upper bound over every instant, not just errors at integer steps."""
    slopes = tangents(a, b)
    span = b[0] - a[0]
    worst = 0.
    for p, q in zip(raw, raw[1:]):
        t0, t1 = (p[0]-a[0])/span, (q[0]-a[0])/span
        v0, d0 = value_and_derivative(a, b, t0, slopes)
        v1, d1 = value_and_derivative(a, b, t1, slopes)
        dt = (t1-t0)/3
        for n in range(4):
            curve = (v0 if n == 0 else v1 if n == 3 else
                     [v0[j]+dt*d0[j] for j in range(2)] if n == 1 else
                     [v1[j]-dt*d1[j] for j in range(2)])
            linear = [p[j+1]+(q[j+1]-p[j+1])*n/3 for j in range(2)]
            worst = max(worst, hypot(*(x-y for x, y in zip(curve, linear))))
    return worst


def simplify(samples):
    # The sixth value describes the incoming span: 0 = constrained Hermite,
    # 1 = linear. A terminal segment is always linear to the committed endpoint.
    samples = list(samples)
    if len(samples) < 2:
        return [(*p[:5], 1) for p in samples]
    result = [(*samples[0][:5], 1)]
    def visit(first, last):
        a, b = samples[first], samples[last]
        if b[0] <= a[0]:
            result.append((*b[:5], 1)); return
        if curve_error(a, b, samples[first:last+1]) <= ERROR_M:
            result.append((*b[:5], 0))
        elif last == first+1:
            result.append((*b[:5], 1))
        else:
            middle = (first+last)//2
            visit(first, middle); visit(middle, last)
    visit(0, len(samples)-1)
    return result


class ShellHistory:
    def __init__(self, step, active, finished):
        self.active = {}
        self.finished = deque()
        self.completed = ()
        self.published = {}
        for p in active:
            row = self._row(p)
            self.active[p['id']] = row
        # Migration uses linear spans; historical endpoint velocities are not
        # available and must not be fabricated into curved motion.
        for p in finished:
            self.finished.append(self._snapshot(self._row(p)))
        self.publish(step)

    @staticmethod
    def _row(p):
        row = {k:v for k,v in p.items() if k != 'trajectory'}
        knots = [(*point, *p['velocity_mps'], 1) for point in p.get('trajectory', ())]
        row['knots'] = knots
        row['pending'] = deque([knots[-1][:5]] if knots else (), maxlen=WINDOW_STEPS+1)
        return row

    @staticmethod
    def _seal(row):
        points = simplify(row['pending'])
        if points:
            # Keep the previous anchor's incoming interpolation mode intact.
            knots = row['knots']
            if knots and knots[-1][0] == points[0][0]:
                knots.extend(points[1:])
            else:
                knots.extend(points)
            while len(knots) > 2 and knots[1][0] < points[-1][0]-WINDOW_STEPS:
                knots.pop(0)
            row['pending'] = deque([points[-1][:5]], maxlen=WINDOW_STEPS+1)

    @staticmethod
    def _snapshot(row):
        return {**{k:v for k,v in row.items() if k not in ('knots', 'pending')},
                'shell_samples': tuple(row['knots'])}

    def record(self, step, projectiles, impacts, terminals):
        current = {p.id:p for p in projectiles}
        completed = []
        for identity in self.active.keys()-current.keys():
            row = self.active.pop(identity)
            self._seal(row)
            hit = impacts.get(identity)
            endpoint = hit['position_m'] if hit else terminals.get(identity, row['position_m'])
            row['knots'].append((step, *endpoint, *row['velocity_mps'], 1))
            row.update(end_step=step, end_m=endpoint,
                       impact=None if hit is None else dict(ship_id=hit['ship_id'], outcome=hit['outcome']))
            terminal = self._snapshot(row)
            self.finished.append(terminal); completed.append(terminal)
        for identity, p in current.items():
            if identity not in self.active:
                self.active[identity] = dict(id=identity, ship_id=p.ship_id, kind='shell',
                    born_step=step, origin_m=p.position, expires_step=p.expires,
                    height_layer=p.height_layer, maximum_durability=p.maximum_durability,
                    knots=[], pending=deque(maxlen=WINDOW_STEPS+1))
            row = self.active[identity]
            row.update(position_m=p.position, velocity_mps=p.velocity)
            row['pending'].append((step, *p.position, *p.velocity))
        while self.finished and (self.finished[0]['end_step'] < step-120 or len(self.finished) > 128):
            self.finished.popleft()
        self.completed = tuple(completed)

    def publish(self, step):
        for row in self.active.values():
            self._seal(row)
        self.published = {identity:self._snapshot(row) for identity,row in self.active.items()}
