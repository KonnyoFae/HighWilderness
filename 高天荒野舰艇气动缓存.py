"""由规范船壳几何生成方向性气动缓存，并提供无状态运行时阻力计算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, ceil, cos, degrees, floor, hypot, isfinite, radians, sin
from typing import Any, Callable

from 高天荒野舰艇数据契约 import DeckInput, Point, canonical_sha256


EPS = 1.0e-9
AERODYNAMIC_CACHE_MODEL = "gaotian.aero.geometry/v1alpha1"
DIRECTION_COUNT = 360
WAVE_SECTION_STEP_M = 5.0


def _project(point: Point, axis: Point) -> float:
    return point[0] * axis[0] + point[1] * axis[1]


def _signed_area(vertices: tuple[Point, ...]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0
        for (x0, y0), (x1, y1) in zip(vertices, vertices[1:] + (vertices[0],))
    )


def _perimeter(vertices: tuple[Point, ...]) -> float:
    return sum(
        hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(vertices, vertices[1:] + (vertices[0],))
    )


def _area(vertices: tuple[Point, ...]) -> float:
    return abs(_signed_area(vertices))


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted((min(start, end), max(start, end)) for start, end in intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + EPS:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _line_intervals(
    vertices: tuple[Point, ...],
    line_axis: Point,
    interval_axis: Point,
    line_value: float,
) -> list[tuple[float, float]]:
    """多边形与 line_axis·p=line_value 的交线在 interval_axis 上的区间。"""

    crossings: list[float] = []
    for start, end in zip(vertices, vertices[1:] + (vertices[0],)):
        start_line = _project(start, line_axis)
        end_line = _project(end, line_axis)
        if not (
            (start_line <= line_value < end_line)
            or (end_line <= line_value < start_line)
        ):
            continue
        amount = (line_value - start_line) / (end_line - start_line)
        start_interval = _project(start, interval_axis)
        end_interval = _project(end, interval_axis)
        crossings.append(start_interval + amount * (end_interval - start_interval))
    crossings.sort()
    return [
        (crossings[index], crossings[index + 1])
        for index in range(0, len(crossings) - 1, 2)
        if crossings[index + 1] - crossings[index] > EPS
    ]


def _section_area_m2(
    decks: tuple[DeckInput, ...],
    flow_axis: Point,
    cross_axis: Point,
    flow_position_m: float,
    deck_height_m: float,
) -> float:
    result = 0.0
    for deck in decks:
        intervals = [
            interval
            for region in deck.regions
            for interval in _line_intervals(
                region.vertices_m, flow_axis, cross_axis, flow_position_m
            )
        ]
        result += sum(end - start for start, end in _merge_intervals(intervals)) * deck_height_m
    return result


def _visible_surface_metrics(
    deck: DeckInput,
    flow_axis: Point,
    cross_axis: Point,
    deck_height_m: float,
) -> tuple[float, float, float]:
    """返回本高度带的投影面积、迎风钝度面积和背风钝度面积。"""

    breakpoints = sorted(
        {
            round(_project(point, cross_axis), 12)
            for region in deck.regions
            for point in region.vertices_m
        }
    )
    projected_area = 0.0
    front_bluntness = 0.0
    rear_bluntness = 0.0

    for lower, upper in zip(breakpoints, breakpoints[1:]):
        width = upper - lower
        if width <= EPS:
            continue
        sample = 0.5 * (lower + upper)
        intersections: list[tuple[float, float]] = []
        for region in deck.regions:
            orientation_sign = 1.0 if _signed_area(region.vertices_m) > 0.0 else -1.0
            for start, end in zip(
                region.vertices_m,
                region.vertices_m[1:] + (region.vertices_m[0],),
            ):
                start_cross = _project(start, cross_axis)
                end_cross = _project(end, cross_axis)
                if not (
                    (start_cross <= sample < end_cross)
                    or (end_cross <= sample < start_cross)
                ):
                    continue
                amount = (sample - start_cross) / (end_cross - start_cross)
                flow_position = _project(start, flow_axis) + amount * (
                    _project(end, flow_axis) - _project(start, flow_axis)
                )
                delta_x = end[0] - start[0]
                delta_y = end[1] - start[1]
                length = hypot(delta_x, delta_y)
                outward_normal = (
                    orientation_sign * delta_y / length,
                    -orientation_sign * delta_x / length,
                )
                normal_flow = _project(outward_normal, flow_axis)
                intersections.append((flow_position, normal_flow))
        if not intersections:
            continue
        rear = min(intersections, key=lambda item: item[0])
        front = max(intersections, key=lambda item: item[0])
        front_mu = max(0.0, front[1])
        rear_mu = max(0.0, -rear[1])
        projected_area += width * deck_height_m
        # S*mu^3 = H*(S 在横向的投影宽度)*mu^2。
        front_bluntness += width * deck_height_m * front_mu * front_mu
        rear_bluntness += width * deck_height_m * rear_mu * rear_mu
    return projected_area, front_bluntness, rear_bluntness


def _wave_area_change_m2(
    decks: tuple[DeckInput, ...],
    flow_axis: Point,
    cross_axis: Point,
    minimum_flow_m: float,
    maximum_flow_m: float,
    deck_height_m: float,
) -> float:
    flow_length = maximum_flow_m - minimum_flow_m
    section_count = max(1, ceil(flow_length / WAVE_SECTION_STEP_M))
    section_step = flow_length / section_count
    positions = [
        minimum_flow_m + (index + 0.5) * section_step
        for index in range(section_count)
    ]
    sections = [0.0]
    sections.extend(
        _section_area_m2(decks, flow_axis, cross_axis, item, deck_height_m)
        for item in positions
    )
    sections.append(0.0)
    integral_proxy = sum(
        (next_area - area) ** 2 / section_step
        for area, next_area in zip(sections, sections[1:])
    )
    return integral_proxy / flow_length


@dataclass(frozen=True)
class AerodynamicDirectionSample:
    angle_deg: int
    projected_area_m2: float
    front_bluntness_area_m2: float
    rear_bluntness_area_m2: float
    flow_length_m: float
    wave_area_change_m2: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "angle_deg": self.angle_deg,
            "flow_length_m": self.flow_length_m,
            "front_bluntness_area_m2": self.front_bluntness_area_m2,
            "projected_area_m2": self.projected_area_m2,
            "rear_bluntness_area_m2": self.rear_bluntness_area_m2,
            "wave_area_change_m2": self.wave_area_change_m2,
        }


@dataclass(frozen=True)
class AerodynamicGeometryCache:
    model: str
    direction_step_deg: float
    deck_height_m: float
    wet_surface_area_m2: float
    directions: tuple[AerodynamicDirectionSample, ...]
    _source_sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_source_sha256", canonical_sha256(self))

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_height_m": self.deck_height_m,
            "direction_step_deg": self.direction_step_deg,
            "directions": [sample.to_dict() for sample in self.directions],
            "model": self.model,
            "wet_surface_area_m2": self.wet_surface_area_m2,
        }


def build_aerodynamic_geometry_cache(
    decks: tuple[DeckInput, ...], deck_height_m: float = 5.0
) -> AerodynamicGeometryCache:
    if not decks or deck_height_m <= 0.0:
        raise ValueError("气动缓存需要至少一层合法船壳和正数层高")
    all_points = tuple(
        point for deck in decks for region in deck.regions for point in region.vertices_m
    )
    directions: list[AerodynamicDirectionSample] = []
    for angle_deg in range(DIRECTION_COUNT):
        angle_rad = radians(angle_deg)
        # β=0 为 +Y 舰艏方向，β=90 为 +X 右舷方向。
        flow_axis = (sin(angle_rad), cos(angle_rad))
        cross_axis = (cos(angle_rad), -sin(angle_rad))
        flow_coordinates = [_project(point, flow_axis) for point in all_points]
        minimum_flow = min(flow_coordinates)
        maximum_flow = max(flow_coordinates)
        projected = 0.0
        front = 0.0
        rear = 0.0
        for deck in decks:
            deck_projected, deck_front, deck_rear = _visible_surface_metrics(
                deck, flow_axis, cross_axis, deck_height_m
            )
            projected += deck_projected
            front += deck_front
            rear += deck_rear
        directions.append(
            AerodynamicDirectionSample(
                angle_deg=angle_deg,
                projected_area_m2=projected,
                front_bluntness_area_m2=front,
                rear_bluntness_area_m2=rear,
                flow_length_m=maximum_flow - minimum_flow,
                wave_area_change_m2=_wave_area_change_m2(
                    decks,
                    flow_axis,
                    cross_axis,
                    minimum_flow,
                    maximum_flow,
                    deck_height_m,
                ),
            )
        )
    base_area = sum(_area(region.vertices_m) for region in decks[0].regions)
    vertical_surface = sum(
        _perimeter(region.vertices_m) * deck_height_m
        for deck in decks
        for region in deck.regions
    )
    return AerodynamicGeometryCache(
        model=AERODYNAMIC_CACHE_MODEL,
        direction_step_deg=1.0,
        deck_height_m=deck_height_m,
        wet_surface_area_m2=2.0 * base_area + vertical_surface,
        directions=tuple(directions),
    )


def interpolate_direction(
    cache: AerodynamicGeometryCache, angle_deg: float
) -> AerodynamicDirectionSample:
    angle = angle_deg % 360.0
    lower_index = floor(angle)
    upper_index = (lower_index + 1) % DIRECTION_COUNT
    amount = angle - lower_index
    lower = cache.directions[lower_index]
    upper = cache.directions[upper_index]

    def blend(first: float, second: float) -> float:
        return first + amount * (second - first)

    return AerodynamicDirectionSample(
        angle_deg=lower_index,
        projected_area_m2=blend(lower.projected_area_m2, upper.projected_area_m2),
        front_bluntness_area_m2=blend(
            lower.front_bluntness_area_m2, upper.front_bluntness_area_m2
        ),
        rear_bluntness_area_m2=blend(
            lower.rear_bluntness_area_m2, upper.rear_bluntness_area_m2
        ),
        flow_length_m=blend(lower.flow_length_m, upper.flow_length_m),
        wave_area_change_m2=blend(
            lower.wave_area_change_m2, upper.wave_area_change_m2
        ),
    )


def velocity_body_to_beta_deg(velocity_x_mps: float, velocity_y_mps: float) -> float:
    if hypot(velocity_x_mps, velocity_y_mps) <= EPS:
        return 0.0
    return degrees(atan2(velocity_x_mps, velocity_y_mps)) % 360.0


@dataclass(frozen=True)
class AerodynamicCoefficients:
    projected_area_coefficient: float
    front_bluntness_coefficient: float
    rear_bluntness_coefficient: float
    roughness_coefficient: float
    reynolds_number_minimum: float


@dataclass(frozen=True)
class DragBreakdown:
    beta_deg: float
    speed_mps: float
    mach: float
    reynolds_number: float
    skin_friction_coefficient: float
    form_area_m2: float
    skin_area_m2: float
    wave_area_m2: float
    equivalent_drag_area_m2: float
    drag_force_n: float


def calculate_drag(
    cache: AerodynamicGeometryCache,
    beta_deg: float,
    speed_mps: float,
    density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    sound_speed_mps: float,
    coefficients: AerodynamicCoefficients,
    wave_coefficient_at_mach: Callable[[float], float],
) -> DragBreakdown:
    numeric_values = (
        beta_deg,
        speed_mps,
        density_kg_m3,
        dynamic_viscosity_pa_s,
        sound_speed_mps,
        coefficients.projected_area_coefficient,
        coefficients.front_bluntness_coefficient,
        coefficients.rear_bluntness_coefficient,
        coefficients.roughness_coefficient,
        coefficients.reynolds_number_minimum,
    )
    if any(not isfinite(value) for value in numeric_values):
        raise ValueError("速度和气动参数必须是有限数")
    if speed_mps < 0.0 or any(value < 0.0 for value in numeric_values):
        raise ValueError("速度和气动参数不得为负数")
    if density_kg_m3 <= 0.0 or dynamic_viscosity_pa_s <= 0.0 or sound_speed_mps <= 0.0:
        raise ValueError("密度、动力黏度和音速必须为正数")
    if coefficients.reynolds_number_minimum <= 0.0:
        raise ValueError("雷诺数下限必须为正数")

    sample = interpolate_direction(cache, beta_deg)
    mach = speed_mps / sound_speed_mps
    raw_reynolds = (
        density_kg_m3 * speed_mps * sample.flow_length_m / dynamic_viscosity_pa_s
    )
    reynolds = max(coefficients.reynolds_number_minimum, raw_reynolds)
    skin_friction = 0.074 / reynolds**0.2
    form_area = (
        coefficients.projected_area_coefficient * sample.projected_area_m2
        + coefficients.front_bluntness_coefficient * sample.front_bluntness_area_m2
        + coefficients.rear_bluntness_coefficient * sample.rear_bluntness_area_m2
    )
    skin_area = (
        coefficients.roughness_coefficient
        * skin_friction
        * cache.wet_surface_area_m2
    )
    wave_response = wave_coefficient_at_mach(mach)
    if not isfinite(wave_response) or wave_response < 0.0:
        raise ValueError("马赫响应系数必须是非负有限数")
    wave_area = wave_response * sample.wave_area_change_m2
    equivalent_area = form_area + skin_area + wave_area
    drag_force = 0.5 * density_kg_m3 * speed_mps * speed_mps * equivalent_area
    return DragBreakdown(
        beta_deg=beta_deg % 360.0,
        speed_mps=speed_mps,
        mach=mach,
        reynolds_number=reynolds,
        skin_friction_coefficient=skin_friction,
        form_area_m2=form_area,
        skin_area_m2=skin_area,
        wave_area_m2=wave_area,
        equivalent_drag_area_m2=equivalent_area,
        drag_force_n=drag_force,
    )
