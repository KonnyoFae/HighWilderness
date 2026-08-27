"""《高天荒野》普通炮弹二维飞行阻力第一版原型。

只计算静止发射平台、无风、同一高度层内沿射线飞行时的速度和飞行时间，
用于检查弹药尺度、三层共用弹道介质与首轮射程锚点。正式战斗更新还要把炮口速度
叠加到发射舰世界速度，并以炮弹相对介质速度计算阻力。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, exp, pi, radians, sqrt


@dataclass(frozen=True)
class MediumLayer:
    name: str
    density_kg_m3: float
    sound_speed_mps: float


@dataclass(frozen=True)
class Projectile:
    name: str
    caliber_mm: float
    mass_kg: float
    muzzle_velocity_mps: float
    form_factor: float
    standard_max_range_km: float
    sample_ranges_km: tuple[float, ...]

    @property
    def reference_area_m2(self) -> float:
        diameter_m = self.caliber_mm / 1000.0
        return pi * diameter_m**2 / 4.0

    @property
    def sectional_density_kg_m2(self) -> float:
        return self.mass_kg / self.reference_area_m2


@dataclass(frozen=True)
class FlightSample:
    distance_m: float
    speed_mps: float
    time_s: float


LAYERS = (
    MediumLayer("上层", 0.55, 320.0),
    MediumLayer("云层", 0.55, 320.0),
    MediumLayer("雨层", 0.55, 320.0),
)

PROJECTILES = (
    Projectile("30毫米高爆燃烧弹", 30.0, 0.363, 1070.0, 1.00, 3.0, (0.5, 1.0, 2.0, 3.0)),
    Projectile("76毫米半穿高爆弹", 76.0, 6.3, 905.0, 0.95, 16.0, (2.0, 5.0, 10.0, 16.0)),
    Projectile("120毫米半穿高爆弹", 120.0, 18.0, 1015.0, 0.90, 8.0, (1.0, 3.0, 5.0, 8.0)),
    Projectile("155毫米半穿高爆弹", 155.0, 42.5, 939.0, 0.85, 30.0, (5.0, 10.0, 20.0, 30.0)),
    Projectile("255毫米半穿高爆弹", 255.0, 200.0, 700.0, 0.85, 35.0, (5.0, 10.0, 20.0, 35.0)),
)

# 这是首轮游戏标定曲线，不冒充某种现实制式弹的风洞表。
# 跨音速附近的峰值用于表现可压缩性阻力上升；弹种以 form_factor 修正。
BASE_DRAG_CURVE = (
    (0.00, 0.160),
    (0.80, 0.170),
    (0.95, 0.250),
    (1.05, 0.340),
    (1.20, 0.300),
    (1.50, 0.250),
    (2.00, 0.220),
    (3.00, 0.200),
    (5.00, 0.180),
)


def base_drag_coefficient(mach: float) -> float:
    if mach <= BASE_DRAG_CURVE[0][0]:
        return BASE_DRAG_CURVE[0][1]
    for (m0, c0), (m1, c1) in zip(BASE_DRAG_CURVE, BASE_DRAG_CURVE[1:]):
        if mach <= m1:
            ratio = (mach - m0) / (m1 - m0)
            return c0 + (c1 - c0) * ratio
    return BASE_DRAG_CURVE[-1][1]


def simulate_flight(
    projectile: Projectile,
    layer: MediumLayer,
    max_distance_m: float,
    step_m: float = 10.0,
    initial_world_speed_mps: float | None = None,
) -> list[FlightSample]:
    """按距离步进积分 dv/dx=-rho*Cd*A*v/(2m)。"""
    distance_m = 0.0
    speed_mps = (
        projectile.muzzle_velocity_mps
        if initial_world_speed_mps is None
        else initial_world_speed_mps
    )
    time_s = 0.0
    samples = [FlightSample(distance_m, speed_mps, time_s)]

    while distance_m < max_distance_m:
        dx = min(step_m, max_distance_m - distance_m)
        mach = speed_mps / layer.sound_speed_mps
        drag_coefficient = base_drag_coefficient(mach) * projectile.form_factor
        decay_per_m = (
            0.5
            * layer.density_kg_m3
            * drag_coefficient
            * projectile.reference_area_m2
            / projectile.mass_kg
        )

        if decay_per_m > 0.0:
            speed_after = speed_mps * exp(-decay_per_m * dx)
            dt = (exp(decay_per_m * dx) - 1.0) / (decay_per_m * speed_mps)
        else:
            speed_after = speed_mps
            dt = dx / speed_mps

        distance_m += dx
        time_s += dt
        speed_mps = speed_after
        samples.append(FlightSample(distance_m, speed_mps, time_s))

    return samples


def inherited_world_speed_mps(
    muzzle_velocity_mps: float,
    ship_speed_mps: float,
    barrel_angle_from_ship_velocity_deg: float,
) -> float:
    angle_rad = radians(barrel_angle_from_ship_velocity_deg)
    return sqrt(
        muzzle_velocity_mps**2
        + ship_speed_mps**2
        + 2.0 * muzzle_velocity_mps * ship_speed_mps * cos(angle_rad)
    )


def sample_at_distance(samples: list[FlightSample], distance_m: float) -> FlightSample:
    # 原型的请求距离均为10米整数倍。
    index = round(distance_m / 10.0)
    sample = samples[index]
    assert abs(sample.distance_m - distance_m) < 1e-6
    return sample


def run_checks() -> None:
    for projectile in PROJECTILES:
        layer_results: dict[str, list[FlightSample]] = {}
        max_distance_m = projectile.standard_max_range_km * 1000.0
        for layer in LAYERS:
            samples = simulate_flight(projectile, layer, max_distance_m)
            layer_results[layer.name] = samples
            assert all(
                earlier.speed_mps >= later.speed_mps
                for earlier, later in zip(samples, samples[1:])
            )
            assert all(
                earlier.time_s <= later.time_s
                for earlier, later in zip(samples, samples[1:])
            )

        upper_end = layer_results["上层"][-1]
        cloud_end = layer_results["云层"][-1]
        rain_end = layer_results["雨层"][-1]
        assert abs(upper_end.speed_mps - cloud_end.speed_mps) < 1e-9
        assert abs(upper_end.speed_mps - rain_end.speed_mps) < 1e-9
        assert abs(upper_end.time_s - cloud_end.time_s) < 1e-9
        assert abs(upper_end.time_s - rain_end.time_s) < 1e-9

    # 标定曲线必须在跨音速附近形成阻力峰。
    assert base_drag_coefficient(1.05) > base_drag_coefficient(0.80)
    assert base_drag_coefficient(1.05) > base_drag_coefficient(2.00)

    forward = inherited_world_speed_mps(1015.0, 300.0, 0.0)
    broadside = inherited_world_speed_mps(1015.0, 300.0, 90.0)
    backward = inherited_world_speed_mps(1015.0, 300.0, 180.0)
    assert abs(forward - 1315.0) < 1e-9
    assert forward > broadside > backward
    assert abs(backward - 715.0) < 1e-9


def print_report() -> None:
    print("### 弹丸尺度")
    print("| 弹种 | 截面积 m² | 截面密度 kg/m² | 形状系数 |")
    print("| --- | ---: | ---: | ---: |")
    for projectile in PROJECTILES:
        print(
            f"| {projectile.name} | {projectile.reference_area_m2:.6f} | "
            f"{projectile.sectional_density_kg_m2:.1f} | {projectile.form_factor:.2f} |"
        )

    for projectile in PROJECTILES:
        max_distance_m = projectile.standard_max_range_km * 1000.0
        print(f"\n### {projectile.name}")
        print("| 层级 | 距离 km | 速度 m/s | 炮口速度比 | 飞行时间 s |")
        print("| --- | ---: | ---: | ---: | ---: |")
        for layer in LAYERS:
            samples = simulate_flight(projectile, layer, max_distance_m)
            for range_km in projectile.sample_ranges_km:
                sample = sample_at_distance(samples, range_km * 1000.0)
                print(
                    f"| {layer.name} | {range_km:g} | {sample.speed_mps:.1f} | "
                    f"{sample.speed_mps / projectile.muzzle_velocity_mps:.3f} | "
                    f"{sample.time_s:.2f} |"
                )

    reference = PROJECTILES[2]
    upper = LAYERS[0]
    print("\n### 发射舰速度继承样例：120毫米、舰速300m/s、上层、5km")
    print("| 相对发射方向 | 初始世界速度 m/s | 5km速度 m/s | 飞行时间 s |")
    print("| --- | ---: | ---: | ---: |")
    for direction_name, angle_deg in (("顺航", 0.0), ("横向", 90.0), ("逆航", 180.0)):
        initial_speed = inherited_world_speed_mps(
            reference.muzzle_velocity_mps, 300.0, angle_deg
        )
        samples = simulate_flight(
            reference,
            upper,
            5000.0,
            initial_world_speed_mps=initial_speed,
        )
        sample = samples[-1]
        print(
            f"| {direction_name} | {initial_speed:.1f} | "
            f"{sample.speed_mps:.1f} | {sample.time_s:.2f} |"
        )


if __name__ == "__main__":
    run_checks()
    print_report()
