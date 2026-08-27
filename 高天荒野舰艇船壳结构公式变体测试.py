"""《高天荒野》船壳结构公式第二轮变体测试。

以 155×20 米的阿利·伯克级网格尺度为统一锚点，测试叠层、
狭窄腰部、混合材质、基础装甲、质量分布与 OverG 逻辑。
材料密度与等效结构厚度均使用现实单位；强度、耐久等性能使用相对系数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, hypot, sqrt
from statistics import median


GRID = 5.0
# 参考大型钢质舰船约 10–15 mm 的普通壳板尺度，将一层内的上下板、
# 纵横骨架和舱壁折算为 100 mm 等效实心钢层；非基底层另计 20 mm
# 层间立柱、支撑与加强连接。二者不是任何一块现实钢板的字面厚度。
DECK_EQUIVALENT_THICKNESS = 0.10
JOINT_EQUIVALENT_THICKNESS = 0.02
DECK_HEIGHT = 5.0
END_MASS_RATIO = 0.025
SHELL_STRENGTH_EFFICIENCY = 0.25
SHELL_HULL_HP_EFFICIENCY = 0.25
STANDARD_GRAVITY = 9.80665
BASELINE_LONGITUDINAL_SAFE_G = 8.0
# 装甲钢采用现实钢材密度；有效允许应力是游戏结构代理值，已经吸收
# 安全系数、接头效率和抽象骨架等简化。
ARMOR_STEEL_DENSITY = 7850.0
ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS = 40_800_567.325


@dataclass(frozen=True)
class Material:
    name: str
    density: float
    strength_coefficient: float
    hull_hp_per_volume: float
    cost_per_mass_coefficient: float = 1.0
    processing_difficulty: float = 1.0


BASE = Material("装甲钢", 7850.0, 1.00, 1.00)
ALUMINUM = Material("铝合金", 2660.0, 0.50, 0.65, 4.00, 1.25)
TITANIUM = Material("钛合金", 4430.0, 1.70, 1.35, 12.00, 2.50)
CARBIDE_COMPOSITE = Material(
    "碳化物复合装甲", 14500.0, 2.50, 0.85, 8.00, 3.50
)
MIXED_COPPER_ALLOY = Material("混铜合金", 3500.0, 0.90, 1.10, 6.00, 2.00)
FROST_SILVER_ALLOY = Material("霜银合金", 4000.0, 2.10, 1.70, 18.00, 3.00)
SPIRIT_METAL_FABRIC = Material(
    "灵化金属纤维织物", 7400.0, 3.00, 2.50, 15.00, 3.50
)
BASE_ARMOR = Material("装甲钢基础装甲", 7850.0, 1.00, 1.00)
LIGHTWEIGHT_CARBIDE_ARMOR = Material(
    "轻质碳化物复合装甲", 3600.0, 0.45, 0.40, 8.00, 4.00
)
LAMINATED_ABLATIVE_ARMOR = Material(
    "积层烧蚀装甲", 6800.0, 0.20, 0.20, 16.00, 5.00
)


@dataclass(frozen=True)
class Layer:
    polygon: tuple[tuple[float, float], ...]
    material: Material
    is_base: bool = False

    @property
    def effective_thickness(self) -> float:
        return DECK_EQUIVALENT_THICKNESS + (
            0.0 if self.is_base else JOINT_EQUIVALENT_THICKNESS
        )


@dataclass(frozen=True)
class PointMass:
    x: float
    y: float
    mass: float


@dataclass
class StructureModel:
    name: str
    layers: list[Layer]
    armor_thickness: float = 0.0
    armor_material: Material = BASE_ARMOR
    point_masses: list[PointMass] = field(default_factory=list)


def standard_planform(length: float = 155.0, beam: float = 20.0) -> tuple[tuple[float, float], ...]:
    cells = int(round(length / GRID))
    aft_cells = cells // 2
    fore_cells = cells - aft_cells
    y_min = -aft_cells * GRID
    y_max = fore_cells * GRID
    taper = round(0.20 * length / GRID) * GRID
    half_beam = beam / 2.0
    stern_half_beam = max(GRID / 2.0, round(beam * 0.25 / 2.5) * 2.5)
    return (
        (0.0, y_max),
        (half_beam, y_max - taper),
        (half_beam, y_min + taper),
        (stern_half_beam, y_min),
        (-stern_half_beam, y_min),
        (-half_beam, y_min + taper),
        (-half_beam, y_max - taper),
    )


def hourglass_planform(neck_beam: float) -> tuple[tuple[float, float], ...]:
    half = 10.0
    neck = neck_beam / 2.0
    return (
        (0.0, 80.0),
        (half, 50.0),
        (half, 20.0),
        (neck, 10.0),
        (neck, -10.0),
        (half, -20.0),
        (half, -45.0),
        (5.0, -75.0),
        (-5.0, -75.0),
        (-half, -45.0),
        (-half, -20.0),
        (-neck, -10.0),
        (-neck, 10.0),
        (-half, 20.0),
        (-half, 50.0),
    )


def polygon_area(poly: tuple[tuple[float, float], ...] | list[tuple[float, float]]) -> float:
    return abs(
        sum(
            x0 * y1 - x1 * y0
            for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]])
        )
    ) / 2.0


def polygon_perimeter(poly: tuple[tuple[float, float], ...]) -> float:
    return sum(
        hypot(x1 - x0, y1 - y0)
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + (poly[0],))
    )


def clip_half_plane(
    poly: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    axis: int,
    value: float,
    keep_less: bool,
) -> list[tuple[float, float]]:
    def inside(point: tuple[float, float]) -> bool:
        return point[axis] <= value if keep_less else point[axis] >= value

    source = list(poly)
    result: list[tuple[float, float]] = []
    for start, end in zip(source, source[1:] + source[:1]):
        start_in = inside(start)
        end_in = inside(end)
        if start_in != end_in:
            delta = end[axis] - start[axis]
            t = (value - start[axis]) / delta
            result.append(
                (
                    start[0] + t * (end[0] - start[0]),
                    start[1] + t * (end[1] - start[1]),
                )
            )
        if end_in:
            result.append(end)
    return result


def cut_length(poly: tuple[tuple[float, float], ...], axis: int, value: float) -> float:
    other = 1 - axis
    crossings: list[float] = []
    for start, end in zip(poly, poly[1:] + (poly[0],)):
        a = start[axis]
        b = end[axis]
        if (a <= value < b) or (b <= value < a):
            t = (value - a) / (b - a)
            crossings.append(start[other] + t * (end[other] - start[other]))
    crossings.sort()
    return sum(
        crossings[index + 1] - crossings[index]
        for index in range(0, len(crossings) - 1, 2)
    )


def segment_fraction_less(
    start: tuple[float, float], end: tuple[float, float], axis: int, value: float
) -> float:
    a = start[axis]
    b = end[axis]
    if a <= value and b <= value:
        return 1.0
    if a > value and b > value:
        return 0.0
    if a == b:
        return 0.5
    crossing = (value - a) / (b - a)
    return crossing if a <= value else 1.0 - crossing


def model_mass(model: StructureModel) -> float:
    internal = sum(
        polygon_area(layer.polygon)
        * layer.effective_thickness
        * layer.material.density
        for layer in model.layers
    )
    shell = sum(
        polygon_perimeter(layer.polygon)
        * DECK_HEIGHT
        * model.armor_thickness
        * model.armor_material.density
        for layer in model.layers
    )
    return internal + shell + sum(point.mass for point in model.point_masses)


def model_hull_hp(model: StructureModel) -> float:
    internal = sum(
        polygon_area(layer.polygon)
        * layer.effective_thickness
        * layer.material.hull_hp_per_volume
        for layer in model.layers
    )
    shell = SHELL_HULL_HP_EFFICIENCY * sum(
        polygon_perimeter(layer.polygon)
        * DECK_HEIGHT
        * model.armor_thickness
        * model.armor_material.hull_hp_per_volume
        for layer in model.layers
    )
    return internal + shell


def model_total_local_armor_hp(model: StructureModel) -> float:
    return sum(
        polygon_perimeter(layer.polygon)
        * DECK_HEIGHT
        * model.armor_thickness
        * model.armor_material.hull_hp_per_volume
        for layer in model.layers
    )


def mass_less(model: StructureModel, axis: int, value: float) -> float:
    total = 0.0
    for layer in model.layers:
        clipped = clip_half_plane(layer.polygon, axis, value, True)
        if len(clipped) >= 3:
            total += (
                polygon_area(clipped)
                * layer.effective_thickness
                * layer.material.density
            )
        if model.armor_thickness > 0.0:
            for start, end in zip(layer.polygon, layer.polygon[1:] + (layer.polygon[0],)):
                edge_length = hypot(end[0] - start[0], end[1] - start[1])
                total += (
                    edge_length
                    * segment_fraction_less(start, end, axis, value)
                    * DECK_HEIGHT
                    * model.armor_thickness
                    * model.armor_material.density
                )
    for point in model.point_masses:
        coordinate = point.x if axis == 0 else point.y
        if coordinate < value:
            total += point.mass
        elif coordinate == value:
            total += point.mass / 2.0
    return total


def cut_capacity(model: StructureModel, axis: int, value: float) -> float:
    capacity = sum(
        cut_length(layer.polygon, axis, value)
        * layer.effective_thickness
        * layer.material.strength_coefficient
        * ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS
        for layer in model.layers
    )
    if model.armor_thickness <= 0.0:
        return capacity
    for layer in model.layers:
        for start, end in zip(layer.polygon, layer.polygon[1:] + (layer.polygon[0],)):
            a = start[axis]
            b = end[axis]
            if not ((a <= value < b) or (b <= value < a)):
                continue
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            edge_length = hypot(dx, dy)
            load_component = abs((dx, dy)[axis] / edge_length)
            capacity += (
                SHELL_STRENGTH_EFFICIENCY
                * model.armor_material.strength_coefficient
                * ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS
                * DECK_HEIGHT
                * model.armor_thickness
                * load_component
            )
    return capacity


def channel_safe_acceleration(model: StructureModel, axis: int) -> tuple[float, float]:
    coordinates = [point[axis] for layer in model.layers for point in layer.polygon]
    lower = min(coordinates)
    upper = max(coordinates)
    total_mass = model_mass(model)

    balance_lower = lower
    balance_upper = upper
    for _ in range(60):
        middle = (balance_lower + balance_upper) / 2.0
        if mass_less(model, axis, middle) < total_mass / 2.0:
            balance_lower = middle
        else:
            balance_upper = middle
    balance = (balance_lower + balance_upper) / 2.0

    sample_count = int(ceil((upper - lower) / GRID))
    positions = [
        lower + (index + 0.5) * GRID
        for index in range(sample_count)
        if lower + (index + 0.5) * GRID < upper
    ]
    positions.append(balance)
    positions = sorted(set(round(position, 9) for position in positions))

    samples: list[tuple[float, float, float]] = []
    for position in positions:
        negative_mass = mass_less(model, axis, position)
        transfer = min(negative_mass, total_mass - negative_mass)
        samples.append((position, cut_capacity(model, axis, position), transfer))

    valid = [
        index
        for index, (_, capacity, transfer) in enumerate(samples)
        if capacity > 0.0 and transfer / total_mass >= END_MASS_RATIO
    ]
    candidates = []
    for index in valid:
        neighborhood = [
            samples[neighbor][1]
            for neighbor in range(max(0, index - 1), min(len(samples), index + 2))
        ]
        filtered_capacity = median(neighborhood)
        position, _, transfer = samples[index]
        candidates.append((filtered_capacity / transfer, position))
    return min(candidates)


def yaw_limits(model: StructureModel, safe_long: float, safe_lat: float) -> tuple[float, float]:
    points = [point for layer in model.layers for point in layer.polygon]
    points.extend((mass.x, mass.y) for mass in model.point_masses)
    alpha_coefficients = []
    omega_squared_coefficients = []
    for x, y in points:
        alpha_coefficients.append(hypot(x / safe_long, -y / safe_lat))
        omega_squared_coefficients.append(hypot(-y / safe_long, -x / safe_lat))
    return (
        1.0 / max(alpha_coefficients),
        sqrt(1.0 / max(omega_squared_coefficients)),
    )


def evaluate(model: StructureModel) -> dict[str, float | str]:
    safe_long, long_cut = channel_safe_acceleration(model, axis=1)
    safe_lat, lat_cut = channel_safe_acceleration(model, axis=0)
    safe_alpha, safe_omega = yaw_limits(model, safe_long, safe_lat)
    return {
        "name": model.name,
        "mass": model_mass(model),
        "hp": model_hull_hp(model),
        "armor_hp": model_total_local_armor_hp(model),
        "long": safe_long,
        "lat": safe_lat,
        "alpha": safe_alpha,
        "omega": safe_omega,
        "long_cut": long_cut,
        "lat_cut": lat_cut,
    }


def print_group(title: str, models: list[StructureModel], baseline: dict[str, float | str]) -> None:
    print(f"\n### {title}\n")
    print("| 方案 | 质量指数 | 船壳耐久指数 | 纵向安全指数 | 纵向结构安全 G | 横向安全指数 | 偏航角加速度指数 | 偏航角速度指数 | 纵向瓶颈位置 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for model in models:
        result = evaluate(model)
        print(
            f"| {model.name} | "
            f"{float(result['mass'])/float(baseline['mass']):.3f} | "
            f"{float(result['hp'])/float(baseline['hp']):.3f} | "
            f"{float(result['long'])/float(baseline['long']):.3f} | "
            f"{float(result['long'])/STANDARD_GRAVITY:.3f}G | "
            f"{float(result['lat'])/float(baseline['lat']):.3f} | "
            f"{float(result['alpha'])/float(baseline['alpha']):.3f} | "
            f"{float(result['omega'])/float(baseline['omega']):.3f} | "
            f"{float(result['long_cut']):.1f}m |"
        )


def main() -> None:
    hull = standard_planform()
    upper = standard_planform(100.0, 15.0)
    baseline_model = StructureModel("单层基准舰", [Layer(hull, BASE, True)])
    baseline = evaluate(baseline_model)

    layers = [
        baseline_model,
        StructureModel(
            "同轮廓双层",
            [Layer(hull, BASE, True), Layer(hull, BASE)],
        ),
        StructureModel(
            "同轮廓三层",
            [Layer(hull, BASE, True), Layer(hull, BASE), Layer(hull, BASE)],
        ),
    ]

    baseline_mass = model_mass(baseline_model)
    waist_models = [baseline_model]
    for neck in (15.0, 10.0):
        neck_poly = hourglass_planform(neck)
        raw = StructureModel(f"{neck:.0f}m 狭腰（等质量）", [Layer(neck_poly, BASE, True)])
        ballast = baseline_mass - model_mass(raw)
        raw.point_masses.append(PointMass(0.0, 0.0, ballast))
        waist_models.append(raw)

    materials = [
        StructureModel(
            "基准下层＋基准短上层",
            [Layer(hull, BASE, True), Layer(upper, BASE)],
        ),
        StructureModel(
            "基准下层＋铝合金短上层",
            [Layer(hull, BASE, True), Layer(upper, ALUMINUM)],
        ),
        StructureModel(
            "基准下层＋钛合金短上层",
            [Layer(hull, BASE, True), Layer(upper, TITANIUM)],
        ),
        StructureModel(
            "基准下层＋碳化物短上层",
            [Layer(hull, BASE, True), Layer(upper, CARBIDE_COMPOSITE)],
        ),
        StructureModel(
            "铝合金下层＋基准短上层",
            [Layer(hull, ALUMINUM, True), Layer(upper, BASE)],
        ),
    ]

    uniform_materials = [
        baseline_model,
        StructureModel("单层铝合金舰", [Layer(hull, ALUMINUM, True)]),
        StructureModel("单层钛合金舰", [Layer(hull, TITANIUM, True)]),
        StructureModel(
            "单层碳化物复合舰", [Layer(hull, CARBIDE_COMPOSITE, True)]
        ),
        StructureModel(
            "单层混铜合金舰", [Layer(hull, MIXED_COPPER_ALLOY, True)]
        ),
        StructureModel(
            "单层霜银合金舰", [Layer(hull, FROST_SILVER_ALLOY, True)]
        ),
        StructureModel(
            "单层灵化金属纤维织物舰",
            [Layer(hull, SPIRIT_METAL_FABRIC, True)],
        ),
    ]

    armor = [
        baseline_model,
        StructureModel(
            "50mm 基准基础装甲",
            [Layer(hull, BASE, True)],
            armor_thickness=0.05,
        ),
        StructureModel(
            "100mm 基准基础装甲",
            [Layer(hull, BASE, True)],
            armor_thickness=0.10,
        ),
        StructureModel(
            "50mm 轻质碳化物复合装甲",
            [Layer(hull, BASE, True)],
            armor_thickness=0.05,
            armor_material=LIGHTWEIGHT_CARBIDE_ARMOR,
        ),
        StructureModel(
            "50mm 积层烧蚀装甲",
            [Layer(hull, BASE, True)],
            armor_thickness=0.05,
            armor_material=LAMINATED_ABLATIVE_ARMOR,
        ),
    ]

    cargo_mass = baseline_mass * 0.20
    cargo = [
        StructureModel(
            "货物位于 CIC",
            [Layer(hull, BASE, True)],
            point_masses=[PointMass(0.0, 0.0, cargo_mass)],
        ),
        StructureModel(
            "货物位于舰艏",
            [Layer(hull, BASE, True)],
            point_masses=[PointMass(0.0, 60.0, cargo_mass)],
        ),
        StructureModel(
            "货物位于舰艉",
            [Layer(hull, BASE, True)],
            point_masses=[PointMass(0.0, -60.0, cargo_mass)],
        ),
        StructureModel(
            "货物位于右舷",
            [Layer(hull, BASE, True)],
            point_masses=[PointMass(8.0, 0.0, cargo_mass)],
        ),
    ]

    print_group("叠层组", layers, baseline)
    print_group("狭腰组", waist_models, baseline)
    print_group("统一结构材质组", uniform_materials, baseline)
    print_group("混合材质组", materials, baseline)
    print_group("基础装甲组", armor, baseline)
    print("\n基础装甲的局部耐久线性校验：")
    for model in armor[1:]:
        result = evaluate(model)
        print(f"- {model.name}: 局部装甲总耐久代理 {float(result['armor_hp']):.3f}")
    print_group("货物位置组", cargo, baseline)

    print("\n### OverG 曲线与乘员安全锁\n")
    print("采用原型参数 `R_ref=1.5`、`T_ref=30s` 时：")
    print("| 结构载荷比 R | 从满耐久降至零的持续时间 |")
    print("| ---: | ---: |")
    for ratio in (1.05, 1.10, 1.25, 1.50, 2.00):
        normalized_rate = ((ratio - 1.0) / (1.5 - 1.0)) ** 2
        duration = 30.0 / normalized_rate
        print(f"| {ratio:.2f} | {duration:.1f}s |")
    print("\n乘员锁逻辑样例：请求 `20G` 时，有人舰输出 `12G`，完全无人舰仍可输出 `20G`；两者均按各自实际结构载荷比计算船壳损伤。")


if __name__ == "__main__":
    main()
