"""E2.1 module durability authority and compiled host invalidation.

Inputs are internal test/domain operations, never a desktop trust interface.
D1c repair is a bounded partial-health operation admitted only at its maintenance boundary.
The separate test_rebuild remains an explicitly enabled fixture reset.
Power, staffing, operating modes and command dependencies belong to E2.2.
"""
from dataclasses import dataclass, replace
from math import isfinite

from 高天荒野舰艇数据契约 import ContractError, RESOURCE_ID_PATTERN
from 高天荒野舰艇运行时参数编译器 import EPS
from 高天荒野舰艇只读资源验证 import require_deeply_immutable

OWNED_REASONS = ("actuator_destroyed", "host_destroyed")


def require(ok, message):
    if not ok:
        raise ContractError("tactical_devices.boundary", "$", message)


def number(value):
    return type(value) in (int, float) and isfinite(value)


@dataclass(frozen=True)
class ModuleDesign:
    instance_id: str
    maximum_durability_points: float
    host_instance_id: str | None = None


@dataclass(frozen=True)
class DeviceSeed:
    propulsion_source_sha256: str
    modules: tuple[ModuleDesign, ...]
    initial_durability_points: tuple[float, ...]


@dataclass(frozen=True)
class DeviceOperation:
    epoch: str
    ship_id: str
    module_id: str
    sequence: int  # contiguous per module, issued by the controlled domain caller
    kind: str  # damage, maintenance-only repair, or explicitly enabled test_rebuild
    amount: float
    fixed_step: int
    phase: str


@dataclass(frozen=True)
class ModuleState:
    durability_points: float
    sequence: int = 0
    last_operation: tuple | None = None  # bounded duplicate receipt, no history chain


@dataclass(frozen=True)
class DeviceState:
    modules: tuple[ModuleState, ...]
    revision: int = 0  # candidate-owned; availability versions are generated here


class DeviceKernel:
    def __init__(self, seed, contributions):
        require_deeply_immutable(seed)
        require(seed.propulsion_source_sha256 == contributions.source_sha256, "Wrong propulsion resource")
        modules = seed.modules
        ids = [m.instance_id for m in modules]
        require(all(type(i) is str and RESOURCE_ID_PATTERN.fullmatch(i) for i in ids), "Invalid module ID")
        require(len(set(ids)) == len(ids), "Duplicate module ID")
        require(len(modules) == len(seed.initial_durability_points), "Incomplete initial device state")
        self.by_id = {name: i for i, name in enumerate(ids)}
        self.seed = seed
        for m, hp in zip(modules, seed.initial_durability_points):
            require(number(m.maximum_durability_points) and m.maximum_durability_points > EPS,
                "Invalid maximum durability")
            require(number(hp) and 0 <= hp <= m.maximum_durability_points, "Invalid initial durability")
            require(m.host_instance_id is None or type(m.host_instance_id) is str
                and m.host_instance_id in self.by_id, "Missing host")
        ancestors = []
        for m in modules:
            chain, seen = [], {m.instance_id}
            host = m.host_instance_id
            while host is not None:
                require(host not in seen, "Cyclic host graph")
                seen.add(host)
                index = self.by_id[host]
                chain.append(index)
                host = modules[index].host_instance_id
            ancestors.append(tuple(chain))
        self.engine_ids = tuple(e.instance_id for e in contributions.engines)
        require(all(i in self.by_id for i in self.engine_ids), "Engine missing from device resources")
        self.engine_modules = tuple(self.by_id[i] for i in self.engine_ids)
        self.ancestors = tuple(ancestors[i] for i in self.engine_modules)
        affected = [[] for _ in modules]
        for engine_index, (e, index, chain) in enumerate(zip(contributions.engines, self.engine_modules, self.ancestors)):
            require(e.host_instance_id == modules[index].host_instance_id, "Engine host binding mismatch")
            for module_index in (index, *chain):
                affected[module_index].append(engine_index)
        self.affected = tuple(tuple(v) for v in affected)

    def initial(self):
        return DeviceState(tuple(ModuleState(v) for v in self.seed.initial_durability_points))

    def reasons(self, state, engine_index):
        return (state.modules[self.engine_modules[engine_index]].durability_points <= EPS,
            any(state.modules[i].durability_points <= EPS for i in self.ancestors[engine_index]))

    def initial_blockers(self, state):
        return tuple((name, tuple(reason for reason, blocked in zip(OWNED_REASONS, self.reasons(state, i)) if blocked))
            for i, name in enumerate(self.engine_ids))

    def validate_operation(self, op, *, epoch, ship_id, step, allow_rebuild, allow_repair=False):
        require(type(op) is DeviceOperation, "Unknown device operation")
        require(op.epoch == epoch and op.ship_id == ship_id and type(op.module_id) is str
            and op.module_id in self.by_id, "Foreign device operation")
        require(op.phase in ("opening", "closing") and type(op.fixed_step) is int
            and op.fixed_step == step + (op.phase == "closing"), "Wrong device boundary")
        require(type(op.sequence) is int and op.sequence > 0, "Invalid operation sequence")
        require(number(op.amount) and op.amount >= 0, "Invalid damage amount")
        require(op.kind == "damage" or op.kind == "test_rebuild" and allow_rebuild and op.amount == 0
            or op.kind == "repair" and allow_repair and op.amount > 0,
            "Unsupported repair or disabled fixture rebuild")

    def boundary(self, before, operations):
        if not operations:
            return before, (), (), ()
        updated, affected, receipts = {}, set(), []
        for op in operations:
            i = self.by_id[op.module_id]
            old = updated.get(i, before.modules[i])
            signature = (op.sequence, op.kind, op.amount, op.fixed_step, op.phase)
            if op.sequence == old.sequence:
                require(signature == old.last_operation, "Conflicting duplicate device operation")
                continue
            require(op.sequence == old.sequence + 1, "Stale or skipped device operation")
            if op.kind == 'repair':
                require(old.durability_points > EPS, 'Repair cannot rebuild a destroyed module')
                hp = min(self.seed.modules[i].maximum_durability_points, old.durability_points + op.amount)
            else:
                hp = max(0.0, old.durability_points - op.amount) if op.kind == "damage" else self.seed.modules[i].maximum_durability_points
            updated[i] = ModuleState(hp, op.sequence, signature)
            # Partial health changes never invalidate fixed engine contributions.
            if (old.durability_points <= EPS) != (hp <= EPS):
                affected.update(self.affected[i])
            receipts.append((op.module_id, op.sequence, op.kind, old.durability_points, hp))
        if not updated:
            return before, (), (), ()
        modules = list(before.modules)  # copy only on domain input, never on a stable step
        for i, item in updated.items():
            modules[i] = item
        after = DeviceState(tuple(modules), before.revision + 1)
        changes = []
        for i in sorted(affected):
            for reason, old, new in zip(OWNED_REASONS, self.reasons(before, i), self.reasons(after, i)):
                if old != new:
                    changes.append((self.engine_ids[i], reason, new, after.revision))
        return after, tuple(changes), tuple(receipts), tuple(sorted(affected))


def seed_from_snapshot(snapshot, instance, contributions):
    """Called only at strict scene preparation, from the validated runtime instance."""
    require(snapshot.source_sha256 == contributions.snapshot_sha256
        and instance.derived_ship_snapshot_sha256 == snapshot.source_sha256, "Device snapshot source mismatch")
    states = {m.instance_id: m for m in instance.module_states}
    modules = snapshot.outfit.instances
    require(len(states) == len(instance.module_states) and set(states) == {m.id for m in modules},
        "Missing or duplicate initial module state")
    return DeviceSeed(contributions.source_sha256,
        tuple(ModuleDesign(m.id, m.prototype.durability_points, m.host_instance_id) for m in modules),
        tuple(states[m.id].current_durability_points for m in modules))
