"""阶段 I12a：显式观测裁决、雷达辐射、火控通道与制导事实映射。"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    FIRE_CONTROL_REQUIREMENTS,
    RESOURCE_ID_PATTERN,
    SENSOR_MODES,
)
from 高天荒野舰艇无界面舾装编译器 import DerivedShipSnapshot
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters
from 高天荒野舰艇导弹制导 import (
    MissileGuidanceProfileCatalog,
    MissileGuidanceRuntimeInput,
)
from 高天荒野舰艇战术弹丸世界 import ProjectileState


TACTICAL_OBSERVATION_INTERFACE_ID = "gaotian.tactical-observation/v1alpha1"
TACTICAL_OBSERVATION_POLICY_ID = (
    "gaotian.tactical-observation/"
    "explicit-adjudication-runtime-capability-step-boundary/v1"
)
SENSOR_WAKE_EVENT = "ship.sensor_scan_required"
FIRE_CONTROL_WAKE_EVENT = "ship.fire_control_required"
OBSERVATION_REASONS = {
    "observed",
    "not_observed",
    "electronic_interference",
    "environment",
    "occluded",
}
NEGATIVE_OBSERVATION_REASONS = OBSERVATION_REASONS - {"observed"}
ACTIVE_RADAR_MODES = {"active_search", "fire_control_lock"}
GUIDANCE_FACT_SEEKER_KINDS = {
    "passive_radar",
    "active_radar",
    "anti_radiation",
    "electro_optical",
}
EPS = 1.0e-8


def _resource_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise ContractError("tactical_observation.resource_id", path, str(value))
    return value


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) < minimum
    ):
        raise ContractError(
            "tactical_observation.number",
            path,
            f"必须是大于等于 {minimum} 的有限数值",
        )
    return float(value)


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(
            "tactical_observation.boolean",
            path,
            "必须是布尔值",
        )
    return value


def _require_step_time(actual: float, expected: float, path: str) -> None:
    if abs(actual - expected) > EPS:
        raise ContractError(
            "tactical_observation.boundary_mismatch",
            path,
            "观测与火控输入必须位于当前固定步步首边界",
        )


@dataclass(frozen=True)
class SensorObservationOutcome:
    outcome_id: str
    tactical_time_s: float
    observer_ship_id: str
    target_ship_id: str
    sensor_instance_id: str
    sensor_mode: str
    contact_available: bool
    resolution_reason: str

    @property
    def route(self) -> tuple[str, str, str, str]:
        return (
            self.observer_ship_id,
            self.target_ship_id,
            self.sensor_instance_id,
            self.sensor_mode,
        )

    def validate(self, path: str = "$") -> None:
        _resource_id(self.outcome_id, f"{path}.outcome_id")
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.observer_ship_id, f"{path}.observer_ship_id")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _resource_id(self.sensor_instance_id, f"{path}.sensor_instance_id")
        if self.observer_ship_id == self.target_ship_id:
            raise ContractError(
                "tactical_observation.self_observation",
                f"{path}.target_ship_id",
                "I12a 不接受以本舰自身为战术观测目标",
            )
        if self.sensor_mode not in SENSOR_MODES:
            raise ContractError(
                "tactical_observation.sensor_mode",
                f"{path}.sensor_mode",
                self.sensor_mode,
            )
        _boolean(self.contact_available, f"{path}.contact_available")
        if self.resolution_reason not in OBSERVATION_REASONS:
            raise ContractError(
                "tactical_observation.resolution_reason",
                f"{path}.resolution_reason",
                self.resolution_reason,
            )
        if self.contact_available != (self.resolution_reason == "observed"):
            raise ContractError(
                "tactical_observation.result_reason_mismatch",
                path,
                "成功观测必须使用 observed，失败观测必须使用明确失败原因",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "contact_available": self.contact_available,
            "observer_ship_id": self.observer_ship_id,
            "outcome_id": self.outcome_id,
            "resolution_reason": self.resolution_reason,
            "sensor_instance_id": self.sensor_instance_id,
            "sensor_mode": self.sensor_mode,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class ProjectileSeekerObservationOutcome:
    outcome_id: str
    tactical_time_s: float
    projectile_id: str
    target_ship_id: str
    contact_available: bool
    resolution_reason: str

    def validate(self, path: str = "$") -> None:
        _resource_id(self.outcome_id, f"{path}.outcome_id")
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.projectile_id, f"{path}.projectile_id")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _boolean(self.contact_available, f"{path}.contact_available")
        if self.resolution_reason not in OBSERVATION_REASONS:
            raise ContractError(
                "tactical_observation.resolution_reason",
                f"{path}.resolution_reason",
                self.resolution_reason,
            )
        if self.contact_available != (self.resolution_reason == "observed"):
            raise ContractError(
                "tactical_observation.result_reason_mismatch",
                path,
                "成功观测必须使用 observed，失败观测必须使用明确失败原因",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "contact_available": self.contact_available,
            "outcome_id": self.outcome_id,
            "projectile_id": self.projectile_id,
            "resolution_reason": self.resolution_reason,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class FireControlAssignment:
    assignment_id: str
    tactical_time_s: float
    source_ship_id: str
    target_ship_id: str
    sensor_instance_id: str
    fire_control_instance_id: str
    requirements: tuple[str, ...]

    @property
    def channel_key(self) -> tuple[str, str]:
        return self.source_ship_id, self.fire_control_instance_id

    @property
    def route(self) -> tuple[str, str, str]:
        return (
            self.source_ship_id,
            self.fire_control_instance_id,
            self.target_ship_id,
        )

    def validate(self, path: str = "$") -> None:
        _resource_id(self.assignment_id, f"{path}.assignment_id")
        _number(self.tactical_time_s, f"{path}.tactical_time_s")
        _resource_id(self.source_ship_id, f"{path}.source_ship_id")
        _resource_id(self.target_ship_id, f"{path}.target_ship_id")
        _resource_id(self.sensor_instance_id, f"{path}.sensor_instance_id")
        _resource_id(
            self.fire_control_instance_id,
            f"{path}.fire_control_instance_id",
        )
        if self.source_ship_id == self.target_ship_id:
            raise ContractError(
                "tactical_observation.self_fire_control",
                f"{path}.target_ship_id",
                "火控通道不能分配给本舰自身",
            )
        if (
            not isinstance(self.requirements, tuple)
            or not self.requirements
            or len(set(self.requirements)) != len(self.requirements)
            or any(
                item == "none" or item not in FIRE_CONTROL_REQUIREMENTS
                for item in self.requirements
            )
        ):
            raise ContractError(
                "tactical_observation.fire_control_requirements",
                f"{path}.requirements",
                "火控需求必须非空、不得重复，且只能使用 solution/continuous_guidance",
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "assignment_id": self.assignment_id,
            "fire_control_instance_id": self.fire_control_instance_id,
            "requirements": sorted(self.requirements),
            "sensor_instance_id": self.sensor_instance_id,
            "source_ship_id": self.source_ship_id,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class TacticalObservationStepInput:
    sensor_observation_outcomes: tuple[SensorObservationOutcome, ...] = ()
    fire_control_assignments: tuple[FireControlAssignment, ...] = ()
    seeker_observation_outcomes: tuple[ProjectileSeekerObservationOutcome, ...] = ()

    def validate(self, path: str = "$") -> None:
        for index, item in enumerate(self.sensor_observation_outcomes):
            item.validate(f"{path}.sensor_observation_outcomes[{index}]")
        for index, item in enumerate(self.fire_control_assignments):
            item.validate(f"{path}.fire_control_assignments[{index}]")
        for index, item in enumerate(self.seeker_observation_outcomes):
            item.validate(f"{path}.seeker_observation_outcomes[{index}]")
        outcome_ids = [
            item.outcome_id
            for item in (
                *self.sensor_observation_outcomes,
                *self.seeker_observation_outcomes,
            )
        ]
        if len(set(outcome_ids)) != len(outcome_ids):
            raise ContractError(
                "tactical_observation.outcome_duplicate",
                path,
                "舰载与弹载观测结果 id 不得重复",
            )
        assignment_ids = [
            item.assignment_id for item in self.fire_control_assignments
        ]
        if len(set(assignment_ids)) != len(assignment_ids):
            raise ContractError(
                "tactical_observation.assignment_duplicate",
                f"{path}.fire_control_assignments",
                "火控分配 id 不得重复",
            )
        routes = [item.route for item in self.sensor_observation_outcomes]
        if len(set(routes)) != len(routes):
            raise ContractError(
                "tactical_observation.sensor_route_duplicate",
                f"{path}.sensor_observation_outcomes",
                "同一传感器、目标和模式在一个固定步内只能有一份结果",
            )
        assignment_routes = [item.route for item in self.fire_control_assignments]
        if len(set(assignment_routes)) != len(assignment_routes):
            raise ContractError(
                "tactical_observation.assignment_route_duplicate",
                f"{path}.fire_control_assignments",
                "同一火控实例对同一目标在一个固定步内只能占用一条通道",
            )
        projectile_ids = [
            item.projectile_id for item in self.seeker_observation_outcomes
        ]
        if len(set(projectile_ids)) != len(projectile_ids):
            raise ContractError(
                "tactical_observation.seeker_projectile_duplicate",
                f"{path}.seeker_observation_outcomes",
                "同一弹丸在一个固定步内只能有一份弹载观测结果",
            )

    def automatic_events_by_ship(self) -> dict[str, tuple[str, ...]]:
        self.validate()
        events: dict[str, set[str]] = {}
        for item in self.sensor_observation_outcomes:
            events.setdefault(item.observer_ship_id, set()).add(SENSOR_WAKE_EVENT)
        for item in self.fire_control_assignments:
            events.setdefault(item.source_ship_id, set()).update(
                {SENSOR_WAKE_EVENT, FIRE_CONTROL_WAKE_EVENT}
            )
        return {
            ship_id: tuple(sorted(values))
            for ship_id, values in sorted(events.items())
        }

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "fire_control_assignments": [
                item.to_dict()
                for item in sorted(
                    self.fire_control_assignments,
                    key=lambda entry: entry.assignment_id,
                )
            ],
            "interface": TACTICAL_OBSERVATION_INTERFACE_ID,
            "policy": TACTICAL_OBSERVATION_POLICY_ID,
            "seeker_observation_outcomes": [
                item.to_dict()
                for item in sorted(
                    self.seeker_observation_outcomes,
                    key=lambda entry: entry.outcome_id,
                )
            ],
            "sensor_observation_outcomes": [
                item.to_dict()
                for item in sorted(
                    self.sensor_observation_outcomes,
                    key=lambda entry: entry.outcome_id,
                )
            ],
        }


@dataclass(frozen=True)
class TacticalObservationShipContext:
    ship_id: str
    snapshot: DerivedShipSnapshot
    runtime: RuntimeShipParameters
    position_xy: tuple[float, float]
    physical_status: str

    def validate(self, path: str = "$") -> None:
        _resource_id(self.ship_id, f"{path}.ship_id")
        if (
            not isinstance(self.position_xy, tuple)
            or len(self.position_xy) != 2
        ):
            raise ContractError(
                "tactical_observation.position",
                f"{path}.position_xy",
                "位置必须是两个有限数值的元组",
            )
        _number(self.position_xy[0], f"{path}.position_xy[0]", -float("inf"))
        _number(self.position_xy[1], f"{path}.position_xy[1]", -float("inf"))
        if self.physical_status not in {"operational", "falling", "exited"}:
            raise ContractError(
                "tactical_observation.physical_status",
                f"{path}.physical_status",
                self.physical_status,
            )


@dataclass(frozen=True)
class SensorObservationEvent:
    outcome_id: str
    tactical_time_s: float
    observer_ship_id: str
    target_ship_id: str
    sensor_instance_id: str
    sensor_mode: str
    contact_available: bool
    resolution_reason: str
    distance_m: float
    function_efficiency: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_available": self.contact_available,
            "distance_m": self.distance_m,
            "function_efficiency": self.function_efficiency,
            "observer_ship_id": self.observer_ship_id,
            "outcome_id": self.outcome_id,
            "resolution_reason": self.resolution_reason,
            "sensor_instance_id": self.sensor_instance_id,
            "sensor_mode": self.sensor_mode,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class RadarEmissionEvent:
    tactical_time_s: float
    emitter_ship_id: str
    sensor_instance_id: str
    sensor_mode: str
    source_observation_outcome_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "emitter_ship_id": self.emitter_ship_id,
            "sensor_instance_id": self.sensor_instance_id,
            "sensor_mode": self.sensor_mode,
            "source_observation_outcome_id": self.source_observation_outcome_id,
            "tactical_time_s": self.tactical_time_s,
        }


@dataclass(frozen=True)
class FireControlSupportEvent:
    assignment_id: str
    tactical_time_s: float
    source_ship_id: str
    target_ship_id: str
    sensor_instance_id: str
    fire_control_instance_id: str
    requirements: tuple[str, ...]
    source_observation_outcome_id: str
    distance_m: float
    function_efficiencies: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "distance_m": self.distance_m,
            "fire_control_instance_id": self.fire_control_instance_id,
            "function_efficiencies": dict(self.function_efficiencies),
            "requirements": list(self.requirements),
            "sensor_instance_id": self.sensor_instance_id,
            "source_observation_outcome_id": self.source_observation_outcome_id,
            "source_ship_id": self.source_ship_id,
            "tactical_time_s": self.tactical_time_s,
            "target_ship_id": self.target_ship_id,
        }


@dataclass(frozen=True)
class GeneratedGuidanceFactEvent:
    projectile_id: str
    tactical_time_s: float
    seeker_kind: str
    target_track_available: bool
    target_radar_emitting: bool
    continuous_illumination_available: bool
    track_source_ids: tuple[str, ...]
    radar_emission_source_ids: tuple[str, ...]
    illumination_assignment_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "continuous_illumination_available": self.continuous_illumination_available,
            "illumination_assignment_ids": list(self.illumination_assignment_ids),
            "projectile_id": self.projectile_id,
            "radar_emission_source_ids": list(self.radar_emission_source_ids),
            "seeker_kind": self.seeker_kind,
            "tactical_time_s": self.tactical_time_s,
            "target_radar_emitting": self.target_radar_emitting,
            "target_track_available": self.target_track_available,
            "track_source_ids": list(self.track_source_ids),
        }


@dataclass(frozen=True)
class TacticalObservationResolution:
    observation_events: tuple[SensorObservationEvent, ...]
    radar_emission_events: tuple[RadarEmissionEvent, ...]
    fire_control_support_events: tuple[FireControlSupportEvent, ...]
    accepted_assignments: tuple[FireControlAssignment, ...]

    def support_assignments(
        self,
        source_ship_id: str,
        target_ship_id: str,
        requirement: str,
        fire_control_instance_id: str | None = None,
    ) -> tuple[FireControlAssignment, ...]:
        return tuple(
            item
            for item in self.accepted_assignments
            if item.source_ship_id == source_ship_id
            and item.target_ship_id == target_ship_id
            and requirement in item.requirements
            and (
                fire_control_instance_id is None
                or item.fire_control_instance_id == fire_control_instance_id
            )
        )


@dataclass(frozen=True)
class GeneratedGuidanceFactsResolution:
    runtime_inputs: tuple[MissileGuidanceRuntimeInput, ...]
    events: tuple[GeneratedGuidanceFactEvent, ...]


def _context_map(
    contexts: Iterable[TacticalObservationShipContext],
) -> dict[str, TacticalObservationShipContext]:
    items = tuple(contexts)
    for index, item in enumerate(items):
        item.validate(f"$.ship_contexts[{index}]")
    result = {item.ship_id: item for item in items}
    if len(result) != len(items):
        raise ContractError(
            "tactical_observation.ship_duplicate",
            "$.ship_contexts",
            "舰艇观测上下文 id 不得重复",
        )
    return result


def _require_context(
    contexts: dict[str, TacticalObservationShipContext],
    ship_id: str,
    path: str,
) -> TacticalObservationShipContext:
    context = contexts.get(ship_id)
    if context is None:
        raise ContractError(
            "tactical_observation.ship_missing",
            path,
            ship_id,
        )
    return context


def _require_module(context: TacticalObservationShipContext, instance_id: str, category: str, path: str):
    module = next(
        (item for item in context.snapshot.outfit.instances if item.id == instance_id),
        None,
    )
    if module is None or module.prototype.category != category:
        raise ContractError(
            "tactical_observation.module_mismatch",
            path,
            f"{instance_id} 必须是当前舰艇上的 {category} 模块",
        )
    runtime = next(
        (item for item in context.runtime.modules if item.instance_id == instance_id),
        None,
    )
    if runtime is None:
        raise ContractError(
            "tactical_observation.runtime_module_missing",
            path,
            instance_id,
        )
    return module, runtime


def _distance(
    source: TacticalObservationShipContext,
    target: TacticalObservationShipContext,
) -> float:
    return hypot(
        target.position_xy[0] - source.position_xy[0],
        target.position_xy[1] - source.position_xy[1],
    )


def _sensor_function(mode: str) -> str:
    return "sensor.search" if mode in {"active_search", "passive_search"} else "sensor.track"


def resolve_tactical_observation_step(
    contexts: Iterable[TacticalObservationShipContext],
    step_input: TacticalObservationStepInput,
    *,
    tactical_time_s: float,
) -> TacticalObservationResolution:
    """用步首实际模块状态验证显式观测结果，并分配火控通道。"""

    step_input.validate()
    target_time = _number(tactical_time_s, "$.tactical_time_s")
    context_by_id = _context_map(contexts)
    observation_events: list[SensorObservationEvent] = []
    emission_events: list[RadarEmissionEvent] = []
    successful: dict[tuple[str, str, str], list[SensorObservationEvent]] = {}

    for outcome in sorted(
        step_input.sensor_observation_outcomes,
        key=lambda item: item.outcome_id,
    ):
        _require_step_time(
            outcome.tactical_time_s,
            target_time,
            f"$.sensor_observation_outcomes.{outcome.outcome_id}.tactical_time_s",
        )
        observer = _require_context(
            context_by_id,
            outcome.observer_ship_id,
            f"$.sensor_observation_outcomes.{outcome.outcome_id}.observer_ship_id",
        )
        target = _require_context(
            context_by_id,
            outcome.target_ship_id,
            f"$.sensor_observation_outcomes.{outcome.outcome_id}.target_ship_id",
        )
        if observer.physical_status != "operational":
            raise ContractError(
                "tactical_observation.observer_unavailable",
                f"$.sensor_observation_outcomes.{outcome.outcome_id}.observer_ship_id",
                observer.physical_status,
            )
        if target.physical_status == "exited":
            raise ContractError(
                "tactical_observation.target_exited",
                f"$.sensor_observation_outcomes.{outcome.outcome_id}.target_ship_id",
                target.ship_id,
            )
        sensor, sensor_runtime = _require_module(
            observer,
            outcome.sensor_instance_id,
            "sensor",
            f"$.sensor_observation_outcomes.{outcome.outcome_id}.sensor_instance_id",
        )
        capability = sensor.prototype.capability.to_dict()
        if outcome.sensor_mode not in set(capability["supported_modes"]):
            raise ContractError(
                "tactical_observation.sensor_mode_unsupported",
                f"$.sensor_observation_outcomes.{outcome.outcome_id}.sensor_mode",
                outcome.sensor_mode,
            )
        function_id = _sensor_function(outcome.sensor_mode)
        efficiency = sensor_runtime.function_efficiency(function_id)
        if efficiency <= EPS:
            raise ContractError(
                "tactical_observation.sensor_unavailable",
                f"$.sensor_observation_outcomes.{outcome.outcome_id}.sensor_instance_id",
                function_id,
            )
        distance_m = _distance(observer, target)
        if distance_m > float(capability["maximum_instrumented_range_m"]) + EPS:
            raise ContractError(
                "tactical_observation.sensor_out_of_range",
                f"$.sensor_observation_outcomes.{outcome.outcome_id}.target_ship_id",
                str(distance_m),
            )
        event = SensorObservationEvent(
            outcome.outcome_id,
            target_time,
            observer.ship_id,
            target.ship_id,
            sensor.id,
            outcome.sensor_mode,
            outcome.contact_available,
            outcome.resolution_reason,
            distance_m,
            efficiency,
        )
        observation_events.append(event)
        if event.contact_available:
            successful.setdefault(
                (observer.ship_id, target.ship_id, sensor.id),
                [],
            ).append(event)
        if (
            capability["sensor_channel"] == "radar"
            and outcome.sensor_mode in ACTIVE_RADAR_MODES
        ):
            emission_events.append(
                RadarEmissionEvent(
                    target_time,
                    observer.ship_id,
                    sensor.id,
                    outcome.sensor_mode,
                    outcome.outcome_id,
                )
            )

    support_events: list[FireControlSupportEvent] = []
    accepted: list[FireControlAssignment] = []
    channel_counts: dict[tuple[str, str], int] = {}
    channel_limits: dict[tuple[str, str], int] = {}
    for assignment in sorted(
        step_input.fire_control_assignments,
        key=lambda item: item.assignment_id,
    ):
        path = f"$.fire_control_assignments.{assignment.assignment_id}"
        _require_step_time(
            assignment.tactical_time_s,
            target_time,
            f"{path}.tactical_time_s",
        )
        source = _require_context(
            context_by_id,
            assignment.source_ship_id,
            f"{path}.source_ship_id",
        )
        target = _require_context(
            context_by_id,
            assignment.target_ship_id,
            f"{path}.target_ship_id",
        )
        if source.physical_status != "operational":
            raise ContractError(
                "tactical_observation.fire_control_source_unavailable",
                f"{path}.source_ship_id",
                source.physical_status,
            )
        if target.physical_status == "exited":
            raise ContractError(
                "tactical_observation.target_exited",
                f"{path}.target_ship_id",
                target.ship_id,
            )
        sensor, _ = _require_module(
            source,
            assignment.sensor_instance_id,
            "sensor",
            f"{path}.sensor_instance_id",
        )
        fire_control, fire_control_runtime = _require_module(
            source,
            assignment.fire_control_instance_id,
            "fire_control",
            f"{path}.fire_control_instance_id",
        )
        observations = successful.get(
            (source.ship_id, target.ship_id, sensor.id),
            [],
        )
        if "continuous_guidance" in assignment.requirements:
            candidates = [
                item for item in observations if item.sensor_mode == "fire_control_lock"
            ]
            sensor_capability = sensor.prototype.capability.to_dict()
            if sensor_capability["sensor_channel"] != "radar":
                raise ContractError(
                    "tactical_observation.continuous_guidance_sensor",
                    f"{path}.sensor_instance_id",
                    "持续照射必须使用雷达传感器",
                )
        else:
            candidates = [
                item
                for item in observations
                if item.sensor_mode in {"track", "fire_control_lock"}
            ]
        if not candidates:
            raise ContractError(
                "tactical_observation.fire_control_track_missing",
                path,
                "火控分配必须绑定同一步成功的跟踪或火控锁定观测",
            )
        source_observation = sorted(
            candidates,
            key=lambda item: (
                0 if item.sensor_mode == "fire_control_lock" else 1,
                item.outcome_id,
            ),
        )[0]
        capability = fire_control.prototype.capability.to_dict()
        supported = set(capability["supported_requirements"])
        if not set(assignment.requirements) <= supported:
            raise ContractError(
                "tactical_observation.fire_control_requirement_unsupported",
                f"{path}.requirements",
                str(sorted(set(assignment.requirements) - supported)),
            )
        distance_m = _distance(source, target)
        if distance_m > float(capability["maximum_lock_range_m"]) + EPS:
            raise ContractError(
                "tactical_observation.fire_control_out_of_range",
                f"{path}.target_ship_id",
                str(distance_m),
            )
        efficiencies: list[tuple[str, float]] = []
        for requirement in sorted(assignment.requirements):
            function_id = (
                "fire_control.solution"
                if requirement == "solution"
                else "fire_control.guidance"
            )
            efficiency = fire_control_runtime.function_efficiency(function_id)
            if efficiency <= EPS:
                raise ContractError(
                    "tactical_observation.fire_control_unavailable",
                    f"{path}.fire_control_instance_id",
                    function_id,
                )
            efficiencies.append((function_id, efficiency))
        channel_counts[assignment.channel_key] = (
            channel_counts.get(assignment.channel_key, 0) + 1
        )
        channel_limits[assignment.channel_key] = int(
            capability["simultaneous_channels"]
        )
        accepted.append(assignment)
        support_events.append(
            FireControlSupportEvent(
                assignment.assignment_id,
                target_time,
                source.ship_id,
                target.ship_id,
                sensor.id,
                fire_control.id,
                tuple(sorted(assignment.requirements)),
                source_observation.outcome_id,
                distance_m,
                tuple(efficiencies),
            )
        )
    for key, count in channel_counts.items():
        if count > channel_limits[key]:
            raise ContractError(
                "tactical_observation.fire_control_channels_exceeded",
                "$.fire_control_assignments",
                f"{key} 使用 {count} 条通道，容量为 {channel_limits[key]}",
            )

    return TacticalObservationResolution(
        tuple(observation_events),
        tuple(
            sorted(
                emission_events,
                key=lambda item: (
                    item.emitter_ship_id,
                    item.sensor_instance_id,
                    item.source_observation_outcome_id,
                ),
            )
        ),
        tuple(support_events),
        tuple(accepted),
    )


def validate_weapon_fire_control_support(
    resolution: TacticalObservationResolution,
    *,
    source_ship_id: str,
    target_ship_id: str,
    fire_control_instance_id: str | None,
    requirement: str,
) -> None:
    if requirement == "none":
        return
    if fire_control_instance_id is None or not resolution.support_assignments(
        source_ship_id,
        target_ship_id,
        requirement,
        fire_control_instance_id,
    ):
        raise ContractError(
            "tactical_observation.weapon_fire_control_support_missing",
            "$.fire_control_assignments",
            (
                f"{source_ship_id} 使用 {fire_control_instance_id} 向 "
                f"{target_ship_id} 开火时缺少 {requirement} 分配"
            ),
        )


def generate_guidance_runtime_inputs(
    projectiles: Iterable[ProjectileState],
    contexts: Iterable[TacticalObservationShipContext],
    guidance_catalog: MissileGuidanceProfileCatalog | None,
    observation_resolution: TacticalObservationResolution,
    seeker_outcomes: Iterable[ProjectileSeekerObservationOutcome],
    *,
    tactical_time_s: float,
) -> GeneratedGuidanceFactsResolution:
    """把有来源的场景观测事实映射为 I10 每枚导弹唯一运行时输入。"""

    target_time = _number(tactical_time_s, "$.tactical_time_s")
    context_by_id = _context_map(contexts)
    projectile_items = tuple(projectiles)
    projectile_by_id = {item.id: item for item in projectile_items}
    if len(projectile_by_id) != len(projectile_items):
        raise ContractError(
            "tactical_observation.projectile_duplicate",
            "$.projectiles",
            "弹丸 id 不得重复",
        )
    guided = {
        item.id: item for item in projectile_items if item.guidance_state is not None
    }
    if guided and guidance_catalog is None:
        raise ContractError(
            "missile_guidance.catalog_required",
            "$.guidance_catalog",
            "自动生成制导事实必须提供精确制导配置目录",
        )
    seeker_items = tuple(seeker_outcomes)
    seeker_by_projectile: dict[str, ProjectileSeekerObservationOutcome] = {}
    for index, outcome in enumerate(seeker_items):
        path = f"$.seeker_observation_outcomes[{index}]"
        outcome.validate(path)
        _require_step_time(
            outcome.tactical_time_s,
            target_time,
            f"{path}.tactical_time_s",
        )
        projectile = guided.get(outcome.projectile_id)
        if projectile is None:
            raise ContractError(
                "tactical_observation.seeker_projectile_unmatched",
                f"{path}.projectile_id",
                outcome.projectile_id,
            )
        if outcome.target_ship_id != projectile.target_ship_id:
            raise ContractError(
                "tactical_observation.seeker_target_mismatch",
                f"{path}.target_ship_id",
                outcome.target_ship_id,
            )
        assert projectile.guidance_state is not None
        if projectile.guidance_state.seeker_kind not in {
            "active_radar",
            "electro_optical",
        }:
            raise ContractError(
                "tactical_observation.seeker_outcome_not_applicable",
                f"{path}.projectile_id",
                projectile.guidance_state.seeker_kind,
            )
        if outcome.projectile_id in seeker_by_projectile:
            raise ContractError(
                "tactical_observation.seeker_projectile_duplicate",
                "$.seeker_observation_outcomes",
                outcome.projectile_id,
            )
        seeker_by_projectile[outcome.projectile_id] = outcome

    emission_ids_by_ship: dict[str, list[str]] = {}
    for event in observation_resolution.radar_emission_events:
        emission_ids_by_ship.setdefault(event.emitter_ship_id, []).append(
            event.source_observation_outcome_id
        )

    runtime_inputs: list[MissileGuidanceRuntimeInput] = []
    events: list[GeneratedGuidanceFactEvent] = []
    for projectile_id, projectile in sorted(guided.items()):
        assert projectile.guidance_state is not None
        _require_context(
            context_by_id,
            projectile.source_ship_id,
            f"$.projectiles.{projectile_id}.source_ship_id",
        )
        target = _require_context(
            context_by_id,
            projectile.target_ship_id,
            f"$.projectiles.{projectile_id}.target_ship_id",
        )
        if target.physical_status == "exited":
            raise ContractError(
                "tactical_observation.target_exited",
                f"$.projectiles.{projectile_id}.target_ship_id",
                target.ship_id,
            )
        assert guidance_catalog is not None
        profile = guidance_catalog.profile(projectile.munition_id)
        seeker_kind = projectile.guidance_state.seeker_kind
        if seeker_kind != profile.seeker_kind or seeker_kind not in GUIDANCE_FACT_SEEKER_KINDS:
            raise ContractError(
                "tactical_observation.seeker_profile_mismatch",
                f"$.projectiles.{projectile_id}.guidance_state.seeker_kind",
                seeker_kind,
            )
        illumination_assignments = observation_resolution.support_assignments(
            projectile.source_ship_id,
            projectile.target_ship_id,
            "continuous_guidance",
        )
        illumination_ids = tuple(
            sorted(item.assignment_id for item in illumination_assignments)
        )
        emission_ids = tuple(
            sorted(set(emission_ids_by_ship.get(projectile.target_ship_id, ())))
        )
        track_source_ids: tuple[str, ...] = ()
        track_available = False
        illumination_available = False
        target_emitting = False
        if seeker_kind == "passive_radar":
            track_source_ids = illumination_ids
            track_available = bool(illumination_ids)
            illumination_available = bool(illumination_ids)
            target_emitting = bool(emission_ids)
        elif seeker_kind in {"active_radar", "electro_optical"}:
            seeker_outcome = seeker_by_projectile.get(projectile_id)
            if seeker_outcome is not None:
                track_source_ids = (seeker_outcome.outcome_id,)
                track_available = seeker_outcome.contact_available
            if seeker_kind == "active_radar":
                target_emitting = bool(emission_ids)
        elif seeker_kind == "anti_radiation":
            target_emitting = bool(emission_ids)
        runtime_input = MissileGuidanceRuntimeInput(
            projectile_id,
            track_available,
            target_emitting,
            illumination_available,
        )
        runtime_input.validate()
        runtime_inputs.append(runtime_input)
        events.append(
            GeneratedGuidanceFactEvent(
                projectile_id,
                target_time,
                seeker_kind,
                track_available,
                target_emitting,
                illumination_available,
                track_source_ids,
                emission_ids,
                illumination_ids if illumination_available else (),
            )
        )

    return GeneratedGuidanceFactsResolution(tuple(runtime_inputs), tuple(events))
