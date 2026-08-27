"""《高天荒野》舰艇雷达反射截面积第一版代理公式原型。

本脚本验证方向性、拆边不变性、船体涂料缩放、外挂模块线性叠加和雷达方程
四次方根接口。它不是全波电磁仿真，也不尝试复现某一条现实舰艇的实测 RCS。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, log10, pi, radians, sin
from typing import Iterable


EPS = 1.0e-9


@dataclass(frozen=True)
class RCSParameters:
    reference_wavelength_m: float = 0.10
    coherent_area_m2: float = 25.0
    specular_scale: float = 0.001
    specular_exponent: float = 4.0
    diffuse_scale: float = 0.50
    corner_scale: float = 8.0
    deck_height_m: float = 5.0
    corner_length_cap_m: float = 5.0


@dataclass(frozen=True)
class ExternalModuleRCS:
    name: str
    base_rcs_m2: float
    direction_multiplier: float = 1.0
    state_multiplier: float = 1.0

    @property
    def rcs_m2(self) -> float:
        return self.base_rcs_m2 * self.direction_multiplier * self.state_multiplier


@dataclass(frozen=True)
class VisibleFacet:
    edge_index: int
    area_m2: float
    facing_cosine: float


@dataclass(frozen=True)
class HullRCSResult:
    specular_m2: float
    diffuse_m2: float
    corner_m2: float

    @property
    def total_m2(self) -> float:
        return self.specular_m2 + self.diffuse_m2 + self.corner_m2


Point = tuple[float, float]


def subtract(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def length(v: Point) -> float:
    return (v[0] ** 2 + v[1] ** 2) ** 0.5


def signed_area(vertices: list[Point]) -> float:
    return 0.5 * sum(
        cross(vertices[index], vertices[(index + 1) % len(vertices)])
        for index in range(len(vertices))
    )


def normalize_polygon(vertices: Iterable[Point]) -> list[Point]:
    """统一绕序并合并所有共线连续边；装甲材质与厚度均不参与 RCS。"""

    result = [(float(vertex[0]), float(vertex[1])) for vertex in vertices]
    if len(result) > 1 and length(subtract(result[0], result[-1])) <= EPS:
        result.pop()
    if len(result) < 3:
        raise ValueError("船壳多边形至少需要三个不同端点")
    if any(
        length(subtract(result[(index + 1) % len(result)], result[index])) <= EPS
        for index in range(len(result))
    ):
        raise ValueError("船壳包含重复端点或零长度边")

    changed = True
    while changed:
        changed = False
        count = len(result)
        for index in range(count):
            current = result[index]
            previous = result[(index - 1) % count]
            following = result[(index + 1) % count]
            a = subtract(current, previous)
            b = subtract(following, current)
            if abs(cross(a, b)) <= EPS and dot(a, b) >= 0.0:
                result.pop(index)
                changed = True
                break
        if len(result) < 3:
            raise ValueError("规范化后船壳多边形退化")

    if signed_area(result) < 0.0:
        result.reverse()
    return result


def view_axes(bearing_deg: float) -> tuple[Point, Point]:
    """0°为舰艏（+Y），90°为右舷（+X）；s 指向目标到雷达。"""

    angle = radians(bearing_deg)
    sight = (sin(angle), cos(angle))
    screen = (cos(angle), -sin(angle))
    return sight, screen


def edge_geometry(vertices: list[Point], index: int) -> tuple[Point, Point, Point, float]:
    start = vertices[index]
    end = vertices[(index + 1) % len(vertices)]
    tangent = subtract(end, start)
    edge_length = length(tangent)
    if edge_length <= EPS:
        raise ValueError("船壳包含零长度边")
    # 逆时针多边形的外法线为边切向的右法线。
    normal = (tangent[1] / edge_length, -tangent[0] / edge_length)
    return start, end, normal, edge_length


def visible_facets(vertices: list[Point], bearing_deg: float, deck_height_m: float) -> list[VisibleFacet]:
    """用正交投影深度缓冲求凹多边形在水平方向上的可见边段。"""

    sight, screen = view_axes(bearing_deg)
    candidates: list[dict[str, float | int]] = []
    events: set[float] = set()

    for index in range(len(vertices)):
        start, end, normal, _ = edge_geometry(vertices, index)
        mu = dot(normal, sight)
        if mu <= EPS:
            continue
        q0, q1 = dot(start, screen), dot(end, screen)
        d0, d1 = dot(start, sight), dot(end, sight)
        if abs(q1 - q0) <= EPS:
            continue
        candidates.append(
            {
                "index": index,
                "mu": mu,
                "q0": q0,
                "q1": q1,
                "d0": d0,
                "d1": d1,
            }
        )
        events.add(q0)
        events.add(q1)

    visible_width: dict[int, float] = {}
    ordered = sorted(events)
    for left, right in zip(ordered, ordered[1:]):
        if right - left <= EPS:
            continue
        midpoint = 0.5 * (left + right)
        front_index: int | None = None
        front_depth = float("-inf")
        for candidate in candidates:
            q0 = float(candidate["q0"])
            q1 = float(candidate["q1"])
            if midpoint < min(q0, q1) - EPS or midpoint > max(q0, q1) + EPS:
                continue
            fraction = (midpoint - q0) / (q1 - q0)
            depth = float(candidate["d0"]) + fraction * (
                float(candidate["d1"]) - float(candidate["d0"])
            )
            if depth > front_depth:
                front_depth = depth
                front_index = int(candidate["index"])
        if front_index is not None:
            visible_width[front_index] = visible_width.get(front_index, 0.0) + right - left

    facets: list[VisibleFacet] = []
    for candidate in candidates:
        index = int(candidate["index"])
        width = visible_width.get(index, 0.0)
        mu = float(candidate["mu"])
        if width <= EPS:
            continue
        visible_length = width / mu
        facets.append(VisibleFacet(index, visible_length * deck_height_m, mu))
    return facets


def hull_rcs(
    raw_vertices: Iterable[Point],
    bearing_deg: float,
    coating_multiplier: float = 1.0,
    parameters: RCSParameters = RCSParameters(),
) -> HullRCSResult:
    if coating_multiplier <= 0.0:
        raise ValueError("船体涂料 RCS 倍率必须大于零")
    vertices = normalize_polygon(raw_vertices)
    facets = visible_facets(vertices, bearing_deg, parameters.deck_height_m)
    visible_by_edge = {facet.edge_index: facet for facet in facets}

    specular = 0.0
    diffuse = 0.0
    for facet in facets:
        area = facet.area_m2
        mu = facet.facing_cosine
        coherence_limited_area_squared = area**2 / (
            1.0 + area / parameters.coherent_area_m2
        )
        ideal_plate_scale = 4.0 * pi / parameters.reference_wavelength_m**2
        specular += (
            parameters.specular_scale
            * coating_multiplier
            * ideal_plate_scale
            * coherence_limited_area_squared
            * mu**parameters.specular_exponent
        )
        # A*mu 等于该可见表面对视线的投影面积，作为边缘、粗糙度和非相干回波底项。
        diffuse += parameters.diffuse_scale * coating_multiplier * area * mu

    corner = 0.0
    for index in range(len(vertices)):
        previous_index = (index - 1) % len(vertices)
        next_index = index
        previous_start, previous_end, previous_normal, previous_length = edge_geometry(
            vertices, previous_index
        )
        next_start, next_end, next_normal, next_length = edge_geometry(vertices, next_index)
        previous_tangent = subtract(previous_end, previous_start)
        next_tangent = subtract(next_end, next_start)
        turn = cross(previous_tangent, next_tangent) / (previous_length * next_length)
        if turn >= -EPS:
            continue
        if previous_index not in visible_by_edge or next_index not in visible_by_edge:
            continue
        sight, _ = view_axes(bearing_deg)
        mu_previous = max(0.0, dot(previous_normal, sight))
        mu_next = max(0.0, dot(next_normal, sight))
        geometry_factor = min(1.0, -turn)
        view_factor = min(1.0, 4.0 * mu_previous * mu_next)
        corner_area = parameters.deck_height_m * min(
            previous_length, next_length, parameters.corner_length_cap_m
        )
        corner += (
            parameters.corner_scale
            * coating_multiplier
            * corner_area
            * geometry_factor
            * view_factor
        )

    return HullRCSResult(specular, diffuse, corner)


def total_rcs(
    vertices: Iterable[Point],
    bearing_deg: float,
    coating_multiplier: float = 1.0,
    modules: Iterable[ExternalModuleRCS] = (),
    parameters: RCSParameters = RCSParameters(),
) -> float:
    hull = hull_rcs(
        vertices,
        bearing_deg,
        coating_multiplier=coating_multiplier,
        parameters=parameters,
    )
    return hull.total_m2 + sum(module.rcs_m2 for module in modules)


def dbsm(rcs_m2: float) -> float:
    return 10.0 * log10(max(rcs_m2, 1.0e-12))


def range_ratio(rcs_ratio: float) -> float:
    return rcs_ratio ** 0.25


def layered_total_rcs(
    layer_polygons: Iterable[Iterable[Point]],
    bearing_deg: float,
    coating_multiplier: float = 1.0,
    modules: Iterable[ExternalModuleRCS] = (),
    parameters: RCSParameters = RCSParameters(),
) -> float:
    """原型中的每项代表一个独立五米高度带；外挂模块只在全舰层级增加一次。"""

    hull_total = sum(
        hull_rcs(
            polygon,
            bearing_deg,
            coating_multiplier=coating_multiplier,
            parameters=parameters,
        ).total_m2
        for polygon in layer_polygons
    )
    return hull_total + sum(module.rcs_m2 for module in modules)


def build_direction_table(vertices: Iterable[Point]) -> list[float]:
    return [total_rcs(vertices, bearing) for bearing in range(360)]


def interpolate_direction_table(table: list[float], bearing_deg: float) -> float:
    if len(table) != 360:
        raise ValueError("第一版 RCS 水平方位表必须恰好包含 360 项")
    wrapped = bearing_deg % 360.0
    lower = int(wrapped)
    upper = (lower + 1) % 360
    fraction = wrapped - lower
    return table[lower] * (1.0 - fraction) + table[upper] * fraction


FLAT_BOW = [(-10.0, -77.5), (10.0, -77.5), (10.0, 77.5), (-10.0, 77.5)]
SPLIT_FLAT_BOW = [
    (-10.0, -77.5),
    (0.0, -77.5),
    (10.0, -77.5),
    (10.0, 0.0),
    (10.0, 77.5),
    (0.0, 77.5),
    (-10.0, 77.5),
    (-10.0, 0.0),
]
POINTED_BOW = [
    (-10.0, -77.5),
    (10.0, -77.5),
    (10.0, 50.0),
    (0.0, 77.5),
    (-10.0, 50.0),
]
POINTED_BOTH = [(0.0, -77.5), (10.0, 0.0), (0.0, 77.5), (-10.0, 0.0)]
FORWARD_NOTCH = [
    (-15.0, -60.0),
    (15.0, -60.0),
    (15.0, 60.0),
    (5.0, 60.0),
    (5.0, 40.0),
    (-5.0, 40.0),
    (-5.0, 60.0),
    (-15.0, 60.0),
]
NARROW_UPPER = [(-7.5, -60.0), (7.5, -60.0), (7.5, 60.0), (-7.5, 60.0)]
DOUBLE_SCALE_FLAT_BOW = [
    (-20.0, -155.0),
    (20.0, -155.0),
    (20.0, 155.0),
    (-20.0, 155.0),
]


def run_checks() -> None:
    # 绘制不变性：共线拆边必须在规范化后给出完全相同的方向表。
    for bearing in range(360):
        a = total_rcs(FLAT_BOW, bearing)
        b = total_rcs(SPLIT_FLAT_BOW, bearing)
        assert abs(a - b) <= max(1.0, a) * 1.0e-10

    # 轴对称船壳的左右方向一致。
    for bearing in range(181):
        left = total_rcs(POINTED_BOW, bearing)
        right = total_rcs(POINTED_BOW, (-bearing) % 360)
        assert abs(left - right) <= max(1.0, left) * 1.0e-10

    assert total_rcs(POINTED_BOW, 0.0) < total_rcs(FLAT_BOW, 0.0)
    assert total_rcs(POINTED_BOTH, 0.0) < total_rcs(POINTED_BOW, 0.0)
    assert total_rcs(FLAT_BOW, 90.0) > total_rcs(FLAT_BOW, 0.0)

    baseline = total_rcs(POINTED_BOW, 35.0, coating_multiplier=1.0)
    low_reflection = total_rcs(POINTED_BOW, 35.0, coating_multiplier=0.5)
    assert abs(low_reflection / baseline - 0.5) < 1.0e-12

    modules = (
        ExternalModuleRCS("搜索雷达", 180.0),
        ExternalModuleRCS("炮塔", 45.0, direction_multiplier=0.8),
    )
    with_modules = total_rcs(POINTED_BOW, 35.0, modules=modules)
    assert abs(with_modules - baseline - 216.0) < 1.0e-9

    # 隐身涂料只缩放船壳表面项，不自动隐藏雷达、炮塔等外部模块。
    coated_with_modules = total_rcs(
        POINTED_BOW, 35.0, coating_multiplier=0.5, modules=modules
    )
    assert abs(coated_with_modules - 0.5 * baseline - 216.0) < 1.0e-9

    no_corner = RCSParameters(corner_scale=0.0)
    assert total_rcs(FORWARD_NOTCH, 0.0) >= total_rcs(
        FORWARD_NOTCH, 0.0, parameters=no_corner
    )

    # 每层固定五米高并独立形成表面组；增加受支撑甲板必然增加 RCS。
    for bearing in (0.0, 30.0, 90.0, 180.0):
        single = total_rcs(FLAT_BOW, bearing)
        double = layered_total_rcs((FLAT_BOW, FLAT_BOW), bearing)
        stepped = layered_total_rcs((FLAT_BOW, NARROW_UPPER), bearing)
        assert abs(double - 2.0 * single) < 1.0e-9
        assert single < stepped < double

    # 舰体尺度扩大后，方向特征保持单调增加。
    for bearing in range(360):
        assert total_rcs(DOUBLE_SCALE_FLAT_BOW, bearing) > total_rcs(
            FLAT_BOW, bearing
        )

    # 运行时线性插值跨越 359°/0° 时仍连续且不会越出相邻样本范围。
    direction_table = build_direction_table(POINTED_BOW)
    for bearing in (0.25, 17.5, 89.9, 180.125, 359.75):
        wrapped = bearing % 360.0
        lower = int(wrapped)
        upper = (lower + 1) % 360
        interpolated = interpolate_direction_table(direction_table, bearing)
        assert min(direction_table[lower], direction_table[upper]) <= interpolated
        assert interpolated <= max(direction_table[lower], direction_table[upper])

    assert abs(range_ratio(4.0) - 2.0**0.5) < 1.0e-12
    assert abs(range_ratio(100.0) - 10.0**0.5) < 1.0e-12


def print_report() -> None:
    hulls = {
        "平艏平艉": FLAT_BOW,
        "尖艏平艉": POINTED_BOW,
        "尖艏尖艉": POINTED_BOTH,
        "前向凹槽": FORWARD_NOTCH,
    }
    bearings = (0, 15, 30, 45, 60, 90, 135, 180)
    print("第一版参数：", RCSParameters())
    print("\n船壳方向性 RCS（m² / dBsm）")
    print("船壳        " + "  ".join(f"{bearing:>6}°" for bearing in bearings))
    for name, vertices in hulls.items():
        cells = []
        for bearing in bearings:
            sigma = total_rcs(vertices, bearing)
            cells.append(f"{sigma:7.0f}/{dbsm(sigma):4.1f}")
        print(f"{name:<10}" + "  ".join(cells))

    print("\n全方向统计")
    for name, vertices in hulls.items():
        values = [total_rcs(vertices, bearing) for bearing in range(360)]
        mean = sum(values) / len(values)
        print(
            f"{name:<10} 最小 {min(values):9.1f} m² ({dbsm(min(values)):5.1f} dBsm)  "
            f"均值 {mean:9.1f} m² ({dbsm(mean):5.1f} dBsm)  "
            f"最大 {max(values):9.1f} m² ({dbsm(max(values)):5.1f} dBsm)"
        )

    print("\n多层与尺度检查（0° / 90°）")
    layered_hulls = {
        "单层基准": (FLAT_BOW,),
        "同形双层": (FLAT_BOW, FLAT_BOW),
        "收窄上层": (FLAT_BOW, NARROW_UPPER),
    }
    for name, layers in layered_hulls.items():
        forward = layered_total_rcs(layers, 0.0)
        broadside = layered_total_rcs(layers, 90.0)
        print(
            f"{name:<10} 正前 {forward:9.1f} m² ({dbsm(forward):5.1f} dBsm)  "
            f"右舷 {broadside:9.1f} m² ({dbsm(broadside):5.1f} dBsm)"
        )
    for bearing in (0.0, 90.0):
        baseline = total_rcs(FLAT_BOW, bearing)
        enlarged = total_rcs(DOUBLE_SCALE_FLAT_BOW, bearing)
        print(
            f"平面长宽放大两倍 {bearing:>4.0f}°：RCS × {enlarged / baseline:.3f}，"
            f"探测距离 × {range_ratio(enlarged / baseline):.3f}"
        )

    print("\n雷达方程四次方根检查")
    for ratio in (0.25, 0.5, 2.0, 4.0, 10.0, 100.0):
        print(f"RCS × {ratio:>6g} -> 同雷达最大探测距离 × {range_ratio(ratio):.4f}")


if __name__ == "__main__":
    run_checks()
    print_report()
