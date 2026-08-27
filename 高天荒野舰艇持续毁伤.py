"""阶段 I11a：显式点燃、持久火灾与损管队固定步求解。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    DamageControlAssignmentInput,
    FireIncidentStateInput,
    RESOURCE_ID_PATTERN,
    ResourceReference,
    ShipContinuousDamageStateInput,
    ShipInstanceSnapshotInput,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters
from 高天荒野舰艇战损原子操作 import apply_module_damage_to_instance


CONTINUOUS_DAMAGE_SCHEMA_ID = "gaotian.continuous-damage/v1alpha1"
CONTINUOUS_DAMAGE_INTERFACE_ID = "gaotian.ship-continuous-damage/v1alpha1"
CONTINUOUS_DAMAGE_POLICY_ID = (
    "gaotian.continuous-damage/explicit-ignition-boundary-firefighting/v1"
)
FIXTURE_LEVELS = {"contract_fixture", "prototype_unbalanced", "balance_reference"}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("resource.id_invalid", path, str(value))
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("type.string", path, "必须是非空字符串")
    return value


def _number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type.number", path, "必须是数值")
    result = float(value)
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ContractError("value.number_range", path, str(value))
    return result


def _positive(value: Any, path: str) -> float:
    result = _number(value, path)
    if result <= 0.0:
        raise ContractError("value.positive", path, "必须为正有限数")
    return result


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError("type.integer", path, f"必须为大于等于 {minimum} 的整数")
    return value


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object.keys", path, f"必须恰含 {sorted(keys)}")
    return value


@dataclass(frozen=True)
class ContinuousDamageProfile:
    id: str
    version: int
    name: str
    fixture_level: str
    module_damage_points_per_intensity_s: float
    hull_integrity_fraction_per_intensity_s: float
    fuel_units_burned_per_intensity_s: float
    natural_intensity_decay_units_per_s: float
    suppression_units_per_team_efficiency_s: float

    @property
    def reference(self) -> ResourceReference:
        return ResourceReference(self.id, self.version)

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    @classmethod
    def parse(cls, value: Any, path: str = "$") -> "ContinuousDamageProfile":
        obj = _exact_object(
            value,
            {
                "schema",
                "kind",
                "id",
                "version",
                "name",
                "fixture_level",
                "module_damage_points_per_intensity_s",
                "hull_integrity_fraction_per_intensity_s",
                "fuel_units_burned_per_intensity_s",
                "natural_intensity_decay_units_per_s",
                "suppression_units_per_team_efficiency_s",
            },
            path,
        )
        if (
            obj["schema"] != CONTINUOUS_DAMAGE_SCHEMA_ID
            or obj["kind"] != "ContinuousDamageProfile"
        ):
            raise ContractError("resource.kind", path, "不是持续毁伤配置")
        fixture_level = obj["fixture_level"]
        if fixture_level not in FIXTURE_LEVELS:
            raise ContractError(
                "continuous_damage.fixture_level",
                f"{path}.fixture_level",
                str(fixture_level),
            )
        hull_rate = _number(
            obj["hull_integrity_fraction_per_intensity_s"],
            f"{path}.hull_integrity_fraction_per_intensity_s",
            0.0,
        )
        if hull_rate > 1.0:
            raise ContractError(
                "continuous_damage.hull_rate",
                f"{path}.hull_integrity_fraction_per_intensity_s",
                "单位强度每秒船壳损伤比例不得超过 1",
            )
        return cls(
            _resource_id(obj["id"], f"{path}.id"),
            _integer(obj["version"], f"{path}.version", 1),
            _string(obj["name"], f"{path}.name"),
            fixture_level,
            _number(
                obj["module_damage_points_per_intensity_s"],
                f"{path}.module_damage_points_per_intensity_s",
                0.0,
            ),
            hull_rate,
            _positive(
                obj["fuel_units_burned_per_intensity_s"],
                f"{path}.fuel_units_burned_per_intensity_s",
            ),
            _number(
                obj["natural_intensity_decay_units_per_s"],
                f"{path}.natural_intensity_decay_units_per_s",
                0.0,
            ),
            _positive(
                obj["suppression_units_per_team_efficiency_s"],
                f"{path}.suppression_units_per_team_efficiency_s",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_level": self.fixture_level,
            "fuel_units_burned_per_intensity_s": self.fuel_units_burned_per_intensity_s,
            "hull_integrity_fraction_per_intensity_s": self.hull_integrity_fraction_per_intensity_s,
            "id": self.id,
            "kind": "ContinuousDamageProfile",
            "module_damage_points_per_intensity_s": self.module_damage_points_per_intensity_s,
            "name": self.name,
            "natural_intensity_decay_units_per_s": self.natural_intensity_decay_units_per_s,
            "schema": CONTINUOUS_DAMAGE_SCHEMA_ID,
            "suppression_units_per_team_efficiency_s": self.suppression_units_per_team_efficiency_s,
            "version": self.version,
        }


@dataclass(frozen=True)
class FireIgnitionOutcome:
    projectile_id: str
    incident_id: str
    target_ship_id: str
    target_module_instance_id: str
    initial_intensity_units: float
    initial_fuel_units: float

    def validate(self, path: str = "$") -> None:
        _resource_id(self.projectile_id, f"{path}.projectile_id")
        _resource_id(self.incident_id, f"{path}.incident_id")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _resource_id(
            self.target_module_instance_id,
            f"{path}.target_module_instance_id",
        )
        _positive(self.initial_intensity_units, f"{path}.initial_intensity_units")
        _positive(self.initial_fuel_units, f"{path}.initial_fuel_units")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "incident_id": self.incident_id,
            "initial_fuel_units": self.initial_fuel_units,
            "initial_intensity_units": self.initial_intensity_units,
            "projectile_id": self.projectile_id,
            "target_module_instance_id": self.target_module_instance_id,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class DamageControlDirective:
    ship_id: str
    damage_control_module_instance_id: str
    team_index: int
    fire_incident_id: str | None

    @property
    def slot(self) -> tuple[str, str, int]:
        return self.ship_id, self.damage_control_module_instance_id, self.team_index

    def validate(self, path: str = "$") -> None:
        _resource_id(self.ship_id, f"{path}.ship_id")
        _resource_id(
            self.damage_control_module_instance_id,
            f"{path}.damage_control_module_instance_id",
        )
        _integer(self.team_index, f"{path}.team_index", 0)
        if self.fire_incident_id is not None:
            _resource_id(self.fire_incident_id, f"{path}.fire_incident_id")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "damage_control_module_instance_id": self.damage_control_module_instance_id,
            "fire_incident_id": self.fire_incident_id,
            "ship_id": self.ship_id,
            "team_index": self.team_index,
        }


@dataclass(frozen=True)
class ContinuousDamageEvent:
    ship_id: str
    tactical_time_s: float
    event_kind: str
    fire_incident_id: str
    target_module_instance_id: str | None = None
    damage_control_module_instance_id: str | None = None
    team_index: int | None = None
    module_damage_points: float | None = None
    hull_damage_fraction: float | None = None
    intensity_before: float | None = None
    intensity_after: float | None = None
    fuel_before: float | None = None
    fuel_after: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event_kind": self.event_kind,
            "fire_incident_id": self.fire_incident_id,
            "ship_id": self.ship_id,
            "tactical_time_s": self.tactical_time_s,
        }
        for key in (
            "target_module_instance_id",
            "damage_control_module_instance_id",
            "team_index",
            "module_damage_points",
            "hull_damage_fraction",
            "intensity_before",
            "intensity_after",
            "fuel_before",
            "fuel_after",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class ContinuousDamageResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    events: tuple[ContinuousDamageEvent, ...]


def initialize_continuous_damage_state(
    profile: ContinuousDamageProfile,
    *,
    tactical_time_s: float,
) -> ShipContinuousDamageStateInput:
    return ShipContinuousDamageStateInput(
        profile.reference,
        profile.source_sha256,
        _number(tactical_time_s, "$.tactical_time_s", 0.0),
        (),
        (),
    )


def _module_map(snapshot: DerivedShipSnapshot) -> dict[str, Any]:
    return {item.id: item for item in snapshot.outfit.instances}


def _damage_control_capacity(module: Any) -> tuple[int, int]:
    capability = module.prototype.capability.to_dict()
    return int(capability["team_capacity"]), int(capability["simultaneous_incidents"])


def validate_continuous_damage_state(
    snapshot: DerivedShipSnapshot,
    state: ShipContinuousDamageStateInput,
    profile: ContinuousDamageProfile,
) -> None:
    if not isfinite(state.tactical_time_s) or state.tactical_time_s < 0.0:
        raise ContractError(
            "continuous_damage.clock",
            "$.continuous_damage_state.tactical_time_s",
            str(state.tactical_time_s),
        )
    if state.profile != profile.reference or state.profile_sha256 != profile.source_sha256:
        raise ContractError(
            "continuous_damage.profile_mismatch",
            "$.continuous_damage_state.profile",
            "持续毁伤状态绑定了其他配置或内容指纹",
        )
    modules = _module_map(snapshot)
    fire_ids = {item.id for item in state.fire_incidents}
    if len(fire_ids) != len(state.fire_incidents):
        raise ContractError(
            "continuous_damage.fire_duplicate",
            "$.continuous_damage_state.fire_incidents",
            "火灾事件 id 不得重复",
        )
    for fire in state.fire_incidents:
        if (
            not isfinite(fire.created_time_s)
            or fire.created_time_s < 0.0
            or fire.created_time_s > state.tactical_time_s + EPS
        ):
            raise ContractError(
                "continuous_damage.fire_from_future",
                "$.continuous_damage_state.fire_incidents.created_time_s",
                str(fire.created_time_s),
            )
        if (
            not isfinite(fire.intensity_units)
            or fire.intensity_units <= 0.0
            or not isfinite(fire.remaining_fuel_units)
            or fire.remaining_fuel_units <= 0.0
        ):
            raise ContractError(
                "continuous_damage.fire_nonpositive",
                "$.continuous_damage_state.fire_incidents",
                fire.id,
            )
    unknown_targets = sorted(
        {
            item.target_module_instance_id
            for item in state.fire_incidents
            if item.target_module_instance_id not in modules
        }
    )
    if unknown_targets:
        raise ContractError(
            "continuous_damage.fire_target_missing",
            "$.continuous_damage_state.fire_incidents",
            str(unknown_targets),
        )
    if len({item.slot for item in state.damage_control_assignments}) != len(
        state.damage_control_assignments
    ):
        raise ContractError(
            "continuous_damage.assignment_slot_duplicate",
            "$.continuous_damage_state.damage_control_assignments",
            "同一损管队槽位不得重复分配",
        )
    unknown_fires = sorted(
        {
            item.fire_incident_id
            for item in state.damage_control_assignments
            if item.fire_incident_id not in fire_ids
        }
    )
    if unknown_fires:
        raise ContractError(
            "continuous_damage.assignment_fire_missing",
            "$.continuous_damage_state.damage_control_assignments",
            str(unknown_fires),
        )
    assignments_by_module: dict[str, list[DamageControlAssignmentInput]] = {}
    for assignment in state.damage_control_assignments:
        module = modules.get(assignment.damage_control_module_instance_id)
        if module is None or module.prototype.category != "damage_control":
            raise ContractError(
                "continuous_damage.damage_control_module",
                "$.continuous_damage_state.damage_control_assignments",
                assignment.damage_control_module_instance_id,
            )
        team_capacity, _ = _damage_control_capacity(module)
        if assignment.team_index >= team_capacity:
            raise ContractError(
                "continuous_damage.team_index",
                "$.continuous_damage_state.damage_control_assignments.team_index",
                str(assignment.team_index),
            )
        assignments_by_module.setdefault(module.id, []).append(assignment)
    for module_id, assignments in assignments_by_module.items():
        module = modules[module_id]
        team_capacity, simultaneous = _damage_control_capacity(module)
        if len(assignments) > team_capacity:
            raise ContractError(
                "continuous_damage.team_capacity",
                "$.continuous_damage_state.damage_control_assignments",
                module_id,
            )
        if len({item.fire_incident_id for item in assignments}) > simultaneous:
            raise ContractError(
                "continuous_damage.simultaneous_incidents",
                "$.continuous_damage_state.damage_control_assignments",
                module_id,
            )


def validate_instance_continuous_damage(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    profile: ContinuousDamageProfile,
) -> None:
    state = instance.continuous_damage_state
    if state is not None:
        validate_continuous_damage_state(snapshot, state, profile)


def continuous_damage_automatic_events(
    instance: ShipInstanceSnapshotInput,
) -> tuple[str, ...]:
    state = instance.continuous_damage_state
    return (
        ("ship.damage_control_required",)
        if state is not None and state.fire_incidents
        else ()
    )


def apply_damage_control_directives(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    profile: ContinuousDamageProfile,
    *,
    ship_id: str,
    tactical_time_s: float,
    directives: Iterable[DamageControlDirective],
) -> ContinuousDamageResolution:
    source_sha256 = canonical_sha256(instance)
    now = _number(tactical_time_s, "$.tactical_time_s", 0.0)
    items = tuple(directives)
    for index, item in enumerate(items):
        item.validate(f"$.damage_control_directives[{index}]")
        if item.ship_id != ship_id:
            raise ContractError(
                "continuous_damage.directive_ship_mismatch",
                f"$.damage_control_directives[{index}].ship_id",
                ship_id,
            )
    if len({item.slot for item in items}) != len(items):
        raise ContractError(
            "continuous_damage.directive_slot_duplicate",
            "$.damage_control_directives",
            "同一固定步不得重复修改同一损管队槽位",
        )
    state = instance.continuous_damage_state
    if state is None:
        if items:
            raise ContractError(
                "continuous_damage.state_required",
                "$.continuous_damage_state",
                "没有持续毁伤状态时不能分配损管队",
            )
        return ContinuousDamageResolution(source_sha256, instance, ())
    validate_continuous_damage_state(snapshot, state, profile)
    if abs(state.tactical_time_s - now) > EPS:
        raise ContractError(
            "continuous_damage.clock_mismatch",
            "$.continuous_damage_state.tactical_time_s",
            "损管指令只能作用于当前场景边界",
        )
    assignments = {item.slot: item for item in state.damage_control_assignments}
    fires = {item.id: item for item in state.fire_incidents}
    modules = _module_map(snapshot)
    events: list[ContinuousDamageEvent] = []
    for directive in sorted(items, key=lambda item: item.slot):
        module = modules.get(directive.damage_control_module_instance_id)
        if module is None or module.prototype.category != "damage_control":
            raise ContractError(
                "continuous_damage.damage_control_module",
                "$.damage_control_directives.damage_control_module_instance_id",
                directive.damage_control_module_instance_id,
            )
        team_capacity, _ = _damage_control_capacity(module)
        if directive.team_index >= team_capacity:
            raise ContractError(
                "continuous_damage.team_index",
                "$.damage_control_directives.team_index",
                str(directive.team_index),
            )
        slot = directive.damage_control_module_instance_id, directive.team_index
        previous = assignments.get(slot)
        if directive.fire_incident_id is None:
            if previous is not None:
                assignments.pop(slot)
                events.append(
                    ContinuousDamageEvent(
                        ship_id,
                        now,
                        "damage_control_assignment_cleared",
                        previous.fire_incident_id,
                        damage_control_module_instance_id=slot[0],
                        team_index=slot[1],
                    )
                )
            continue
        if directive.fire_incident_id not in fires:
            raise ContractError(
                "continuous_damage.assignment_fire_missing",
                "$.damage_control_directives.fire_incident_id",
                directive.fire_incident_id,
            )
        assignment = DamageControlAssignmentInput(
            slot[0], slot[1], directive.fire_incident_id
        )
        assignments[slot] = assignment
        if previous != assignment:
            events.append(
                ContinuousDamageEvent(
                    ship_id,
                    now,
                    "damage_control_assignment_set",
                    directive.fire_incident_id,
                    damage_control_module_instance_id=slot[0],
                    team_index=slot[1],
                )
            )
    resulting_state = replace(
        state,
        damage_control_assignments=tuple(
            sorted(assignments.values(), key=lambda item: item.slot)
        ),
    )
    validate_continuous_damage_state(snapshot, resulting_state, profile)
    resulting = replace(instance, continuous_damage_state=resulting_state)
    return ContinuousDamageResolution(source_sha256, resulting, tuple(events))


def register_fire_ignition(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    profile: ContinuousDamageProfile,
    outcome: FireIgnitionOutcome,
    *,
    ship_id: str,
    created_time_s: float,
    state_tactical_time_s: float,
) -> ContinuousDamageResolution:
    source_sha256 = canonical_sha256(instance)
    outcome.validate("$.fire_ignition_outcome")
    if outcome.target_ship_id != ship_id:
        raise ContractError(
            "continuous_damage.ignition_ship_mismatch",
            "$.fire_ignition_outcome.target_ship_id",
            ship_id,
        )
    modules = _module_map(snapshot)
    if outcome.target_module_instance_id not in modules:
        raise ContractError(
            "continuous_damage.fire_target_missing",
            "$.fire_ignition_outcome.target_module_instance_id",
            outcome.target_module_instance_id,
        )
    state_time = _number(
        state_tactical_time_s, "$.state_tactical_time_s", 0.0
    )
    created = _number(created_time_s, "$.created_time_s", 0.0)
    if created > state_time + EPS:
        raise ContractError(
            "continuous_damage.fire_from_future",
            "$.created_time_s",
            "点燃时刻不得晚于状态时钟",
        )
    state = instance.continuous_damage_state
    if state is None:
        state = initialize_continuous_damage_state(
            profile, tactical_time_s=state_time
        )
    validate_continuous_damage_state(snapshot, state, profile)
    if abs(state.tactical_time_s - state_time) > EPS:
        raise ContractError(
            "continuous_damage.clock_mismatch",
            "$.continuous_damage_state.tactical_time_s",
            "只能在当前持续毁伤边界登记新火灾",
        )
    if outcome.incident_id in {item.id for item in state.fire_incidents}:
        raise ContractError(
            "continuous_damage.fire_duplicate",
            "$.fire_ignition_outcome.incident_id",
            outcome.incident_id,
        )
    fire = FireIncidentStateInput(
        outcome.incident_id,
        outcome.projectile_id,
        outcome.target_module_instance_id,
        created,
        outcome.initial_intensity_units,
        outcome.initial_fuel_units,
    )
    resulting_state = replace(
        state,
        fire_incidents=tuple(
            sorted((*state.fire_incidents, fire), key=lambda item: item.id)
        ),
    )
    validate_continuous_damage_state(snapshot, resulting_state, profile)
    resulting = replace(instance, continuous_damage_state=resulting_state)
    event = ContinuousDamageEvent(
        ship_id,
        created,
        "fire_started",
        fire.id,
        target_module_instance_id=fire.target_module_instance_id,
        intensity_after=fire.intensity_units,
        fuel_after=fire.remaining_fuel_units,
    )
    return ContinuousDamageResolution(source_sha256, resulting, (event,))


def _module_durability(
    instance: ShipInstanceSnapshotInput, module_instance_id: str
) -> float:
    return next(
        item.current_durability_points
        for item in instance.module_states
        if item.instance_id == module_instance_id
    )


def advance_continuous_damage(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    runtime: RuntimeShipParameters,
    profile: ContinuousDamageProfile,
    *,
    ship_id: str,
    target_tactical_time_s: float,
) -> ContinuousDamageResolution:
    source_sha256 = canonical_sha256(instance)
    state = instance.continuous_damage_state
    if state is None:
        return ContinuousDamageResolution(source_sha256, instance, ())
    validate_continuous_damage_state(snapshot, state, profile)
    runtime_state = runtime.instance_snapshot.continuous_damage_state
    if runtime_state != state:
        raise ContractError(
            "continuous_damage.runtime_state_mismatch",
            "$.runtime.instance_snapshot.continuous_damage_state",
            "持续毁伤求解必须使用本步步首状态编译的运行参数",
        )
    target_time = _number(
        target_tactical_time_s, "$.target_tactical_time_s", 0.0
    )
    if target_time + EPS < state.tactical_time_s:
        raise ContractError(
            "continuous_damage.time_reversed",
            "$.target_tactical_time_s",
            "持续毁伤时钟不得倒退",
        )
    duration = target_time - state.tactical_time_s
    if duration <= EPS:
        return ContinuousDamageResolution(source_sha256, instance, ())

    assignments_by_fire: dict[str, list[DamageControlAssignmentInput]] = {}
    for assignment in state.damage_control_assignments:
        assignments_by_fire.setdefault(assignment.fire_incident_id, []).append(
            assignment
        )
    current = instance
    surviving: list[FireIncidentStateInput] = []
    events: list[ContinuousDamageEvent] = []
    for fire in state.fire_incidents:
        suppression_rate = 0.0
        for assignment in assignments_by_fire.get(fire.id, ()):
            efficiency = runtime.module(
                assignment.damage_control_module_instance_id
            ).function_efficiency("damage_control.firefighting")
            suppression_rate += (
                profile.suppression_units_per_team_efficiency_s * efficiency
            )
        module_before = _module_durability(
            current, fire.target_module_instance_id
        )
        requested_module_damage = (
            profile.module_damage_points_per_intensity_s
            * fire.intensity_units
            * duration
        )
        current, _ = apply_module_damage_to_instance(
            current,
            (fire.target_module_instance_id,),
            requested_module_damage,
        )
        module_after = _module_durability(current, fire.target_module_instance_id)
        hull_before = current.current_hull_integrity_fraction
        requested_hull_damage = (
            profile.hull_integrity_fraction_per_intensity_s
            * fire.intensity_units
            * duration
        )
        hull_after = max(0.0, hull_before - requested_hull_damage)
        current = replace(current, current_hull_integrity_fraction=hull_after)
        fuel_after = max(
            0.0,
            fire.remaining_fuel_units
            - profile.fuel_units_burned_per_intensity_s
            * fire.intensity_units
            * duration,
        )
        intensity_after = max(
            0.0,
            fire.intensity_units
            - (
                profile.natural_intensity_decay_units_per_s
                + suppression_rate
            )
            * duration,
        )
        events.append(
            ContinuousDamageEvent(
                ship_id,
                target_time,
                "fire_damage_applied",
                fire.id,
                target_module_instance_id=fire.target_module_instance_id,
                module_damage_points=module_before - module_after,
                hull_damage_fraction=hull_before - hull_after,
                intensity_before=fire.intensity_units,
                intensity_after=intensity_after,
                fuel_before=fire.remaining_fuel_units,
                fuel_after=fuel_after,
            )
        )
        if suppression_rate > EPS:
            intensity_without_suppression = max(
                0.0,
                fire.intensity_units
                - profile.natural_intensity_decay_units_per_s * duration,
            )
            events.append(
                ContinuousDamageEvent(
                    ship_id,
                    target_time,
                    "fire_suppressed",
                    fire.id,
                    target_module_instance_id=fire.target_module_instance_id,
                    intensity_before=intensity_without_suppression,
                    intensity_after=intensity_after,
                )
            )
        if fuel_after <= EPS:
            events.append(
                ContinuousDamageEvent(
                    ship_id,
                    target_time,
                    "fire_burned_out",
                    fire.id,
                    target_module_instance_id=fire.target_module_instance_id,
                    intensity_before=fire.intensity_units,
                    intensity_after=0.0,
                    fuel_before=fire.remaining_fuel_units,
                    fuel_after=0.0,
                )
            )
        elif intensity_after <= EPS:
            events.append(
                ContinuousDamageEvent(
                    ship_id,
                    target_time,
                    "fire_extinguished",
                    fire.id,
                    target_module_instance_id=fire.target_module_instance_id,
                    intensity_before=fire.intensity_units,
                    intensity_after=0.0,
                    fuel_before=fire.remaining_fuel_units,
                    fuel_after=fuel_after,
                )
            )
        else:
            surviving.append(
                replace(
                    fire,
                    intensity_units=intensity_after,
                    remaining_fuel_units=fuel_after,
                )
            )
    surviving_ids = {item.id for item in surviving}
    resulting_state = replace(
        state,
        tactical_time_s=target_time,
        fire_incidents=tuple(surviving),
        damage_control_assignments=tuple(
            item
            for item in state.damage_control_assignments
            if item.fire_incident_id in surviving_ids
        ),
    )
    validate_continuous_damage_state(snapshot, resulting_state, profile)
    current = replace(current, continuous_damage_state=resulting_state)
    return ContinuousDamageResolution(source_sha256, current, tuple(events))


def load_continuous_damage_profile(
    path: str | Path,
) -> ContinuousDamageProfile:
    return ContinuousDamageProfile.parse(
        json.loads(Path(path).read_text(encoding="utf-8")),
        str(path),
    )
