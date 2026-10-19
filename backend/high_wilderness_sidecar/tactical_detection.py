"""Roster-backed inputs for one sensor phase; never retained by observations.

Filter metadata before resolving entities. Kinematics and threat hints are
shared only within this phase, while visibility/EW is evaluated on every scan.
Only accepted fresh observations become immutable Target samples.
"""
from math import hypot

from . import projectile_observation as observed
from .tactical_layers import LAYERS
from .tactical_observation import Target


ADJACENT = {layer: tuple(other for i, other in enumerate(LAYERS) if abs(i-n) <= 1)
            for n, layer in enumerate(LAYERS)}
_UNSET = object()


class Candidate:
    __slots__ = ('stage', 'entity', 'id', 'kind', 'side', 'position', 'velocity', 'layer',
                 'large', 'powered', 'heading', 'yaw', 'durability', 'radar_signature',
                 'infrared_signature', '_payload', '_threat', '_sample')

    def __init__(self, stage, entry):
        self.stage = stage
        self.entity = entity = stage.access.resolve(entry)
        self.id, self.side, self.layer = entry.handle.target_id, entry.side, entry.layer
        self.large, self.powered, self.heading, self.yaw = True, False, 0., 0.
        self.durability = None
        self.radar_signature, self.infrared_signature = 1000., 10.
        self._payload, self._threat, self._sample = _UNSET, False, None
        if entry.handle.family == 'ship':
            self.kind = 'ship'; m = entity.motion
            self.position = tuple(m.position_world_m.to_list())
            self.velocity = tuple(m.velocity_world_mps.to_list())
            self.heading, self.yaw = m.heading_rad, m.yaw_rate_radps
            self._payload = None
        elif entry.handle.family == 'projectile':
            # Display category (including bombs) must not change weapon/sensor kind.
            self.kind = 'missile' if entity.missile else 'shell'
            self.position, self.velocity = entity.position, entity.velocity
            self.powered = bool(entity.missile and entity.missile.phase != 'coast')
            self.large = bool(entity.flight_profile and entity.flight_profile.caliber_mm >= 75)
            self.durability = entity.durability
            self._threat = _UNSET
        else:
            self.kind = 'aircraft'; model = stage.models[self.id]
            self.position, self.velocity = tuple(entity['position']), tuple(entity['velocity'])
            self.large, self.powered = False, True
            self.heading, self.durability = entity['heading'], entity['hp']
            self.radar_signature = model['radar_signature_m2'] * entity['modifiers']['radar_signature']
            self.infrared_signature = model['infrared_signature'] * entity['modifiers']['infrared_signature']

    @property
    def payload(self):
        if self._payload is _UNSET:
            if self.kind == 'aircraft':
                self._payload = observed.Sample(self.id, self.position, self.velocity, self.layer,
                    self.entity.get('step', 0)+3600, None, self.durability, observed.altitude(self.layer))
            else:
                self._payload = observed.sample(self.entity)
        return self._payload

    @property
    def threat(self):
        if self._threat is _UNSET:
            self._threat = self.stage.threat(self.entity, self.side, self.payload)
        return self._threat

    def freeze(self):
        if self._sample is None:
            self._sample = Target(self.id, self.kind, self.side, self.position, self.velocity, self.layer,
                self.large, self.powered, self.heading, self.yaw, self.durability,
                self.payload, self.threat, self.radar_signature, self.infrared_signature)
        return self._sample


def freeze(target):
    return target.freeze() if isinstance(target, Candidate) else target


class TargetStage:
    def __init__(self, runtime, world, projectiles, flights, inventories, extras=()):
        self.world = world
        # Advance changes each aircraft's layer/velocity and can remove it while
        # later aircraft still scan the incoming phase. Capture those fields now;
        # contacts/tasks are not read through these shallow pose records.
        poses = {key: dict(f) for key, f in flights.items()}
        self.models = {}
        owners = {}
        for key, f in poses.items():
            if f['hp'] <= 0:continue
            n = f['owner']
            if n not in owners:
                inv = inventories[n]
                owners[n] = ({a['id']: a['model_id'] for a in inv._value['aviation']['manifest']['aircraft']},
                             {m['id']: m for m in inv._definition['aviation']['catalog']['aircraft']})
            planes, models = owners[n]
            self.models[key] = models[planes[key]]
        self.access = runtime.battle.target_roster.begin().bind(world, projectiles, poses, inventories)
        self.extras = tuple(extras)
        self._groups, self._values = {}, {}
        self._motion = tuple((runtime.battle._sides[n], tuple(s.motion.position_world_m.to_list()),
                              tuple(s.motion.velocity_world_mps.to_list()), s.motion.height_layer)
                             for n, s in enumerate(world.ships))

    def query(self, side, layer, *, include_ids=None, exclude_ids=()):
        key = side, layer
        if key not in self._groups:
            self._groups[key] = tuple(self.access.frame.query(exclude_side=side, layers=ADJACENT[layer]))
        for entry in self._groups[key]:
            handle = entry.handle
            if handle.target_id in exclude_ids or include_ids is not None and handle.target_id not in include_ids:continue
            if handle not in self._values:self._values[handle] = Candidate(self, entry)
            yield self._values[handle]
        for target in self.extras:
            if (target.side != side and target.layer in ADJACENT[layer] and target.id not in exclude_ids
                    and (include_ids is None or target.id in include_ids)):
                yield target

    def threat(self, projectile, side, measured):
        for other, position, velocity, layer in self._motion:
            if other == side:continue
            delta = tuple(a-c for a, c in zip(position, projectile.position))
            relative = tuple(a-c for a, c in zip(projectile.velocity, velocity))
            speed2 = sum(v*v for v in relative)
            time = sum(a*v for a, v in zip(delta, relative))/speed2 if speed2 else -1
            if (0 <= time <= (projectile.expires-self.world.fixed_step)/60 and observed.layer_at(measured, time) == layer
                    and hypot(*(a-v*time for a, v in zip(delta, relative))) < 100):return True
        return False
