"""《高天荒野》普通炮弹二维飞行与甲弹对抗的稳定公式层。

本文件只保存已经由原型回归锁定的数学关系：
- 三个高度层可以选择各自的介质参数，雨层不会被隐式附加阻力；
- 以炮弹相对介质的世界速度计算阻力；
- 以炮弹相对命中点局部表面的速度计算入射角与穿深；
- 局部装甲耐久只决定装甲是否完全失效，失效前不递减有效厚度。

弹种参数和损伤换算不在本层硬编码，由上层数据目录提供。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import acos, cos, exp, hypot, log1p, pi, radians, degrees


COSINE_FLOOR = 0.05
RICOCHET_SUPPRESSION_RATIO = 4.0

# 第一版游戏标定曲线，不冒充任何现实制式弹的风洞表。
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


class ImpactOutcome(str, Enum):
    RICOCHET = "ricochet"
    STOPPED = "stopped"
    PENETRATED = "penetrated"


class Aftereffect(str, Enum):
    INTERNAL_BLAST = "internal_blast"
    SURFACE_BLAST = "surface_blast"
    KINETIC_RAY = "kinetic_ray"
    FIRE = "fire"


@dataclass(frozen=True)
class BallisticProjectileProfile:
    caliber_mm: float
    mass_kg: float
    muzzle_velocity_mps: float
    form_factor: float

    @property
    def reference_area_m2(self) -> float:
        diameter_m = self.caliber_mm / 1000.0
        return pi * diameter_m**2 / 4.0


@dataclass(frozen=True)
class PenetrationProjectileProfile:
    reference_penetration_mm: float
    reference_speed_mps: float
    velocity_exponent: float
    obliquity_exponent: float
    normalization_deg: float
    ricochet_start_deg: float
    ricochet_full_deg: float
    impact_armor_damage_at_reference_speed: float
    surface_effect_armor_damage: float
    can_ricochet: bool
    aftereffect: Aftereffect


@dataclass(frozen=True)
class ArmorState:
    protection_coefficient: float
    thickness_mm: float
    current_local_durability: float

    @property
    def active(self) -> bool:
        return self.current_local_durability > 0.0 and self.thickness_mm > 0.0


@dataclass(frozen=True)
class ImpactResult:
    outcome: ImpactOutcome
    impact_angle_deg: float
    effective_angle_deg: float
    available_penetration_mm: float
    required_penetration_mm: float
    penetration_ratio: float
    ricochet_probability: float
    residual_speed_ratio: float
    residual_energy_ratio: float
    armor_damage_formula_points: float
    effect_location: str


@dataclass(frozen=True)
class BallisticStepResult:
    position_xy: tuple[float, float]
    velocity_xy: tuple[float, float]
    distance_m: float


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def smoothstep(value: float) -> float:
    value = clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def base_drag_coefficient(mach: float) -> float:
    if mach <= BASE_DRAG_CURVE[0][0]:
        return BASE_DRAG_CURVE[0][1]
    for (m0, c0), (m1, c1) in zip(BASE_DRAG_CURVE, BASE_DRAG_CURVE[1:]):
        if mach <= m1:
            ratio = (mach - m0) / (m1 - m0)
            return c0 + (c1 - c0) * ratio
    return BASE_DRAG_CURVE[-1][1]


def integrate_ballistic_step(
    position_xy: tuple[float, float],
    velocity_xy: tuple[float, float],
    projectile: BallisticProjectileProfile,
    *,
    density_kg_m3: float,
    sound_speed_mps: float,
    duration_s: float,
) -> BallisticStepResult:
    """在一个固定时间步内冻结 Cd，解析积分二次阻力。

    当前版本没有风，所以速度方向在单步内不变。上层通过缩短
    duration_s 处理可变 Cd 与碰撞检测。
    """
    if duration_s < 0.0:
        raise ValueError("积分时长不得为负")
    speed = hypot(*velocity_xy)
    if duration_s == 0.0 or speed <= 0.0:
        return BallisticStepResult(position_xy, velocity_xy, 0.0)
    if density_kg_m3 < 0.0 or sound_speed_mps <= 0.0:
        raise ValueError("介质密度不得为负，音速必须为正")

    mach = speed / sound_speed_mps
    cd = base_drag_coefficient(mach) * projectile.form_factor
    drag_factor = (
        0.5 * density_kg_m3 * cd * projectile.reference_area_m2 / projectile.mass_kg
    )
    if drag_factor > 0.0:
        factor = 1.0 + drag_factor * speed * duration_s
        speed_after = speed / factor
        distance = log1p(drag_factor * speed * duration_s) / drag_factor
    else:
        speed_after = speed
        distance = speed * duration_s
    direction = velocity_xy[0] / speed, velocity_xy[1] / speed
    return BallisticStepResult(
        (
            position_xy[0] + direction[0] * distance,
            position_xy[1] + direction[1] * distance,
        ),
        (direction[0] * speed_after, direction[1] * speed_after),
        distance,
    )


def relative_impact_velocity_xy(
    projectile_world_velocity_xy: tuple[float, float],
    target_world_velocity_xy: tuple[float, float],
    target_angular_velocity_rad_s: float,
    impact_offset_from_target_center_xy: tuple[float, float],
) -> tuple[float, float]:
    rx, ry = impact_offset_from_target_center_xy
    surface_velocity = (
        target_world_velocity_xy[0] - target_angular_velocity_rad_s * ry,
        target_world_velocity_xy[1] + target_angular_velocity_rad_s * rx,
    )
    return (
        projectile_world_velocity_xy[0] - surface_velocity[0],
        projectile_world_velocity_xy[1] - surface_velocity[1],
    )


def incidence_angle_deg(
    velocity_xy: tuple[float, float],
    edge_start_xy: tuple[float, float],
    edge_end_xy: tuple[float, float],
) -> float:
    speed = hypot(*velocity_xy)
    if speed <= 0.0:
        raise ValueError("命中速度必须大于零")
    ex = edge_end_xy[0] - edge_start_xy[0]
    ey = edge_end_xy[1] - edge_start_xy[1]
    edge_length = hypot(ex, ey)
    if edge_length <= 0.0:
        raise ValueError("命中边长度必须大于零")
    nx, ny = -ey / edge_length, ex / edge_length
    cosine = abs((velocity_xy[0] * nx + velocity_xy[1] * ny) / speed)
    return degrees(acos(clamp(cosine, 0.0, 1.0)))


def available_penetration_mm(
    projectile: PenetrationProjectileProfile, speed_mps: float
) -> float:
    if speed_mps <= 0.0:
        return 0.0
    return projectile.reference_penetration_mm * (
        speed_mps / projectile.reference_speed_mps
    ) ** projectile.velocity_exponent


def effective_angle_deg(
    projectile: PenetrationProjectileProfile, impact_angle_deg: float
) -> float:
    return clamp(impact_angle_deg - projectile.normalization_deg, 0.0, 89.9)


def required_penetration_mm(
    projectile: PenetrationProjectileProfile,
    armor: ArmorState,
    impact_angle_deg: float,
) -> float:
    if not armor.active:
        return 0.0
    angle = effective_angle_deg(projectile, impact_angle_deg)
    cosine = max(cos(radians(angle)), COSINE_FLOOR)
    return (
        armor.thickness_mm
        * armor.protection_coefficient
        / cosine**projectile.obliquity_exponent
    )


def ricochet_probability(
    projectile: PenetrationProjectileProfile,
    armor: ArmorState,
    impact_angle_deg: float,
    penetration_mm: float,
) -> float:
    if not projectile.can_ricochet or not armor.active:
        return 0.0
    angle = effective_angle_deg(projectile, impact_angle_deg)
    if angle <= projectile.ricochet_start_deg:
        return 0.0
    if projectile.ricochet_full_deg <= projectile.ricochet_start_deg:
        angle_probability = 1.0
    else:
        interval = projectile.ricochet_full_deg - projectile.ricochet_start_deg
        angle_probability = smoothstep(
            (angle - projectile.ricochet_start_deg) / interval
        )
    normal_resistance = armor.thickness_mm * armor.protection_coefficient
    normal_ratio = (
        penetration_mm / normal_resistance
        if normal_resistance > 0.0
        else float("inf")
    )
    power_suppression = clamp(
        (RICOCHET_SUPPRESSION_RATIO - normal_ratio)
        / (RICOCHET_SUPPRESSION_RATIO - 1.0),
        0.0,
        1.0,
    )
    return angle_probability * power_suppression


def resolve_armor_impact(
    projectile: PenetrationProjectileProfile,
    armor: ArmorState,
    speed_mps: float,
    impact_angle_deg_value: float,
    *,
    ricochet_roll: float = 1.0,
) -> ImpactResult:
    penetration = available_penetration_mm(projectile, speed_mps)
    angle = effective_angle_deg(projectile, impact_angle_deg_value)
    required = required_penetration_mm(projectile, armor, impact_angle_deg_value)
    probability = ricochet_probability(
        projectile, armor, impact_angle_deg_value, penetration
    )
    impact_damage = projectile.impact_armor_damage_at_reference_speed * (
        speed_mps / projectile.reference_speed_mps
    ) ** 2

    if not armor.active:
        return ImpactResult(
            ImpactOutcome.PENETRATED,
            impact_angle_deg_value,
            angle,
            penetration,
            0.0,
            float("inf"),
            0.0,
            1.0,
            1.0,
            0.0,
            "internal",
        )

    ratio = penetration / required if required > 0.0 else float("inf")
    if clamp(ricochet_roll, 0.0, 1.0) < probability:
        return ImpactResult(
            ImpactOutcome.RICOCHET,
            impact_angle_deg_value,
            angle,
            penetration,
            required,
            ratio,
            probability,
            0.0,
            0.0,
            impact_damage * 0.25,
            "external",
        )
    if penetration < required:
        return ImpactResult(
            ImpactOutcome.STOPPED,
            impact_angle_deg_value,
            angle,
            penetration,
            required,
            ratio,
            probability,
            0.0,
            0.0,
            impact_damage * 0.60 + projectile.surface_effect_armor_damage,
            "external",
        )

    reserve = clamp(1.0 - required / penetration, 0.0, 1.0)
    residual_speed_ratio = reserve ** (1.0 / projectile.velocity_exponent)
    return ImpactResult(
        ImpactOutcome.PENETRATED,
        impact_angle_deg_value,
        angle,
        penetration,
        required,
        ratio,
        probability,
        residual_speed_ratio,
        residual_speed_ratio**2,
        impact_damage,
        "internal",
    )
