"""《高天荒野》船壳结构公式的无量纲几何原型测试。

本脚本只检验尺寸、长宽比、切面瓶颈与偏航半径的相对关系。
它不使用正式结构材质，也不输出可直接用于游戏的 G 值。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, hypot, sqrt
from statistics import median


GRID = 5.0
END_MASS_RATIO = 0.025


@dataclass(frozen=True)
class ReferenceHull:
    name: str
    real_length_m: float
    real_beam_m: float

    @property
    def grid_length_m(self) -> float:
        return round(self.real_length_m / GRID) * GRID

    @property
    def grid_beam_m(self) -> float:
        return round(self.real_beam_m / GRID) * GRID


REFERENCES = (
    ReferenceHull("弗吉尼亚级", 114.8, 10.36),
    ReferenceHull("独立级濒海战斗舰", 127.6, 31.6),
    ReferenceHull("阿利·伯克级 Flight IIA/III", 155.29, 18.0),
    ReferenceHull("朱姆沃尔特级", 185.93, 24.60),
    ReferenceHull("圣安东尼奥级", 208.5, 31.9),
    ReferenceHull("衣阿华级（密苏里号数据）", 270.43, 32.97),
    ReferenceHull("尼米兹级（水线舰宽）", 332.85, 40.84),
)


def quantize_half_grid(value: float) -> float:
    """关于 CIC 中心轴对称时，边界可以落在 2.5 米半格坐标。"""

    return round(value / (GRID / 2.0)) * (GRID / 2.0)


def standard_planform(length: float, beam: float) -> list[tuple[float, float]]:
    """生成统一的轴对称单体舰平面模板，避免真实舰艏细节干扰对比。"""

    cells = int(round(length / GRID))
    aft_cells = cells // 2
    fore_cells = cells - aft_cells
    y_min = -aft_cells * GRID
    y_max = fore_cells * GRID
    taper = round(0.20 * length / GRID) * GRID
    half_beam = beam / 2.0
    stern_half_beam = max(GRID / 2.0, quantize_half_grid(beam * 0.25))
    return [
        (0.0, y_max),
        (half_beam, y_max - taper),
        (half_beam, y_min + taper),
        (stern_half_beam, y_min),
        (-stern_half_beam, y_min),
        (-half_beam, y_min + taper),
        (-half_beam, y_max - taper),
    ]


def polygon_area(poly: list[tuple[float, float]]) -> float:
    return abs(
        sum(
            x0 * y1 - x1 * y0
            for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1])
        )
    ) / 2.0


def clip_half_plane(
    poly: list[tuple[float, float]], axis: int, value: float, keep_less: bool
) -> list[tuple[float, float]]:
    """Sutherland–Hodgman：裁出坐标小于或大于给定切线的一侧。"""

    def inside(point: tuple[float, float]) -> bool:
        return point[axis] <= value if keep_less else point[axis] >= value

    result: list[tuple[float, float]] = []
    for start, end in zip(poly, poly[1:] + poly[:1]):
        start_in = inside(start)
        end_in = inside(end)
        if start_in != end_in:
            delta = end[axis] - start[axis]
            t = (value - start[axis]) / delta
            intersection = (
                start[0] + t * (end[0] - start[0]),
                start[1] + t * (end[1] - start[1]),
            )
            result.append(intersection)
        if end_in:
            result.append(end)
    return result


def cut_length(poly: list[tuple[float, float]], axis: int, value: float) -> float:
    """计算切线穿过多边形的线段总长；半格采样避免恰好穿过顶点。"""

    other = 1 - axis
    crossings: list[float] = []
    for start, end in zip(poly, poly[1:] + poly[:1]):
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


def channel_safe_acceleration(
    poly: list[tuple[float, float]], axis: int
) -> tuple[float, float]:
    """使用单位密度、单位有效厚度、单位允许应力计算无量纲安全值。"""

    coordinates = [point[axis] for point in poly]
    lower = min(coordinates)
    upper = max(coordinates)
    total_mass = polygon_area(poly)
    sample_count = int(ceil((upper - lower) / GRID))
    # 五米区间中点可能漏过窄舰的中心控制切面，因此每个轴额外加入
    # 一条将结构质量平分的切面。它也能覆盖舾装后质量分布偏移的情形。
    balance_lower = lower
    balance_upper = upper
    for _ in range(60):
        balance_mid = (balance_lower + balance_upper) / 2.0
        negative = clip_half_plane(poly, axis, balance_mid, True)
        negative_mass = polygon_area(negative) if len(negative) >= 3 else 0.0
        if negative_mass < total_mass / 2.0:
            balance_lower = balance_mid
        else:
            balance_upper = balance_mid
    balance_position = (balance_lower + balance_upper) / 2.0

    positions = [
        lower + (index + 0.5) * GRID
        for index in range(sample_count)
        if lower + (index + 0.5) * GRID < upper
    ]
    positions.append(balance_position)
    positions = sorted(set(round(position, 9) for position in positions))
    samples: list[tuple[float, float, float]] = []
    for position in positions:
        negative = clip_half_plane(poly, axis, position, True)
        negative_mass = polygon_area(negative) if len(negative) >= 3 else 0.0
        transfer_mass = min(negative_mass, total_mass - negative_mass)
        capacity = cut_length(poly, axis, position)
        samples.append((position, capacity, transfer_mass))

    valid_indices = [
        index
        for index, (_, capacity, transfer) in enumerate(samples)
        if capacity > 0.0 and transfer / total_mass >= END_MASS_RATIO
    ]
    candidates: list[tuple[float, float]] = []
    for index in valid_indices:
        neighborhood = [
            samples[neighbor][1]
            for neighbor in range(max(0, index - 1), min(len(samples), index + 2))
        ]
        filtered_capacity = median(neighborhood)
        position, _, transfer_mass = samples[index]
        candidates.append((filtered_capacity / transfer_mass, position))
    return min(candidates)


def yaw_limits(
    poly: list[tuple[float, float]], safe_long: float, safe_lat: float
) -> tuple[float, float]:
    """分别求 omega=0 时的安全角加速度和 alpha=0 时的安全角速度。"""

    alpha_coefficients = []
    omega_squared_coefficients = []
    for x, y in poly:
        alpha_ax = -y
        alpha_ay = x
        alpha_coefficients.append(
            hypot(alpha_ay / safe_long, alpha_ax / safe_lat)
        )
        centripetal_ax = -x
        centripetal_ay = -y
        omega_squared_coefficients.append(
            hypot(centripetal_ay / safe_long, centripetal_ax / safe_lat)
        )
    safe_alpha = 1.0 / max(alpha_coefficients)
    safe_omega = sqrt(1.0 / max(omega_squared_coefficients))
    return safe_alpha, safe_omega


def main() -> None:
    results = []
    for reference in REFERENCES:
        poly = standard_planform(reference.grid_length_m, reference.grid_beam_m)
        safe_long, long_position = channel_safe_acceleration(poly, axis=1)
        safe_lat, lat_position = channel_safe_acceleration(poly, axis=0)
        safe_alpha, safe_omega = yaw_limits(poly, safe_long, safe_lat)
        results.append(
            {
                "reference": reference,
                "area": polygon_area(poly),
                "safe_long": safe_long,
                "safe_lat": safe_lat,
                "lat_long_ratio": safe_lat / safe_long,
                "safe_alpha": safe_alpha,
                "safe_omega": safe_omega,
                "long_position": long_position,
                "lat_position": lat_position,
            }
        )

    baseline = next(
        result for result in results if result["reference"].name.startswith("阿利·伯克")
    )
    print(
        "| 参考舰型 | 五米网格尺寸 | 网格长宽比 | 模板面积 | "
        "纵向安全指数 | 横/纵安全比 | 偏航角加速度指数 | 偏航角速度指数 |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for result in results:
        reference = result["reference"]
        print(
            f"| {reference.name} | {reference.grid_length_m:.0f}×{reference.grid_beam_m:.0f} m | "
            f"{reference.grid_length_m/reference.grid_beam_m:.2f} | {result['area']:.0f} m² | "
            f"{result['safe_long']/baseline['safe_long']:.3f} | "
            f"{result['lat_long_ratio']:.2f} | "
            f"{result['safe_alpha']/baseline['safe_alpha']:.3f} | "
            f"{result['safe_omega']/baseline['safe_omega']:.3f} |"
        )


if __name__ == "__main__":
    main()
