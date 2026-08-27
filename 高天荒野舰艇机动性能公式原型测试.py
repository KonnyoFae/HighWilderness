"""《高天荒野》舰艇固定步二维机动闭环第一版原型。

本脚本验证舵盘目标角速度、物理反推制动、半隐式欧拉积分、结构限制、
有人舰 12G 安全锁与 OverG 损伤。气动部分使用可替换的方向性阻力面积
夹具；正式游戏应接入船壳蓝图保存的三百六十度气动缓存。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, cos, degrees, hypot, pi, radians, sin, sqrt


EPS = 1.0e-9


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


def body_to_world(vector: Vec2, heading_rad: float) -> Vec2:
    """标准二维逆时针旋转；heading=0 时舰艏 +Y_body 对齐 +Y_world。"""

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
class DirectionalDragFixture:
    """机动原型的气动适配器，不替代正式 f_D 方向表。"""

    forward_area_m2: float
    backward_area_m2: float
    lateral_area_m2: float
    rho_upper: float = 1.0
    rho_cloud: float = 1.3
    rho_rain: float = 1.3
    wind_world_mps: Vec2 = Vec2()

    def density(self, layer: str) -> float:
        return {
            "upper": self.rho_upper,
            "cloud": self.rho_cloud,
            "rain": self.rho_rain,
        }[layer]

    def equivalent_area(self, velocity_body: Vec2) -> float:
        speed = velocity_body.length
        if speed <= EPS:
            return self.forward_area_m2
        forward_cos = velocity_body.y / speed
        lateral_sin = abs(velocity_body.x / speed)
        longitudinal_area = (
            self.forward_area_m2 if forward_cos >= 0.0 else self.backward_area_m2
        )
        return sqrt(
            (longitudinal_area * abs(forward_cos)) ** 2
            + (self.lateral_area_m2 * lateral_sin) ** 2
        )

    def force_world(self, state: "MotionState") -> Vec2:
        relative_world = state.velocity_world_mps - self.wind_world_mps
        speed = relative_world.length
        if speed <= EPS:
            return Vec2()
        relative_body = world_to_body(relative_world, state.heading_rad)
        area = self.equivalent_area(relative_body)
        magnitude = 0.5 * self.density(state.height_layer) * speed * speed * area
        return relative_world * (-magnitude / speed)


@dataclass(frozen=True)
class ActuatorLimits:
    force_right_n: float
    force_left_n: float
    force_forward_n: float
    force_reverse_n: float
    torque_ccw_nm: float
    torque_cw_nm: float


@dataclass(frozen=True)
class ShipParameters:
    mass_kg: float
    inertia_kg_m2: float
    safe_longitudinal_mps2: float
    safe_lateral_mps2: float
    structure_points_body_m: tuple[Vec2, ...]
    actuators: ActuatorLimits
    drag: DirectionalDragFixture
    turn_scale: float = 1.0
    wheel_target_max_radps: float = radians(30.0)
    wheel_response_s: float = 0.60
    brake_response_s: float = 1.00
    gravity_mps2: float = 9.80665
    hull_hp_max: float = 100_000.0
    overg_reference_ratio: float = 2.0
    overg_reference_time_s: float = 10.0


@dataclass(frozen=True)
class MotionState:
    position_world_m: Vec2 = Vec2()
    velocity_world_mps: Vec2 = Vec2()
    heading_rad: float = 0.0
    yaw_rate_radps: float = 0.0
    height_layer: str = "upper"
    hull_hp: float = 100_000.0


@dataclass(frozen=True)
class ControlInput:
    move_body: Vec2 = Vec2()
    wheel: float = 0.0
    brake: bool = False
    overg: bool = False
    crew_present: bool = True


@dataclass(frozen=True)
class AllocatedActuation:
    """刚体求解器与发动机分配层之间的稳定接口。"""

    main_force_body_n: Vec2
    turning_force_body_n: Vec2
    main_torque_nm: float
    turning_torque_nm: float
    target_yaw_rate_radps: float

    @property
    def active_force_body_n(self) -> Vec2:
        return self.main_force_body_n + self.turning_force_body_n

    @property
    def active_torque_nm(self) -> float:
        return self.main_torque_nm + self.turning_torque_nm


@dataclass(frozen=True)
class LoadMetrics:
    structure_ratio: float
    crew_g: float
    acceleration_body_mps2: Vec2
    angular_acceleration_radps2: float


@dataclass(frozen=True)
class StepDiagnostics:
    command_scale: float
    target_yaw_rate_radps: float
    structure_ratio: float
    crew_g: float
    hull_damage: float
    active_force_body_n: Vec2
    active_torque_nm: float
    drag_force_world_n: Vec2


def signed_axis_force(command: float, positive_n: float, negative_n: float) -> float:
    command = clamp(command, -1.0, 1.0)
    return command * (positive_n if command >= 0.0 else negative_n)


def structure_ratio(
    ship: ShipParameters,
    acceleration_body_mps2: Vec2,
    angular_acceleration_radps2: float,
    yaw_rate_radps: float,
) -> float:
    result = 0.0
    for point in ship.structure_points_body_m:
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
            (local_y / ship.safe_longitudinal_mps2) ** 2
            + (local_x / ship.safe_lateral_mps2) ** 2
        )
        result = max(result, ratio)
    return result


def safe_steady_yaw_rate(ship: ShipParameters) -> float:
    """求 R_struct(a=0,alpha=0,omega)=1 的正角速度。"""

    if not ship.structure_points_body_m:
        return float("inf")
    low = 0.0
    high = 1.0
    while structure_ratio(ship, Vec2(), 0.0, high) < 1.0 and high < 64.0:
        high *= 2.0
    if high >= 64.0 and structure_ratio(ship, Vec2(), 0.0, high) < 1.0:
        return float("inf")
    for _ in range(60):
        middle = 0.5 * (low + high)
        if structure_ratio(ship, Vec2(), 0.0, middle) <= 1.0:
            low = middle
        else:
            high = middle
    return low


def allocate_controls(
    ship: ShipParameters,
    state: MotionState,
    controls: ControlInput,
) -> AllocatedActuation:
    limits = ship.actuators

    if controls.brake:
        velocity_body = world_to_body(state.velocity_world_mps, state.heading_rad)
        requested_force = velocity_body * (-ship.mass_kg / ship.brake_response_s)
        main_force = Vec2(
            clamp(requested_force.x, -limits.force_left_n, limits.force_right_n),
            clamp(requested_force.y, -limits.force_reverse_n, limits.force_forward_n),
        )
    else:
        main_force = Vec2(
            signed_axis_force(
                controls.move_body.x, limits.force_right_n, limits.force_left_n
            ),
            signed_axis_force(
                controls.move_body.y, limits.force_forward_n, limits.force_reverse_n
            ),
        )

    wheel = clamp(controls.wheel, -1.0, 1.0)
    target_rate = wheel * ship.wheel_target_max_radps
    if not controls.overg:
        safe_rate = safe_steady_yaw_rate(ship)
        target_rate = clamp(target_rate, -safe_rate, safe_rate)

    requested_alpha = (target_rate - state.yaw_rate_radps) / ship.wheel_response_s
    requested_torque = requested_alpha * ship.inertia_kg_m2 / ship.turn_scale
    turning_torque = clamp(
        requested_torque, -limits.torque_cw_nm, limits.torque_ccw_nm
    )

    # L0 积分器测试故意使用纯职责合成夹具；正式布局由执行器聚合器保留物理副作用。
    return AllocatedActuation(
        main_force_body_n=main_force,
        turning_force_body_n=Vec2(),
        main_torque_nm=0.0,
        turning_torque_nm=turning_torque,
        target_yaw_rate_radps=target_rate,
    )


def load_metrics_for_scale(
    ship: ShipParameters,
    state: MotionState,
    actuation: AllocatedActuation,
    drag_world_n: Vec2,
    scale: float,
    dt: float,
) -> LoadMetrics:
    active_world = body_to_world(actuation.active_force_body_n * scale, state.heading_rad)
    acceleration_world = (active_world + drag_world_n) / ship.mass_kg
    acceleration_body = world_to_body(acceleration_world, state.heading_rad)
    angular_acceleration = (
        ship.turn_scale * actuation.active_torque_nm * scale / ship.inertia_kg_m2
    )
    predicted_yaw_rate = state.yaw_rate_radps + angular_acceleration * dt
    ratio = structure_ratio(
        ship, acceleration_body, angular_acceleration, predicted_yaw_rate
    )
    horizontal_g = acceleration_world.length / ship.gravity_mps2
    crew_g = sqrt(1.0 + horizontal_g * horizontal_g)
    return LoadMetrics(ratio, crew_g, acceleration_body, angular_acceleration)


def command_allowed(metrics: LoadMetrics, controls: ControlInput) -> bool:
    structure_ok = controls.overg or metrics.structure_ratio <= 1.0 + 1.0e-9
    crew_ok = (not controls.crew_present) or metrics.crew_g <= 12.0 + 1.0e-9
    return structure_ok and crew_ok


def violation_score(metrics: LoadMetrics, controls: ControlInput) -> float:
    structure_score = 0.0 if controls.overg else max(0.0, metrics.structure_ratio - 1.0)
    crew_score = (
        max(0.0, metrics.crew_g / 12.0 - 1.0)
        if controls.crew_present
        else 0.0
    )
    return structure_score + crew_score


def choose_command_scale(
    ship: ShipParameters,
    state: MotionState,
    controls: ControlInput,
    actuation: AllocatedActuation,
    drag_world_n: Vec2,
    dt: float,
) -> tuple[float, LoadMetrics]:
    """寻找满足结构/乘员限制的最大倍率；超限时允许真正减载的指令。"""

    samples: list[tuple[float, LoadMetrics]] = []
    for index in range(65):
        scale = index / 64.0
        metrics = load_metrics_for_scale(
            ship, state, actuation, drag_world_n, scale, dt
        )
        samples.append((scale, metrics))

    allowed = [(scale, metrics) for scale, metrics in samples if command_allowed(metrics, controls)]
    if allowed:
        low_scale, low_metrics = allowed[-1]
        if low_scale >= 1.0 - EPS:
            return 1.0, low_metrics
        high_scale = low_scale + 1.0 / 64.0
        for _ in range(50):
            middle = 0.5 * (low_scale + high_scale)
            middle_metrics = load_metrics_for_scale(
                ship, state, actuation, drag_world_n, middle, dt
            )
            if command_allowed(middle_metrics, controls):
                low_scale, low_metrics = middle, middle_metrics
            else:
                high_scale = middle
        return low_scale, low_metrics

    # 外力或已有角速度已经使零输入超限时，选择最能降低违规程度的请求。
    return min(samples, key=lambda item: violation_score(item[1], controls))


def integrate_step(
    ship: ShipParameters,
    state: MotionState,
    controls: ControlInput,
    dt: float = 1.0 / 60.0,
) -> tuple[MotionState, StepDiagnostics]:
    if dt <= 0.0:
        raise ValueError("固定物理步必须大于零")

    actuation = allocate_controls(ship, state, controls)
    drag_world = ship.drag.force_world(state)
    scale, metrics = choose_command_scale(
        ship, state, controls, actuation, drag_world, dt
    )

    active_force_body = actuation.active_force_body_n * scale
    active_force_world = body_to_world(active_force_body, state.heading_rad)
    acceleration_world = (active_force_world + drag_world) / ship.mass_kg
    active_torque = actuation.active_torque_nm * scale
    angular_acceleration = ship.turn_scale * active_torque / ship.inertia_kg_m2

    velocity = state.velocity_world_mps + acceleration_world * dt
    position = state.position_world_m + velocity * dt
    yaw_rate = state.yaw_rate_radps + angular_acceleration * dt
    heading = wrap_angle(state.heading_rad + yaw_rate * dt)

    overload = max(0.0, metrics.structure_ratio - 1.0)
    denominator = max(EPS, ship.overg_reference_ratio - 1.0)
    damage_rate = (
        ship.hull_hp_max
        / ship.overg_reference_time_s
        * (overload / denominator) ** 2
    )
    hull_damage = damage_rate * dt
    hull_hp = max(0.0, state.hull_hp - hull_damage)

    next_state = MotionState(
        position_world_m=position,
        velocity_world_mps=velocity,
        heading_rad=heading,
        yaw_rate_radps=yaw_rate,
        height_layer=state.height_layer,
        hull_hp=hull_hp,
    )
    diagnostics = StepDiagnostics(
        command_scale=scale,
        target_yaw_rate_radps=actuation.target_yaw_rate_radps,
        structure_ratio=metrics.structure_ratio,
        crew_g=metrics.crew_g,
        hull_damage=hull_damage,
        active_force_body_n=active_force_body,
        active_torque_nm=active_torque,
        drag_force_world_n=drag_world,
    )
    return next_state, diagnostics


def run_for(
    ship: ShipParameters,
    state: MotionState,
    seconds: float,
    dt: float,
    control_function,
) -> tuple[MotionState, StepDiagnostics]:
    step_count = round(seconds / dt)
    diagnostics = None
    for index in range(step_count):
        controls = control_function(index * dt)
        state, diagnostics = integrate_step(ship, state, controls, dt)
    assert diagnostics is not None
    return state, diagnostics


def make_test_ship(**changes) -> ShipParameters:
    base = ShipParameters(
        mass_kg=50_000_000.0,
        inertia_kg_m2=120_000_000_000.0,
        safe_longitudinal_mps2=8.0 * 9.80665,
        safe_lateral_mps2=5.0 * 9.80665,
        structure_points_body_m=(
            Vec2(0.0, 77.5),
            Vec2(0.0, -77.5),
            Vec2(10.0, 0.0),
            Vec2(-10.0, 0.0),
        ),
        actuators=ActuatorLimits(
            force_right_n=400_000_000.0,
            force_left_n=400_000_000.0,
            force_forward_n=500_000_000.0,
            force_reverse_n=400_000_000.0,
            torque_ccw_nm=60_000_000_000.0,
            torque_cw_nm=60_000_000_000.0,
        ),
        drag=DirectionalDragFixture(
            forward_area_m2=4_000.0,
            backward_area_m2=5_000.0,
            lateral_area_m2=18_000.0,
        ),
    )
    return replace(base, **changes)


def test_force_mass_inertia_relations() -> tuple[float, float, float, float]:
    dt = 1.0 / 60.0
    controls = ControlInput(move_body=Vec2(0.0, 1.0), crew_present=False)
    base = make_test_ship()
    stronger = replace(
        base,
        actuators=replace(base.actuators, force_forward_n=750_000_000.0),
    )
    heavier = replace(base, mass_kg=75_000_000.0)

    _, base_diag = integrate_step(base, MotionState(), controls, dt)
    _, strong_diag = integrate_step(stronger, MotionState(), controls, dt)
    _, heavy_diag = integrate_step(heavier, MotionState(), controls, dt)
    base_accel = base_diag.active_force_body_n.y / base.mass_kg
    strong_accel = strong_diag.active_force_body_n.y / stronger.mass_kg
    heavy_accel = heavy_diag.active_force_body_n.y / heavier.mass_kg
    assert strong_accel > base_accel > heavy_accel

    turn = ControlInput(wheel=1.0, crew_present=False)
    high_inertia = replace(base, inertia_kg_m2=240_000_000_000.0)
    low_next, _ = integrate_step(base, MotionState(), turn, dt)
    high_next, _ = integrate_step(high_inertia, MotionState(), turn, dt)
    low_alpha = low_next.yaw_rate_radps / dt
    high_alpha = high_next.yaw_rate_radps / dt
    assert low_alpha > high_alpha
    return base_accel, strong_accel, heavy_accel, high_alpha


def test_wheel_centering() -> tuple[float, float, float]:
    ship = make_test_ship()
    state = MotionState()
    dt = 1.0 / 60.0
    state, _ = run_for(
        ship,
        state,
        2.0,
        dt,
        lambda _: ControlInput(wheel=1.0, crew_present=False),
    )
    release_rate = state.yaw_rate_radps
    state, diagnostics = run_for(
        ship,
        state,
        8.0,
        dt,
        lambda _: ControlInput(wheel=0.0, crew_present=False),
    )
    assert release_rate > radians(5.0)
    assert abs(state.yaw_rate_radps) < radians(0.001)
    assert abs(diagnostics.active_torque_nm) < 1_000_000.0
    return release_rate, state.yaw_rate_radps, state.heading_rad


def test_inertial_velocity_and_brake() -> tuple[float, float, float]:
    ship = make_test_ship()
    initial = MotionState(velocity_world_mps=Vec2(100.0, 0.0))
    turned, _ = run_for(
        ship,
        initial,
        1.0,
        1.0 / 60.0,
        lambda _: ControlInput(wheel=1.0, crew_present=False),
    )
    initial_direction = atan2(initial.velocity_world_mps.y, initial.velocity_world_mps.x)
    final_direction = atan2(turned.velocity_world_mps.y, turned.velocity_world_mps.x)
    direction_change = abs(wrap_angle(final_direction - initial_direction))
    assert direction_change < radians(0.0001)
    assert abs(turned.heading_rad) > radians(1.0)

    moving = MotionState(velocity_world_mps=Vec2(0.0, 200.0))
    braked, _ = run_for(
        ship,
        moving,
        8.0,
        1.0 / 60.0,
        lambda _: ControlInput(brake=True, crew_present=False),
    )
    # 反推受 400MN 实际能力限制；八秒只要求显著减速，不允许瞬时清零。
    assert 0.0 < braked.velocity_world_mps.length < 140.0
    return degrees(direction_change), degrees(turned.heading_rad), braked.velocity_world_mps.length


def test_structure_overg_and_crew_lock() -> tuple[float, float, float, float, float]:
    base = make_test_ship()
    extreme = replace(
        base,
        actuators=replace(base.actuators, force_forward_n=10_000_000_000.0),
    )
    dt = 1.0 / 60.0

    _, normal = integrate_step(
        extreme,
        MotionState(),
        ControlInput(move_body=Vec2(0.0, 1.0), crew_present=False),
        dt,
    )
    assert normal.structure_ratio <= 1.0 + 1.0e-7
    assert normal.command_scale < 1.0

    _, unmanned_overg = integrate_step(
        extreme,
        MotionState(),
        ControlInput(
            move_body=Vec2(0.0, 1.0), overg=True, crew_present=False
        ),
        dt,
    )
    assert unmanned_overg.command_scale == 1.0
    assert unmanned_overg.structure_ratio > 1.0
    assert unmanned_overg.hull_damage > 0.0

    _, crewed_overg = integrate_step(
        extreme,
        MotionState(),
        ControlInput(move_body=Vec2(0.0, 1.0), overg=True, crew_present=True),
        dt,
    )
    assert crewed_overg.command_scale < 1.0
    assert crewed_overg.crew_g <= 12.0 + 1.0e-7
    assert crewed_overg.structure_ratio > 1.0
    return (
        normal.command_scale,
        normal.structure_ratio,
        unmanned_overg.structure_ratio,
        unmanned_overg.hull_damage,
        crewed_overg.crew_g,
    )


def test_terminal_speed() -> tuple[float, float]:
    ship = make_test_ship()
    expected = sqrt(
        2.0
        * ship.actuators.force_forward_n
        / (ship.drag.rho_upper * ship.drag.forward_area_m2)
    )
    state, _ = run_for(
        ship,
        MotionState(),
        250.0,
        1.0 / 60.0,
        lambda _: ControlInput(move_body=Vec2(0.0, 1.0), crew_present=False),
    )
    simulated = state.velocity_world_mps.length
    assert abs(simulated - expected) / expected < 0.01
    return expected, simulated


def convergence_case(dt: float) -> MotionState:
    ship = make_test_ship()

    def controls(time_s: float) -> ControlInput:
        if time_s < 4.0:
            return ControlInput(
                move_body=Vec2(0.0, 0.7), wheel=0.65, crew_present=False
            )
        if time_s < 8.0:
            return ControlInput(move_body=Vec2(0.0, 0.7), crew_present=False)
        return ControlInput(brake=True, crew_present=False)

    state, _ = run_for(ship, MotionState(), 12.0, dt, controls)
    return state


def test_fixed_step_convergence() -> list[tuple[int, MotionState]]:
    results = [
        (30, convergence_case(1.0 / 30.0)),
        (60, convergence_case(1.0 / 60.0)),
        (120, convergence_case(1.0 / 120.0)),
    ]
    reference = results[-1][1]
    for _, state in results[:-1]:
        position_error = (state.position_world_m - reference.position_world_m).length
        velocity_error = (state.velocity_world_mps - reference.velocity_world_mps).length
        heading_error = abs(wrap_angle(state.heading_rad - reference.heading_rad))
        assert position_error < 3.0
        assert velocity_error < 0.5
        assert heading_error < radians(0.5)
    return results


def main() -> None:
    base_accel, strong_accel, heavy_accel, high_inertia_alpha = (
        test_force_mass_inertia_relations()
    )
    release_rate, centered_rate, final_heading = test_wheel_centering()
    direction_change, inertial_heading, brake_speed = test_inertial_velocity_and_brake()
    normal_scale, normal_ratio, overg_ratio, overg_damage, crew_g = (
        test_structure_overg_and_crew_lock()
    )
    terminal_expected, terminal_simulated = test_terminal_speed()
    convergence = test_fixed_step_convergence()

    print("机动闭环第一版自动断言：全部通过")
    print(
        "推力/质量关系(m/s^2): "
        f"基准={base_accel:.3f}, 强推力={strong_accel:.3f}, "
        f"重舰={heavy_accel:.3f}"
    )
    print(f"双倍惯量首步角加速度(rad/s^2): {high_inertia_alpha:.6f}")
    print(
        "舵盘回中(rad/s): "
        f"释放时={release_rate:.6f}, 8秒后={centered_rate:.9f}, "
        f"最终舰艏角={degrees(final_heading):.3f}deg"
    )
    print(
        "惯性/制动: "
        f"转舰艏时速度方向变化={direction_change:.8f}deg, "
        f"舰艏变化={inertial_heading:.3f}deg, 8秒制动后={brake_speed:.6f}m/s"
    )
    print(
        "结构与OverG: "
        f"正常倍率={normal_scale:.6f}, 正常R={normal_ratio:.6f}, "
        f"无人OverG R={overg_ratio:.6f}, 单步损伤={overg_damage:.3f}, "
        f"有人OverG G={crew_g:.6f}"
    )
    print(
        "持续最大速度(m/s): "
        f"解析={terminal_expected:.3f}, 固定步模拟={terminal_simulated:.3f}"
    )
    print("固定步收敛:")
    for hz, state in convergence:
        print(
            f"  {hz:>3}Hz  p=({state.position_world_m.x:.3f},"
            f"{state.position_world_m.y:.3f})m  "
            f"v=({state.velocity_world_mps.x:.3f},"
            f"{state.velocity_world_mps.y:.3f})m/s  "
            f"psi={degrees(state.heading_rad):.3f}deg  "
            f"omega={degrees(state.yaw_rate_radps):.6f}deg/s"
        )


if __name__ == "__main__":
    main()
