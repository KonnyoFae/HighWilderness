"""阶段 I11d：显式火势传播、弹药殉爆与二次爆炸原子结算。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import hypot, isfinite
from typing import Any, Iterable

from 高天荒野舰艇出航配置编译器 import validate_ship_ammunition_state
from 高天荒野舰艇持续毁伤 import (
    ContinuousDamageEvent,
    ContinuousDamageProfile,
    validate_continuous_damage_state,
)
from 高天荒野舰艇数据契约 import (
    AmmunitionInventoryEntryInput,
    ContractError,
    FireIncidentStateInput,
    MagazineAmmunitionStateInput,
    RESOURCE_ID_PATTERN,
    ShipAmmunitionStateInput,
    ShipInstanceSnapshotInput,
    canonical_sha256,
)
from 高天荒野舰艇实例设计状态 import validate_instance_current_design
from 高天荒野舰艇无界面舾装编译器 import (
    CompiledModuleInstance,
    DerivedShipSnapshot,
)
from 高天荒野舰艇战损原子操作 import apply_module_damage_to_instance


SECONDARY_DAMAGE_INTERFACE_ID = "gaotian.ship-secondary-damage/v1alpha1"
SECONDARY_DAMAGE_POLICY_ID = (
    "gaotian.secondary-damage/explicit-fire-source-inventory-conserved-boundary/v1"
)
FIRE_PROPAGATION_ADJACENCY_M = 5.0
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("secondary_damage.resource_id", path, str(value))
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(
            "secondary_damage.integer",
            path,
            f"必须是大于等于 {minimum} 的整数",
        )
    return value


def _number(
    value: Any,
    path: str,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) < minimum
        or (maximum is not None and float(value) > maximum)
    ):
        raise ContractError(
            "secondary_damage.number",
            path,
            "不是允许范围内的有限数值",
        )
    return float(value)


def _positive(value: Any, path: str) -> float:
    result = _number(value, path)
    if result <= 0.0:
        raise ContractError(
            "secondary_damage.positive",
            path,
            "必须为正有限数",
        )
    return result


@dataclass(frozen=True)
class FirePropagationOutcome:
    outcome_id: str
    source_fire_incident_id: str
    incident_id: str
    tactical_time_s: float
    target_ship_id: str
    target_module_instance_id: str
    initial_intensity_units: float
    initial_fuel_units: float

    @property
    def route(self) -> tuple[str, str]:
        return self.source_fire_incident_id, self.target_module_instance_id

    def validate(self, path: str = "$") -> None:
        _resource_id(self.outcome_id, f"{path}.outcome_id")
        _resource_id(
            self.source_fire_incident_id,
            f"{path}.source_fire_incident_id",
        )
        _resource_id(self.incident_id, f"{path}.incident_id")
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _resource_id(
            self.target_module_instance_id,
            f"{path}.target_module_instance_id",
        )
        _positive(
            self.initial_intensity_units,
            f"{path}.initial_intensity_units",
        )
        _positive(self.initial_fuel_units, f"{path}.initial_fuel_units")
        if self.incident_id == self.source_fire_incident_id:
            raise ContractError(
                "secondary_damage.fire_self_parent",
                f"{path}.incident_id",
                "传播产生的新火灾不能复用来源火灾 id",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "incident_id": self.incident_id,
            "initial_fuel_units": self.initial_fuel_units,
            "initial_intensity_units": self.initial_intensity_units,
            "outcome_id": self.outcome_id,
            "source_fire_incident_id": self.source_fire_incident_id,
            "tactical_time_s": self.tactical_time_s,
            "target_module_instance_id": self.target_module_instance_id,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class AmmunitionCookoffConsumption:
    munition_id: str
    units: int

    def validate(self, path: str = "$") -> None:
        _resource_id(self.munition_id, f"{path}.munition_id")
        _integer(self.units, f"{path}.units", 1)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"munition_id": self.munition_id, "units": self.units}


@dataclass(frozen=True)
class SecondaryExplosionModuleDamage:
    module_instance_id: str
    damage_points: float

    def validate(self, path: str = "$") -> None:
        _resource_id(self.module_instance_id, f"{path}.module_instance_id")
        _positive(self.damage_points, f"{path}.damage_points")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "damage_points": self.damage_points,
            "module_instance_id": self.module_instance_id,
        }


@dataclass(frozen=True)
class SecondaryFireIgnitionOutcome:
    incident_id: str
    target_module_instance_id: str
    initial_intensity_units: float
    initial_fuel_units: float

    def validate(self, path: str = "$") -> None:
        _resource_id(self.incident_id, f"{path}.incident_id")
        _resource_id(
            self.target_module_instance_id,
            f"{path}.target_module_instance_id",
        )
        _positive(
            self.initial_intensity_units,
            f"{path}.initial_intensity_units",
        )
        _positive(self.initial_fuel_units, f"{path}.initial_fuel_units")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "incident_id": self.incident_id,
            "initial_fuel_units": self.initial_fuel_units,
            "initial_intensity_units": self.initial_intensity_units,
            "target_module_instance_id": self.target_module_instance_id,
        }


@dataclass(frozen=True)
class AmmunitionCookoffOutcome:
    outcome_id: str
    explosion_id: str
    source_fire_incident_id: str
    tactical_time_s: float
    target_ship_id: str
    magazine_instance_id: str
    consumed_ammunition: tuple[AmmunitionCookoffConsumption, ...]
    module_damage: tuple[SecondaryExplosionModuleDamage, ...]
    hull_damage_fraction: float
    secondary_fires: tuple[SecondaryFireIgnitionOutcome, ...] = ()

    def validate(self, path: str = "$") -> None:
        _resource_id(self.outcome_id, f"{path}.outcome_id")
        _resource_id(self.explosion_id, f"{path}.explosion_id")
        _resource_id(
            self.source_fire_incident_id,
            f"{path}.source_fire_incident_id",
        )
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _resource_id(
            self.magazine_instance_id,
            f"{path}.magazine_instance_id",
        )
        if not self.consumed_ammunition:
            raise ContractError(
                "secondary_damage.cookoff_consumption_empty",
                f"{path}.consumed_ammunition",
                "殉爆必须明确消耗至少一种真实库存弹药",
            )
        for index, item in enumerate(self.consumed_ammunition):
            item.validate(f"{path}.consumed_ammunition[{index}]")
        if len({item.munition_id for item in self.consumed_ammunition}) != len(
            self.consumed_ammunition
        ):
            raise ContractError(
                "secondary_damage.munition_duplicate",
                f"{path}.consumed_ammunition",
                "同一殉爆结果中的弹种不得重复",
            )
        for index, item in enumerate(self.module_damage):
            item.validate(f"{path}.module_damage[{index}]")
        if len({item.module_instance_id for item in self.module_damage}) != len(
            self.module_damage
        ):
            raise ContractError(
                "secondary_damage.module_damage_duplicate",
                f"{path}.module_damage",
                "同一二次爆炸中的模块损伤目标不得重复",
            )
        _number(
            self.hull_damage_fraction,
            f"{path}.hull_damage_fraction",
            0.0,
            1.0,
        )
        for index, item in enumerate(self.secondary_fires):
            item.validate(f"{path}.secondary_fires[{index}]")
        if len({item.incident_id for item in self.secondary_fires}) != len(
            self.secondary_fires
        ):
            raise ContractError(
                "secondary_damage.secondary_fire_duplicate",
                f"{path}.secondary_fires",
                "同一二次爆炸产生的火灾 id 不得重复",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "consumed_ammunition": [
                item.to_dict()
                for item in sorted(
                    self.consumed_ammunition,
                    key=lambda item: item.munition_id,
                )
            ],
            "explosion_id": self.explosion_id,
            "hull_damage_fraction": self.hull_damage_fraction,
            "magazine_instance_id": self.magazine_instance_id,
            "module_damage": [
                item.to_dict()
                for item in sorted(
                    self.module_damage,
                    key=lambda item: item.module_instance_id,
                )
            ],
            "outcome_id": self.outcome_id,
            "secondary_fires": [
                item.to_dict()
                for item in sorted(
                    self.secondary_fires,
                    key=lambda item: item.incident_id,
                )
            ],
            "source_fire_incident_id": self.source_fire_incident_id,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class FirePropagationEvent:
    ship_id: str
    tactical_time_s: float
    outcome_id: str
    source_fire_incident_id: str
    fire_incident_id: str
    source_module_instance_id: str
    target_module_instance_id: str
    initial_intensity_units: float
    initial_fuel_units: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_kind": "fire_propagated",
            "fire_incident_id": self.fire_incident_id,
            "initial_fuel_units": self.initial_fuel_units,
            "initial_intensity_units": self.initial_intensity_units,
            "outcome_id": self.outcome_id,
            "ship_id": self.ship_id,
            "source_fire_incident_id": self.source_fire_incident_id,
            "source_module_instance_id": self.source_module_instance_id,
            "tactical_time_s": self.tactical_time_s,
            "target_module_instance_id": self.target_module_instance_id,
        }


@dataclass(frozen=True)
class AppliedSecondaryModuleDamage:
    module_instance_id: str
    requested_damage_points: float
    applied_damage_points: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_damage_points": self.applied_damage_points,
            "module_instance_id": self.module_instance_id,
            "requested_damage_points": self.requested_damage_points,
        }


@dataclass(frozen=True)
class AmmunitionCookoffEvent:
    ship_id: str
    tactical_time_s: float
    outcome_id: str
    explosion_id: str
    source_fire_incident_id: str
    magazine_instance_id: str
    consumed_ammunition: tuple[AmmunitionCookoffConsumption, ...]
    module_damage: tuple[AppliedSecondaryModuleDamage, ...]
    hull_damage_fraction: float
    started_fire_incident_ids: tuple[str, ...]

    @property
    def damaged_module_instance_ids(self) -> tuple[str, ...]:
        return tuple(
            item.module_instance_id
            for item in self.module_damage
            if item.applied_damage_points > EPS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumed_ammunition": [
                item.to_dict() for item in self.consumed_ammunition
            ],
            "event_kind": "ammunition_cookoff",
            "explosion_id": self.explosion_id,
            "hull_damage_fraction": self.hull_damage_fraction,
            "magazine_instance_id": self.magazine_instance_id,
            "module_damage": [item.to_dict() for item in self.module_damage],
            "outcome_id": self.outcome_id,
            "ship_id": self.ship_id,
            "source_fire_incident_id": self.source_fire_incident_id,
            "started_fire_incident_ids": list(self.started_fire_incident_ids),
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class SecondaryDamageResolution:
    source_instance_sha256: str
    resulting_instance: ShipInstanceSnapshotInput
    fire_propagation_events: tuple[FirePropagationEvent, ...]
    ammunition_cookoff_events: tuple[AmmunitionCookoffEvent, ...]


def _module_map(snapshot: DerivedShipSnapshot) -> dict[str, CompiledModuleInstance]:
    return {item.id: item for item in snapshot.outfit.instances}


def _modules_are_adjacent(
    source: CompiledModuleInstance,
    target: CompiledModuleInstance,
) -> bool:
    if source.id == target.id:
        return False
    if source.host_instance_id == target.id or target.host_instance_id == source.id:
        return True
    if (
        source.host_instance_id is not None
        and source.host_instance_id == target.host_instance_id
    ):
        return True
    return any(
        source_band == target_band
        and hypot(source_x - target_x, source_y - target_y)
        <= FIRE_PROPAGATION_ADJACENCY_M + EPS
        for source_band, source_x, source_y in source.body_spatial_keys
        for target_band, target_x, target_y in target.body_spatial_keys
    )


def _module_durability(
    instance: ShipInstanceSnapshotInput,
    module_instance_id: str,
) -> float:
    return next(
        item.current_durability_points
        for item in instance.module_states
        if item.instance_id == module_instance_id
    )


def _validate_source(
    source_fire_id: str,
    source_fires: dict[str, FireIncidentStateInput],
    source_events: dict[str, ContinuousDamageEvent],
    *,
    target_time: float,
    path: str,
) -> FireIncidentStateInput:
    fire = source_fires.get(source_fire_id)
    event = source_events.get(source_fire_id)
    if fire is None or event is None:
        raise ContractError(
            "secondary_damage.fire_source_unmatched",
            path,
            source_fire_id,
        )
    if (
        event.event_kind != "fire_damage_applied"
        or abs(event.tactical_time_s - target_time) > EPS
        or event.target_module_instance_id != fire.target_module_instance_id
    ):
        raise ContractError(
            "secondary_damage.fire_source_unmatched",
            path,
            source_fire_id,
        )
    return fire


def apply_secondary_damage_outcomes(
    snapshot: DerivedShipSnapshot,
    instance: ShipInstanceSnapshotInput,
    profile: ContinuousDamageProfile,
    *,
    ship_id: str,
    target_tactical_time_s: float,
    source_fire_incidents: Iterable[FireIncidentStateInput],
    source_fire_events: Iterable[ContinuousDamageEvent],
    fire_propagation_outcomes: Iterable[FirePropagationOutcome] = (),
    ammunition_cookoff_outcomes: Iterable[AmmunitionCookoffOutcome] = (),
) -> SecondaryDamageResolution:
    """在旧火灾完成本步伤害后，原子应用显式传播与殉爆结果。"""

    source_sha256 = canonical_sha256(instance)
    _resource_id(ship_id, "$.ship_id")
    target_time = _number(
        target_tactical_time_s,
        "$.target_tactical_time_s",
    )
    propagations = tuple(fire_propagation_outcomes)
    cookoffs = tuple(ammunition_cookoff_outcomes)
    for index, item in enumerate(propagations):
        item.validate(f"$.fire_propagation_outcomes[{index}]")
        if item.target_ship_id != ship_id:
            raise ContractError(
                "secondary_damage.ship_mismatch",
                f"$.fire_propagation_outcomes[{index}].target_ship_id",
                item.target_ship_id,
            )
        if abs(item.tactical_time_s - target_time) > EPS:
            raise ContractError(
                "secondary_damage.boundary_mismatch",
                f"$.fire_propagation_outcomes[{index}].tactical_time_s",
                "火势传播结果必须位于当前固定步边界",
            )
    for index, item in enumerate(cookoffs):
        item.validate(f"$.ammunition_cookoff_outcomes[{index}]")
        if item.target_ship_id != ship_id:
            raise ContractError(
                "secondary_damage.ship_mismatch",
                f"$.ammunition_cookoff_outcomes[{index}].target_ship_id",
                item.target_ship_id,
            )
        if abs(item.tactical_time_s - target_time) > EPS:
            raise ContractError(
                "secondary_damage.boundary_mismatch",
                f"$.ammunition_cookoff_outcomes[{index}].tactical_time_s",
                "弹药殉爆结果必须位于当前固定步边界",
            )
    all_outcome_ids = [item.outcome_id for item in (*propagations, *cookoffs)]
    if len(set(all_outcome_ids)) != len(all_outcome_ids):
        raise ContractError(
            "secondary_damage.outcome_duplicate",
            "$.secondary_damage_outcomes",
            "同一固定步的二次毁伤结果 id 不得重复",
        )
    if len({item.route for item in propagations}) != len(propagations):
        raise ContractError(
            "secondary_damage.propagation_route_duplicate",
            "$.fire_propagation_outcomes",
            "同一火灾不得在同一步重复传播到同一模块",
        )
    if len({item.explosion_id for item in cookoffs}) != len(cookoffs):
        raise ContractError(
            "secondary_damage.explosion_duplicate",
            "$.ammunition_cookoff_outcomes",
            "同一固定步的二次爆炸 id 不得重复",
        )
    if len({item.magazine_instance_id for item in cookoffs}) != len(cookoffs):
        raise ContractError(
            "secondary_damage.magazine_cookoff_duplicate",
            "$.ammunition_cookoff_outcomes",
            "同一弹药库在同一步只能结算一次殉爆",
        )
    if not propagations and not cookoffs:
        return SecondaryDamageResolution(source_sha256, instance, (), ())

    validate_instance_current_design(snapshot, instance)
    state = instance.continuous_damage_state
    if state is None:
        raise ContractError(
            "secondary_damage.continuous_state_required",
            "$.continuous_damage_state",
            "传播或殉爆必须在持续毁伤状态中结算",
        )
    validate_continuous_damage_state(snapshot, state, profile)
    if abs(state.tactical_time_s - target_time) > EPS:
        raise ContractError(
            "secondary_damage.clock_mismatch",
            "$.continuous_damage_state.tactical_time_s",
            "二次毁伤只能在当前持续毁伤边界结算",
        )
    source_fires_tuple = tuple(source_fire_incidents)
    if len({item.id for item in source_fires_tuple}) != len(source_fires_tuple):
        raise ContractError(
            "secondary_damage.source_fire_duplicate",
            "$.source_fire_incidents",
            "来源火灾 id 不得重复",
        )
    source_fires = {item.id: item for item in source_fires_tuple}
    source_events_tuple = tuple(
        item
        for item in source_fire_events
        if item.ship_id == ship_id and item.event_kind == "fire_damage_applied"
    )
    if len({item.fire_incident_id for item in source_events_tuple}) != len(
        source_events_tuple
    ):
        raise ContractError(
            "secondary_damage.source_event_duplicate",
            "$.source_fire_events",
            "同一来源火灾在固定步内只能有一条伤害事件",
        )
    source_events = {item.fire_incident_id: item for item in source_events_tuple}
    modules = _module_map(snapshot)
    current = instance
    fires = {item.id: item for item in state.fire_incidents}
    burning_targets = {item.target_module_instance_id for item in fires.values()}
    propagation_events: list[FirePropagationEvent] = []
    cookoff_events: list[AmmunitionCookoffEvent] = []

    for outcome in sorted(propagations, key=lambda item: item.outcome_id):
        source = _validate_source(
            outcome.source_fire_incident_id,
            source_fires,
            source_events,
            target_time=target_time,
            path=f"$.fire_propagation_outcomes.{outcome.outcome_id}",
        )
        source_module = modules.get(source.target_module_instance_id)
        target_module = modules.get(outcome.target_module_instance_id)
        if source_module is None or target_module is None:
            raise ContractError(
                "secondary_damage.module_missing",
                f"$.fire_propagation_outcomes.{outcome.outcome_id}",
                outcome.target_module_instance_id,
            )
        if not _modules_are_adjacent(source_module, target_module):
            raise ContractError(
                "secondary_damage.propagation_not_adjacent",
                f"$.fire_propagation_outcomes.{outcome.outcome_id}.target_module_instance_id",
                "火势传播目标必须与来源模块共享宿主或处于同层五米相邻格",
            )
        if outcome.incident_id in fires:
            raise ContractError(
                "continuous_damage.fire_duplicate",
                f"$.fire_propagation_outcomes.{outcome.outcome_id}.incident_id",
                outcome.incident_id,
            )
        if outcome.target_module_instance_id in burning_targets:
            raise ContractError(
                "secondary_damage.target_already_burning",
                f"$.fire_propagation_outcomes.{outcome.outcome_id}.target_module_instance_id",
                outcome.target_module_instance_id,
            )
        fire = FireIncidentStateInput(
            outcome.incident_id,
            source.source_projectile_id,
            outcome.target_module_instance_id,
            target_time,
            outcome.initial_intensity_units,
            outcome.initial_fuel_units,
            outcome.source_fire_incident_id,
            None,
        )
        fires[fire.id] = fire
        burning_targets.add(fire.target_module_instance_id)
        propagation_events.append(
            FirePropagationEvent(
                ship_id,
                target_time,
                outcome.outcome_id,
                outcome.source_fire_incident_id,
                fire.id,
                source.target_module_instance_id,
                fire.target_module_instance_id,
                fire.intensity_units,
                fire.remaining_fuel_units,
            )
        )

    for outcome in sorted(cookoffs, key=lambda item: item.outcome_id):
        source = _validate_source(
            outcome.source_fire_incident_id,
            source_fires,
            source_events,
            target_time=target_time,
            path=f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}",
        )
        magazine_module = modules.get(outcome.magazine_instance_id)
        if (
            magazine_module is None
            or magazine_module.prototype.category != "ammunition_magazine"
        ):
            raise ContractError(
                "secondary_damage.magazine_module",
                f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.magazine_instance_id",
                outcome.magazine_instance_id,
            )
        if source.target_module_instance_id != outcome.magazine_instance_id:
            raise ContractError(
                "secondary_damage.cookoff_source_mismatch",
                f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.source_fire_incident_id",
                "殉爆来源火灾必须实际位于指定弹药库",
            )
        ammunition = current.ammunition_state
        if ammunition is None:
            raise ContractError(
                "secondary_damage.ammunition_state_required",
                "$.ammunition_state",
                "弹药殉爆必须读取真实物理库存",
            )
        validate_ship_ammunition_state(
            snapshot,
            ammunition,
            namespace="secondary_damage",
            path_prefix="$.ammunition_state",
        )
        magazine_states = {item.instance_id: item for item in ammunition.magazines}
        magazine = magazine_states[outcome.magazine_instance_id]
        inventory = {item.munition_id: item.units for item in magazine.inventory}
        for consumed in outcome.consumed_ammunition:
            available = inventory.get(consumed.munition_id, 0)
            if consumed.units > available:
                raise ContractError(
                    "secondary_damage.insufficient_ammunition",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.consumed_ammunition.{consumed.munition_id}",
                    f"库存 {available}，请求殉爆 {consumed.units}",
                )
            inventory[consumed.munition_id] = available - consumed.units
        magazine_states[outcome.magazine_instance_id] = MagazineAmmunitionStateInput(
            outcome.magazine_instance_id,
            tuple(
                AmmunitionInventoryEntryInput(munition_id, units)
                for munition_id, units in sorted(inventory.items())
                if units > 0
            ),
        )
        updated_ammunition = ShipAmmunitionStateInput(
            tuple(sorted(magazine_states.values(), key=lambda item: item.instance_id)),
            ammunition.weapons,
        )
        validate_ship_ammunition_state(
            snapshot,
            updated_ammunition,
            namespace="secondary_damage",
            path_prefix="$.ammunition_state",
        )
        current = replace(current, ammunition_state=updated_ammunition)

        allowed_fire_targets = {
            outcome.magazine_instance_id,
            *(item.module_instance_id for item in outcome.module_damage),
        }
        applied_damage: list[AppliedSecondaryModuleDamage] = []
        for damage in sorted(
            outcome.module_damage,
            key=lambda item: item.module_instance_id,
        ):
            if damage.module_instance_id not in modules:
                raise ContractError(
                    "secondary_damage.module_missing",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.module_damage",
                    damage.module_instance_id,
                )
            before = _module_durability(current, damage.module_instance_id)
            current, _ = apply_module_damage_to_instance(
                current,
                (damage.module_instance_id,),
                damage.damage_points,
            )
            after = _module_durability(current, damage.module_instance_id)
            applied_damage.append(
                AppliedSecondaryModuleDamage(
                    damage.module_instance_id,
                    damage.damage_points,
                    before - after,
                )
            )
        hull_before = current.current_hull_integrity_fraction
        hull_after = max(0.0, hull_before - outcome.hull_damage_fraction)
        current = replace(current, current_hull_integrity_fraction=hull_after)

        started_fire_ids: list[str] = []
        for ignition in sorted(
            outcome.secondary_fires,
            key=lambda item: item.incident_id,
        ):
            if ignition.target_module_instance_id not in modules:
                raise ContractError(
                    "secondary_damage.module_missing",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.secondary_fires",
                    ignition.target_module_instance_id,
                )
            if ignition.target_module_instance_id not in allowed_fire_targets:
                raise ContractError(
                    "secondary_damage.secondary_fire_without_damage_target",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.secondary_fires",
                    "二次爆炸只能引燃弹药库自身或本次显式损伤的模块",
                )
            if ignition.incident_id in fires:
                raise ContractError(
                    "continuous_damage.fire_duplicate",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.secondary_fires",
                    ignition.incident_id,
                )
            if ignition.target_module_instance_id in burning_targets:
                raise ContractError(
                    "secondary_damage.target_already_burning",
                    f"$.ammunition_cookoff_outcomes.{outcome.outcome_id}.secondary_fires",
                    ignition.target_module_instance_id,
                )
            fire = FireIncidentStateInput(
                ignition.incident_id,
                source.source_projectile_id,
                ignition.target_module_instance_id,
                target_time,
                ignition.initial_intensity_units,
                ignition.initial_fuel_units,
                None,
                outcome.explosion_id,
            )
            fires[fire.id] = fire
            burning_targets.add(fire.target_module_instance_id)
            started_fire_ids.append(fire.id)
        cookoff_events.append(
            AmmunitionCookoffEvent(
                ship_id,
                target_time,
                outcome.outcome_id,
                outcome.explosion_id,
                outcome.source_fire_incident_id,
                outcome.magazine_instance_id,
                tuple(
                    sorted(
                        outcome.consumed_ammunition,
                        key=lambda item: item.munition_id,
                    )
                ),
                tuple(applied_damage),
                hull_before - hull_after,
                tuple(started_fire_ids),
            )
        )

    resulting_state = replace(
        state,
        fire_incidents=tuple(sorted(fires.values(), key=lambda item: item.id)),
    )
    validate_continuous_damage_state(snapshot, resulting_state, profile)
    resulting = replace(current, continuous_damage_state=resulting_state)
    return SecondaryDamageResolution(
        source_sha256,
        resulting,
        tuple(propagation_events),
        tuple(cookoff_events),
    )
