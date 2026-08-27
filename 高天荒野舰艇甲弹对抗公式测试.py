"""《高天荒野》甲弹对抗第一版公式原型测试。

测试值只用于验证公式关系，不是正式火炮或弹药内容参数。装甲参数读取已经
敲定的第一版材质值；具体弹种以后以同一数据接口标定。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import acos, cos, degrees, hypot, radians


COSINE_FLOOR = 0.05
RICOCHET_SUPPRESSION_RATIO = 4.0


class Outcome(str, Enum):
    RICOCHET = "跳弹"
    STOPPED = "未击穿"
    PENETRATED = "击穿"


class Aftereffect(str, Enum):
    INTERNAL_BLAST = "穿后爆炸"
    SURFACE_BLAST = "表面爆炸"
    KINETIC_RAY = "动能直线"
    FIRE = "引燃"


@dataclass(frozen=True)
class ArmorMaterial:
    name: str
    protection_coefficient: float


@dataclass(frozen=True)
class ArmorState:
    material: ArmorMaterial
    thickness_mm: float
    current_local_hp: float = 1.0

    @property
    def active(self) -> bool:
        return self.current_local_hp > 0.0 and self.thickness_mm > 0.0


@dataclass(frozen=True)
class ProjectileProfile:
    name: str
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
class ImpactResult:
    outcome: Outcome
    impact_angle_deg: float
    effective_angle_deg: float
    available_penetration_mm: float
    required_penetration_mm: float
    penetration_ratio: float
    ricochet_probability: float
    residual_speed_ratio: float
    residual_energy_ratio: float
    armor_damage: float
    effect_location: str


ARMOR_STEEL = ArmorMaterial("装甲钢", 1.00)
LIGHTWEIGHT_CARBIDE = ArmorMaterial("轻质碳化物复合装甲", 1.35)
LAMINATED_ABLATIVE = ArmorMaterial("积层烧蚀装甲", 3.00)


# 以下均为公式测试弹，不是正式武器内容值。
TEST_SAPHE = ProjectileProfile(
    "测试半穿高爆弹",
    reference_penetration_mm=140.0,
    reference_speed_mps=900.0,
    velocity_exponent=1.40,
    obliquity_exponent=1.10,
    normalization_deg=5.0,
    ricochet_start_deg=65.0,
    ricochet_full_deg=75.0,
    impact_armor_damage_at_reference_speed=20.0,
    surface_effect_armor_damage=10.0,
    can_ricochet=True,
    aftereffect=Aftereffect.INTERNAL_BLAST,
)
TEST_HE = ProjectileProfile(
    "测试高爆弹",
    reference_penetration_mm=35.0,
    reference_speed_mps=900.0,
    velocity_exponent=1.20,
    obliquity_exponent=1.20,
    normalization_deg=0.0,
    ricochet_start_deg=90.0,
    ricochet_full_deg=90.0,
    impact_armor_damage_at_reference_speed=5.0,
    surface_effect_armor_damage=40.0,
    can_ricochet=False,
    aftereffect=Aftereffect.SURFACE_BLAST,
)
TEST_APFSDS = ProjectileProfile(
    "测试尾翼稳定脱壳穿甲弹",
    reference_penetration_mm=320.0,
    reference_speed_mps=1500.0,
    velocity_exponent=1.60,
    obliquity_exponent=0.95,
    normalization_deg=2.0,
    ricochet_start_deg=70.0,
    ricochet_full_deg=82.0,
    impact_armor_damage_at_reference_speed=18.0,
    surface_effect_armor_damage=0.0,
    can_ricochet=True,
    aftereffect=Aftereffect.KINETIC_RAY,
)
TEST_INCENDIARY = ProjectileProfile(
    "测试燃烧弹",
    reference_penetration_mm=5.0,
    reference_speed_mps=850.0,
    velocity_exponent=1.00,
    obliquity_exponent=1.20,
    normalization_deg=0.0,
    ricochet_start_deg=90.0,
    ricochet_full_deg=90.0,
    impact_armor_damage_at_reference_speed=2.0,
    surface_effect_armor_damage=5.0,
    can_ricochet=False,
    aftereffect=Aftereffect.FIRE,
)
TEST_GUIDED_HE = ProjectileProfile(
    "测试制导高爆弹",
    reference_penetration_mm=30.0,
    reference_speed_mps=800.0,
    velocity_exponent=1.20,
    obliquity_exponent=1.20,
    normalization_deg=0.0,
    ricochet_start_deg=90.0,
    ricochet_full_deg=90.0,
    impact_armor_damage_at_reference_speed=4.0,
    surface_effect_armor_damage=35.0,
    can_ricochet=False,
    aftereffect=Aftereffect.SURFACE_BLAST,
)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def smoothstep(value: float) -> float:
    value = clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def incidence_angle_deg(
    velocity_xy: tuple[float, float],
    edge_start_xy: tuple[float, float],
    edge_end_xy: tuple[float, float],
) -> float:
    vx, vy = velocity_xy
    speed = hypot(vx, vy)
    if speed <= 0.0:
        raise ValueError("命中速度必须大于零")

    ex = edge_end_xy[0] - edge_start_xy[0]
    ey = edge_end_xy[1] - edge_start_xy[1]
    edge_length = hypot(ex, ey)
    if edge_length <= 0.0:
        raise ValueError("命中边长度必须大于零")

    nx, ny = -ey / edge_length, ex / edge_length
    cosine = abs((vx * nx + vy * ny) / speed)
    return degrees(acos(clamp(cosine, 0.0, 1.0)))


def relative_impact_velocity_xy(
    projectile_world_velocity_xy: tuple[float, float],
    target_world_velocity_xy: tuple[float, float],
    target_angular_velocity_rad_s: float,
    impact_offset_from_target_center_xy: tuple[float, float],
) -> tuple[float, float]:
    """返回炮弹相对目标命中点局部表面的二维速度。"""
    rx, ry = impact_offset_from_target_center_xy
    local_surface_velocity_xy = (
        target_world_velocity_xy[0] - target_angular_velocity_rad_s * ry,
        target_world_velocity_xy[1] + target_angular_velocity_rad_s * rx,
    )
    return (
        projectile_world_velocity_xy[0] - local_surface_velocity_xy[0],
        projectile_world_velocity_xy[1] - local_surface_velocity_xy[1],
    )


def available_penetration_mm(projectile: ProjectileProfile, speed_mps: float) -> float:
    if speed_mps <= 0.0:
        return 0.0
    speed_ratio = speed_mps / projectile.reference_speed_mps
    return projectile.reference_penetration_mm * speed_ratio ** projectile.velocity_exponent


def effective_angle_deg(projectile: ProjectileProfile, impact_angle_deg: float) -> float:
    return clamp(impact_angle_deg - projectile.normalization_deg, 0.0, 89.9)


def required_penetration_mm(
    projectile: ProjectileProfile,
    armor: ArmorState,
    impact_angle_deg: float,
) -> float:
    if not armor.active:
        return 0.0
    angle = effective_angle_deg(projectile, impact_angle_deg)
    cosine = max(cos(radians(angle)), COSINE_FLOOR)
    normal_equivalent = armor.thickness_mm * armor.material.protection_coefficient
    return normal_equivalent / cosine ** projectile.obliquity_exponent


def ricochet_probability(
    projectile: ProjectileProfile,
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
        angle_probability = smoothstep((angle - projectile.ricochet_start_deg) / interval)

    normal_resistance = armor.thickness_mm * armor.material.protection_coefficient
    normal_ratio = penetration_mm / normal_resistance if normal_resistance > 0.0 else float("inf")
    power_suppression = clamp(
        (RICOCHET_SUPPRESSION_RATIO - normal_ratio)
        / (RICOCHET_SUPPRESSION_RATIO - 1.0),
        0.0,
        1.0,
    )
    return angle_probability * power_suppression


def resolve_impact(
    projectile: ProjectileProfile,
    armor: ArmorState,
    speed_mps: float,
    impact_angle_deg: float,
    ricochet_roll: float = 1.0,
) -> ImpactResult:
    penetration = available_penetration_mm(projectile, speed_mps)
    angle = effective_angle_deg(projectile, impact_angle_deg)
    required = required_penetration_mm(projectile, armor, impact_angle_deg)
    probability = ricochet_probability(
        projectile, armor, impact_angle_deg, penetration
    )
    impact_damage = projectile.impact_armor_damage_at_reference_speed * (
        speed_mps / projectile.reference_speed_mps
    ) ** 2

    if not armor.active:
        return ImpactResult(
            Outcome.PENETRATED,
            impact_angle_deg,
            angle,
            penetration,
            0.0,
            float("inf"),
            0.0,
            1.0,
            1.0,
            0.0,
            "内部",
        )

    ratio = penetration / required if required > 0.0 else float("inf")
    if clamp(ricochet_roll, 0.0, 1.0) < probability:
        return ImpactResult(
            Outcome.RICOCHET,
            impact_angle_deg,
            angle,
            penetration,
            required,
            ratio,
            probability,
            0.0,
            0.0,
            impact_damage * 0.25,
            "外部",
        )

    if penetration < required:
        return ImpactResult(
            Outcome.STOPPED,
            impact_angle_deg,
            angle,
            penetration,
            required,
            ratio,
            probability,
            0.0,
            0.0,
            impact_damage * 0.60 + projectile.surface_effect_armor_damage,
            "外部",
        )

    reserve = clamp(1.0 - required / penetration, 0.0, 1.0)
    residual_speed_ratio = reserve ** (1.0 / projectile.velocity_exponent)
    residual_energy_ratio = residual_speed_ratio**2
    return ImpactResult(
        Outcome.PENETRATED,
        impact_angle_deg,
        angle,
        penetration,
        required,
        ratio,
        probability,
        residual_speed_ratio,
        residual_energy_ratio,
        impact_damage,
        "内部",
    )


def validate() -> None:
    horizontal_edge = ((-5.0, 0.0), (5.0, 0.0))
    assert abs(incidence_angle_deg((0.0, -1.0), *horizontal_edge) - 0.0) < 1e-9
    assert abs(incidence_angle_deg((1.0, -1.0), *horizontal_edge) - 45.0) < 1e-9

    chase_velocity = relative_impact_velocity_xy(
        (900.0, 0.0), (300.0, 0.0), 0.0, (0.0, 0.0)
    )
    head_on_velocity = relative_impact_velocity_xy(
        (900.0, 0.0), (-300.0, 0.0), 0.0, (0.0, 0.0)
    )
    rotating_surface_velocity = relative_impact_velocity_xy(
        (900.0, 0.0), (0.0, 0.0), 0.5, (0.0, 20.0)
    )
    assert chase_velocity == (600.0, 0.0)
    assert head_on_velocity == (1200.0, 0.0)
    assert rotating_surface_velocity == (910.0, 0.0)

    speeds = [600.0, 900.0, 1200.0]
    penetrations = [available_penetration_mm(TEST_SAPHE, speed) for speed in speeds]
    assert penetrations[0] < penetrations[1] < penetrations[2]

    steel = ArmorState(ARMOR_STEEL, 50.0)
    resistances = [
        required_penetration_mm(TEST_SAPHE, steel, angle)
        for angle in (0.0, 45.0, 60.0, 75.0)
    ]
    assert resistances == sorted(resistances)

    material_results = [
        resolve_impact(TEST_SAPHE, ArmorState(material, 50.0), 900.0, 0.0)
        for material in (ARMOR_STEEL, LIGHTWEIGHT_CARBIDE, LAMINATED_ABLATIVE)
    ]
    assert material_results[0].outcome == Outcome.PENETRATED
    assert material_results[1].outcome == Outcome.PENETRATED
    assert material_results[2].outcome == Outcome.STOPPED
    assert (
        material_results[0].residual_energy_ratio
        > material_results[1].residual_energy_ratio
        > material_results[2].residual_energy_ratio
    )

    depleted = resolve_impact(
        TEST_HE,
        ArmorState(LAMINATED_ABLATIVE, 50.0, current_local_hp=0.0),
        900.0,
        75.0,
    )
    assert depleted.outcome == Outcome.PENETRATED
    assert depleted.required_penetration_mm == 0.0
    assert depleted.residual_energy_ratio == 1.0

    probabilities = [
        ricochet_probability(
            TEST_SAPHE,
            ArmorState(ARMOR_STEEL, 100.0),
            angle,
            available_penetration_mm(TEST_SAPHE, 900.0),
        )
        for angle in (60.0, 70.0, 75.0, 80.0)
    ]
    assert probabilities == sorted(probabilities)
    assert all(0.0 <= probability <= 1.0 for probability in probabilities)

    threshold_profile = ProjectileProfile(
        "阈值测试弹",
        50.0,
        1000.0,
        1.5,
        1.0,
        0.0,
        90.0,
        90.0,
        1.0,
        0.0,
        False,
        Aftereffect.KINETIC_RAY,
    )
    threshold = resolve_impact(threshold_profile, steel, 1000.0, 0.0)
    assert threshold.outcome == Outcome.PENETRATED
    assert abs(threshold.residual_energy_ratio) < 1e-9


def print_tests() -> None:
    print("### 相对命中速度接口\n")
    print("| 情景 | 炮弹世界速度 | 目标速度/局部表面运动 | 相对命中速度 |")
    print("| --- | ---: | ---: | ---: |")
    print("| 同向追击 | 900m/s | 同向300m/s | 600m/s |")
    print("| 迎头对撞 | 900m/s | 反向300m/s | 1200m/s |")
    print("| 目标原地逆时针回转，命中点位于中心上方20m | 900m/s | 局部表面向左10m/s | 910m/s |")

    print("\n### 速度缩放\n")
    print("| 命中速度 | 可用法向钢板穿深 |")
    print("| ---: | ---: |")
    for speed in (600.0, 900.0, 1200.0):
        print(f"| {speed:.0f}m/s | {available_penetration_mm(TEST_SAPHE, speed):.2f}mm |")

    print("\n### 材质与入射角\n")
    print("| 装甲 | 实际厚度 | 入射角 | 所需穿深 | 结果 | 穿后能量比例 |")
    print("| --- | ---: | ---: | ---: | --- | ---: |")
    cases = [
        (ARMOR_STEEL, 50.0, 0.0),
        (LIGHTWEIGHT_CARBIDE, 50.0, 0.0),
        (LAMINATED_ABLATIVE, 50.0, 0.0),
        (ARMOR_STEEL, 50.0, 45.0),
        (ARMOR_STEEL, 50.0, 60.0),
        (ARMOR_STEEL, 50.0, 75.0),
    ]
    for material, thickness, angle in cases:
        result = resolve_impact(
            TEST_SAPHE,
            ArmorState(material, thickness),
            900.0,
            angle,
            ricochet_roll=1.0,
        )
        print(
            f"| {material.name} | {thickness:.0f}mm | {angle:.0f}° | "
            f"{result.required_penetration_mm:.2f}mm | {result.outcome.value} | "
            f"{result.residual_energy_ratio:.3f} |"
        )

    print("\n### 跳弹概率\n")
    print("| 原始入射角 | 归一化后角度 | 跳弹概率 |")
    print("| ---: | ---: | ---: |")
    penetration = available_penetration_mm(TEST_SAPHE, 900.0)
    armor = ArmorState(ARMOR_STEEL, 100.0)
    for angle in (60.0, 70.0, 75.0, 80.0):
        probability = ricochet_probability(TEST_SAPHE, armor, angle, penetration)
        print(
            f"| {angle:.0f}° | {effective_angle_deg(TEST_SAPHE, angle):.0f}° | "
            f"{probability:.3f} |"
        )

    print("\n### 弹种接口样例\n")
    print("| 测试弹 | 法向钢板穿深 | 主要穿后效应 | 可否跳弹 |")
    print("| --- | ---: | --- | --- |")
    for projectile in (
        TEST_SAPHE,
        TEST_HE,
        TEST_INCENDIARY,
        TEST_APFSDS,
        TEST_GUIDED_HE,
    ):
        print(
            f"| {projectile.name} | {projectile.reference_penetration_mm:.0f}mm @ "
            f"{projectile.reference_speed_mps:.0f}m/s | {projectile.aftereffect.value} | "
            f"{'是' if projectile.can_ricochet else '否'} |"
        )


if __name__ == "__main__":
    validate()
    print_tests()
