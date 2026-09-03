"""由 RuntimeShipParameters 驱动的正式二维战术机动、气动、RCS 与换层适配器。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import atan2, cos, degrees, hypot, pi, radians, sin, sqrt
from typing import Any

from 高天荒野舰艇RCS缓存 import interpolate_hull_rcs
from 高天荒野舰艇气动缓存 import (
    AerodynamicCoefficients,
    DragBreakdown,
    calculate_drag,
    velocity_body_to_beta_deg,
)
from 高天荒野舰艇数据契约 import ContractError, ShipInstanceSnapshotInput
from 高天荒野舰艇实际推进合同 import (
    ACTUAL_INTEGRATION_POLICY_ID, ActualActuationRequest, finite_number, fixed_step_index,
)
from 高天荒野舰艇无界面舾装编译器 import (
    ActuatorAggregation,
    DerivedShipSnapshot,
    MainDirectionCapability,
    TurningDirectionCapability,
)
from 高天荒野舰艇运行时参数编译器 import RuntimeShipParameters


TACTICAL_DYNAMICS_INTERFACE_ID = "gaotian.tactical-dynamics/runtime-adapter/v1alpha1"
FIXED_STEP_POLICY_ID = "gaotian.tactical-dynamics/semi-implicit-euler-60hz/v1"
PROTOTYPE_ENVIRONMENT_PROFILE_ID = "gtw.tactical-environment.prototype.v1"
PROTOTYPE_TUNING_ID = "gtw.tactical-dynamics.prototype.v1"
EPS = 1.0e-9
LAYER_ORDER = {"rain": 0, "cloud": 1, "upper": 2}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scale: float) -> "Vec2":
        return Vec2(self.x * scale, self.y * scale)

    __rmul__ = __mul__

    def __truediv__(self, scale: float) -> "Vec2":
        return Vec2(self.x / scale, self.y / scale)

    @property
    def length(self) -> float:
        return hypot(self.x, self.y)

    def to_list(self) -> list[float]:
        return [self.x, self.y]


def body_to_world(vector: Vec2, heading_rad: float) -> Vec2:
    c = cos(heading_rad)
    s = sin(heading_rad)
    return Vec2(c * vector.x - s * vector.y, s * vector.x + c * vector.y)


def world_to_body(vector: Vec2, heading_rad: float) -> Vec2:
    c = cos(heading_rad)
    s = sin(heading_rad)
    return Vec2(c * vector.x + s * vector.y, -s * vector.x + c * vector.y)


def wrap_angle(angle_rad: float) -> float:
    return (angle_rad + pi) % (2.0 * pi) - pi


@dataclass(frozen=True)
class HeightLayerEnvironment:
    layer: str
    density_kg_m3: float
    dynamic_viscosity_pa_s: float
    sound_speed_mps: float
    wind_world_mps: Vec2 = Vec2()

    def to_dict(self) -> dict[str, Any]:
        return {
            "density_kg_m3": self.density_kg_m3,
            "dynamic_viscosity_pa_s": self.dynamic_viscosity_pa_s,
            "layer": self.layer,
            "sound_speed_mps": self.sound_speed_mps,
            "wind_world_mps": self.wind_world_mps.to_list(),
        }


@dataclass(frozen=True)
class TacticalEnvironmentProfile:
    id: str
    balance_status: str
    layers: tuple[HeightLayerEnvironment, ...]
    upper_cloud_transition_s: float
    cloud_rain_transition_s: float

    def __post_init__(self) -> None:
        by_layer = {item.layer: item for item in self.layers}
        if set(by_layer) != set(LAYER_ORDER) or len(by_layer) != len(self.layers):
            raise ValueError("环境配置必须恰好包含 upper、cloud、rain 各一次")
        if self.upper_cloud_transition_s <= 0.0 or self.cloud_rain_transition_s <= 0.0:
            raise ValueError("相邻高度层换层时间必须为正数")
        for item in self.layers:
            if (
                item.density_kg_m3 <= 0.0
                or item.dynamic_viscosity_pa_s <= 0.0
                or item.sound_speed_mps <= 0.0
            ):
                raise ValueError("高度层密度、黏度与音速必须为正数")

    def layer(self, layer: str) -> HeightLayerEnvironment:
        try:
            return next(item for item in self.layers if item.layer == layer)
        except StopIteration as error:
            raise ValueError(f"未知高度层：{layer}") from error

    def transition_duration_s(self, source: str, target: str) -> float:
        if source not in LAYER_ORDER or target not in LAYER_ORDER or source == target:
            raise ValueError("换层必须指定两个不同的合法高度层")
        lower = min(LAYER_ORDER[source], LAYER_ORDER[target])
        upper = max(LAYER_ORDER[source], LAYER_ORDER[target])
        duration = 0.0
        for boundary in range(lower, upper):
            duration += (
                self.cloud_rain_transition_s
                if boundary == 0
                else self.upper_cloud_transition_s
            )
        return duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "balance_status": self.balance_status,
            "cloud_rain_transition_s": self.cloud_rain_transition_s,
            "id": self.id,
            "layers": [item.to_dict() for item in self.layers],
            "upper_cloud_transition_s": self.upper_cloud_transition_s,
        }


PROTOTYPE_TACTICAL_ENVIRONMENT = TacticalEnvironmentProfile(
    id=PROTOTYPE_ENVIRONMENT_PROFILE_ID,
    balance_status="prototype_unbalanced",
    layers=(
        HeightLayerEnvironment("cloud", 1.30, 1.8e-5, 340.0),
        HeightLayerEnvironment("rain", 1.30, 1.8e-5, 340.0),
        HeightLayerEnvironment("upper", 1.00, 1.8e-5, 340.0),
    ),
    upper_cloud_transition_s=30.0,
    cloud_rain_transition_s=30.0,
)


@dataclass(frozen=True)
class TacticalDynamicsTuning:
    id: str
    balance_status: str
    fixed_step_s: float
    turn_scale: float
    wheel_target_max_radps: float
    wheel_response_s: float
    brake_response_s: float
    gravity_mps2: float
    overg_reference_ratio: float
    overg_reference_time_s: float
    aerodynamic_coefficients: AerodynamicCoefficients
    wave_drag_start_mach: float
    wave_drag_full_mach: float
    wave_drag_full_coefficient: float

    def __post_init__(self) -> None:
        positive = (
            self.fixed_step_s,
            self.turn_scale,
            self.wheel_target_max_radps,
            self.wheel_response_s,
            self.brake_response_s,
            self.gravity_mps2,
            self.overg_reference_time_s,
            self.wave_drag_full_mach,
        )
        if any(value <= 0.0 for value in positive):
            raise ValueError("战术机动正值参数必须大于零")
        if self.overg_reference_ratio <= 1.0:
            raise ValueError("OverG 参考载荷比必须大于一")
        if self.wave_drag_start_mach < 0.0 or self.wave_drag_full_mach <= self.wave_drag_start_mach:
            raise ValueError("波阻马赫数区间非法")
        if self.wave_drag_full_coefficient < 0.0:
            raise ValueError("波阻系数不得为负数")

    def wave_coefficient(self, mach: float) -> float:
        if mach <= self.wave_drag_start_mach:
            return 0.0
        if mach >= self.wave_drag_full_mach:
            return self.wave_drag_full_coefficient
        amount = (mach - self.wave_drag_start_mach) / (
            self.wave_drag_full_mach - self.wave_drag_start_mach
        )
        return amount * self.wave_drag_full_coefficient

    def to_dict(self) -> dict[str, Any]:
        coefficients = self.aerodynamic_coefficients
        return {
            "aerodynamic_coefficients": {
                "front_bluntness_coefficient": coefficients.front_bluntness_coefficient,
                "projected_area_coefficient": coefficients.projected_area_coefficient,
                "rear_bluntness_coefficient": coefficients.rear_bluntness_coefficient,
                "reynolds_number_minimum": coefficients.reynolds_number_minimum,
                "roughness_coefficient": coefficients.roughness_coefficient,
            },
            "balance_status": self.balance_status,
            "brake_response_s": self.brake_response_s,
            "fixed_step_s": self.fixed_step_s,
            "gravity_mps2": self.gravity_mps2,
            "id": self.id,
            "overg_reference_ratio": self.overg_reference_ratio,
            "overg_reference_time_s": self.overg_reference_time_s,
            "turn_scale": self.turn_scale,
            "wave_drag_full_coefficient": self.wave_drag_full_coefficient,
            "wave_drag_full_mach": self.wave_drag_full_mach,
            "wave_drag_start_mach": self.wave_drag_start_mach,
            "wheel_response_s": self.wheel_response_s,
            "wheel_target_max_radps": self.wheel_target_max_radps,
        }


PROTOTYPE_TACTICAL_DYNAMICS_TUNING = TacticalDynamicsTuning(
    id=PROTOTYPE_TUNING_ID,
    balance_status="prototype_unbalanced",
    fixed_step_s=1.0 / 60.0,
    turn_scale=1.0,
    wheel_target_max_radps=radians(30.0),
    wheel_response_s=0.60,
    brake_response_s=1.00,
    gravity_mps2=9.80665,
    overg_reference_ratio=2.0,
    overg_reference_time_s=10.0,
    aerodynamic_coefficients=AerodynamicCoefficients(
        projected_area_coefficient=0.20,
        front_bluntness_coefficient=0.40,
        rear_bluntness_coefficient=0.30,
        roughness_coefficient=1.10,
        reynolds_number_minimum=100_000.0,
    ),
    wave_drag_start_mach=0.80,
    wave_drag_full_mach=1.20,
    wave_drag_full_coefficient=0.50,
)


@dataclass(frozen=True)
class LayerTransitionState:
    source_layer: str
    target_layer: str
    elapsed_s: float
    duration_s: float

    @property
    def progress(self) -> float:
        return clamp(self.elapsed_s / self.duration_s, 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_s": self.duration_s,
            "elapsed_s": self.elapsed_s,
            "progress": self.progress,
            "source_layer": self.source_layer,
            "target_layer": self.target_layer,
        }


@dataclass(frozen=True)
class TacticalMotionState:
    position_world_m: Vec2
    velocity_world_mps: Vec2
    heading_rad: float
    yaw_rate_radps: float
    height_layer: str
    layer_transition: LayerTransitionState | None
    hull_integrity_fraction: float
    fuel_units: float
    fixed_step_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed_step_index": self.fixed_step_index,
            "fuel_units": self.fuel_units,
            "heading_rad": self.heading_rad,
            "height_layer": self.height_layer,
            "hull_integrity_fraction": self.hull_integrity_fraction,
            "layer_transition": None
            if self.layer_transition is None
            else self.layer_transition.to_dict(),
            "position_world_m": self.position_world_m.to_list(),
            "velocity_world_mps": self.velocity_world_mps.to_list(),
            "yaw_rate_radps": self.yaw_rate_radps,
        }


@dataclass(frozen=True)
class TacticalControlInput:
    move_body: Vec2 = Vec2()
    wheel: float = 0.0
    brake: bool = False
    overg: bool = False


@dataclass(frozen=True)
class TacticalShipStaticModel:
    derived_snapshot_sha256: str
    structure_points_body_m: tuple[Vec2, ...]
    environment: TacticalEnvironmentProfile
    tuning: TacticalDynamicsTuning
    aerodynamic_cache: Any
    aerodynamic_cache_sha256: str
    hull_rcs_cache: Any
    hull_rcs_cache_sha256: str
    coating_rcs_multiplier: float
    known_external_rcs_m2: float
    unresolved_external_rcs_instances: tuple[str, ...]


@dataclass(frozen=True)
class TacticalShipModel:
    runtime: RuntimeShipParameters
    derived_snapshot_sha256: str
    runtime_parameters_sha256: str
    structure_points_body_m: tuple[Vec2, ...]
    actuator_aggregation: ActuatorAggregation
    environment: TacticalEnvironmentProfile
    tuning: TacticalDynamicsTuning
    aerodynamic_cache: Any
    hull_rcs_cache: Any
    coating_rcs_multiplier: float
    known_external_rcs_m2: float
    unresolved_external_rcs_instances: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_policy": self.actuator_aggregation.policy_id,
            "derived_snapshot_sha256": self.derived_snapshot_sha256,
            "environment_profile": self.environment.to_dict(),
            "fixed_step_policy": FIXED_STEP_POLICY_ID,
            "interface": TACTICAL_DYNAMICS_INTERFACE_ID,
            "rcs": {
                "complete": not self.unresolved_external_rcs_instances,
                "known_external_rcs_m2": self.known_external_rcs_m2,
                "unresolved_external_rcs_instances": list(
                    self.unresolved_external_rcs_instances
                ),
            },
            "runtime_parameters_sha256": self.runtime_parameters_sha256,
            "structure_point_count": len(self.structure_points_body_m),
            "tuning": self.tuning.to_dict(),
        }


def build_tactical_ship_static_model(
    snapshot: DerivedShipSnapshot,
    *,
    environment: TacticalEnvironmentProfile = PROTOTYPE_TACTICAL_ENVIRONMENT,
    tuning: TacticalDynamicsTuning = PROTOTYPE_TACTICAL_DYNAMICS_TUNING,
) -> TacticalShipStaticModel:
    points = tuple(
        sorted(
            {
                Vec2(float(x), float(y))
                for deck in snapshot.hull.normalized_blueprint.decks
                for region in deck.regions
                for x, y in region.vertices_m
            },
            key=lambda point: (point.x, point.y),
        )
    )
    if not points:
        raise ContractError("tactical.structure_points_missing", "$", "船壳没有结构代表点")
    return TacticalShipStaticModel(
        snapshot.source_sha256,
        points,
        environment,
        tuning,
        snapshot.hull.aerodynamic_cache,
        snapshot.hull.aerodynamic_cache.source_sha256,
        snapshot.hull.hull_rcs_cache,
        snapshot.hull.hull_rcs_cache.source_sha256,
        snapshot.outfit.coating_rcs_multiplier,
        snapshot.outfit.known_external_rcs_m2,
        snapshot.outfit.unresolved_external_rcs_instances,
    )


def bind_tactical_ship_model(
    runtime: RuntimeShipParameters,
    static: TacticalShipStaticModel,
) -> TacticalShipModel:
    if runtime.derived_snapshot_sha256 != static.derived_snapshot_sha256:
        raise ContractError(
            "tactical.derived_snapshot_mismatch",
            "$.derived_snapshot_sha256",
            "运行时参数与设计态派生快照不匹配",
        )
    if (
        runtime.aerodynamic_cache_sha256
        != static.aerodynamic_cache_sha256
    ):
        raise ContractError(
            "tactical.aerodynamic_cache_mismatch",
            "$.aerodynamic_cache",
            "运行时引用的气动缓存已经变化",
        )
    if runtime.hull_rcs_cache_sha256 != static.hull_rcs_cache_sha256:
        raise ContractError(
            "tactical.rcs_cache_mismatch",
            "$.hull_rcs_cache",
            "运行时引用的 RCS 缓存已经变化",
        )
    return TacticalShipModel(
        runtime=runtime,
        derived_snapshot_sha256=static.derived_snapshot_sha256,
        runtime_parameters_sha256=runtime.source_sha256,
        structure_points_body_m=static.structure_points_body_m,
        actuator_aggregation=runtime.actuator_aggregation,
        environment=static.environment,
        tuning=static.tuning,
        aerodynamic_cache=static.aerodynamic_cache,
        hull_rcs_cache=static.hull_rcs_cache,
        coating_rcs_multiplier=static.coating_rcs_multiplier,
        known_external_rcs_m2=static.known_external_rcs_m2,
        unresolved_external_rcs_instances=static.unresolved_external_rcs_instances,
    )


def build_tactical_ship_model(
    runtime: RuntimeShipParameters,
    snapshot: DerivedShipSnapshot,
    *,
    environment: TacticalEnvironmentProfile = PROTOTYPE_TACTICAL_ENVIRONMENT,
    tuning: TacticalDynamicsTuning = PROTOTYPE_TACTICAL_DYNAMICS_TUNING,
) -> TacticalShipModel:
    return bind_tactical_ship_model(
        runtime,
        build_tactical_ship_static_model(
            snapshot,
            environment=environment,
            tuning=tuning,
        ),
    )


def initialize_tactical_motion_state(model: TacticalShipModel) -> TacticalMotionState:
    operational = model.runtime.instance_snapshot.operational_state
    return TacticalMotionState(
        position_world_m=Vec2(),
        velocity_world_mps=Vec2(),
        heading_rad=0.0,
        yaw_rate_radps=0.0,
        height_layer=model.runtime.height_layer,
        layer_transition=None,
        hull_integrity_fraction=model.runtime.current_hull_integrity_fraction,
        fuel_units=operational.fuel_units,
    )


def request_layer_transition(
    model: TacticalShipModel,
    state: TacticalMotionState,
    target_layer: str,
) -> TacticalMotionState:
    if target_layer not in LAYER_ORDER:
        raise ContractError("tactical.height_layer", "$.target_layer", target_layer)
    if state.layer_transition is not None:
        raise ContractError(
            "tactical.layer_transition_active",
            "$.layer_transition",
            "当前换层完成前不能再次下达换层命令",
        )
    if target_layer == state.height_layer:
        return state
    moving_up = LAYER_ORDER[target_layer] > LAYER_ORDER[state.height_layer]
    if moving_up and model.runtime.current_lift_margin_n <= EPS:
        raise ContractError(
            "tactical.insufficient_lift_for_ascent",
            "$.target_layer",
            "剩余升力必须大于零才能开始上升换层",
        )
    duration = model.environment.transition_duration_s(state.height_layer, target_layer)
    return replace(
        state,
        layer_transition=LayerTransitionState(
            state.height_layer,
            target_layer,
            0.0,
            duration,
        ),
    )


@dataclass(frozen=True)
class TacticalDragResult:
    force_world_n: Vec2
    relative_velocity_world_mps: Vec2
    breakdown: DragBreakdown


def calculate_tactical_drag(
    model: TacticalShipModel, state: TacticalMotionState
) -> TacticalDragResult:
    layer = model.environment.layer(state.height_layer)
    relative_world = state.velocity_world_mps - layer.wind_world_mps
    speed = relative_world.length
    relative_body = world_to_body(relative_world, state.heading_rad)
    beta = velocity_body_to_beta_deg(relative_body.x, relative_body.y)
    breakdown = calculate_drag(
        model.aerodynamic_cache,
        beta,
        speed,
        layer.density_kg_m3,
        layer.dynamic_viscosity_pa_s,
        layer.sound_speed_mps,
        model.tuning.aerodynamic_coefficients,
        model.tuning.wave_coefficient,
    )
    force = Vec2()
    if speed > EPS:
        force = relative_world * (-breakdown.drag_force_n / speed)
    return TacticalDragResult(force, relative_world, breakdown)


@dataclass(frozen=True)
class TacticalRCSResult:
    bearing_body_deg: float
    coated_hull_rcs_m2: float
    known_external_rcs_m2: float
    known_total_rcs_m2: float
    complete: bool
    unresolved_external_rcs_instances: tuple[str, ...]


def query_tactical_rcs_to_observer(
    model: TacticalShipModel,
    state: TacticalMotionState,
    observer_world_position_m: Vec2,
) -> TacticalRCSResult:
    line_world = observer_world_position_m - state.position_world_m
    if line_world.length <= EPS:
        raise ValueError("观察者位置不能与舰艇位置重合")
    line_body = world_to_body(line_world, state.heading_rad)
    bearing = degrees(atan2(line_body.x, line_body.y)) % 360.0
    hull = (
        interpolate_hull_rcs(model.hull_rcs_cache, bearing).total_m2
        * model.coating_rcs_multiplier
    )
    known_total = hull + model.known_external_rcs_m2
    return TacticalRCSResult(
        bearing,
        hull,
        model.known_external_rcs_m2,
        known_total,
        not model.unresolved_external_rcs_instances,
        model.unresolved_external_rcs_instances,
    )


@dataclass(frozen=True)
class AllocatedActuation:
    main_force_body_n: Vec2
    turning_force_body_n: Vec2
    main_torque_n_m: float
    turning_torque_n_m: float
    target_yaw_rate_radps: float
    fuel_units_per_s: float

    @property
    def active_force_body_n(self) -> Vec2:
        return self.main_force_body_n + self.turning_force_body_n

    @property
    def active_torque_n_m(self) -> float:
        return self.main_torque_n_m + self.turning_torque_n_m


def _scaled_main(
    capability: MainDirectionCapability, scale: float
) -> tuple[Vec2, float, float]:
    return (
        Vec2(*capability.net_force_body_n) * scale,
        capability.residual_torque_about_cic_n_m * scale,
        capability.fuel_units_per_s * scale,
    )


def _scaled_turning(
    capability: TurningDirectionCapability, scale: float
) -> tuple[Vec2, float, float]:
    return (
        Vec2(*capability.net_force_body_n) * scale,
        capability.signed_torque_about_cic_n_m * scale,
        capability.fuel_units_per_s * scale,
    )


def _brake_axis_command(requested_force_n: float, positive_capacity: float, negative_capacity: float) -> float:
    if requested_force_n >= 0.0:
        return 0.0 if positive_capacity <= EPS else clamp(requested_force_n / positive_capacity, 0.0, 1.0)
    return 0.0 if negative_capacity <= EPS else -clamp(-requested_force_n / negative_capacity, 0.0, 1.0)


def allocate_tactical_actuation(
    model: TacticalShipModel,
    state: TacticalMotionState,
    controls: TacticalControlInput,
) -> AllocatedActuation:
    if state.fuel_units <= EPS or not model.runtime.fuel_available:
        return AllocatedActuation(Vec2(), Vec2(), 0.0, 0.0, 0.0, 0.0)
    aggregation = model.actuator_aggregation
    forward = aggregation.main("forward")
    reverse = aggregation.main("reverse")
    right = aggregation.main("right")
    left = aggregation.main("left")
    if controls.brake:
        velocity_body = world_to_body(state.velocity_world_mps, state.heading_rad)
        requested = velocity_body * (-model.runtime.current_mass_kg / model.tuning.brake_response_s)
        command_x = _brake_axis_command(
            requested.x, right.total_used_thrust_n, left.total_used_thrust_n
        )
        command_y = _brake_axis_command(
            requested.y, forward.total_used_thrust_n, reverse.total_used_thrust_n
        )
    else:
        command_x = clamp(controls.move_body.x, -1.0, 1.0)
        command_y = clamp(controls.move_body.y, -1.0, 1.0)

    main_force = Vec2()
    main_torque = 0.0
    fuel_rate = 0.0
    for command, positive, negative in (
        (command_x, right, left),
        (command_y, forward, reverse),
    ):
        capability = positive if command >= 0.0 else negative
        force, torque, fuel = _scaled_main(capability, abs(command))
        main_force += force
        main_torque += torque
        fuel_rate += fuel

    wheel = clamp(controls.wheel, -1.0, 1.0)
    target_rate = wheel * model.tuning.wheel_target_max_radps
    if not controls.overg:
        target_rate = clamp(
            target_rate,
            -model.runtime.safe_yaw_rate_rad_s,
            model.runtime.safe_yaw_rate_rad_s,
        )
    requested_alpha = (target_rate - state.yaw_rate_radps) / model.tuning.wheel_response_s
    requested_total_torque = (
        requested_alpha * model.runtime.current_inertia_kg_m2 / model.tuning.turn_scale
    )
    requested_turning_torque = requested_total_torque - main_torque
    turning = (
        aggregation.turning("counterclockwise")
        if requested_turning_torque >= 0.0
        else aggregation.turning("clockwise")
    )
    turning_scale = (
        0.0
        if turning.torque_capacity_n_m <= EPS
        else clamp(
            abs(requested_turning_torque) / turning.torque_capacity_n_m,
            0.0,
            1.0,
        )
    )
    turning_force, turning_torque, turning_fuel = _scaled_turning(
        turning, turning_scale
    )
    return AllocatedActuation(
        main_force,
        turning_force,
        main_torque,
        turning_torque,
        target_rate,
        fuel_rate + turning_fuel,
    )


@dataclass(frozen=True)
class LoadMetrics:
    structure_ratio: float
    crew_g: float
    acceleration_body_mps2: Vec2
    angular_acceleration_radps2: float


def structure_ratio(
    model: TacticalShipModel,
    acceleration_body_mps2: Vec2,
    angular_acceleration_radps2: float,
    yaw_rate_radps: float,
) -> float:
    result = 0.0
    for point in model.structure_points_body_m:
        local_x = (
            acceleration_body_mps2.x
            - angular_acceleration_radps2 * point.y
            - yaw_rate_radps * yaw_rate_radps * point.x
        )
        local_y = (
            acceleration_body_mps2.y
            + angular_acceleration_radps2 * point.x
            - yaw_rate_radps * yaw_rate_radps * point.y
        )
        ratio = sqrt(
            (local_y / model.runtime.safe_longitudinal_mps2) ** 2
            + (local_x / model.runtime.safe_lateral_mps2) ** 2
        )
        result = max(result, ratio)
    return result


def _load_metrics(
    model: TacticalShipModel,
    state: TacticalMotionState,
    actuation: AllocatedActuation,
    drag_world_n: Vec2,
    scale: float,
    dt: float,
) -> LoadMetrics:
    active_world = body_to_world(actuation.active_force_body_n * scale, state.heading_rad)
    acceleration_world = (active_world + drag_world_n) / model.runtime.current_mass_kg
    acceleration_body = world_to_body(acceleration_world, state.heading_rad)
    angular_acceleration = (
        model.tuning.turn_scale
        * actuation.active_torque_n_m
        * scale
        / model.runtime.current_inertia_kg_m2
    )
    predicted_rate = state.yaw_rate_radps + angular_acceleration * dt
    ratio = structure_ratio(model, acceleration_body, angular_acceleration, predicted_rate)
    horizontal_g = acceleration_world.length / model.tuning.gravity_mps2
    return LoadMetrics(ratio, sqrt(1.0 + horizontal_g * horizontal_g), acceleration_body, angular_acceleration)


def _command_allowed(model: TacticalShipModel, metrics: LoadMetrics, controls: TacticalControlInput) -> bool:
    structure_ok = controls.overg or metrics.structure_ratio <= 1.0 + EPS
    crew_ok = (
        not model.runtime.crew_safety_lock_enabled
        or metrics.crew_g <= 12.0 + EPS
    )
    return structure_ok and crew_ok


def _violation_score(model: TacticalShipModel, metrics: LoadMetrics, controls: TacticalControlInput) -> float:
    structure_score = 0.0 if controls.overg else max(0.0, metrics.structure_ratio - 1.0)
    crew_score = (
        max(0.0, metrics.crew_g / 12.0 - 1.0)
        if model.runtime.crew_safety_lock_enabled
        else 0.0
    )
    return structure_score + crew_score


def _choose_command_scale(
    model: TacticalShipModel,
    state: TacticalMotionState,
    controls: TacticalControlInput,
    actuation: AllocatedActuation,
    drag_world_n: Vec2,
    dt: float,
) -> tuple[float, LoadMetrics]:
    requested_metrics = _load_metrics(
        model,
        state,
        actuation,
        drag_world_n,
        1.0,
        dt,
    )
    if _command_allowed(model, requested_metrics, controls):
        return 1.0, requested_metrics
    samples = [
        (
            index / 64.0,
            _load_metrics(
                model,
                state,
                actuation,
                drag_world_n,
                index / 64.0,
                dt,
            ),
        )
        for index in range(64)
    ]
    samples.append((1.0, requested_metrics))
    allowed = [
        item for item in samples if _command_allowed(model, item[1], controls)
    ]
    if allowed:
        low_scale, low_metrics = allowed[-1]
        if low_scale >= 1.0 - EPS:
            return 1.0, low_metrics
        high_scale = low_scale + 1.0 / 64.0
        for _ in range(50):
            middle = 0.5 * (low_scale + high_scale)
            metrics = _load_metrics(
                model, state, actuation, drag_world_n, middle, dt
            )
            if _command_allowed(model, metrics, controls):
                low_scale, low_metrics = middle, metrics
            else:
                high_scale = middle
        return low_scale, low_metrics
    return min(
        samples,
        key=lambda item: _violation_score(model, item[1], controls),
    )


@dataclass(frozen=True)
class TacticalStepDiagnostics:
    command_scale: float
    target_yaw_rate_radps: float
    structure_ratio: float
    crew_g: float
    hull_integrity_damage: float
    fuel_units_consumed: float
    active_force_body_n: Vec2
    active_torque_n_m: float
    drag_force_world_n: Vec2
    drag_breakdown: DragBreakdown


def _advance_layer_transition(state: TacticalMotionState, dt: float) -> tuple[str, LayerTransitionState | None]:
    transition = state.layer_transition
    if transition is None:
        return state.height_layer, None
    elapsed = min(transition.duration_s, transition.elapsed_s + dt)
    if elapsed >= transition.duration_s - EPS:
        return transition.target_layer, None
    return state.height_layer, replace(transition, elapsed_s=elapsed)


def _integrate_delivered_actuation(
    model: TacticalShipModel,
    state: TacticalMotionState,
    actuation: AllocatedActuation,
    drag: TacticalDragResult,
    scale: float,
    metrics: LoadMetrics,
    step: float,
) -> tuple[TacticalMotionState, TacticalStepDiagnostics]:
    """新旧入口共享既有公式；这里不分配控制，也不决定交付比例。"""
    active_force_body = actuation.active_force_body_n * scale
    active_force_world = body_to_world(active_force_body, state.heading_rad)
    acceleration_world = (
        active_force_world + drag.force_world_n
    ) / model.runtime.current_mass_kg
    active_torque = actuation.active_torque_n_m * scale
    angular_acceleration = (
        model.tuning.turn_scale
        * active_torque
        / model.runtime.current_inertia_kg_m2
    )
    velocity = state.velocity_world_mps + acceleration_world * step
    position = state.position_world_m + velocity * step
    yaw_rate = state.yaw_rate_radps + angular_acceleration * step
    heading = wrap_angle(state.heading_rad + yaw_rate * step)
    overload = max(0.0, metrics.structure_ratio - 1.0)
    hull_damage = (
        step
        / model.tuning.overg_reference_time_s
        * (
            overload
            / (model.tuning.overg_reference_ratio - 1.0)
        )
        ** 2
    )
    fuel_consumed = min(
        state.fuel_units,
        actuation.fuel_units_per_s * scale * step,
    )
    height_layer, transition = _advance_layer_transition(state, step)
    next_state = TacticalMotionState(
        position_world_m=position,
        velocity_world_mps=velocity,
        heading_rad=heading,
        yaw_rate_radps=yaw_rate,
        height_layer=height_layer,
        layer_transition=transition,
        hull_integrity_fraction=max(0.0, state.hull_integrity_fraction - hull_damage),
        fuel_units=max(0.0, state.fuel_units - fuel_consumed),
        fixed_step_index=state.fixed_step_index + 1,
    )
    return next_state, TacticalStepDiagnostics(
        scale,
        actuation.target_yaw_rate_radps,
        metrics.structure_ratio,
        metrics.crew_g,
        hull_damage,
        fuel_consumed,
        active_force_body,
        active_torque,
        drag.force_world_n,
        drag.breakdown,
    )


def integrate_tactical_step(
    model: TacticalShipModel,
    state: TacticalMotionState,
    controls: TacticalControlInput,
    *,
    dt: float | None = None,
) -> tuple[TacticalMotionState, TacticalStepDiagnostics]:
    step = model.tuning.fixed_step_s if dt is None else dt
    if abs(step - model.tuning.fixed_step_s) > 1.0e-12:
        raise ValueError("正式战术求解器只接受配置中声明的固定物理步")
    actuation = allocate_tactical_actuation(model, state, controls)
    drag = calculate_tactical_drag(model, state)
    scale, metrics = _choose_command_scale(
        model, state, controls, actuation, drag.force_world_n, step
    )
    if actuation.fuel_units_per_s > EPS:
        fuel_scale = state.fuel_units / (actuation.fuel_units_per_s * step)
        if fuel_scale < scale:
            scale = max(0.0, fuel_scale)
            metrics = _load_metrics(
                model, state, actuation, drag.force_world_n, scale, step
            )
    return _integrate_delivered_actuation(model, state, actuation, drag, scale, metrics, step)


@dataclass(frozen=True)
class ActualTacticalStepDiagnostics:
    request: ActualActuationRequest
    resulting_fixed_step_index: int
    requested_fuel_units: float
    fuel_delivery_fraction: float
    structure_ratio: float
    crew_g: float
    hull_integrity_damage: float
    fuel_units_consumed: float
    active_force_body_n: Vec2
    active_torque_n_m: float
    drag_force_world_n: Vec2
    drag_breakdown: DragBreakdown

    @property
    def soft_governor_status(self) -> str:
        return "unwired"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["request"] = self.request.to_dict()
        value.update(interface="gaotian.actual-propulsion-step-diagnostics/v1alpha1",
                     policy=ACTUAL_INTEGRATION_POLICY_ID, soft_governor_status="unwired",
                     hard_fault_status="unwired", direction_interlock_status="unwired")
        return value


def integrate_actual_tactical_step(
    model: TacticalShipModel,
    state: TacticalMotionState,
    request: ActualActuationRequest,
    *,
    dt: float | None = None,
) -> tuple[TacticalMotionState, ActualTacticalStepDiagnostics]:
    """只供显式技术夹具：实际力/力矩积分，燃料约束独立，软保护尚未接线。"""
    if not isinstance(request, ActualActuationRequest):
        raise ContractError("actual_propulsion.request_type", "$.request", "必须显式提供实际执行量合同")
    configured_step = finite_number(model.tuning.fixed_step_s, "$.tuning.fixed_step_s", 0)
    step = finite_number(configured_step if dt is None else dt, "$.dt", 0)
    if step <= 0 or abs(step - configured_step) > 1.0e-12:
        raise ContractError("actual_propulsion.fixed_step", "$.dt", "只接受配置中的固定物理步")
    fixed_step_index(state.fixed_step_index)
    if request.source_fixed_step_index != state.fixed_step_index:
        raise ContractError("actual_propulsion.source_step", "$.request", "执行量已过期或超前当前边界")
    runtime = model.runtime
    if request.runtime_parameters_sha256 != runtime.source_sha256 or model.runtime_parameters_sha256 != runtime.source_sha256 or (
        request.derived_snapshot_sha256 != model.derived_snapshot_sha256 or model.derived_snapshot_sha256 != runtime.derived_snapshot_sha256
    ):
        raise ContractError("actual_propulsion.model_source", "$.request", "实际执行量必须绑定当前精确模型")
    for key in ("current_mass_kg", "current_inertia_kg_m2"):
        if finite_number(getattr(runtime, key), f"$.runtime.{key}", 0) <= 0:
            raise ContractError("actual_propulsion.runtime_mass", "$.runtime", "质量和惯量必须大于零")
    for key in ("heading_rad", "yaw_rate_radps", "hull_integrity_fraction", "fuel_units"):
        finite_number(getattr(state, key), f"$.state.{key}")
    for vector in (state.position_world_m, state.velocity_world_mps):
        finite_number(vector.x, "$.state.vector.x")
        finite_number(vector.y, "$.state.vector.y")
    if state.fuel_units < 0 or not 0 <= state.hull_integrity_fraction <= 1:
        raise ContractError("actual_propulsion.state_range", "$.state", "燃料或船壳完整度非法")
    if (state.fuel_units != runtime.instance_snapshot.operational_state.fuel_units
        or state.hull_integrity_fraction != runtime.current_hull_integrity_fraction
        or state.height_layer != runtime.height_layer):
        raise ContractError("actual_propulsion.stale_runtime", "$.state", "燃料、船壳或高度层变化后必须重新编译运行时")
    if not runtime.fuel_available and (request.fuel_units_per_s or request.torque_n_m or any(request.force_body_n)):
        raise ContractError("actual_propulsion.runtime_unavailable", "$.request", "断油运行时不能交付非零推进请求")
    requested_fuel = finite_number(request.fuel_units_per_s * step, "$.requested_fuel_units", 0)
    fraction = min(1.0, state.fuel_units / requested_fuel) if requested_fuel > 0 else 1.0
    actuation = AllocatedActuation(Vec2(*request.force_body_n), Vec2(), request.torque_n_m, 0.0, 0.0, request.fuel_units_per_s)
    drag = calculate_tactical_drag(model, state)
    try:
        metrics = _load_metrics(model, state, actuation, drag.force_world_n, fraction, step)
        result, delivered = _integrate_delivered_actuation(model, state, actuation, drag, fraction, metrics, step)
    except (OverflowError, ZeroDivisionError) as error:
        raise ContractError("actual_propulsion.numeric_range", "$.integration", "输入导致非有限物理结果") from error
    for value in (result.position_world_m.x, result.position_world_m.y,
                  result.velocity_world_mps.x, result.velocity_world_mps.y,
                  result.heading_rad, result.yaw_rate_radps, delivered.structure_ratio,
                  delivered.crew_g, delivered.hull_integrity_damage, delivered.fuel_units_consumed):
        finite_number(value, "$.integration.result")
    return result, ActualTacticalStepDiagnostics(
        request, result.fixed_step_index, requested_fuel, fraction, delivered.structure_ratio,
        delivered.crew_g, delivered.hull_integrity_damage, delivered.fuel_units_consumed,
        delivered.active_force_body_n, delivered.active_torque_n_m, delivered.drag_force_world_n,
        delivered.drag_breakdown,
    )


def commit_tactical_state_to_instance(
    model: TacticalShipModel,
    state: TacticalMotionState,
) -> ShipInstanceSnapshotInput:
    operational = replace(
        model.runtime.instance_snapshot.operational_state,
        height_layer=state.height_layer,
        fuel_units=state.fuel_units,
    )
    return replace(
        model.runtime.instance_snapshot,
        current_hull_integrity_fraction=state.hull_integrity_fraction,
        operational_state=operational,
    )
