"""E1c.2 offline flight authority using fixed contributions and event deltas.

No combat simulation or production transport. Device/authority events are an
internal experiment seam; E2 must connect the actual domain producers.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from math import isfinite
from threading import get_ident
from uuid import uuid4

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from 高天荒野舰艇推进状态合同 import EngineRuntimeState
from 高天荒野舰艇推进时间内核 import _ExactTimingCapability, _apply_command, _commit_due_transition
from 高天荒野舰艇推进安全判定器 import (
    THRUST_OUTPUT_STAGES_PERCENT, TELEGRAPH_NOTCH_PERCENT, _soft_reasons, _release_safe, _active_reasons,
)
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS
from 高天荒野舰艇定向推进控制桥 import (
    DirectionalPropulsionControlInput, directional_control, automatic_linear_brake_control,
)
import 高天荒野舰艇战术机动求解器 as dynamics
from .simplified_propulsion import CompiledShipContributions
from .tactical_devices import DeviceSeed, DeviceState, DeviceKernel, DeviceOperation, OWNED_REASONS
from .tactical_resources_runtime import ResourceSeed, ResourceState, ResourceKernel, ResourceOperation, REASONS as RESOURCE_REASONS
from .tactical_command_runtime import CommandSeed, CommandState, CommandKernel, ExitOperation

INTERFACE = "gaotian.simplified-flight-experiment/v1alpha1"
REASONS = ("actuator_destroyed", "host_destroyed", "power_unavailable", "crew_unavailable",
           "fuel_unavailable", "engine_tripped", "mode_disabled", "emergency_cut", "command_unavailable", "lifecycle_unavailable")
OPPOSITE = (1, 0, 3, 2, 5, 4)
NOTCH = {percent: name for name, percent in TELEGRAPH_NOTCH_PERCENT}


def require(value, detail):
    if not value:
        raise ContractError("simplified_flight.boundary", "$", detail)


@dataclass(frozen=True)
class AvailabilityEvent:
    epoch: str
    ship_id: str
    engine_id: str
    reason: str
    active: bool
    version: int
    fixed_step: int
    phase: str  # opening at n, closing at n+1


@dataclass(frozen=True)
class AuthorityEvent:
    epoch: str
    ship_id: str
    allowed: bool
    version: int
    fixed_step: int
    phase: str


@dataclass(frozen=True)
class EngineSlot:
    engine: EngineRuntimeState
    blocked: tuple[bool, ...] = (False,) * len(REASONS)
    versions: tuple[int, ...] = (0,) * len(REASONS)


@dataclass(frozen=True)
class Governor:
    ceiling: int = 100
    reasons: tuple[str, ...] = ()
    limited_since: int | None = None
    release_since: int | None = None


@dataclass(frozen=True)
class PropulsionState:
    engines: tuple[EngineSlot, ...]
    available_units: tuple[int, ...]
    output_percent_units: tuple[int, ...]
    schedule: tuple[tuple[int, int], ...]  # one live (due, engine index) per engine
    targets: tuple[int, ...] = (0,) * 6
    governors: tuple[Governor, ...] = (Governor(),) * 6
    phase_revision: int = 0


@dataclass(frozen=True)
class MotionParameters:
    current_mass_kg: float
    current_inertia_kg_m2: float
    safe_longitudinal_mps2: float
    safe_lateral_mps2: float
    crew_safety_lock_enabled: bool


@dataclass(frozen=True)
class MotionModel:
    runtime: MotionParameters
    structure_points_body_m: tuple
    environment: object
    tuning: object
    aerodynamic_cache: object

    @classmethod
    def from_legacy(cls, model):
        r = model.runtime
        return cls(MotionParameters(r.current_mass_kg, r.current_inertia_kg_m2,
            r.safe_longitudinal_mps2, r.safe_lateral_mps2, r.crew_safety_lock_enabled),
            model.structure_points_body_m, model.environment, model.tuning, model.aerodynamic_cache)


@dataclass(frozen=True)
class ShipSeed:
    contributions: CompiledShipContributions
    model: MotionModel
    motion: dynamics.TacticalMotionState
    initially_ready: bool = True
    initial_blockers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    devices: DeviceSeed | None = None
    resources: ResourceSeed | None = None
    command: CommandSeed | None = None


@dataclass(frozen=True)
class FlightShip:
    ship_id: str
    motion: dynamics.TacticalMotionState
    propulsion: PropulsionState
    control: DirectionalPropulsionControlInput
    authority_allowed: bool
    authority_version: int = 0
    devices: DeviceState | None = None
    resources: ResourceState | None = None
    command: CommandState | None = None


@dataclass(frozen=True)
class FlightWorld:
    epoch: str
    fixed_step: int
    ships: tuple[FlightShip, ...]


@dataclass(frozen=True)
class StepResult:
    fixed_step: int
    events: tuple
    diagnostics: tuple


class PropulsionKernel:
    """Private compiled indexes; published state and contributions are immutable."""
    def __init__(self, contributions):
        self.resources = contributions
        self.by_id = {e.instance_id: e.index for e in contributions.engines}
        self.channels = tuple(next((d for d, v in enumerate(e.contribution_units) if v), None)
                              for e in contributions.engines)
        self.members = tuple(tuple(i for i, c in enumerate(self.channels) if c == d) for d in range(6))
        self.timings = tuple(_ExactTimingCapability(e.category,
            Decimal(e.startup_seconds[0]) / Decimal(e.startup_seconds[1]) * 60,
            Decimal(e.response_seconds[0]) / Decimal(e.response_seconds[1]) * 60,
            e.startup_steps, e.response_steps) for e in contributions.engines)

    def initial(self, ready, blockers):
        require(type(ready) is bool, "Initial readiness must be boolean")
        blocked_by_id = dict(blockers)
        require(len(blocked_by_id) == len(blockers) and set(blocked_by_id) <= set(self.by_id), "Unknown initial engine")
        slots = []
        total = [0] * 6
        for e in self.resources.engines:
            reasons = blocked_by_id.get(e.instance_id, ())
            require(len(set(reasons)) == len(reasons) and set(reasons) <= set(REASONS), "Invalid initial reasons")
            active = tuple(r in reasons for r in REASONS)
            is_ready = ready and not any(active)
            slots.append(EngineSlot(EngineRuntimeState(e.instance_id, e.category,
                "ready" if is_ready else "off", "stop" if e.category == "main_engine" else None,
                0, 0, 0 if is_ready else None, None), active))
            if not any(active):
                total = [a + b for a, b in zip(total, e.contribution_units)]
        return PropulsionState(tuple(slots), tuple(total), (0,) * 6, ())

    def boundary(self, source, n, requested, events, *, load=None, profile=None, overg=False, crew_lock=True):
        # Allocate engine arrays only when actual changes occur. A stable boundary
        # reads six totals and the earliest deadline, not the full engine set.
        slots, schedule = None, None
        available, outputs = list(source.available_units), list(source.output_percent_units)
        changed, emitted = set(), []
        phase_revision = source.phase_revision

        def get(i):
            return source.engines[i] if slots is None else slots[i]

        def put(i, value):
            nonlocal slots, schedule, phase_revision
            old = get(i)
            if old == value:
                return
            if old.engine.phase != value.engine.phase:
                phase_revision += 1
            if slots is None:
                slots = list(source.engines)
                schedule = {i: step for step, i in source.schedule}
            vector = self.resources.engines[i].contribution_units
            da = int(not any(value.blocked)) - int(not any(old.blocked))
            do = value.engine.actual_output_percent - old.engine.actual_output_percent
            for d, v in enumerate(vector):
                available[d] += da * v
                outputs[d] += do * v
            slots[i] = value
            schedule.pop(i, None)
            if value.engine.next_transition_step is not None:
                schedule[i] = value.engine.next_transition_step
            changed.add(i)

        # Coalesce each source reason, then apply all reasons for an engine once.
        updates = {}
        seen = {}
        for event in events:
            i, r = self.by_id[event.engine_id], REASONS.index(event.reason)
            key = (i, r, event.version)
            require(key not in seen or seen[key] == event.active, "Conflicting duplicate availability event")
            seen[key] = event.active
            slot = updates.get(i, get(i))
            if event.version < slot.versions[r]:
                continue
            if event.version == slot.versions[r]:
                require(slot.blocked[r] == event.active, "Conflicting committed availability version")
                continue
            blocked, versions = list(slot.blocked), list(slot.versions)
            blocked[r], versions[r] = event.active, event.version
            updates[i] = replace(slot, blocked=tuple(blocked), versions=tuple(versions))
        for i in sorted(updates):
            old, new = get(i), updates[i]
            if any(old.blocked) != any(new.blocked) or old.blocked[5] != new.blocked[5]:
                # Recovery starts from off; damaged equipment never retains an old deadline.
                new = replace(new, engine=EngineRuntimeState(new.engine.actuator_instance_id,
                    new.engine.actuator_category, "tripped" if new.blocked[5] else "off", "stop" if new.engine.actuator_category == "main_engine" else None,
                    0, 0, None, None))
                emitted.append((n, "availability", new.engine.actuator_instance_id, not any(new.blocked)))
            put(i, new)

        targets = list(requested)
        for d in range(6):
            if outputs[OPPOSITE[d]] > 0:
                targets[d] = 0
        governors = list(source.governors)
        current_outputs = tuple(outputs)
        due = []
        for step, i in source.schedule:
            if step > n:
                break
            # Availability may have canceled this deadline above.
            state = get(i).engine
            if state.next_transition_step != step:
                continue
            require(step == n, "Missed engine deadline")
            next_state, time_events = _commit_due_transition(state, self.timings[i], n)
            due.append((i, state, next_state, time_events))

        rising = {i for i, old, new, _ in due if new.actual_output_percent > old.actual_output_percent}
        allowed = set(rising)
        if load is not None:
            def reasons(vector):
                return _soft_reasons(load(vector), profile, overg=overg, crew_safety_lock_enabled=crew_lock)

            current_load = load(current_outputs)
            current_reasons = reasons(current_outputs)
            for d, old in enumerate(governors):
                active = _active_reasons(old.reasons, overg=overg, crew_safety_lock_enabled=crew_lock)
                if not active:
                    governors[d] = Governor()
                elif current_reasons or not _release_safe(current_load, active, profile):
                    governors[d] = replace(old, reasons=active, release_since=None)
                else:
                    since = n if old.release_since is None else old.release_since
                    governors[d] = Governor() if n - since + 1 >= profile.release_hold_steps else replace(old, release_since=since)

            def limit(d, cap, why):
                old = governors[d]
                cap = min(old.ceiling, cap)
                if cap < 100:
                    governors[d] = Governor(cap, tuple(r for r in ("structure_limit", "crew_limit") if r in old.reasons or r in why),
                        n if old.limited_since is None else old.limited_since, None)

            for i, old, new, _ in due:
                d = self.channels[i]
                if i in rising:
                    if current_reasons:
                        limit(d, old.actual_output_percent, current_reasons)
                    if current_reasons or new.actual_output_percent > min(targets[d], governors[d].ceiling):
                        allowed.discard(i)

            def projected(eligible):
                vector = list(current_outputs)
                for i, old, new, _ in due:
                    if i not in rising or i in eligible:
                        delta = new.actual_output_percent - old.actual_output_percent
                        vector = [v + delta * c for v, c in zip(vector, self.resources.engines[i].contribution_units)]
                return tuple(vector)

            committed = projected(allowed)
            remaining = reasons(committed)
            if remaining and allowed:
                for i, old, _, _ in due:
                    if i in allowed:
                        limit(self.channels[i], old.actual_output_percent, remaining)
                allowed.clear()
                committed = projected(allowed)
                remaining = reasons(committed)
            if remaining:
                # Only unsafe boundaries scan engines to evaluate capped alternatives.
                values = [slot.engine.actual_output_percent for slot in (source.engines if slots is None else slots)]
                for i, _, new, _ in due:
                    if i not in rising or i in allowed:
                        values[i] = new.actual_output_percent
                selected = 0
                for cap in reversed(THRUST_OUTPUT_STAGES_PERCENT):
                    if cap > max(values, default=0):
                        continue
                    vector = [0] * 6
                    for i, value in enumerate(values):
                        d = self.channels[i]
                        if d is not None:
                            percent = min(value, targets[d], governors[d].ceiling, cap)
                            for axis, contribution in enumerate(self.resources.engines[i].contribution_units):
                                vector[axis] += percent * contribution
                    if not reasons(tuple(vector)):
                        selected = cap
                        break
                for d in range(6):
                    if requested[d] or outputs[d]:
                        limit(d, min(selected, requested[d]), remaining)

        for i, old, new, time_events in due:
            if i not in rising or i in allowed:
                put(i, replace(get(i), engine=new))
                emitted.extend((n, e.kind, e.actuator_instance_id, e.resulting_stage_percent) for e in time_events)
        effective = tuple(min(targets[d], governors[d].ceiling) for d in range(6))
        touched = set(changed) | {i for i, *_ in due}
        for d in range(6):
            if effective[d] != source.targets[d]:
                touched.update(self.members[d])
        for i in sorted(touched):
            slot = get(i)
            d = self.channels[i]
            target = 0 if d is None or any(slot.blocked) else effective[d]
            state = slot.engine
            # Translate effective targets without changing the original player request.
            notch = NOTCH.get(requested[d], "stop") if state.actuator_category == "main_engine" and d is not None else None
            if state.phase != "tripped" and (target != state.target_output_percent or state.next_transition_step == n or state.commanded_notch != notch):
                state, command_events = _apply_command(state, self.timings[i], n, notch, target)
                put(i, replace(slot, engine=state))
                emitted.extend((n, e.kind, e.actuator_instance_id, e.resulting_stage_percent) for e in command_events)
        for d, (old, new) in enumerate(zip(source.governors, governors)):
            if (old.ceiling, old.reasons) != (new.ceiling, new.reasons):
                emitted.append((n, "safety", DIRECTIONAL_CHANNELS[d], new.ceiling))
        result = PropulsionState(source.engines if slots is None else tuple(slots), tuple(available), tuple(outputs),
            source.schedule if schedule is None else tuple(sorted((step, i) for i, step in schedule.items())),
            effective, tuple(governors), phase_revision)
        return (source if result == source else result), tuple(emitted)


def actuation(contributions, outputs):
    scale = contributions.unit_denominator * 100
    force = dynamics.Vec2((outputs[3] - outputs[2]) / scale, (outputs[0] - outputs[1]) / scale)
    torque = (outputs[4] - outputs[5]) / scale
    return dynamics.AllocatedActuation(force, dynamics.Vec2(), 0.0, torque, 0.0, 0.0)


def validate_motion(motion):
    require(all(isfinite(x) for x in (motion.position_world_m.x, motion.position_world_m.y,
        motion.velocity_world_mps.x, motion.velocity_world_mps.y, motion.heading_rad, motion.yaw_rate_radps,
        motion.fuel_units, motion.hull_integrity_fraction)), "Non-finite motion")
    require(motion.fuel_units >= 0 and 0 <= motion.hull_integrity_fraction <= 1, "Invalid motion range")


class SimplifiedFlightSession:
    def __init__(self, seeds, safety_profile, *, direct_ship_id, allow_test_device_rebuild=False):
        seeds = tuple(seeds)
        require_deeply_immutable((seeds, safety_profile))
        require(seeds and len({s.contributions.ship_id for s in seeds}) == len(seeds), "Unique ships required")
        require(direct_ship_id in {s.contributions.ship_id for s in seeds}, "Unknown direct ship")
        require(canonical_sha256(safety_profile) == safety_profile.source_sha256, "Invalid safety resource")
        self._seeds, self._profile, self._direct = seeds, safety_profile, direct_ship_id
        self._kernels = tuple(PropulsionKernel(s.contributions) for s in seeds)
        require(type(allow_test_device_rebuild) is bool, "Invalid fixture rebuild option")
        self._allow_test_device_rebuild = allow_test_device_rebuild
        self._device_kernels = tuple(DeviceKernel(s.devices, s.contributions) if s.devices is not None else None for s in seeds)
        require(all(s.resources is None or dk is not None for s,dk in zip(seeds,self._device_kernels)),
            "Resources require the device domain")
        self._resource_kernels = tuple(ResourceKernel(s.resources,dk,s.contributions) if s.resources is not None else None
            for s,dk in zip(seeds,self._device_kernels))
        require(all(s.command is None or rk is not None for s,rk in zip(seeds,self._resource_kernels)), 'Command requires resources')
        self._command_kernels=tuple(CommandKernel(s.command,rk,s.contributions,direct=s.contributions.ship_id==direct_ship_id)
            if s.command is not None else None for s,rk in zip(seeds,self._resource_kernels))
        ships = []
        for seed, kernel, device_kernel, rk, ck in zip(seeds, self._kernels, self._device_kernels, self._resource_kernels,self._command_kernels):
            r = seed.model.runtime
            require(all(isfinite(v) and v > 0 for v in (r.current_mass_kg, r.current_inertia_kg_m2,
                r.safe_longitudinal_mps2, r.safe_lateral_mps2)), "Invalid motion parameters")
            require(seed.model.tuning.fixed_step_s == 1 / 60, "Only 60 Hz is supported")
            require(seed.motion.fixed_step_index == 0 and seed.motion.layer_transition is None, "Fresh level-flight scene required")
            validate_motion(seed.motion)
            devices, blockers = None, seed.initial_blockers
            if device_kernel is not None:
                require(not any(r in OWNED_REASONS for _, reasons in blockers for r in reasons),
                    "Device-owned initial reasons must derive from durability")
                devices = device_kernel.initial()
                merged = dict(blockers)
                require(len(merged) == len(blockers), "Duplicate initial blocker engine")
                for name, reasons in device_kernel.initial_blockers(devices):
                    merged[name] = (*merged.get(name, ()), *reasons)
                blockers = tuple(merged.items())
            propulsion=kernel.initial(seed.initially_ready, blockers)
            resources=None
            if rk is not None:
                require(not any(r in RESOURCE_REASONS for _,rs in blockers for r in rs), 'Resource-owned initial blockers')
                resources,updates=rk.resolve(rk.initial(),devices,propulsion)
                events=tuple(AvailabilityEvent('',seed.contributions.ship_id,*u,0,'opening') for u in updates)
                if events:
                    propulsion,_=kernel.boundary(propulsion,0,(0,)*6,events)
            command=None
            allowed=seed.contributions.ship_id==direct_ship_id
            if ck is not None:
                command=ck.resolve(ck.initial(),devices,resources,seed.motion,mass=r.current_mass_kg,step=0)
                allowed=command.allowed
                cut=command.suppress or ck.direct and not command.allowed
                if cut:
                    changes=tuple(AvailabilityEvent('',seed.contributions.ship_id,e.instance_id,'command_unavailable',True,command.revision,0,'opening')
                        for e in seed.contributions.engines)
                    propulsion,_=kernel.boundary(propulsion,0,(0,)*6,changes)
            ships.append(FlightShip(seed.contributions.ship_id, seed.motion,propulsion,directional_control(),
                allowed, devices=devices,resources=resources,command=command))
        self._world = FlightWorld(uuid4().hex, 0, tuple(ships))
        self._owner, self._executing, self._last = get_ident(), False, None

    @property
    def world(self):
        return self._world

    @property
    def last_result(self):
        return self._last

    def _requested(self, ship, state, control):
        if not ship.authority_allowed or ship.motion.hull_integrity_fraction <= 0:
            return (0,) * 6
        if control.automatic_brake:
            velocity = dynamics.world_to_body(ship.motion.velocity_world_mps, ship.motion.heading_rad)
            selection = automatic_linear_brake_control(lateral_velocity_body_mps=velocity.x,
                longitudinal_velocity_body_mps=velocity.y,
                available_translation_channels=tuple(DIRECTIONAL_CHANNELS[d] for d in range(4) if state.available_units[d]),
                overg_requested=control.overg_requested)
            control = selection.control
        return tuple(c.requested_percent for c in control.channel_commands)

    def step(self, control=None, *, ship_id=None, events=(), authority_events=(), device_operations=(), resource_operations=(), exit_operations=(), project=None):
        require(get_ident() == self._owner and not self._executing, "Single non-reentrant authority required")
        before = self._world
        ship_id = self._direct if ship_id is None else ship_id
        if control is not None:
            require(ship_id == self._direct and any(s.ship_id == ship_id and s.authority_allowed
                and s.motion.hull_integrity_fraction > 0 for s in before.ships), "Direct control denied")
            control = DirectionalPropulsionControlInput.parse(control.to_dict() if isinstance(control, DirectionalPropulsionControlInput) else control)
        events, authority_events = tuple(events), tuple(authority_events)
        indexes = {s.ship_id: i for i, s in enumerate(before.ships)}
        device_operations = tuple(device_operations)
        resource_operations = tuple(resource_operations)
        exit_operations=tuple(exit_operations)
        seen_exits=set()
        for op in exit_operations:
            require(type(op) is ExitOperation and op.epoch==before.epoch and type(op.ship_id) is str and op.ship_id in indexes
                and self._command_kernels[indexes[op.ship_id]] is not None, 'Invalid exit operation')
            require(op.phase in ('opening','closing') and type(op.fixed_step) is int
                and op.fixed_step==before.fixed_step+(op.phase=='closing') and (op.ship_id,op.phase) not in seen_exits,'Invalid exit boundary')
            seen_exits.add((op.ship_id,op.phase))
        for op in resource_operations:
            require(type(op) is ResourceOperation and type(op.ship_id) is str and op.ship_id in indexes
                and self._resource_kernels[indexes[op.ship_id]] is not None, 'Unknown resource operation ship')
            require(op.phase in ('opening','closing') and type(op.fixed_step) is int
                and op.fixed_step==before.fixed_step+(op.phase=='closing'), 'Wrong resource operation boundary')
        for op in device_operations:
            require(type(op) is DeviceOperation and type(op.ship_id) is str and op.ship_id in indexes,
                "Unknown device operation ship")
            dk = self._device_kernels[indexes[op.ship_id]]
            require(dk is not None, "Device domain is not enabled")
            dk.validate_operation(op, epoch=before.epoch, ship_id=op.ship_id, step=before.fixed_step,
                allow_rebuild=self._allow_test_device_rebuild)
        for e in (*events, *authority_events):
            require(type(e) in (AvailabilityEvent, AuthorityEvent), "Unknown event type")
            require(e.epoch == before.epoch and e.ship_id in indexes and e.phase in ("opening", "closing"), "Foreign event")
            require(type(e.fixed_step) is int and e.fixed_step == before.fixed_step + (e.phase == "closing"), "Wrong event boundary")
            require(type(e.version) is int and e.version > 0, "Invalid event version")
        for e in events:
            require(type(e) is AvailabilityEvent and type(e.active) is bool and e.reason in REASONS[:-1]
                and e.engine_id in self._kernels[indexes[e.ship_id]].by_id, "Invalid availability event")
            require(self._device_kernels[indexes[e.ship_id]] is None or e.reason not in OWNED_REASONS,
                "Device-owned availability must be produced by the device domain")
            require(self._resource_kernels[indexes[e.ship_id]] is None or e.reason not in RESOURCE_REASONS,
                'Resource-owned availability must derive from resource state')
            require(self._command_kernels[indexes[e.ship_id]] is None or e.reason!='command_unavailable',
                'Command availability must derive from command state')
        for e in authority_events:
            require(type(e) is AuthorityEvent and type(e.allowed) is bool and (not e.allowed or
                e.ship_id == self._direct and before.ships[indexes[e.ship_id]].motion.hull_integrity_fraction > 0), "Invalid authority event")
            require(self._command_kernels[indexes[e.ship_id]] is None,'Authority must derive from command domain')
        self._executing = True
        try:
            candidates, emitted, diagnostics = [], [], []
            for seed, kernel, dk, rk, ck, original in zip(self._seeds, self._kernels, self._device_kernels, self._resource_kernels,self._command_kernels, before.ships):
                ship = original
                selected = control if control is not None and ship.ship_id == ship_id else ship.control
                for phase in ("opening", "closing"):
                    n = before.fixed_step + (phase == "closing")
                    if ship.command is not None and ship.command.lifecycle.physical_status=='exited':
                        require(not any(op.ship_id==ship.ship_id and op.phase==phase for op in (*device_operations,*resource_operations,*exit_operations)),
                            'Exited ship cannot receive domain operations')
                        if phase=='opening':
                            ship=replace(ship,motion=replace(ship.motion,fixed_step_index=n+1))
                            diagnostics.append((ship.ship_id,None))
                        continue
                    changes = [e for e in authority_events if e.ship_id == ship.ship_id and e.phase == phase]
                    for e in sorted(changes, key=lambda e: e.version):
                        if e.version == ship.authority_version:
                            require(e.allowed == ship.authority_allowed, "Conflicting authority version")
                        if e.version > ship.authority_version:
                            ship = replace(ship, authority_allowed=e.allowed, authority_version=e.version)
                            emitted.append((n, "authority", ship.ship_id, e.allowed))
                    if not ship.authority_allowed:
                        selected = directional_control()
                    device_events = tuple(e for e in events if e.ship_id == ship.ship_id and e.phase == phase)
                    if dk is not None:
                        operations = tuple(op for op in device_operations if op.ship_id == ship.ship_id and op.phase == phase)
                        devices, updates, receipts, affected = dk.boundary(ship.devices, operations)
                        ship = replace(ship, devices=devices)
                        device_events += tuple(AvailabilityEvent(before.epoch, ship.ship_id, name, reason, active, version, n, phase)
                            for name, reason, active, version in updates)
                        if receipts:
                            emitted.append((ship.ship_id, phase, "devices", devices.revision, receipts, affected))
                    if rk is not None:
                        ops=tuple(op for op in resource_operations if op.ship_id==ship.ship_id and op.phase==phase)
                        resources,resets,receipts=rk.operations(ship.resources,ops,epoch=before.epoch,ship_id=ship.ship_id,
                            step=n,phase=phase,can_reset=ship.authority_allowed and ship.motion.hull_integrity_fraction>0)
                        # New device facts participate in reset checks before propulsion is mutated.
                        if resets:
                            require(not any(active and name in resets for name,_,active,_ in updates), 'Reset blocked by new device fault')
                            require(not any(e.active and e.engine_id in resets and e.version > ship.propulsion.engines[
                                kernel.by_id[e.engine_id]].versions[REASONS.index(e.reason)] for e in device_events),
                                'Reset blocked by new external fault')
                        resources,resource_updates=rk.resolve(resources,ship.devices,ship.propulsion,resets=resets)
                        ship=replace(ship,resources=resources)
                        device_events += tuple(AvailabilityEvent(before.epoch,ship.ship_id,*u,n,phase) for u in resource_updates)
                        if receipts:
                            emitted.append((ship.ship_id,phase,'resources',resources.sequence,receipts))
                    def command_boundary(current,propulsion,exit_reason=None):
                        if ck is None:
                            return current,()
                        command=ck.resolve(current.command,current.devices,current.resources,current.motion,
                            mass=seed.model.runtime.current_mass_kg,step=n,exit_reason=exit_reason)
                        if command is current.command:
                            return current,()
                        if (command.lifecycle,command.fleet_phase)!=(current.command.lifecycle,current.command.fleet_phase):
                            emitted.append((ship.ship_id,phase,'command',command.lifecycle,command.fleet_phase,command.loss_reason))
                        current=replace(current,command=command,authority_allowed=command.allowed,authority_version=command.revision)
                        cut=command.suppress or ck.direct and not command.allowed
                        r=REASONS.index('command_unavailable')
                        changes=tuple(AvailabilityEvent(before.epoch,ship.ship_id,e.instance_id,'command_unavailable',cut,command.revision,n,phase)
                            for e,slot in zip(seed.contributions.engines,propulsion.engines) if slot.blocked[r]!=cut)
                        return current,changes
                    exit_reason=next((op.reason for op in exit_operations if op.ship_id==ship.ship_id and op.phase==phase),None)
                    ship,command_events=command_boundary(ship,ship.propulsion,exit_reason)
                    device_events+=command_events
                    if ck is not None:
                        require(not resets or ship.authority_allowed,'Reset denied by current command state')
                    if not ship.authority_allowed:
                        selected=directional_control()
                    if ship.motion.hull_integrity_fraction <= 0:
                        selected = directional_control()
                        ship = replace(ship, authority_allowed=False)
                        if any(ship.propulsion.available_units):
                            device_events += tuple(AvailabilityEvent(before.epoch, ship.ship_id, e.instance_id,
                                "lifecycle_unavailable", True, slot.versions[-1] + 1, n, phase)
                                for e, slot in zip(seed.contributions.engines, ship.propulsion.engines))
                    requested = self._requested(ship, ship.propulsion, selected)
                    model=seed.model
                    if ck is not None and model.runtime.crew_safety_lock_enabled!=ship.command.crew_lock:
                        model=replace(model,runtime=replace(model.runtime,crew_safety_lock_enabled=ship.command.crew_lock))
                    drag = dynamics.calculate_tactical_drag(model, ship.motion)
                    def load(outputs):
                        metrics = dynamics._load_metrics(model, ship.motion, actuation(seed.contributions, outputs),
                            drag.force_world_n, 1.0, 1 / 60)
                        require(isfinite(metrics.structure_ratio) and isfinite(metrics.crew_g), "Non-finite safety load")
                        return metrics
                    state, facts = kernel.boundary(ship.propulsion, n, requested, device_events,
                        load=load if phase == "closing" else None, profile=self._profile,
                        overg=selected.overg_requested, crew_lock=model.runtime.crew_safety_lock_enabled)
                    emitted.extend((ship.ship_id, phase, fact) for fact in facts)
                    if rk is not None:
                        # A command/due transition can change demand at this very boundary.
                        # Cuts can invalidate a host or release shared resources. Latches
                        # only accumulate here; a bounded cascade must settle before delivery.
                        for _ in range(len(seed.contributions.engines)+2):
                            resources,resource_updates=rk.resolve(ship.resources,ship.devices,state)
                            ship=replace(ship,resources=resources)
                            ship,command_events=command_boundary(ship,state)
                            if not ship.authority_allowed:
                                selected=directional_control()
                                requested=(0,)*6
                            if ck is not None:
                                require(not resets or ship.authority_allowed,'Reset invalidated by command dependency')
                            if not resource_updates and not command_events:
                                break
                            changes=tuple(AvailabilityEvent(before.epoch,ship.ship_id,*u,n,phase) for u in resource_updates)+command_events
                            state,cuts=kernel.boundary(state,n,requested,changes)
                            emitted.extend((ship.ship_id,phase,fact) for fact in cuts)
                        else:
                            require(False, 'Resource cascade failed to settle')
                    ship = replace(ship, propulsion=state, control=selected)
                    if phase == "opening":
                        if ship.command is not None and ship.command.lifecycle.physical_status=='exited':
                            ship=replace(ship,motion=replace(ship.motion,fixed_step_index=n+1))
                            diagnostics.append((ship.ship_id,None))
                            continue
                        delivery = actuation(seed.contributions, state.output_percent_units)
                        metrics = load(state.output_percent_units)
                        motion, diagnostic = dynamics._integrate_delivered_actuation(model, ship.motion,
                            delivery, drag, 1.0, metrics, 1 / 60)
                        validate_motion(motion)
                        require(all(isfinite(v) for v in (diagnostic.structure_ratio, diagnostic.crew_g,
                            diagnostic.hull_integrity_damage)), "Non-finite diagnostics")
                        ship = replace(ship, motion=motion)
                        diagnostics.append((ship.ship_id, diagnostic))
                candidates.append(ship)
            candidate = FlightWorld(before.epoch, before.fixed_step + 1, tuple(candidates))
            result = StepResult(candidate.fixed_step, tuple(emitted), tuple(diagnostics))
            if project is not None:
                project(candidate, result)
            self._world, self._last = candidate, result
            return result
        finally:
            self._executing = False


def build_sample_session(root, *, with_devices=False, with_resources=False, with_command=False, allow_test_device_rebuild=False):
    """Strict E1 setup + I9 authorization once; no legacy scene in the new loop."""
    from .realtime_flight import RealtimeFlightSession
    from 高天荒野舰艇运行时参数编译器 import compile_runtime_ship_parameters
    from 高天荒野舰艇定向直控仲裁 import prepare_directional_direct_command
    from .tactical_devices import seed_from_snapshot
    from .tactical_resources_runtime import seed_from_snapshot as resource_seed
    from .tactical_command_runtime import seed_from_snapshot as command_seed
    old = RealtimeFlightSession(root)
    prepare_directional_direct_command(old.world.scene, old.world.command,
        old.resources.command_tuning, "ship.web.blue")
    seeds = []
    for ship, resources, table in zip(old.world.scene.ships, old.resources.ships, old.resources.simplified_propulsion):
        require(ship.ship_id == resources.ship_id == table.ship_id, "Source ordering mismatch")
        runtime = compile_runtime_ship_parameters(resources.snapshot, resources.sortie, ship.combat_state.instance)
        # The current named sample is intact and fully available. Do not silently
        # generalize this adapter to damaged player designs or active processes.
        require(not runtime.terminal_failures and runtime.fuel_available, "Sample requires operational equipment")
        engine_ids = {e.instance_id for e in table.engines}
        require(all(m.active_available and m.durability_fraction == 1 for m in runtime.modules if m.instance_id in engine_ids),
            "Sample adapter requires intact available engines")
        model = MotionModel.from_legacy(dynamics.build_tactical_ship_model(runtime, resources.snapshot))
        devices = seed_from_snapshot(resources.snapshot, runtime.instance_snapshot, table) if with_devices or with_resources or with_command else None
        supply = resource_seed(resources.snapshot,runtime.instance_snapshot,table) if with_resources or with_command else None
        command=command_seed(resources.snapshot,runtime.instance_snapshot,resources.sortie,table) if with_command else None
        seeds.append(ShipSeed(table, model, ship.motion_state, devices=devices,resources=supply,command=command))
    return SimplifiedFlightSession(seeds, old.resources.propulsion.safety_profile, direct_ship_id="ship.web.blue",
        allow_test_device_rebuild=allow_test_device_rebuild)
