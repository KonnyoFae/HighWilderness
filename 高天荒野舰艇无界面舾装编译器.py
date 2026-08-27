"""《高天荒野》v1alpha1 无界面舾装编译器。

本切片负责 OutfitPlan 的确定性放置、占用、净空、基础派生和设计状态执行器聚合。
主机按作用线两侧总推力配平，中轴主机独立输出；主机残余偏航和转向机残余平动力
均保留真实物理结果，供后续控制系统补偿。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    GridModulePlacement,
    HostedModulePlacement,
    HullCoatingCatalog,
    ModulePrototype,
    ModulePrototypeCatalog,
    OutfitModuleInstanceInput,
    OutfitPlanInput,
    ResourceReference,
    SideModulePlacement,
    canonical_sha256,
)
from 高天荒野舰艇无界面船壳编译器 import (
    CompiledDeckResult,
    CompiledHull,
    SideMountSlot,
    point_inside_or_on_polygon,
    point_inside_polygon,
)


OUTFIT_COMPILER_INTERFACE_ID = "gaotian.outfit-compiler/v1alpha1"
ACTUATOR_AGGREGATION_POLICY_ID = "gaotian.actuator-aggregation/physical-residuals-v1"
DERIVED_SHIP_SNAPSHOT_INTERFACE_ID = "gaotian.derived-ship-snapshot/v1alpha1"
STANDARD_GRAVITY_MPS2 = 9.80665
CELL_SIZE_M = 5.0
HALF_CELL_M = 2.5
EPS = 1.0e-8


GridOccupancy = tuple[int, int, int]
SideSlotKey = tuple[str, str, int, int]
SpatialKey = tuple[int, float, float]
MassPoint = tuple[float, float]
Vector2 = tuple[float, float]

MAIN_DIRECTION_AXES: tuple[tuple[str, Vector2], ...] = (
    ("forward", (0.0, 1.0)),
    ("reverse", (0.0, -1.0)),
    ("right", (1.0, 0.0)),
    ("left", (-1.0, 0.0)),
)


def _sorted_counts(values: dict[str, int]) -> dict[str, int]:
    return {key: values[key] for key in sorted(values)}


def _rounded_point(point: MassPoint) -> MassPoint:
    return round(point[0], 8), round(point[1], 8)


def _clean_scalar(value: float) -> float:
    if abs(value) <= EPS:
        return 0.0
    return round(value, 10)


def _add_vector(left: Vector2, right: Vector2) -> Vector2:
    return _clean_scalar(left[0] + right[0]), _clean_scalar(left[1] + right[1])


def _scaled_vector(vector: Vector2, scale: float) -> Vector2:
    return _clean_scalar(vector[0] * scale), _clean_scalar(vector[1] * scale)


def _spatial_key(band: int, point: MassPoint) -> SpatialKey:
    rounded = _rounded_point(point)
    return band, rounded[0], rounded[1]


def _rotate_clockwise_half_cell(point: tuple[int, int], rotation_deg: int) -> tuple[int, int]:
    x, y = point
    if rotation_deg == 0:
        return x, y
    if rotation_deg == 90:
        return y, -x
    if rotation_deg == 180:
        return -x, -y
    if rotation_deg == 270:
        return -y, x
    raise AssertionError(rotation_deg)


def _rotate_clockwise_vector(point: tuple[float, float], rotation_deg: int) -> tuple[float, float]:
    x, y = point
    if rotation_deg == 0:
        return x, y
    if rotation_deg == 90:
        return y, -x
    if rotation_deg == 180:
        return -x, -y
    if rotation_deg == 270:
        return -y, x
    raise AssertionError(rotation_deg)


def _grid_cell_from_half(
    anchor: tuple[int, int], offset: tuple[int, int], rotation_deg: int, path: str
) -> tuple[int, int]:
    rotated = _rotate_clockwise_half_cell(offset, rotation_deg)
    value = anchor[0] + rotated[0], anchor[1] + rotated[1]
    if value[0] % 2 or value[1] % 2:
        raise ContractError(
            "outfit.grid_cell_off_center",
            path,
            "变换后的模块格心没有落在船壳五米格心上",
        )
    return value[0] // 2, value[1] // 2


def _module_inertia(mass_kg: float, points: tuple[MassPoint, ...]) -> float:
    if not points:
        raise ContractError("outfit.mass_points_empty", "$", "模块没有可用于质量分布的位置")
    point_mass = mass_kg / len(points)
    cell_self_inertia = CELL_SIZE_M * CELL_SIZE_M / 6.0
    return sum(
        point_mass * (x * x + y * y + cell_self_inertia) for x, y in points
    )


@dataclass(frozen=True)
class OutfitWarning:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class ActuatorInstance:
    instance_id: str
    category: str
    thrust_n: float
    application_point_m: MassPoint
    direction_body: tuple[float, float]
    torque_about_cic_n_m: float
    fuel_units_per_s: float
    response_time_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_point_m": list(self.application_point_m),
            "category": self.category,
            "direction_body": list(self.direction_body),
            "fuel_units_per_s": self.fuel_units_per_s,
            "instance_id": self.instance_id,
            "response_time_s": self.response_time_s,
            "thrust_n": self.thrust_n,
            "torque_about_cic_n_m": self.torque_about_cic_n_m,
        }


@dataclass(frozen=True)
class AggregatedActuatorUse:
    instance_id: str
    available_thrust_n: float
    output_scale: float
    used_thrust_n: float
    force_body_n: Vector2
    torque_about_cic_n_m: float
    fuel_units_per_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "available_thrust_n": self.available_thrust_n,
            "force_body_n": list(self.force_body_n),
            "fuel_units_per_s": self.fuel_units_per_s,
            "instance_id": self.instance_id,
            "output_scale": self.output_scale,
            "torque_about_cic_n_m": self.torque_about_cic_n_m,
            "used_thrust_n": self.used_thrust_n,
        }


@dataclass(frozen=True)
class MainDirectionCapability:
    direction: str
    direction_body: Vector2
    centerline_capacity_n: float
    positive_moment_side_capacity_n: float
    negative_moment_side_capacity_n: float
    balanced_off_axis_thrust_each_side_n: float
    total_used_thrust_n: float
    net_force_body_n: Vector2
    residual_torque_about_cic_n_m: float
    fuel_units_per_s: float
    response_time_s: float
    uses: tuple[AggregatedActuatorUse, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "balanced_off_axis_thrust_each_side_n": self.balanced_off_axis_thrust_each_side_n,
            "centerline_capacity_n": self.centerline_capacity_n,
            "direction": self.direction,
            "direction_body": list(self.direction_body),
            "fuel_units_per_s": self.fuel_units_per_s,
            "negative_moment_side_capacity_n": self.negative_moment_side_capacity_n,
            "net_force_body_n": list(self.net_force_body_n),
            "positive_moment_side_capacity_n": self.positive_moment_side_capacity_n,
            "residual_torque_about_cic_n_m": self.residual_torque_about_cic_n_m,
            "response_time_s": self.response_time_s,
            "total_used_thrust_n": self.total_used_thrust_n,
            "uses": [use.to_dict() for use in self.uses],
        }


@dataclass(frozen=True)
class TurningDirectionCapability:
    direction: str
    net_force_body_n: Vector2
    signed_torque_about_cic_n_m: float
    torque_capacity_n_m: float
    fuel_units_per_s: float
    response_time_s: float
    uses: tuple[AggregatedActuatorUse, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "fuel_units_per_s": self.fuel_units_per_s,
            "net_force_body_n": list(self.net_force_body_n),
            "response_time_s": self.response_time_s,
            "signed_torque_about_cic_n_m": self.signed_torque_about_cic_n_m,
            "torque_capacity_n_m": self.torque_capacity_n_m,
            "uses": [use.to_dict() for use in self.uses],
        }


@dataclass(frozen=True)
class ActuatorAggregation:
    policy_id: str
    main_directions: tuple[MainDirectionCapability, ...]
    turning_directions: tuple[TurningDirectionCapability, ...]
    zero_torque_maneuver_thruster_instances: tuple[str, ...]

    def main(self, direction: str) -> MainDirectionCapability:
        return next(item for item in self.main_directions if item.direction == direction)

    def turning(self, direction: str) -> TurningDirectionCapability:
        return next(item for item in self.turning_directions if item.direction == direction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "main_directions": [item.to_dict() for item in self.main_directions],
            "policy": {
                "centerline_main_engines": "independent_full_output",
                "main_engine_balance": "equal_total_thrust_across_moment_sides",
                "main_engine_residual_yaw": "preserve_physical_torque",
                "maneuver_thruster_residual_translation": "preserve_physical_force",
                "policy_id": self.policy_id,
            },
            "turning_directions": [item.to_dict() for item in self.turning_directions],
            "zero_torque_maneuver_thruster_instances": list(
                self.zero_torque_maneuver_thruster_instances
            ),
        }


def _aggregated_use(actuator: ActuatorInstance, scale: float) -> AggregatedActuatorUse:
    used_thrust = actuator.thrust_n * scale
    force = _scaled_vector(actuator.direction_body, used_thrust)
    return AggregatedActuatorUse(
        instance_id=actuator.instance_id,
        available_thrust_n=actuator.thrust_n,
        output_scale=_clean_scalar(scale),
        used_thrust_n=_clean_scalar(used_thrust),
        force_body_n=force,
        torque_about_cic_n_m=_clean_scalar(actuator.torque_about_cic_n_m * scale),
        fuel_units_per_s=_clean_scalar(actuator.fuel_units_per_s * scale),
    )


def _aggregate_main_direction(
    direction_name: str,
    direction_body: Vector2,
    actuators: tuple[ActuatorInstance, ...],
) -> MainDirectionCapability:
    candidates = tuple(
        sorted(
            (
                actuator
                for actuator in actuators
                if actuator.category == "main_engine"
                and actuator.direction_body == direction_body
            ),
            key=lambda actuator: actuator.instance_id,
        )
    )
    positive = tuple(
        actuator for actuator in candidates if actuator.torque_about_cic_n_m > EPS
    )
    negative = tuple(
        actuator for actuator in candidates if actuator.torque_about_cic_n_m < -EPS
    )
    centerline = tuple(
        actuator for actuator in candidates if abs(actuator.torque_about_cic_n_m) <= EPS
    )
    positive_capacity = sum(actuator.thrust_n for actuator in positive)
    negative_capacity = sum(actuator.thrust_n for actuator in negative)
    centerline_capacity = sum(actuator.thrust_n for actuator in centerline)
    balanced_each = min(positive_capacity, negative_capacity)
    positive_scale = balanced_each / positive_capacity if positive_capacity > EPS else 0.0
    negative_scale = balanced_each / negative_capacity if negative_capacity > EPS else 0.0
    scales = {
        **{actuator.instance_id: positive_scale for actuator in positive},
        **{actuator.instance_id: negative_scale for actuator in negative},
        **{actuator.instance_id: 1.0 for actuator in centerline},
    }
    uses = tuple(
        _aggregated_use(actuator, scales[actuator.instance_id]) for actuator in candidates
    )
    net_force: Vector2 = (0.0, 0.0)
    for use in uses:
        net_force = _add_vector(net_force, use.force_body_n)
    used_actuators = [
        actuator
        for actuator in candidates
        if scales[actuator.instance_id] > EPS
    ]
    return MainDirectionCapability(
        direction=direction_name,
        direction_body=direction_body,
        centerline_capacity_n=_clean_scalar(centerline_capacity),
        positive_moment_side_capacity_n=_clean_scalar(positive_capacity),
        negative_moment_side_capacity_n=_clean_scalar(negative_capacity),
        balanced_off_axis_thrust_each_side_n=_clean_scalar(balanced_each),
        total_used_thrust_n=_clean_scalar(sum(use.used_thrust_n for use in uses)),
        net_force_body_n=net_force,
        residual_torque_about_cic_n_m=_clean_scalar(
            sum(use.torque_about_cic_n_m for use in uses)
        ),
        fuel_units_per_s=_clean_scalar(sum(use.fuel_units_per_s for use in uses)),
        response_time_s=max(
            (actuator.response_time_s for actuator in used_actuators), default=0.0
        ),
        uses=uses,
    )


def _aggregate_turning_direction(
    direction_name: str,
    torque_sign: int,
    actuators: tuple[ActuatorInstance, ...],
) -> TurningDirectionCapability:
    candidates = tuple(
        sorted(
            (
                actuator
                for actuator in actuators
                if actuator.category == "maneuver_thruster"
                and actuator.torque_about_cic_n_m * torque_sign > EPS
            ),
            key=lambda actuator: actuator.instance_id,
        )
    )
    uses = tuple(_aggregated_use(actuator, 1.0) for actuator in candidates)
    net_force: Vector2 = (0.0, 0.0)
    for use in uses:
        net_force = _add_vector(net_force, use.force_body_n)
    signed_torque = sum(use.torque_about_cic_n_m for use in uses)
    return TurningDirectionCapability(
        direction=direction_name,
        net_force_body_n=net_force,
        signed_torque_about_cic_n_m=_clean_scalar(signed_torque),
        torque_capacity_n_m=_clean_scalar(abs(signed_torque)),
        fuel_units_per_s=_clean_scalar(sum(use.fuel_units_per_s for use in uses)),
        response_time_s=max(
            (actuator.response_time_s for actuator in candidates), default=0.0
        ),
        uses=uses,
    )


def aggregate_actuators(actuators: tuple[ActuatorInstance, ...]) -> ActuatorAggregation:
    """从设计状态逐实例执行器生成确定性的配平后方向能力。"""

    return ActuatorAggregation(
        policy_id=ACTUATOR_AGGREGATION_POLICY_ID,
        main_directions=tuple(
            _aggregate_main_direction(name, direction, actuators)
            for name, direction in MAIN_DIRECTION_AXES
        ),
        turning_directions=(
            _aggregate_turning_direction("counterclockwise", 1, actuators),
            _aggregate_turning_direction("clockwise", -1, actuators),
        ),
        zero_torque_maneuver_thruster_instances=tuple(
            sorted(
                actuator.instance_id
                for actuator in actuators
                if actuator.category == "maneuver_thruster"
                and abs(actuator.torque_about_cic_n_m) <= EPS
            )
        ),
    )


@dataclass(frozen=True)
class CompiledModuleInstance:
    id: str
    prototype: ModulePrototype
    placement_kind: str
    base_deck_level: int
    anchor_m: MassPoint
    rotation_deg: int
    host_instance_id: str | None
    internal_cells: tuple[GridOccupancy, ...]
    top_cells: tuple[GridOccupancy, ...]
    side_slots: tuple[SideSlotKey, ...]
    body_spatial_keys: tuple[SpatialKey, ...]
    clearance_spatial_keys: tuple[SpatialKey, ...]
    mass_points_m: tuple[MassPoint, ...]
    inertia_kg_m2: float
    actuator: ActuatorInstance | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator": None if self.actuator is None else self.actuator.to_dict(),
            "anchor_m": list(self.anchor_m),
            "base_deck_level": self.base_deck_level,
            "body_spatial_keys": [list(value) for value in self.body_spatial_keys],
            "clearance_spatial_keys": [list(value) for value in self.clearance_spatial_keys],
            "host_instance_id": self.host_instance_id,
            "id": self.id,
            "inertia_kg_m2": self.inertia_kg_m2,
            "internal_cells": [list(value) for value in self.internal_cells],
            "mass_points_m": [list(value) for value in self.mass_points_m],
            "placement_kind": self.placement_kind,
            "prototype": self.prototype.reference.to_dict(),
            "rotation_deg": self.rotation_deg,
            "side_slots": [
                {
                    "deck_id": deck_id,
                    "edge_index": edge_index,
                    "region_id": region_id,
                    "slot_index": slot_index,
                }
                for deck_id, region_id, edge_index, slot_index in self.side_slots
            ],
            "top_cells": [list(value) for value in self.top_cells],
        }


@dataclass(frozen=True)
class CompiledOutfit:
    normalized_plan: OutfitPlanInput
    source_sha256: str
    hull_source_sha256: str
    module_catalog_reference: ResourceReference
    module_catalog_source_sha256: str
    coating_catalog_reference: ResourceReference
    coating_catalog_source_sha256: str
    coating_reference: ResourceReference
    coating_rcs_multiplier: float
    instances: tuple[CompiledModuleInstance, ...]
    module_mass_kg: float
    design_mass_kg: float
    module_inertia_kg_m2: float
    design_inertia_kg_m2: float
    lift_force_n: float
    supported_design_mass_kg: float
    lift_margin_n: float
    generation_kw: float
    standby_load_kw_by_category: tuple[tuple[str, float], ...]
    active_load_kw_by_category: tuple[tuple[str, float], ...]
    minimum_crew: tuple[tuple[str, int], ...]
    standard_crew: tuple[tuple[str, int], ...]
    crew_capacity: tuple[tuple[str, int], ...]
    actuators: tuple[ActuatorInstance, ...]
    actuator_aggregation: ActuatorAggregation
    unresolved_external_rcs_instances: tuple[str, ...]
    known_external_rcs_m2: float
    warnings: tuple[OutfitWarning, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuators": [actuator.to_dict() for actuator in self.actuators],
            "actuator_aggregation": self.actuator_aggregation.to_dict(),
            "coating": {
                "rcs_multiplier": self.coating_rcs_multiplier,
                "reference": self.coating_reference.to_dict(),
            },
            "compiler_capabilities": [
                "exact_hull_module_and_coating_references",
                "grid_side_and_hosted_placements",
                "internal_top_cross_deck_and_side_occupancy",
                "body_clearance_and_exhaust_collision",
                "module_mass_and_inertia",
                "lift_power_and_crew_aggregation",
                "per_instance_actuator_vectors",
                "balanced_main_and_turning_actuator_aggregation",
            ],
            "compiler_interface": OUTFIT_COMPILER_INTERFACE_ID,
            "crew": {
                "capacity": dict(self.crew_capacity),
                "minimum": dict(self.minimum_crew),
                "standard": dict(self.standard_crew),
            },
            "deferred_capabilities": [
                "compound_internal_side_module_anchor_relation",
                "runtime_power_allocation_and_damage_state",
                "complete_external_module_rcs",
            ],
            "design_inertia_kg_m2": self.design_inertia_kg_m2,
            "design_mass_kg": self.design_mass_kg,
            "generation_kw": self.generation_kw,
            "hull_source_sha256": self.hull_source_sha256,
            "instances": [instance.to_dict() for instance in self.instances],
            "lift": {
                "lift_force_n": self.lift_force_n,
                "lift_margin_n": self.lift_margin_n,
                "supported_design_mass_kg": self.supported_design_mass_kg,
            },
            "module_inertia_kg_m2": self.module_inertia_kg_m2,
            "module_mass_kg": self.module_mass_kg,
            "normalized_plan": self.normalized_plan.to_dict(),
            "source_catalogs": {
                "coating_catalog": {
                    "reference": self.coating_catalog_reference.to_dict(),
                    "source_sha256": self.coating_catalog_source_sha256,
                },
                "module_catalog": {
                    "reference": self.module_catalog_reference.to_dict(),
                    "source_sha256": self.module_catalog_source_sha256,
                },
            },
            "power_load_kw": {
                "active_by_category": dict(self.active_load_kw_by_category),
                "standby_by_category": dict(self.standby_load_kw_by_category),
            },
            "rcs": {
                "known_external_rcs_m2": self.known_external_rcs_m2,
                "unresolved_external_rcs_instances": list(
                    self.unresolved_external_rcs_instances
                ),
            },
            "source_sha256": self.source_sha256,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True)
class DerivedShipSnapshot:
    """由精确船壳与舾装结果生成、禁止手填覆盖的设计态派生快照。"""

    hull: CompiledHull
    outfit: CompiledOutfit

    @property
    def id(self) -> str:
        return f"{self.outfit.normalized_plan.id}.derived"

    @property
    def version(self) -> int:
        return self.outfit.normalized_plan.version

    @property
    def source_sha256(self) -> str:
        return canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        plan = self.outfit.normalized_plan
        return {
            "actuator_aggregation": self.outfit.actuator_aggregation.to_dict(),
            "aerodynamic_cache": self.hull.aerodynamic_cache.to_dict(),
            "balance_status": plan.fixture_level,
            "compiler_interfaces": {
                "derived_snapshot": DERIVED_SHIP_SNAPSHOT_INTERFACE_ID,
                "hull": self.hull.to_dict()["compiler_interface"],
                "outfit": OUTFIT_COMPILER_INTERFACE_ID,
            },
            "crew": {
                "capacity": dict(self.outfit.crew_capacity),
                "minimum": dict(self.outfit.minimum_crew),
                "standard": dict(self.outfit.standard_crew),
            },
            "deferred_runtime_state": [
                "sortie_cargo_mass_and_inertia",
                "current_module_damage_power_and_crew",
                "height_layer_and_transition_state",
            ],
            "design": {
                "inertia_kg_m2": self.outfit.design_inertia_kg_m2,
                "mass_kg": self.outfit.design_mass_kg,
                "module_inertia_kg_m2": self.outfit.module_inertia_kg_m2,
                "module_mass_kg": self.outfit.module_mass_kg,
            },
            "id": self.id,
            "kind": "DerivedShipSnapshot",
            "lift": {
                "force_n": self.outfit.lift_force_n,
                "margin_at_design_mass_n": self.outfit.lift_margin_n,
                "supported_design_mass_kg": self.outfit.supported_design_mass_kg,
            },
            "name": f"{plan.name}·派生快照",
            "power": {
                "active_load_kw_by_category": dict(
                    self.outfit.active_load_kw_by_category
                ),
                "generation_kw": self.outfit.generation_kw,
                "standby_load_kw_by_category": dict(
                    self.outfit.standby_load_kw_by_category
                ),
            },
            "rcs": {
                "coating_multiplier": self.outfit.coating_rcs_multiplier,
                "hull_baseline_cache": self.hull.hull_rcs_cache.to_dict(),
                "known_external_rcs_m2": self.outfit.known_external_rcs_m2,
                "unresolved_external_rcs_instances": list(
                    self.outfit.unresolved_external_rcs_instances
                ),
            },
            "schema": "gaotian.ship/v1alpha1",
            "sources": {
                "coating": self.outfit.coating_reference.to_dict(),
                "coating_catalog": {
                    "reference": self.outfit.coating_catalog_reference.to_dict(),
                    "source_sha256": self.outfit.coating_catalog_source_sha256,
                },
                "hull_blueprint": {
                    "compiled_sha256": canonical_sha256(self.hull),
                    "reference": {
                        "id": self.hull.normalized_blueprint.id,
                        "version": self.hull.normalized_blueprint.version,
                    },
                    "source_sha256": self.hull.source_sha256,
                },
                "module_catalog": {
                    "reference": self.outfit.module_catalog_reference.to_dict(),
                    "source_sha256": self.outfit.module_catalog_source_sha256,
                },
                "outfit_plan": {
                    "compiled_sha256": canonical_sha256(self.outfit),
                    "reference": {"id": plan.id, "version": plan.version},
                    "source_sha256": self.outfit.source_sha256,
                },
            },
            "structure": {
                "hull_durability_volume_proxy_m3": self.hull.hull_durability_volume_proxy_m3,
                "lateral_bottleneck_m": self.hull.lateral_bottleneck_m,
                "longitudinal_bottleneck_m": self.hull.longitudinal_bottleneck_m,
                "safe_lateral_mps2": self.hull.safe_lateral_mps2,
                "safe_longitudinal_mps2": self.hull.safe_longitudinal_mps2,
                "safe_yaw_acceleration_rad_s2": self.hull.safe_yaw_acceleration_rad_s2,
                "safe_yaw_rate_rad_s": self.hull.safe_yaw_rate_rad_s,
            },
            "version": self.version,
            "warnings": [warning.to_dict() for warning in self.outfit.warnings],
        }


class _OutfitCompiler:
    def __init__(
        self,
        plan: OutfitPlanInput,
        hull: CompiledHull,
        modules: ModulePrototypeCatalog,
        coatings: HullCoatingCatalog,
    ) -> None:
        self.plan = plan
        self.hull = hull
        self.module_catalog = modules
        self.coating_catalog = coatings
        self.deck_by_id = {deck.id: deck for deck in hull.decks}
        self.deck_by_level = {deck.level: deck for deck in hull.decks}
        self.instance_input_by_id = {instance.id: instance for instance in plan.modules}
        self.prototype_by_instance = {
            instance.id: modules.module(instance.prototype, f"$.modules[{instance.id}].prototype")
            for instance in plan.modules
        }
        self.compiled_by_id: dict[str, CompiledModuleInstance] = {}
        self.compiling: set[str] = set()
        self.hosted_slot_users: dict[tuple[str, str], str] = {}

    def compile(self) -> CompiledOutfit:
        hull_reference = ResourceReference(
            self.hull.normalized_blueprint.id, self.hull.normalized_blueprint.version
        )
        if self.plan.hull_blueprint != hull_reference:
            raise ContractError(
                "outfit.hull_reference_mismatch",
                "$.hull_blueprint",
                f"方案绑定 {self.plan.hull_blueprint}，实际船壳为 {hull_reference}",
            )
        coating = self.coating_catalog.coating(self.plan.hull_coating, "$.hull_coating")
        if not coating.runtime_usable or coating.rcs_multiplier is None:
            raise ContractError(
                "coating.not_runtime_usable", "$.hull_coating", "所选涂料尚不可装备"
            )
        for instance in self.plan.modules:
            self._compile_instance(instance.id)
        instances = tuple(self.compiled_by_id[instance.id] for instance in self.plan.modules)
        self._validate_cic(instances)
        self._validate_occupancy(instances)

        module_mass = sum(instance.prototype.mass_kg for instance in instances)
        module_inertia = sum(instance.inertia_kg_m2 for instance in instances)
        design_mass = self.hull.hull_mass_kg + module_mass
        lift = sum(
            float(instance.prototype.capability.to_dict()["lift_force_n"])
            for instance in instances
            if instance.prototype.category == "lift_fuel_tank"
        )
        lift_margin = lift - design_mass * STANDARD_GRAVITY_MPS2
        if lift_margin < -EPS:
            raise ContractError(
                "outfit.insufficient_lift",
                "$",
                f"设计质量需要 {design_mass * STANDARD_GRAVITY_MPS2:.3f}N，现有升力 {lift:.3f}N",
            )

        generation = sum(instance.prototype.power.generation_kw for instance in instances)
        standby_load = {
            category: 0.0
            for category in (
                "damage_control",
                "weapons_and_active_defense",
                "fire_control",
                "sensors",
            )
        }
        active_load = dict(standby_load)
        minimum_crew: dict[str, int] = {}
        standard_crew: dict[str, int] = {}
        crew_capacity: dict[str, int] = {}
        known_external_rcs = 0.0
        unresolved_rcs: list[str] = []
        for instance in instances:
            prototype = instance.prototype
            if prototype.power.consumer_category is not None:
                category = prototype.power.consumer_category
                standby_load[category] += prototype.power.standby_load_kw
                active_load[category] += prototype.power.active_load_kw
            for crew_type, count in prototype.minimum_crew_counts().items():
                minimum_crew[crew_type] = minimum_crew.get(crew_type, 0) + count
            for crew_type, count in prototype.standard_crew_counts().items():
                standard_crew[crew_type] = standard_crew.get(crew_type, 0) + count
            if prototype.category == "crew_quarters":
                for capacity in prototype.capability.to_dict()["capacities"]:
                    crew_type = str(capacity["crew_type"])
                    crew_capacity[crew_type] = crew_capacity.get(crew_type, 0) + int(
                        capacity["capacity"]
                    )
            has_external = bool(
                prototype.installation.top_footprint_half_cells
                or prototype.installation.side_external_footprint_half_cells
            )
            if has_external:
                if prototype.base_external_rcs_m2 is None:
                    unresolved_rcs.append(instance.id)
                else:
                    known_external_rcs += prototype.base_external_rcs_m2

        warnings: list[OutfitWarning] = []
        if not any(instance.prototype.category == "main_engine" for instance in instances):
            warnings.append(OutfitWarning("outfit.no_main_engine", "$", "没有主发动机，设计无法主动平动"))
        if not any(instance.prototype.category == "maneuver_thruster" for instance in instances):
            warnings.append(
                OutfitWarning("outfit.no_maneuver_thruster", "$", "没有转向发动机，设计无法主动回转")
            )
        if generation + EPS < sum(active_load.values()):
            warnings.append(
                OutfitWarning(
                    "outfit.power_deficit",
                    "$",
                    "全部设备同时工作时发电量不足，运行时将按优先级跳闸",
                )
            )
        for crew_type, count in sorted(minimum_crew.items()):
            if crew_capacity.get(crew_type, 0) < count:
                warnings.append(
                    OutfitWarning(
                        "outfit.minimum_crew_capacity_shortfall",
                        f"$.crew.{crew_type}",
                        f"最低需求 {count}，人员容量 {crew_capacity.get(crew_type, 0)}",
                    )
                )
        if unresolved_rcs:
            warnings.append(
                OutfitWarning(
                    "outfit.external_rcs_unresolved",
                    "$",
                    "部分外露模块尚无正式基准 RCS，不能生成完整整舰 RCS",
                )
            )

        actuators = tuple(
            instance.actuator for instance in instances if instance.actuator is not None
        )
        actuator_aggregation = aggregate_actuators(actuators)
        if actuator_aggregation.turning("counterclockwise").torque_capacity_n_m <= EPS:
            warnings.append(
                OutfitWarning(
                    "outfit.no_counterclockwise_turning_torque",
                    "$",
                    "没有能够产生逆时针力矩的转向发动机",
                )
            )
        if actuator_aggregation.turning("clockwise").torque_capacity_n_m <= EPS:
            warnings.append(
                OutfitWarning(
                    "outfit.no_clockwise_turning_torque",
                    "$",
                    "没有能够产生顺时针力矩的转向发动机",
                )
            )
        if actuator_aggregation.zero_torque_maneuver_thruster_instances:
            warnings.append(
                OutfitWarning(
                    "outfit.zero_torque_maneuver_thruster",
                    "$",
                    "部分转向发动机的推力作用线穿过 CIC，无法产生回转力矩",
                )
            )
        return CompiledOutfit(
            normalized_plan=self.plan,
            source_sha256=canonical_sha256(self.plan),
            hull_source_sha256=self.hull.source_sha256,
            module_catalog_reference=ResourceReference(
                self.module_catalog.id, self.module_catalog.version
            ),
            module_catalog_source_sha256=canonical_sha256(self.module_catalog),
            coating_catalog_reference=ResourceReference(
                self.coating_catalog.id, self.coating_catalog.version
            ),
            coating_catalog_source_sha256=canonical_sha256(self.coating_catalog),
            coating_reference=coating.reference,
            coating_rcs_multiplier=coating.rcs_multiplier,
            instances=instances,
            module_mass_kg=module_mass,
            design_mass_kg=design_mass,
            module_inertia_kg_m2=module_inertia,
            design_inertia_kg_m2=self.hull.hull_inertia_kg_m2 + module_inertia,
            lift_force_n=lift,
            supported_design_mass_kg=lift / STANDARD_GRAVITY_MPS2,
            lift_margin_n=lift_margin,
            generation_kw=generation,
            standby_load_kw_by_category=tuple(standby_load.items()),
            active_load_kw_by_category=tuple(active_load.items()),
            minimum_crew=tuple(sorted(minimum_crew.items())),
            standard_crew=tuple(sorted(standard_crew.items())),
            crew_capacity=tuple(sorted(crew_capacity.items())),
            actuators=actuators,
            actuator_aggregation=actuator_aggregation,
            unresolved_external_rcs_instances=tuple(sorted(unresolved_rcs)),
            known_external_rcs_m2=known_external_rcs,
            warnings=tuple(warnings),
        )

    def _compile_instance(self, instance_id: str) -> CompiledModuleInstance:
        existing = self.compiled_by_id.get(instance_id)
        if existing is not None:
            return existing
        if instance_id in self.compiling:
            raise ContractError("outfit.host_cycle", f"$.modules[{instance_id}]", "嵌入宿主引用形成循环")
        try:
            instance_input = self.instance_input_by_id[instance_id]
        except KeyError as error:
            raise ContractError(
                "outfit.host_missing", f"$.modules[{instance_id}]", "找不到宿主实例"
            ) from error
        self.compiling.add(instance_id)
        try:
            prototype = self.prototype_by_instance[instance_id]
            placement = instance_input.placement
            if isinstance(placement, GridModulePlacement):
                compiled = self._compile_grid(instance_input, prototype, placement)
            elif isinstance(placement, SideModulePlacement):
                compiled = self._compile_side(instance_input, prototype, placement)
            elif isinstance(placement, HostedModulePlacement):
                compiled = self._compile_hosted(instance_input, prototype, placement)
            else:
                raise AssertionError(type(placement))
            self.compiled_by_id[instance_id] = compiled
            return compiled
        finally:
            self.compiling.remove(instance_id)

    def _deck(self, deck_id: str, path: str) -> CompiledDeckResult:
        try:
            return self.deck_by_id[deck_id]
        except KeyError as error:
            raise ContractError("outfit.deck_missing", path, f"找不到甲板 {deck_id}") from error

    @staticmethod
    def _check_rotation(prototype: ModulePrototype, rotation_deg: int, path: str) -> None:
        if rotation_deg not in prototype.installation.allowed_rotations_deg:
            raise ContractError(
                "outfit.rotation_not_allowed",
                path,
                f"原型不允许旋转到 {rotation_deg}°",
            )

    def _compile_grid(
        self,
        instance_input: OutfitModuleInstanceInput,
        prototype: ModulePrototype,
        placement: GridModulePlacement,
    ) -> CompiledModuleInstance:
        path = f"$.modules[{instance_input.id}]"
        geometry = prototype.installation
        if geometry.host_slot is not None:
            raise ContractError("outfit.hosted_requires_host", path, "嵌入模块必须使用 hosted 放置")
        if geometry.side_external_footprint_half_cells:
            if geometry.internal_footprint_half_cells or geometry.top_footprint_half_cells:
                raise ContractError(
                    "outfit.compound_internal_side_deferred",
                    path,
                    "内部—侧挂复合锚点关系尚未进入本接口版本",
                )
            raise ContractError("outfit.placement_kind_mismatch", path, "侧挂模块必须使用 side 放置")
        if not (geometry.internal_footprint_half_cells or geometry.top_footprint_half_cells):
            raise ContractError("outfit.placement_kind_mismatch", path, "原型没有网格安装几何")
        self._check_rotation(prototype, placement.rotation_deg, f"{path}.placement.rotation_deg")
        base_deck = self._deck(placement.deck_id, f"{path}.placement.deck_id")
        if geometry.deck_rule == "base_only" and base_deck.level != 0:
            raise ContractError("outfit.deck_rule", path, "该模块只能安装在基底层")

        internal: list[GridOccupancy] = []
        top: list[GridOccupancy] = []
        body_spatial: list[SpatialKey] = []
        clearance_spatial: list[SpatialKey] = []
        mass_points: list[MassPoint] = []
        for span_offset in range(geometry.internal_deck_span):
            level = base_deck.level + span_offset
            target = self.deck_by_level.get(level)
            if target is None:
                raise ContractError("outfit.cross_deck_outside", path, "跨层模块超出船壳甲板范围")
            valid = set(target.internal_cells)
            for index, offset in enumerate(geometry.internal_footprint_half_cells):
                cell = _grid_cell_from_half(
                    placement.anchor_half_cell,
                    offset,
                    placement.rotation_deg,
                    f"{path}.installation.internal[{index}]",
                )
                if cell not in valid:
                    raise ContractError("outfit.internal_cell_invalid", path, f"内部格 {level}:{cell} 不可用")
                internal.append((level, cell[0], cell[1]))
                point = cell[0] * CELL_SIZE_M, cell[1] * CELL_SIZE_M
                body_spatial.append(_spatial_key(level, point))
                mass_points.append(point)

        if geometry.top_footprint_half_cells:
            level = base_deck.level + geometry.top_deck_offset
            target = self.deck_by_level.get(level)
            if target is None:
                raise ContractError("outfit.top_deck_outside", path, "顶挂部分超出船壳甲板范围")
            valid = set(target.exposed_top_cells)
            for index, offset in enumerate(geometry.top_footprint_half_cells):
                cell = _grid_cell_from_half(
                    placement.anchor_half_cell,
                    offset,
                    placement.rotation_deg,
                    f"{path}.installation.top[{index}]",
                )
                if cell not in valid:
                    raise ContractError("outfit.top_cell_invalid", path, f"露天顶挂格 {level}:{cell} 不可用")
                top.append((level, cell[0], cell[1]))
                point = cell[0] * CELL_SIZE_M, cell[1] * CELL_SIZE_M
                body_spatial.append(_spatial_key(level + 1, point))
                mass_points.append(point)
            upper = self.deck_by_level.get(level + 1)
            upper_cells = set() if upper is None else set(upper.internal_cells)
            for index, offset in enumerate(geometry.top_clearance_half_cells):
                cell = _grid_cell_from_half(
                    placement.anchor_half_cell,
                    offset,
                    placement.rotation_deg,
                    f"{path}.installation.top_clearance[{index}]",
                )
                if cell in upper_cells:
                    raise ContractError("outfit.top_clearance_hull_conflict", path, "顶挂净空与上层船壳冲突")
                clearance_spatial.append(
                    _spatial_key(level + 1, (cell[0] * CELL_SIZE_M, cell[1] * CELL_SIZE_M))
                )

        for index, offset in enumerate(geometry.exhaust_clearance_half_cells):
            cell = _grid_cell_from_half(
                placement.anchor_half_cell,
                offset,
                placement.rotation_deg,
                f"{path}.installation.exhaust[{index}]",
            )
            clearance_spatial.append(
                _spatial_key(base_deck.level, (cell[0] * CELL_SIZE_M, cell[1] * CELL_SIZE_M))
            )

        anchor_m = (
            placement.anchor_half_cell[0] * HALF_CELL_M,
            placement.anchor_half_cell[1] * HALF_CELL_M,
        )
        actuator = self._actuator(
            instance_input.id,
            prototype,
            anchor_m,
            _rotate_clockwise_vector((0.0, 1.0), placement.rotation_deg),
        )
        points = tuple(_rounded_point(point) for point in mass_points)
        return CompiledModuleInstance(
            instance_input.id,
            prototype,
            "grid",
            base_deck.level,
            anchor_m,
            placement.rotation_deg,
            None,
            tuple(sorted(internal)),
            tuple(sorted(top)),
            (),
            tuple(sorted(set(body_spatial))),
            tuple(sorted(set(clearance_spatial))),
            points,
            _module_inertia(prototype.mass_kg, points),
            actuator,
        )

    def _compile_side(
        self,
        instance_input: OutfitModuleInstanceInput,
        prototype: ModulePrototype,
        placement: SideModulePlacement,
    ) -> CompiledModuleInstance:
        path = f"$.modules[{instance_input.id}]"
        geometry = prototype.installation
        if geometry.host_slot is not None:
            raise ContractError("outfit.hosted_requires_host", path, "嵌入模块必须使用 hosted 放置")
        if geometry.internal_footprint_half_cells or geometry.top_footprint_half_cells:
            raise ContractError(
                "outfit.compound_internal_side_deferred",
                path,
                "内部—侧挂复合锚点关系尚未进入本接口版本",
            )
        if not geometry.side_external_footprint_half_cells:
            raise ContractError("outfit.placement_kind_mismatch", path, "原型不是侧挂模块")
        self._check_rotation(prototype, placement.rotation_deg, f"{path}.placement.rotation_deg")
        deck = self._deck(placement.deck_id, f"{path}.placement.deck_id")
        if geometry.deck_rule == "base_only" and deck.level != 0:
            raise ContractError("outfit.deck_rule", path, "该模块只能安装在基底层")
        slot_map = {
            (slot.region_id, slot.edge_index, slot.slot_index): slot
            for slot in deck.side_mount_slots
        }
        selected: list[SideMountSlot] = []
        for offset in range(geometry.side_mount_length_steps):
            key = (placement.region_id, placement.edge_index, placement.start_slot_index + offset)
            slot = slot_map.get(key)
            if slot is None:
                raise ContractError("outfit.side_slot_invalid", path, f"侧挂槽位不存在：{key}")
            selected.append(slot)
        first = selected[0]
        last = selected[-1]
        anchor = (
            0.5 * (first.start_m[0] + last.end_m[0]),
            0.5 * (first.start_m[1] + last.end_m[1]),
        )
        length = hypot(first.end_m[0] - first.start_m[0], first.end_m[1] - first.start_m[1])
        tangent = (
            (first.end_m[0] - first.start_m[0]) / length,
            (first.end_m[1] - first.start_m[1]) / length,
        )
        outward = tangent[1], -tangent[0]

        def local_to_world(offset: tuple[int, int]) -> MassPoint:
            rotated = _rotate_clockwise_half_cell(offset, placement.rotation_deg)
            x_m = rotated[0] * HALF_CELL_M
            y_m = rotated[1] * HALF_CELL_M
            return _rounded_point(
                (
                    anchor[0] + tangent[0] * x_m + outward[0] * y_m,
                    anchor[1] + tangent[1] * x_m + outward[1] * y_m,
                )
            )

        body_points = tuple(local_to_world(offset) for offset in geometry.side_external_footprint_half_cells)
        clearance_points = tuple(
            local_to_world(offset)
            for offset in geometry.side_clearance_half_cells + geometry.exhaust_clearance_half_cells
        )
        deck_input = next(item for item in self.hull.normalized_blueprint.decks if item.id == deck.id)
        for point in body_points:
            if any(point_inside_polygon(point, region.vertices_m) for region in deck_input.regions):
                raise ContractError("outfit.side_body_inside_hull", path, "侧挂本体进入船壳内部")
        for point in clearance_points:
            if any(point_inside_or_on_polygon(point, region.vertices_m) for region in deck_input.regions):
                raise ContractError("outfit.side_clearance_hull_conflict", path, "侧挂净空或尾焰穿过船壳")

        local_direction = _rotate_clockwise_vector((0.0, 1.0), placement.rotation_deg)
        direction = (
            tangent[0] * local_direction[0] + outward[0] * local_direction[1],
            tangent[1] * local_direction[0] + outward[1] * local_direction[1],
        )
        actuator = self._actuator(instance_input.id, prototype, anchor, direction)
        side_slots = tuple(
            (deck.id, placement.region_id, placement.edge_index, slot.slot_index)
            for slot in selected
        )
        return CompiledModuleInstance(
            instance_input.id,
            prototype,
            "side",
            deck.level,
            _rounded_point(anchor),
            placement.rotation_deg,
            None,
            (),
            (),
            side_slots,
            tuple(sorted({_spatial_key(deck.level, point) for point in body_points})),
            tuple(sorted({_spatial_key(deck.level, point) for point in clearance_points})),
            body_points,
            _module_inertia(prototype.mass_kg, body_points),
            actuator,
        )

    def _compile_hosted(
        self,
        instance_input: OutfitModuleInstanceInput,
        prototype: ModulePrototype,
        placement: HostedModulePlacement,
    ) -> CompiledModuleInstance:
        path = f"$.modules[{instance_input.id}]"
        slot = prototype.installation.host_slot
        if slot is None:
            raise ContractError("outfit.placement_kind_mismatch", path, "非嵌入模块不得使用 hosted 放置")
        if placement.host_instance_id == instance_input.id:
            raise ContractError("outfit.host_cycle", path, "模块不能嵌入自身")
        if placement.host_instance_id not in self.instance_input_by_id:
            raise ContractError("outfit.host_missing", path, "找不到宿主实例")
        host = self._compile_instance(placement.host_instance_id)
        if slot not in host.prototype.installation.provided_slots:
            raise ContractError("outfit.host_slot_missing", path, f"宿主不提供槽位 {slot}")
        slot_key = placement.host_instance_id, slot
        existing = self.hosted_slot_users.get(slot_key)
        if existing is not None:
            raise ContractError("outfit.host_slot_occupied", path, f"槽位已被 {existing} 使用")
        self.hosted_slot_users[slot_key] = instance_input.id
        points = host.mass_points_m
        return CompiledModuleInstance(
            instance_input.id,
            prototype,
            "hosted",
            host.base_deck_level,
            host.anchor_m,
            host.rotation_deg,
            placement.host_instance_id,
            (),
            (),
            (),
            (),
            (),
            points,
            _module_inertia(prototype.mass_kg, points),
            None,
        )

    @staticmethod
    def _actuator(
        instance_id: str,
        prototype: ModulePrototype,
        anchor_m: MassPoint,
        direction: tuple[float, float],
    ) -> ActuatorInstance | None:
        if prototype.category not in {"main_engine", "maneuver_thruster"}:
            return None
        capability = prototype.capability.to_dict()
        thrust = float(capability["thrust_n"])
        torque = anchor_m[0] * thrust * direction[1] - anchor_m[1] * thrust * direction[0]
        return ActuatorInstance(
            instance_id,
            prototype.category,
            thrust,
            _rounded_point(anchor_m),
            (round(direction[0], 10), round(direction[1], 10)),
            torque,
            float(capability["fuel_units_per_s"]),
            float(capability["response_time_s"]),
        )

    @staticmethod
    def _validate_cic(instances: tuple[CompiledModuleInstance, ...]) -> None:
        cics = [instance for instance in instances if instance.prototype.category == "cic"]
        if len(cics) != 1:
            raise ContractError("outfit.cic_count", "$", "舾装方案必须恰好安装一个 CIC")
        cic = cics[0]
        if (
            cic.placement_kind != "grid"
            or cic.base_deck_level != 0
            or cic.anchor_m != (0.0, 0.0)
            or cic.rotation_deg != 0
        ):
            raise ContractError("outfit.cic_origin", f"$.modules[{cic.id}]", "CIC 必须位于基底层原点且旋转为 0°")

    @staticmethod
    def _validate_occupancy(instances: tuple[CompiledModuleInstance, ...]) -> None:
        internal_users: dict[GridOccupancy, str] = {}
        top_users: dict[GridOccupancy, str] = {}
        side_users: dict[SideSlotKey, str] = {}
        body_users: dict[SpatialKey, str] = {}
        for instance in instances:
            for values, users, code in (
                (instance.internal_cells, internal_users, "outfit.internal_overlap"),
                (instance.top_cells, top_users, "outfit.top_overlap"),
                (instance.side_slots, side_users, "outfit.side_overlap"),
            ):
                for value in values:
                    other = users.get(value)
                    if other is not None:
                        raise ContractError(code, f"$.modules[{instance.id}]", f"与 {other} 重叠：{value}")
                    users[value] = instance.id
            for key in instance.body_spatial_keys:
                other = body_users.get(key)
                if other is not None:
                    raise ContractError(
                        "outfit.external_body_overlap",
                        f"$.modules[{instance.id}]",
                        f"与 {other} 的本体空间重叠：{key}",
                    )
                body_users[key] = instance.id
        for instance in instances:
            for key in instance.clearance_spatial_keys:
                other = body_users.get(key)
                if other is not None:
                    raise ContractError(
                        "outfit.clearance_conflict",
                        f"$.modules[{instance.id}]",
                        f"净空与 {other} 的本体冲突：{key}",
                    )


def compile_outfit(
    plan: OutfitPlanInput,
    hull: CompiledHull,
    module_catalog: ModulePrototypeCatalog,
    coating_catalog: HullCoatingCatalog,
) -> CompiledOutfit:
    return _OutfitCompiler(plan, hull, module_catalog, coating_catalog).compile()


def build_derived_ship_snapshot(
    hull: CompiledHull, outfit: CompiledOutfit
) -> DerivedShipSnapshot:
    """核对来源后生成设计态派生快照。"""

    if outfit.hull_source_sha256 != hull.source_sha256:
        raise ContractError(
            "snapshot.hull_source_mismatch",
            "$",
            "舾装编译结果与传入船壳的规范源指纹不一致",
        )
    plan_reference = outfit.normalized_plan.hull_blueprint
    hull_reference = ResourceReference(
        hull.normalized_blueprint.id, hull.normalized_blueprint.version
    )
    if plan_reference != hull_reference:
        raise ContractError(
            "snapshot.hull_reference_mismatch",
            "$",
            "舾装方案与传入船壳的精确引用不一致",
        )
    return DerivedShipSnapshot(hull=hull, outfit=outfit)
