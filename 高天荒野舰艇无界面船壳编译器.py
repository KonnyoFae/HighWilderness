"""《高天荒野》v1alpha1 无界面船壳编译器。

支持唯一基底层、连续多层、上层多个分离区域、逐层完整支撑和整船轴对称。
所有派生量都来自 HullBlueprint 与 MaterialCatalog，不接受手填整舰质量、
惯量、安装空间或结构安全值。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, floor, hypot, sqrt
from statistics import median
from typing import Any

from 高天荒野舰艇RCS缓存 import HullRCSCache, build_hull_rcs_cache
from 高天荒野舰艇气动缓存 import (
    AerodynamicGeometryCache,
    build_aerodynamic_geometry_cache,
)
from 高天荒野舰艇数据契约 import (
    BaseArmorMaterial,
    ContractError,
    DeckInput,
    EdgeArmorInput,
    HullBlueprintInput,
    HullRegionInput,
    MaterialRegistry,
    Point,
    StructureMaterial,
    canonical_sha256,
)


EPS = 1.0e-9
HALF_GRID_M = 2.5
DECK_EQUIVALENT_THICKNESS_M = 0.10
JOINT_EQUIVALENT_THICKNESS_M = 0.02
DECK_HEIGHT_M = 5.0
END_MASS_RATIO = 0.025
SHELL_STRENGTH_EFFICIENCY = 0.25
SHELL_HULL_HP_EFFICIENCY = 0.25
ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS_PA = 40_800_567.325
STANDARD_GRAVITY_MPS2 = 9.80665
HULL_COMPILER_INTERFACE_ID = "gaotian.hull-compiler/v1alpha1"


def _round_point(point: Point) -> Point:
    return round(point[0], 9), round(point[1], 9)


def _edge_key(start: Point, end: Point) -> tuple[Point, Point]:
    a = _round_point(start)
    b = _round_point(end)
    return (a, b) if a <= b else (b, a)


def _mirror_edge_key(key: tuple[Point, Point]) -> tuple[Point, Point]:
    return _edge_key((-key[0][0], key[0][1]), (-key[1][0], key[1][1]))


def signed_polygon_area(vertices: tuple[Point, ...] | list[Point]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0
        for (x0, y0), (x1, y1) in zip(vertices, list(vertices[1:]) + [vertices[0]])
    )


def polygon_area(vertices: tuple[Point, ...] | list[Point]) -> float:
    return abs(signed_polygon_area(vertices))


def polygon_perimeter(vertices: tuple[Point, ...]) -> float:
    return sum(
        hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(vertices, vertices[1:] + (vertices[0],))
    )


def polygon_polar_area_moment(vertices: tuple[Point, ...]) -> float:
    """简单多边形围绕 CIC 原点的面积极惯性。"""

    result = 0.0
    for (x0, y0), (x1, y1) in zip(vertices, vertices[1:] + (vertices[0],)):
        cross = x0 * y1 - x1 * y0
        result += cross * (
            x0 * x0
            + x0 * x1
            + x1 * x1
            + y0 * y0
            + y0 * y1
            + y1 * y1
        )
    return abs(result) / 12.0


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        abs(_orientation(a, b, p)) <= EPS
        and min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
        and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if ((o1 > EPS and o2 < -EPS) or (o1 < -EPS and o2 > EPS)) and (
        (o3 > EPS and o4 < -EPS) or (o3 < -EPS and o4 > EPS)
    ):
        return True
    return any(
        (
            abs(value) <= EPS and _on_segment(start, end, point)
            for value, start, end, point in (
                (o1, a, b, c),
                (o2, a, b, d),
                (o3, c, d, a),
                (o4, c, d, b),
            )
        )
    )


def validate_simple_polygon(vertices: tuple[Point, ...], path: str) -> None:
    count = len(vertices)
    if count < 3:
        raise ContractError("hull.polygon_degenerate", path, "船壳多边形退化")
    if vertices[0] == vertices[-1]:
        raise ContractError("hull.duplicate_closing_vertex", path, "不得重复保存闭合端点")
    for index, (start, end) in enumerate(zip(vertices, vertices[1:] + (vertices[0],))):
        if hypot(end[0] - start[0], end[1] - start[1]) <= EPS:
            raise ContractError("hull.zero_length_edge", f"{path}.edge[{index}]", "零长度边")
    for first in range(count):
        a = vertices[first]
        b = vertices[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count, (first - 1) % count}:
                continue
            if first == 0 and second == count - 1:
                continue
            c = vertices[second]
            d = vertices[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise ContractError(
                    "hull.polygon_self_intersection",
                    path,
                    f"边 {first} 与边 {second} 相交或接触",
                )
    if polygon_area(vertices) <= EPS:
        raise ContractError("hull.polygon_degenerate", path, "船壳多边形退化")


def point_inside_polygon(point: Point, vertices: tuple[Point, ...]) -> bool:
    for start, end in zip(vertices, vertices[1:] + (vertices[0],)):
        if _on_segment(start, end, point):
            return False
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(vertices, vertices[1:] + (vertices[0],)):
        if (y0 > y) != (y1 > y):
            crossing_x = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if crossing_x > x:
                inside = not inside
    return inside


def point_inside_or_on_polygon(point: Point, vertices: tuple[Point, ...]) -> bool:
    if any(
        _on_segment(start, end, point)
        for start, end in zip(vertices, vertices[1:] + (vertices[0],))
    ):
        return True
    return point_inside_polygon(point, vertices)


def _segment_intersects_open_rectangle(
    start: Point,
    end: Point,
    minimum_x: float,
    maximum_x: float,
    minimum_y: float,
    maximum_y: float,
) -> bool:
    """判断船壳边是否穿过格子内部；仅接触格边或格角不算。"""

    shrink = 1.0e-8
    bounds = (
        (minimum_x + shrink, maximum_x - shrink, start[0], end[0] - start[0]),
        (minimum_y + shrink, maximum_y - shrink, start[1], end[1] - start[1]),
    )
    lower_t = 0.0
    upper_t = 1.0
    for lower, upper, origin, delta in bounds:
        if lower >= upper:
            return False
        if abs(delta) <= EPS:
            if not (lower < origin < upper):
                return False
            continue
        first = (lower - origin) / delta
        second = (upper - origin) / delta
        if first > second:
            first, second = second, first
        lower_t = max(lower_t, first)
        upper_t = min(upper_t, second)
        if lower_t > upper_t + EPS:
            return False
    return lower_t <= upper_t + EPS


def cell_is_fully_covered(
    cell: tuple[int, int], vertices: tuple[Point, ...], cell_size_m: float = 5.0
) -> bool:
    center_x = cell[0] * cell_size_m
    center_y = cell[1] * cell_size_m
    half = 0.5 * cell_size_m
    minimum_x = center_x - half
    maximum_x = center_x + half
    minimum_y = center_y - half
    maximum_y = center_y + half
    corners = (
        (minimum_x, minimum_y),
        (maximum_x, minimum_y),
        (maximum_x, maximum_y),
        (minimum_x, maximum_y),
    )
    if not point_inside_or_on_polygon((center_x, center_y), vertices):
        return False
    if not all(point_inside_or_on_polygon(point, vertices) for point in corners):
        return False
    return not any(
        _segment_intersects_open_rectangle(
            start,
            end,
            minimum_x,
            maximum_x,
            minimum_y,
            maximum_y,
        )
        for start, end in zip(vertices, vertices[1:] + (vertices[0],))
    )


def generate_strict_internal_cells(
    vertices: tuple[Point, ...], cell_size_m: float = 5.0
) -> tuple[tuple[int, int], ...]:
    """生成被船壳完整覆盖的格；返回顺序为由舰艉到舰艏、由左到右。"""

    half = 0.5 * cell_size_m
    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    minimum_x_index = ceil((min(xs) + half - EPS) / cell_size_m)
    maximum_x_index = floor((max(xs) - half + EPS) / cell_size_m)
    minimum_y_index = ceil((min(ys) + half - EPS) / cell_size_m)
    maximum_y_index = floor((max(ys) - half + EPS) / cell_size_m)
    cells = [
        (x_index, y_index)
        for y_index in range(minimum_y_index, maximum_y_index + 1)
        for x_index in range(minimum_x_index, maximum_x_index + 1)
        if cell_is_fully_covered((x_index, y_index), vertices, cell_size_m)
    ]
    return tuple(cells)


def cell_has_positive_overlap(
    cell: tuple[int, int], vertices: tuple[Point, ...], cell_size_m: float = 5.0
) -> bool:
    """判断船壳是否占用格子上方的任何正面积；仅边界接触不算覆盖。"""

    center_x = cell[0] * cell_size_m
    center_y = cell[1] * cell_size_m
    half = 0.5 * cell_size_m
    minimum_x = center_x - half
    maximum_x = center_x + half
    minimum_y = center_y - half
    maximum_y = center_y + half
    corners = (
        (minimum_x, minimum_y),
        (maximum_x, minimum_y),
        (maximum_x, maximum_y),
        (minimum_x, maximum_y),
    )
    if any(point_inside_polygon(corner, vertices) for corner in corners):
        return True
    if any(
        minimum_x + EPS < x < maximum_x - EPS
        and minimum_y + EPS < y < maximum_y - EPS
        for x, y in vertices
    ):
        return True
    if point_inside_polygon((center_x, center_y), vertices):
        return True
    return any(
        _segment_intersects_open_rectangle(
            start,
            end,
            minimum_x,
            maximum_x,
            minimum_y,
            maximum_y,
        )
        for start, end in zip(vertices, vertices[1:] + (vertices[0],))
    )


def normalize_region(region: HullRegionInput, path: str) -> HullRegionInput:
    vertices = tuple(_round_point(point) for point in region.vertices_m)
    for index, point in enumerate(vertices):
        for component_index, component in enumerate(point):
            quotient = component / HALF_GRID_M
            if abs(quotient - round(quotient)) > EPS:
                raise ContractError(
                    "hull.coordinate_off_half_grid",
                    f"{path}.vertices_m[{index}][{component_index}]",
                    "端点必须落在 2.5m 半格步长",
                )
    validate_simple_polygon(vertices, path)

    armor_by_edge: dict[tuple[Point, Point], EdgeArmorInput] = {}
    for start, end, armor in zip(vertices, vertices[1:] + (vertices[0],), region.edge_armor):
        key = _edge_key(start, end)
        if key in armor_by_edge:
            raise ContractError("hull.duplicate_edge", path, f"重复边 {key}")
        armor_by_edge[key] = armor

    ordered = list(vertices)
    if signed_polygon_area(ordered) < 0.0:
        ordered.reverse()
    first_index = min(range(len(ordered)), key=lambda index: ordered[index])
    ordered = ordered[first_index:] + ordered[:first_index]
    normalized_vertices = tuple(ordered)
    normalized_armor = tuple(
        armor_by_edge[_edge_key(start, end)]
        for start, end in zip(
            normalized_vertices, normalized_vertices[1:] + (normalized_vertices[0],)
        )
    )
    return replace(region, vertices_m=normalized_vertices, edge_armor=normalized_armor)


def validate_regions_y_symmetry(
    regions: tuple[HullRegionInput, ...], path: str
) -> None:
    edge_armor = {
        _edge_key(start, end): armor
        for region in regions
        for start, end, armor in zip(
            region.vertices_m,
            region.vertices_m[1:] + (region.vertices_m[0],),
            region.edge_armor,
        )
    }
    edge_count = sum(len(region.vertices_m) for region in regions)
    if len(edge_armor) != edge_count:
        raise ContractError("hull.regions_touch", path, "同层区域存在重合边")
    for key, armor in edge_armor.items():
        mirrored = _mirror_edge_key(key)
        if mirrored not in edge_armor:
            raise ContractError("hull.symmetry_geometry", path, f"边 {key} 没有镜像边")
        mirror_armor = edge_armor[mirrored]
        if (
            armor.material != mirror_armor.material
            or abs(armor.thickness_m - mirror_armor.thickness_m) > EPS
        ):
            raise ContractError("hull.symmetry_armor", path, f"边 {key} 的装甲不对称")


def _segments_properly_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    return (
        ((o1 > EPS and o2 < -EPS) or (o1 < -EPS and o2 > EPS))
        and ((o3 > EPS and o4 < -EPS) or (o3 < -EPS and o4 > EPS))
    )


def polygons_overlap_or_touch(
    first: tuple[Point, ...], second: tuple[Point, ...]
) -> bool:
    if any(
        _segments_intersect(first_start, first_end, second_start, second_end)
        for first_start, first_end in zip(first, first[1:] + (first[0],))
        for second_start, second_end in zip(second, second[1:] + (second[0],))
    ):
        return True
    return point_inside_polygon(first[0], second) or point_inside_polygon(second[0], first)


def validate_regions_separated(
    regions: tuple[HullRegionInput, ...], path: str
) -> None:
    for first_index, first in enumerate(regions):
        for second_index in range(first_index + 1, len(regions)):
            second = regions[second_index]
            if polygons_overlap_or_touch(first.vertices_m, second.vertices_m):
                raise ContractError(
                    "hull.regions_overlap_or_touch",
                    path,
                    f"区域 {first.id} 与 {second.id} 重叠或接触，应合并为一个连续区域",
                )


def polygon_fully_contained(
    subject: tuple[Point, ...], container: tuple[Point, ...]
) -> bool:
    """无孔简单多边形的完整包含判定，允许边界重合。"""

    if not all(point_inside_or_on_polygon(point, container) for point in subject):
        return False
    if any(
        _segments_properly_intersect(subject_start, subject_end, container_start, container_end)
        for subject_start, subject_end in zip(subject, subject[1:] + (subject[0],))
        for container_start, container_end in zip(container, container[1:] + (container[0],))
    ):
        return False
    # 若容器的凹入边界落进被支撑区域内部，被支撑区域实际跨过了无支撑凹口。
    if any(point_inside_polygon(point, subject) for point in container):
        return False
    return True


def validate_deck_support(
    upper: DeckInput, lower: DeckInput, path: str
) -> None:
    for region_index, upper_region in enumerate(upper.regions):
        if not any(
            polygon_fully_contained(upper_region.vertices_m, lower_region.vertices_m)
            for lower_region in lower.regions
        ):
            raise ContractError(
                "hull.upper_deck_unsupported",
                f"{path}.regions[{region_index}]",
                "上层区域必须被相邻下层的某一连续区域完整支撑，不允许悬挑",
            )


@dataclass(frozen=True)
class CompiledEdge:
    start: Point
    end: Point
    input: EdgeArmorInput
    material: BaseArmorMaterial

    @property
    def length_m(self) -> float:
        return hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    @property
    def volume_m3(self) -> float:
        return self.length_m * DECK_HEIGHT_M * self.input.thickness_m

    @property
    def mass_kg(self) -> float:
        return self.volume_m3 * self.material.density_kg_m3

    @property
    def local_durability_proxy(self) -> float:
        return self.volume_m3 * self.material.local_durability_coefficient


@dataclass(frozen=True)
class SideMountSlot:
    deck_id: str
    deck_level: int
    region_id: str
    edge_index: int
    slot_index: int
    start_offset_m: float
    end_offset_m: float
    start_m: Point
    end_m: Point

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_id": self.deck_id,
            "deck_level": self.deck_level,
            "edge_index": self.edge_index,
            "end_m": list(self.end_m),
            "end_offset_m": self.end_offset_m,
            "region_id": self.region_id,
            "slot_index": self.slot_index,
            "start_m": list(self.start_m),
            "start_offset_m": self.start_offset_m,
        }


def generate_side_mount_slots(
    vertices: tuple[Point, ...],
    deck_id: str,
    deck_level: int,
    region_id: str,
    step_m: float = 5.0,
) -> tuple[SideMountSlot, ...]:
    """把每条绘制边独立分成居中的五米侧挂步长，不允许跨越拐角。"""

    slots: list[SideMountSlot] = []
    for edge_index, (start, end) in enumerate(
        zip(vertices, vertices[1:] + (vertices[0],))
    ):
        length = hypot(end[0] - start[0], end[1] - start[1])
        count = floor((length + EPS) / step_m)
        margin = 0.5 * (length - count * step_m)
        direction = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
        for slot_index in range(count):
            start_offset = margin + slot_index * step_m
            end_offset = start_offset + step_m
            slot_start = (
                start[0] + direction[0] * start_offset,
                start[1] + direction[1] * start_offset,
            )
            slot_end = (
                start[0] + direction[0] * end_offset,
                start[1] + direction[1] * end_offset,
            )
            slots.append(
                SideMountSlot(
                    deck_id=deck_id,
                    deck_level=deck_level,
                    region_id=region_id,
                    edge_index=edge_index,
                    slot_index=slot_index,
                    start_offset_m=start_offset,
                    end_offset_m=end_offset,
                    start_m=_round_point(slot_start),
                    end_m=_round_point(slot_end),
                )
            )
    return tuple(slots)


def clip_half_plane(
    vertices: tuple[Point, ...], axis: int, value: float, keep_less: bool
) -> list[Point]:
    def inside(point: Point) -> bool:
        return point[axis] <= value if keep_less else point[axis] >= value

    result: list[Point] = []
    for start, end in zip(vertices, vertices[1:] + (vertices[0],)):
        start_inside = inside(start)
        end_inside = inside(end)
        if start_inside != end_inside:
            delta = end[axis] - start[axis]
            amount = (value - start[axis]) / delta
            result.append(
                (
                    start[0] + amount * (end[0] - start[0]),
                    start[1] + amount * (end[1] - start[1]),
                )
            )
        if end_inside:
            result.append(end)
    return result


def cut_length(vertices: tuple[Point, ...], axis: int, value: float) -> float:
    other = 1 - axis
    crossings: list[float] = []
    for start, end in zip(vertices, vertices[1:] + (vertices[0],)):
        a = start[axis]
        b = end[axis]
        if (a <= value < b) or (b <= value < a):
            amount = (value - a) / (b - a)
            crossings.append(start[other] + amount * (end[other] - start[other]))
    crossings.sort()
    return sum(
        crossings[index + 1] - crossings[index]
        for index in range(0, len(crossings) - 1, 2)
    )


def segment_fraction_less(start: Point, end: Point, axis: int, value: float) -> float:
    a = start[axis]
    b = end[axis]
    if a <= value and b <= value:
        return 1.0
    if a > value and b > value:
        return 0.0
    if abs(a - b) <= EPS:
        return 0.5
    crossing = (value - a) / (b - a)
    return crossing if a <= value else 1.0 - crossing


@dataclass(frozen=True)
class CompiledRegion:
    deck_id: str
    deck_level: int
    input: HullRegionInput
    material: StructureMaterial
    effective_structure_thickness_m: float
    edges: tuple[CompiledEdge, ...]
    internal_cells: tuple[tuple[int, int], ...]

    @property
    def structure_surface_density_kg_m2(self) -> float:
        return self.effective_structure_thickness_m * self.material.density_kg_m3

    @property
    def area_m2(self) -> float:
        return polygon_area(self.input.vertices_m)

    @property
    def perimeter_m(self) -> float:
        return polygon_perimeter(self.input.vertices_m)

    @property
    def structure_volume_m3(self) -> float:
        return self.area_m2 * self.effective_structure_thickness_m

    @property
    def structure_mass_kg(self) -> float:
        return self.area_m2 * self.structure_surface_density_kg_m2

    @property
    def armor_volume_m3(self) -> float:
        return sum(edge.volume_m3 for edge in self.edges)

    @property
    def armor_mass_kg(self) -> float:
        return sum(edge.mass_kg for edge in self.edges)


@dataclass(frozen=True)
class StructureContext:
    regions: tuple[CompiledRegion, ...]

    @property
    def vertices(self) -> tuple[Point, ...]:
        return tuple(point for region in self.regions for point in region.input.vertices_m)

    @property
    def structure_mass_kg(self) -> float:
        return sum(region.structure_mass_kg for region in self.regions)

    @property
    def total_mass_kg(self) -> float:
        return self.structure_mass_kg + sum(
            region.armor_mass_kg for region in self.regions
        )


def mass_less(context: StructureContext, axis: int, value: float) -> float:
    structure_mass = 0.0
    for region in context.regions:
        clipped = clip_half_plane(region.input.vertices_m, axis, value, True)
        if len(clipped) >= 3:
            structure_mass += polygon_area(clipped) * region.structure_surface_density_kg_m2
    armor_mass = sum(
        edge.mass_kg * segment_fraction_less(edge.start, edge.end, axis, value)
        for region in context.regions
        for edge in region.edges
    )
    return structure_mass + armor_mass


def cut_capacity_n(context: StructureContext, axis: int, value: float) -> float:
    capacity = sum(
        cut_length(region.input.vertices_m, axis, value)
        * region.effective_structure_thickness_m
        * region.material.strength_coefficient
        * ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS_PA
        for region in context.regions
    )
    for region in context.regions:
        for edge in region.edges:
            a = edge.start[axis]
            b = edge.end[axis]
            if not ((a <= value < b) or (b <= value < a)):
                continue
            vector = (edge.end[0] - edge.start[0], edge.end[1] - edge.start[1])
            load_component = abs(vector[axis] / edge.length_m)
            capacity += (
                SHELL_STRENGTH_EFFICIENCY
                * edge.material.shell_strength_coefficient
                * ARMOR_STEEL_EFFECTIVE_ALLOWABLE_STRESS_PA
                * DECK_HEIGHT_M
                * edge.input.thickness_m
                * load_component
            )
    return capacity


def channel_safe_acceleration(context: StructureContext, axis: int) -> tuple[float, float]:
    coordinates = [point[axis] for point in context.vertices]
    lower = min(coordinates)
    upper = max(coordinates)
    total_mass = context.total_mass_kg

    balance_lower = lower
    balance_upper = upper
    for _ in range(60):
        middle = 0.5 * (balance_lower + balance_upper)
        if mass_less(context, axis, middle) < total_mass / 2.0:
            balance_lower = middle
        else:
            balance_upper = middle
    balance = 0.5 * (balance_lower + balance_upper)

    sample_count = int(ceil((upper - lower) / 5.0))
    positions = [
        lower + (index + 0.5) * 5.0
        for index in range(sample_count)
        if lower + (index + 0.5) * 5.0 < upper
    ]
    positions.append(balance)
    positions = sorted(set(round(position, 9) for position in positions))

    samples: list[tuple[float, float, float]] = []
    for position in positions:
        negative_mass = mass_less(context, axis, position)
        transfer_mass = min(negative_mass, total_mass - negative_mass)
        samples.append((position, cut_capacity_n(context, axis, position), transfer_mass))

    candidates: list[tuple[float, float]] = []
    for index, (position, capacity, transfer_mass) in enumerate(samples):
        if capacity <= 0.0 or transfer_mass / total_mass < END_MASS_RATIO:
            continue
        neighborhood = [
            samples[neighbor][1]
            for neighbor in range(max(0, index - 1), min(len(samples), index + 2))
        ]
        candidates.append((median(neighborhood) / transfer_mass, position))
    if not candidates:
        raise ContractError("hull.structure_no_valid_cut", "$", "没有有效结构切面")
    return min(candidates)


def yaw_limits(
    vertices: tuple[Point, ...], safe_long_mps2: float, safe_lat_mps2: float
) -> tuple[float, float]:
    alpha_coefficients = [
        hypot(x / safe_long_mps2, -y / safe_lat_mps2) for x, y in vertices
    ]
    omega_squared_coefficients = [
        hypot(-y / safe_long_mps2, -x / safe_lat_mps2) for x, y in vertices
    ]
    return (
        1.0 / max(alpha_coefficients),
        sqrt(1.0 / max(omega_squared_coefficients)),
    )


def armor_edge_inertia(edge: CompiledEdge) -> float:
    midpoint_x = 0.5 * (edge.start[0] + edge.end[0])
    midpoint_y = 0.5 * (edge.start[1] + edge.end[1])
    return edge.mass_kg * (
        edge.length_m * edge.length_m / 12.0 + midpoint_x * midpoint_x + midpoint_y * midpoint_y
    )


@dataclass(frozen=True)
class CompiledDeckResult:
    id: str
    level: int
    region_ids: tuple[str, ...]
    area_m2: float
    perimeter_m: float
    structure_volume_m3: float
    structure_mass_kg: float
    base_armor_volume_m3: float
    base_armor_mass_kg: float
    internal_cells: tuple[tuple[int, int], ...]
    exposed_top_cells: tuple[tuple[int, int], ...]
    side_mount_slots: tuple[SideMountSlot, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "area_m2": self.area_m2,
            "base_armor_mass_kg": self.base_armor_mass_kg,
            "base_armor_volume_m3": self.base_armor_volume_m3,
            "exposed_top_cells": [list(cell) for cell in self.exposed_top_cells],
            "id": self.id,
            "internal_cells": [list(cell) for cell in self.internal_cells],
            "level": self.level,
            "perimeter_m": self.perimeter_m,
            "region_ids": list(self.region_ids),
            "side_mount_slots": [slot.to_dict() for slot in self.side_mount_slots],
            "structure_mass_kg": self.structure_mass_kg,
            "structure_volume_m3": self.structure_volume_m3,
        }


@dataclass(frozen=True)
class CompiledHull:
    normalized_blueprint: HullBlueprintInput
    source_sha256: str
    length_m: float
    beam_m: float
    base_planform_area_m2: float
    area_m2: float
    perimeter_m: float
    structure_volume_m3: float
    base_armor_volume_m3: float
    structure_mass_kg: float
    base_armor_mass_kg: float
    hull_mass_kg: float
    hull_inertia_kg_m2: float
    hull_durability_volume_proxy_m3: float
    local_armor_durability_proxy: tuple[tuple[str, int, str, int, float], ...]
    safe_longitudinal_mps2: float
    safe_lateral_mps2: float
    safe_longitudinal_g: float
    safe_lateral_g: float
    safe_yaw_acceleration_rad_s2: float
    safe_yaw_rate_rad_s: float
    longitudinal_bottleneck_m: float
    lateral_bottleneck_m: float
    decks: tuple[CompiledDeckResult, ...]
    aerodynamic_cache: AerodynamicGeometryCache
    hull_rcs_cache: HullRCSCache

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_armor_mass_kg": self.base_armor_mass_kg,
            "base_armor_volume_m3": self.base_armor_volume_m3,
            "aerodynamic_cache": self.aerodynamic_cache.to_dict(),
            "compiler_capabilities": [
                "multi_deck_and_upper_multi_region",
                "polygon_validation_and_normalization",
                "deck_level_continuity_and_full_support",
                "deck_wide_y_axis_geometry_and_armor_symmetry",
                "strict_internal_grid_generation",
                "local_exposed_top_grid_generation",
                "side_mount_step_generation",
                "multi_material_hull_mass_and_inertia",
                "longitudinal_lateral_and_yaw_structure_limits",
                "directional_aerodynamic_geometry_cache",
                "directional_baseline_hull_rcs_cache",
            ],
            "compiler_interface": HULL_COMPILER_INTERFACE_ID,
            "decks": [deck.to_dict() for deck in self.decks],
            "deferred_capabilities": [
                "module_and_outfit_compilation",
            ],
            "geometry": {
                "base_planform_area_m2": self.base_planform_area_m2,
                "beam_m": self.beam_m,
                "length_m": self.length_m,
                "total_deck_area_m2": self.area_m2,
                "total_perimeter_m": self.perimeter_m,
            },
            "hull_durability_volume_proxy_m3": self.hull_durability_volume_proxy_m3,
            "hull_inertia_kg_m2": self.hull_inertia_kg_m2,
            "hull_mass_kg": self.hull_mass_kg,
            "hull_rcs_cache": self.hull_rcs_cache.to_dict(),
            "local_armor_durability_proxy": [
                {
                    "deck_id": deck_id,
                    "deck_level": deck_level,
                    "durability_proxy": durability,
                    "edge_index": edge_index,
                    "region_id": region_id,
                }
                for deck_id, deck_level, region_id, edge_index, durability in self.local_armor_durability_proxy
            ],
            "normalized_blueprint": self.normalized_blueprint.to_dict(),
            "safe_structure": {
                "lateral_bottleneck_m": self.lateral_bottleneck_m,
                "longitudinal_bottleneck_m": self.longitudinal_bottleneck_m,
                "safe_lateral_g": self.safe_lateral_g,
                "safe_lateral_mps2": self.safe_lateral_mps2,
                "safe_longitudinal_g": self.safe_longitudinal_g,
                "safe_longitudinal_mps2": self.safe_longitudinal_mps2,
                "safe_yaw_acceleration_rad_s2": self.safe_yaw_acceleration_rad_s2,
                "safe_yaw_rate_rad_s": self.safe_yaw_rate_rad_s,
            },
            "source_sha256": self.source_sha256,
            "structure_mass_kg": self.structure_mass_kg,
            "structure_volume_m3": self.structure_volume_m3,
        }


def _normalize_and_validate_decks(blueprint: HullBlueprintInput) -> tuple[DeckInput, ...]:
    ordered = tuple(sorted(blueprint.decks, key=lambda deck: (deck.level, deck.id)))
    if len({deck.id for deck in ordered}) != len(ordered):
        raise ContractError("hull.deck_id_duplicate", "$.decks", "甲板 id 不得重复")
    if len({deck.level for deck in ordered}) != len(ordered):
        raise ContractError("hull.deck_level_duplicate", "$.decks", "每个高度层只能有一层甲板")
    if tuple(deck.level for deck in ordered) != tuple(range(len(ordered))):
        raise ContractError(
            "hull.deck_levels_non_contiguous", "$.decks", "甲板层级必须从 0 开始连续排列"
        )
    if not ordered[0].is_base or len(ordered[0].regions) != 1:
        raise ContractError(
            "hull.base_deck_invalid", "$.decks[0]", "基底层必须是 level=0 且只能有一个连续区域"
        )
    if any(deck.is_base for deck in ordered[1:]):
        raise ContractError("hull.base_deck_duplicate", "$.decks", "只能存在一个基底层")

    normalized: list[DeckInput] = []
    for deck_index, deck in enumerate(ordered):
        if len({region.id for region in deck.regions}) != len(deck.regions):
            raise ContractError(
                "hull.region_id_duplicate", f"$.decks[{deck_index}].regions", "同层区域 id 不得重复"
            )
        regions = tuple(
            sorted(
                (
                    normalize_region(region, f"$.decks[{deck_index}].regions[{region_index}]")
                    for region_index, region in enumerate(deck.regions)
                ),
                key=lambda region: region.id,
            )
        )
        validate_regions_separated(regions, f"$.decks[{deck_index}].regions")
        validate_regions_y_symmetry(regions, f"$.decks[{deck_index}].regions")
        normalized.append(replace(deck, regions=regions))

    normalized_decks = tuple(normalized)
    for deck_index in range(1, len(normalized_decks)):
        validate_deck_support(
            normalized_decks[deck_index],
            normalized_decks[deck_index - 1],
            f"$.decks[{deck_index}]",
        )
    return normalized_decks


def compile_hull(
    blueprint: HullBlueprintInput, registry: MaterialRegistry
) -> CompiledHull:
    normalized_decks = _normalize_and_validate_decks(blueprint)
    normalized_blueprint = replace(blueprint, decks=normalized_decks)
    aerodynamic_cache = build_aerodynamic_geometry_cache(
        normalized_decks, blueprint.grid.deck_height_m
    )
    hull_rcs_cache = build_hull_rcs_cache(
        normalized_decks, blueprint.grid.deck_height_m
    )
    base_region = normalized_decks[0].regions[0]
    if not point_inside_polygon((0.0, 0.0), base_region.vertices_m):
        raise ContractError(
            "hull.cic_origin_outside", "$.grid.cic_origin_cell", "CIC 原点必须位于基底船壳内部"
        )

    compiled_by_level: dict[int, tuple[CompiledRegion, ...]] = {}
    all_regions: list[CompiledRegion] = []
    for deck_index, deck in enumerate(normalized_decks):
        structure_material = registry.structure(
            deck.structure_material, f"$.decks[{deck_index}].structure_material"
        )
        effective_thickness = DECK_EQUIVALENT_THICKNESS_M + (
            JOINT_EQUIVALENT_THICKNESS_M if deck.level > 0 else 0.0
        )
        compiled_deck_regions: list[CompiledRegion] = []
        for region_index, region in enumerate(deck.regions):
            edges: list[CompiledEdge] = []
            for edge_index, (start, end, armor_input) in enumerate(
                zip(
                    region.vertices_m,
                    region.vertices_m[1:] + (region.vertices_m[0],),
                    region.edge_armor,
                )
            ):
                armor_material = registry.base_armor(
                    armor_input.material,
                    f"$.decks[{deck_index}].regions[{region_index}].edge_armor[{edge_index}].material",
                )
                edges.append(CompiledEdge(start, end, armor_input, armor_material))
            compiled_region = CompiledRegion(
                deck_id=deck.id,
                deck_level=deck.level,
                input=region,
                material=structure_material,
                effective_structure_thickness_m=effective_thickness,
                edges=tuple(edges),
                internal_cells=generate_strict_internal_cells(
                    region.vertices_m, blueprint.grid.cell_size_m
                ),
            )
            compiled_deck_regions.append(compiled_region)
            all_regions.append(compiled_region)
        compiled_by_level[deck.level] = tuple(compiled_deck_regions)

    if blueprint.grid.cic_origin_cell not in compiled_by_level[0][0].internal_cells:
        raise ContractError(
            "hull.cic_origin_cell_incomplete",
            "$.grid.cic_origin_cell",
            "CIC 原点格必须被基底船壳完整覆盖",
        )

    deck_results: list[CompiledDeckResult] = []
    for deck in normalized_decks:
        regions = compiled_by_level[deck.level]
        internal_cells = tuple(
            sorted(
                {cell for region in regions for cell in region.internal_cells},
                key=lambda cell: (cell[1], cell[0]),
            )
        )
        upper_regions = compiled_by_level.get(deck.level + 1, ())
        exposed_cells = tuple(
            cell
            for cell in internal_cells
            if not any(
                cell_has_positive_overlap(
                    cell, upper.input.vertices_m, blueprint.grid.cell_size_m
                )
                for upper in upper_regions
            )
        )
        side_slots = tuple(
            slot
            for region in regions
            for slot in generate_side_mount_slots(
                region.input.vertices_m,
                deck.id,
                deck.level,
                region.input.id,
                blueprint.grid.cell_size_m,
            )
        )
        deck_results.append(
            CompiledDeckResult(
                id=deck.id,
                level=deck.level,
                region_ids=tuple(region.input.id for region in regions),
                area_m2=sum(region.area_m2 for region in regions),
                perimeter_m=sum(region.perimeter_m for region in regions),
                structure_volume_m3=sum(region.structure_volume_m3 for region in regions),
                structure_mass_kg=sum(region.structure_mass_kg for region in regions),
                base_armor_volume_m3=sum(region.armor_volume_m3 for region in regions),
                base_armor_mass_kg=sum(region.armor_mass_kg for region in regions),
                internal_cells=internal_cells,
                exposed_top_cells=exposed_cells,
                side_mount_slots=side_slots,
            )
        )

    context = StructureContext(tuple(all_regions))
    structure_volume = sum(region.structure_volume_m3 for region in all_regions)
    base_armor_volume = sum(region.armor_volume_m3 for region in all_regions)
    structure_inertia = sum(
        polygon_polar_area_moment(region.input.vertices_m)
        * region.effective_structure_thickness_m
        * region.material.density_kg_m3
        for region in all_regions
    )
    armor_inertia = sum(
        armor_edge_inertia(edge) for region in all_regions for edge in region.edges
    )
    safe_long, long_bottleneck = channel_safe_acceleration(context, axis=1)
    safe_lat, lat_bottleneck = channel_safe_acceleration(context, axis=0)
    safe_alpha, safe_omega = yaw_limits(context.vertices, safe_long, safe_lat)
    durability_proxy = sum(
        region.structure_volume_m3 * region.material.durability_coefficient
        + SHELL_HULL_HP_EFFICIENCY
        * sum(
            edge.volume_m3 * edge.material.local_durability_coefficient
            for edge in region.edges
        )
        for region in all_regions
    )
    base_xs = [point[0] for point in base_region.vertices_m]
    base_ys = [point[1] for point in base_region.vertices_m]

    return CompiledHull(
        normalized_blueprint=normalized_blueprint,
        source_sha256=canonical_sha256(normalized_blueprint),
        length_m=max(base_ys) - min(base_ys),
        beam_m=max(base_xs) - min(base_xs),
        base_planform_area_m2=polygon_area(base_region.vertices_m),
        area_m2=sum(region.area_m2 for region in all_regions),
        perimeter_m=sum(region.perimeter_m for region in all_regions),
        structure_volume_m3=structure_volume,
        base_armor_volume_m3=base_armor_volume,
        structure_mass_kg=context.structure_mass_kg,
        base_armor_mass_kg=context.total_mass_kg - context.structure_mass_kg,
        hull_mass_kg=context.total_mass_kg,
        hull_inertia_kg_m2=structure_inertia + armor_inertia,
        hull_durability_volume_proxy_m3=durability_proxy,
        local_armor_durability_proxy=tuple(
            (
                region.deck_id,
                region.deck_level,
                region.input.id,
                edge_index,
                edge.local_durability_proxy,
            )
            for region in all_regions
            for edge_index, edge in enumerate(region.edges)
        ),
        safe_longitudinal_mps2=safe_long,
        safe_lateral_mps2=safe_lat,
        safe_longitudinal_g=safe_long / STANDARD_GRAVITY_MPS2,
        safe_lateral_g=safe_lat / STANDARD_GRAVITY_MPS2,
        safe_yaw_acceleration_rad_s2=safe_alpha,
        safe_yaw_rate_rad_s=safe_omega,
        longitudinal_bottleneck_m=long_bottleneck,
        lateral_bottleneck_m=lat_bottleneck,
        decks=tuple(deck_results),
        aerodynamic_cache=aerodynamic_cache,
        hull_rcs_cache=hull_rcs_cache,
    )
