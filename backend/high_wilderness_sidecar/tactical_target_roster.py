"""Battle-local target metadata. Entity state and observed samples stay separate.

Only changed metadata replaces a row/family. Candidate plans never mutate the
published frame, and access contexts resolve against their explicit stage, not
a cached step number. No positions, inventories or entity objects live in rows.
"""
from dataclasses import dataclass, replace
from heapq import merge
from types import MappingProxyType


FAMILIES = ('ship', 'projectile', 'aircraft')
LABELS = dict(ship='舰艇', aircraft='飞机', missile='导弹', shell='炮弹', bomb='炸弹')


@dataclass(frozen=True, slots=True)
class Handle:
    family: str
    target_id: str | int


@dataclass(frozen=True, slots=True)
class Entry:
    handle: Handle
    name: str
    side: str
    layer: str
    category: str


@dataclass(frozen=True, slots=True)
class Family:
    rows: tuple
    by_id: object
    groups: object

    @classmethod
    def build(cls, rows=()):
        rows = tuple(rows)
        groups = {}
        for index, row in enumerate(rows):
            groups.setdefault((row.side, row.layer), []).append((index, row))
        return cls(rows, MappingProxyType({r.handle.target_id: r for r in rows}),
                   MappingProxyType({key: tuple(values) for key, values in groups.items()}))


@dataclass(frozen=True, slots=True)
class Frame:
    epoch: str
    revision: int
    families: tuple

    def get(self, handle):
        return self.families[FAMILIES.index(handle.family)].by_id.get(handle.target_id)

    def query(self, *, exclude_side=None, layers=None, categories=None):
        """Return original family/row order even when layer groups interleave."""
        for family in self.families:
            groups = [rows for (side, layer), rows in family.groups.items()
                      if side != exclude_side and (layers is None or layer in layers)]
            for _, row in merge(*groups, key=lambda item: item[0]):
                if categories is None or row.category in categories:
                    yield row


class Access:
    """Short-lived read context for one supplied candidate phase.

    Callers own the phase inputs and must finish reading before mutating them.
    The roster retains no Access instances. Resolving an entry does not grant
    an observation or make its live state suitable for a historical Track.
    """
    def __init__(self, frame, world, projectiles, flights):
        if frame.epoch != world.epoch:
            raise ValueError('Target access belongs to another battle')
        self.frame = frame
        self.world, self.projectiles, self.flights = world, tuple(projectiles), flights
        self._ships = self._projectiles = None

    def resolve(self, entry):
        if self.frame.get(entry.handle) is not entry:
            raise ValueError('Target entry does not belong to this stage')
        key = entry.handle.target_id
        if entry.handle.family == 'ship':
            if self._ships is None:
                self._ships = {s.ship_id: s for s in self.world.ships}
            return self._ships[key]
        if entry.handle.family == 'projectile':
            if self._projectiles is None:
                self._projectiles = {p.id: p for p in self.projectiles}
            return self._projectiles[key]
        return self.flights[key]


class Plan:
    def __init__(self, roster):
        self.owner = roster
        self.base = self.frame = roster.frame
        self.closed = False

    def _guard(self):
        if self.closed:
            raise ValueError('Target roster plan is closed')

    def _sync(self, family, items, describe):
        """Check cheap lifecycle fields at owner boundaries; allocate on change.

        No Target/sample/model copies are built for retained entities. Even the
        ordered row tuple and group index survive an unchanged phase intact.
        """
        self._guard()
        index = FAMILIES.index(family)
        old = self.frame.families[index]
        changed = None
        seen = set()
        count = 0
        for identity, side, layer, entity in items:
            if type(identity) not in (str, int) or identity in seen:
                raise ValueError('Duplicate or invalid target identity')
            seen.add(identity)
            row = old.by_id.get(identity)
            if row is None:
                name, category = describe(entity)
                row = Entry(Handle(family, identity), name, side, layer, category)
            elif row.side != side or row.layer != layer:
                row = replace(row, side=side, layer=layer)
            if changed is None and (count >= len(old.rows) or old.rows[count] is not row):
                changed = list(old.rows[:count])
            if changed is not None:
                changed.append(row)
            count += 1
        if changed is None:
            if count == len(old.rows):
                return
            changed = old.rows[:count]
        families = list(self.frame.families)
        families[index] = Family.build(changed)
        self.frame = Frame(self.frame.epoch, self.frame.revision + 1, tuple(families))

    def ships(self, world):
        self._guard()
        if world.epoch != self.frame.epoch:
            raise ValueError('Target roster belongs to another battle')
        roster = self.owner
        self._sync('ship', ((s.ship_id, roster.sides[s.ship_id], s.motion.height_layer, s)
                            for s in world.ships if s.command.lifecycle.physical_status == 'operational'
                            and s.motion.hull_integrity_fraction > 0),
                   lambda s: (roster.names.get(s.ship_id, s.ship_id), 'ship'))

    def projectiles(self, projectiles, inventories):
        def describe(p):
            category = 'missile' if p.missile else 'shell'
            if p.missile and p.missile.profile.model_id.startswith('gtw.aviation.weapon.'):
                from .aviation_weapons import policy
                key = p.missile.profile.model_id.removeprefix('gtw.aviation.weapon.')
                inv = inventories[self.owner.ship_indices[p.ship_id]]
                combat = inv._definition.get('aviation', {}).get('combat')
                spec = (combat if combat is not None else policy())['payloads'].get(key)
                if spec and spec['motion'] in ('bomb', 'guided_bomb'):
                    category = 'bomb'
            return f'{LABELS[category]} #{p.id}', category
        self._sync('projectile', ((p.id, self.owner.sides[p.ship_id], p.height_layer, p)
                                  for p in projectiles if p.durability is not None and p.durability > 0
                                  and not p.aircraft_body), describe)

    def aircraft(self, flights, inventories):
        def describe(f):
            inv = inventories[f['owner']]
            manifest = inv._value['aviation']['manifest']['aircraft']
            plane = next(a for a in manifest if a['id'] == f['id'])
            model = next(m for m in inv._definition['aviation']['catalog']['aircraft']
                         if m['id'] == plane['model_id'])
            return f"{model['name']} #{f['id']}", 'aircraft'
        self._sync('aircraft', ((key, self.owner.side_order[f['owner']], f['layer'], f)
                                for key, f in flights.items() if f['hp'] > 0), describe)

    def synchronize(self, world, projectiles, flights, inventories, *, ending=False):
        self._guard()
        if world.epoch != self.frame.epoch:
            raise ValueError('Target roster belongs to another battle')
        if ending:
            self.clear()
        else:
            self.ships(world)
            self.projectiles(projectiles, inventories)
            self.aircraft(flights, inventories)
        return self.frame

    def bind(self, world, projectiles, flights, inventories):
        projectiles = tuple(projectiles)
        self.synchronize(world, projectiles, flights, inventories)
        return Access(self.frame, world, projectiles, flights)

    def clear(self):
        self._guard()
        if any(f.rows for f in self.frame.families):
            self.frame = Frame(self.frame.epoch, self.frame.revision + 1,
                               tuple(Family.build() for _ in FAMILIES))


class TargetRoster:
    def __init__(self, world, sides, names):
        self.side_order = tuple(sides)
        self.sides = MappingProxyType({s.ship_id: side for s, side in zip(world.ships, sides)})
        self.ship_indices = MappingProxyType({s.ship_id: n for n, s in enumerate(world.ships)})
        self.names = MappingProxyType(dict(names))
        self.frame = Frame(world.epoch, 0, tuple(Family.build() for _ in FAMILIES))
        self._directories = {}

    def view(self, side, observation):
        from .tactical_target_directory import Directory
        if side not in self._directories:
            self._directories[side] = Directory()
        return self._directories[side].view(self.frame, side, observation, self.names)

    def begin(self):
        return Plan(self)

    def commit(self, plan):
        plan._guard()
        if plan.owner is not self or plan.base is not self.frame:
            raise ValueError('Stale or foreign target roster plan')
        self.frame = plan.frame
        plan.closed = True

    def rebuild(self, world, projectiles, flights, inventories, *, ending=False):
        """Explicit entry/restore reconciliation, never called by a read/view."""
        plan = self.begin()
        plan.clear()
        plan.synchronize(world, projectiles, flights, inventories, ending=ending)
        self.commit(plan)
