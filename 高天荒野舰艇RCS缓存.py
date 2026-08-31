"""由规范多层船壳生成普通涂料基准 RCS 方向缓存。"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, floor, hypot, isfinite, log10, pi, radians, sin
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError,
    DeckInput,
    HullCoatingDefinition,
    Point,
    canonical_sha256,
)


EPS = 1.0e-9
RCS_CACHE_MODEL = "gaotian.rcs.hull/v1alpha1"
DIRECTION_COUNT = 360


def _subtract(first: Point, second: Point) -> Point:
    return first[0] - second[0], first[1] - second[1]


def _dot(first: Point, second: Point) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _length(vector: Point) -> float:
    return hypot(vector[0], vector[1])


def _signed_area(vertices: list[Point] | tuple[Point, ...]) -> float:
    return 0.5 * sum(
        _cross(vertices[index], vertices[(index + 1) % len(vertices)])
        for index in range(len(vertices))
    )


def normalize_rcs_polygon(vertices: tuple[Point, ...]) -> tuple[Point, ...]:
    """RCS 分组忽略装甲边界，合并全部连续共线边。"""

    result = [(float(x), float(y)) for x, y in vertices]
    changed = True
    while changed:
        changed = False
        for index in range(len(result)):
            previous = result[(index - 1) % len(result)]
            current = result[index]
            following = result[(index + 1) % len(result)]
            incoming = _subtract(current, previous)
            outgoing = _subtract(following, current)
            if abs(_cross(incoming, outgoing)) <= EPS and _dot(incoming, outgoing) >= 0.0:
                result.pop(index)
                changed = True
                break
        if len(result) < 3:
            raise ValueError("RCS 规范化后船壳多边形退化")
    if _signed_area(result) < 0.0:
        result.reverse()
    return tuple(result)


def _view_axes(bearing_deg: float) -> tuple[Point, Point]:
    angle = radians(bearing_deg)
    sight = (sin(angle), cos(angle))
    screen = (cos(angle), -sin(angle))
    return sight, screen


def _edge_geometry(
    vertices: tuple[Point, ...], index: int
) -> tuple[Point, Point, Point, float]:
    start = vertices[index]
    end = vertices[(index + 1) % len(vertices)]
    tangent = _subtract(end, start)
    edge_length = _length(tangent)
    normal = (tangent[1] / edge_length, -tangent[0] / edge_length)
    return start, end, normal, edge_length


@dataclass(frozen=True)
class VisibleFacet:
    region_index: int
    edge_index: int
    area_m2: float
    facing_cosine: float


def _visible_facets_for_deck(
    polygons: tuple[tuple[Point, ...], ...], bearing_deg: float, deck_height_m: float
) -> tuple[VisibleFacet, ...]:
    """同一高度带共用一次正交深度缓冲，避免分离区域在视线上重复计数。"""

    sight, screen = _view_axes(bearing_deg)
    candidates: list[dict[str, float | int]] = []
    events: set[float] = set()
    for region_index, vertices in enumerate(polygons):
        for edge_index in range(len(vertices)):
            start, end, normal, _ = _edge_geometry(vertices, edge_index)
            facing = _dot(normal, sight)
            if facing <= EPS:
                continue
            screen_start = _dot(start, screen)
            screen_end = _dot(end, screen)
            if abs(screen_end - screen_start) <= EPS:
                continue
            candidates.append(
                {
                    "region_index": region_index,
                    "edge_index": edge_index,
                    "facing": facing,
                    "screen_start": screen_start,
                    "screen_end": screen_end,
                    "depth_start": _dot(start, sight),
                    "depth_end": _dot(end, sight),
                }
            )
            events.add(screen_start)
            events.add(screen_end)

    visible_width: dict[tuple[int, int], float] = {}
    ordered_events = sorted(events)
    for left, right in zip(ordered_events, ordered_events[1:]):
        if right - left <= EPS:
            continue
        midpoint = 0.5 * (left + right)
        front_key: tuple[int, int] | None = None
        front_depth = float("-inf")
        for candidate in candidates:
            screen_start = float(candidate["screen_start"])
            screen_end = float(candidate["screen_end"])
            if not min(screen_start, screen_end) < midpoint < max(screen_start, screen_end):
                continue
            amount = (midpoint - screen_start) / (screen_end - screen_start)
            depth = float(candidate["depth_start"]) + amount * (
                float(candidate["depth_end"]) - float(candidate["depth_start"])
            )
            if depth > front_depth:
                front_depth = depth
                front_key = (
                    int(candidate["region_index"]),
                    int(candidate["edge_index"]),
                )
        if front_key is not None:
            visible_width[front_key] = visible_width.get(front_key, 0.0) + right - left

    candidate_by_key = {
        (int(candidate["region_index"]), int(candidate["edge_index"])): candidate
        for candidate in candidates
    }
    facets: list[VisibleFacet] = []
    for key, width in sorted(visible_width.items()):
        candidate = candidate_by_key[key]
        facing = float(candidate["facing"])
        facets.append(
            VisibleFacet(
                region_index=key[0],
                edge_index=key[1],
                area_m2=width / facing * deck_height_m,
                facing_cosine=facing,
            )
        )
    return tuple(facets)


@dataclass(frozen=True)
class RCSParameters:
    parameter_set_id: str
    balance_status: str
    reference_wavelength_m: float
    coherent_area_m2: float
    specular_scale: float
    specular_exponent: float
    diffuse_scale: float
    corner_scale: float
    corner_length_cap_m: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "balance_status": self.balance_status,
            "coherent_area_m2": self.coherent_area_m2,
            "corner_length_cap_m": self.corner_length_cap_m,
            "corner_scale": self.corner_scale,
            "diffuse_scale": self.diffuse_scale,
            "parameter_set_id": self.parameter_set_id,
            "reference_wavelength_m": self.reference_wavelength_m,
            "specular_exponent": self.specular_exponent,
            "specular_scale": self.specular_scale,
        }


PROTOTYPE_RCS_PARAMETERS = RCSParameters(
    parameter_set_id="gtw.rcs.prototype.v1",
    balance_status="prototype_unbalanced",
    reference_wavelength_m=0.10,
    coherent_area_m2=25.0,
    specular_scale=0.001,
    specular_exponent=4.0,
    diffuse_scale=0.50,
    corner_scale=8.0,
    corner_length_cap_m=5.0,
)


@dataclass(frozen=True)
class RCSDirectionSample:
    angle_deg: int
    specular_m2: float
    diffuse_m2: float
    corner_m2: float

    @property
    def total_m2(self) -> float:
        return self.specular_m2 + self.diffuse_m2 + self.corner_m2

    def to_dict(self) -> dict[str, float | int]:
        return {
            "angle_deg": self.angle_deg,
            "corner_m2": self.corner_m2,
            "diffuse_m2": self.diffuse_m2,
            "specular_m2": self.specular_m2,
            "total_m2": self.total_m2,
        }


@dataclass(frozen=True)
class HullRCSCache:
    model: str
    direction_step_deg: float
    elevation_band: str
    baseline_coating_multiplier: float
    parameters: RCSParameters
    directions: tuple[RCSDirectionSample, ...]
    minimum_rcs_m2: float
    minimum_angle_deg: int
    mean_rcs_m2: float
    maximum_rcs_m2: float
    maximum_angle_deg: int
    _source_sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_source_sha256", canonical_sha256(self))

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_coating_multiplier": self.baseline_coating_multiplier,
            "direction_step_deg": self.direction_step_deg,
            "directions": [sample.to_dict() for sample in self.directions],
            "elevation_band": self.elevation_band,
            "maximum_angle_deg": self.maximum_angle_deg,
            "maximum_rcs_m2": self.maximum_rcs_m2,
            "mean_rcs_m2": self.mean_rcs_m2,
            "minimum_angle_deg": self.minimum_angle_deg,
            "minimum_rcs_m2": self.minimum_rcs_m2,
            "model": self.model,
            "parameters": self.parameters.to_dict(),
        }


def _deck_rcs(
    deck: DeckInput,
    bearing_deg: float,
    deck_height_m: float,
    parameters: RCSParameters,
) -> RCSDirectionSample:
    polygons = tuple(normalize_rcs_polygon(region.vertices_m) for region in deck.regions)
    facets = _visible_facets_for_deck(polygons, bearing_deg, deck_height_m)
    visible_by_edge = {(facet.region_index, facet.edge_index): facet for facet in facets}
    ideal_plate_scale = 4.0 * pi / parameters.reference_wavelength_m**2

    specular = 0.0
    diffuse = 0.0
    for facet in facets:
        area = facet.area_m2
        facing = facet.facing_cosine
        coherence_limited_area_squared = area * area / (
            1.0 + area / parameters.coherent_area_m2
        )
        specular += (
            parameters.specular_scale
            * ideal_plate_scale
            * coherence_limited_area_squared
            * facing**parameters.specular_exponent
        )
        diffuse += parameters.diffuse_scale * area * facing

    sight, _ = _view_axes(bearing_deg)
    corner = 0.0
    for region_index, vertices in enumerate(polygons):
        for vertex_index in range(len(vertices)):
            previous_index = (vertex_index - 1) % len(vertices)
            next_index = vertex_index
            _, _, previous_normal, previous_length = _edge_geometry(
                vertices, previous_index
            )
            _, _, next_normal, next_length = _edge_geometry(vertices, next_index)
            previous_tangent = _subtract(
                vertices[vertex_index], vertices[previous_index]
            )
            next_tangent = _subtract(
                vertices[(vertex_index + 1) % len(vertices)], vertices[vertex_index]
            )
            turn = _cross(previous_tangent, next_tangent) / (
                previous_length * next_length
            )
            if turn >= -EPS:
                continue
            if (
                (region_index, previous_index) not in visible_by_edge
                or (region_index, next_index) not in visible_by_edge
            ):
                continue
            previous_facing = max(0.0, _dot(previous_normal, sight))
            next_facing = max(0.0, _dot(next_normal, sight))
            geometry_factor = min(1.0, -turn)
            view_factor = min(1.0, 4.0 * previous_facing * next_facing)
            corner_area = deck_height_m * min(
                previous_length, next_length, parameters.corner_length_cap_m
            )
            corner += (
                parameters.corner_scale
                * corner_area
                * geometry_factor
                * view_factor
            )
    return RCSDirectionSample(int(bearing_deg) % 360, specular, diffuse, corner)


def build_hull_rcs_cache(
    decks: tuple[DeckInput, ...],
    deck_height_m: float = 5.0,
    parameters: RCSParameters = PROTOTYPE_RCS_PARAMETERS,
) -> HullRCSCache:
    if not decks or deck_height_m <= 0.0:
        raise ValueError("RCS 缓存需要至少一层合法船壳和正数层高")
    positive_parameter_values = (
        parameters.reference_wavelength_m,
        parameters.coherent_area_m2,
        parameters.specular_exponent,
        parameters.corner_length_cap_m,
    )
    scale_values = (
        parameters.specular_scale,
        parameters.diffuse_scale,
        parameters.corner_scale,
    )
    if any(not isfinite(value) or value <= 0.0 for value in positive_parameter_values):
        raise ValueError("RCS 波长、相干面积、方向指数和凹角长度上限必须是正有限数")
    if any(not isfinite(value) or value < 0.0 for value in scale_values):
        raise ValueError("RCS 各分项缩放系数必须是非负有限数")

    directions: list[RCSDirectionSample] = []
    for angle_deg in range(DIRECTION_COUNT):
        deck_samples = tuple(
            _deck_rcs(deck, angle_deg, deck_height_m, parameters) for deck in decks
        )
        directions.append(
            RCSDirectionSample(
                angle_deg=angle_deg,
                specular_m2=sum(sample.specular_m2 for sample in deck_samples),
                diffuse_m2=sum(sample.diffuse_m2 for sample in deck_samples),
                corner_m2=sum(sample.corner_m2 for sample in deck_samples),
            )
        )
    values = [sample.total_m2 for sample in directions]
    minimum_angle = min(range(DIRECTION_COUNT), key=values.__getitem__)
    maximum_angle = max(range(DIRECTION_COUNT), key=values.__getitem__)
    return HullRCSCache(
        model=RCS_CACHE_MODEL,
        direction_step_deg=1.0,
        elevation_band="LEVEL",
        baseline_coating_multiplier=1.0,
        parameters=parameters,
        directions=tuple(directions),
        minimum_rcs_m2=values[minimum_angle],
        minimum_angle_deg=minimum_angle,
        mean_rcs_m2=sum(values) / DIRECTION_COUNT,
        maximum_rcs_m2=values[maximum_angle],
        maximum_angle_deg=maximum_angle,
    )


def interpolate_hull_rcs(cache: HullRCSCache, bearing_deg: float) -> RCSDirectionSample:
    if not isfinite(bearing_deg):
        raise ValueError("雷达相对方位必须是有限数")
    bearing = bearing_deg % 360.0
    lower_index = floor(bearing)
    upper_index = (lower_index + 1) % DIRECTION_COUNT
    amount = bearing - lower_index
    lower = cache.directions[lower_index]
    upper = cache.directions[upper_index]

    def blend(first: float, second: float) -> float:
        return first + amount * (second - first)

    return RCSDirectionSample(
        angle_deg=lower_index,
        specular_m2=blend(lower.specular_m2, upper.specular_m2),
        diffuse_m2=blend(lower.diffuse_m2, upper.diffuse_m2),
        corner_m2=blend(lower.corner_m2, upper.corner_m2),
    )


def coated_hull_rcs_m2(
    cache: HullRCSCache, bearing_deg: float, coating: HullCoatingDefinition
) -> float:
    if not coating.runtime_usable or coating.rcs_multiplier is None:
        raise ContractError(
            "coating.not_runtime_usable",
            "$",
            f"涂料 {coating.reference} 尚未完成标定，不能装备",
        )
    return interpolate_hull_rcs(cache, bearing_deg).total_m2 * coating.rcs_multiplier


def dbsm(rcs_m2: float) -> float:
    if not isfinite(rcs_m2) or rcs_m2 < 0.0:
        raise ValueError("RCS 必须是非负有限数")
    return 10.0 * log10(max(rcs_m2, 1.0e-12))


def radar_range_ratio(rcs_ratio: float) -> float:
    if not isfinite(rcs_ratio) or rcs_ratio < 0.0:
        raise ValueError("RCS 比率必须是非负有限数")
    return rcs_ratio**0.25
