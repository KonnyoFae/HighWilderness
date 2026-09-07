"""Hull-only horizontal fire obstruction, shared by preview and launch validation.

Angles are degrees clockwise from local bow (+Y). Tangency blocks firing.
Higher deck footprints are opaque; modules and lower/same decks are excluded.
"""
from math import atan2, cos, degrees, hypot, isfinite, radians, sin
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇无界面船壳编译器 import point_inside_or_on_polygon

EPS = 1e-9
POLICY = "gaotian.horizontal-fire-arc/higher-hull/v1"


def higher_regions(hull, level):
    return tuple((deck.id, region) for deck in hull.normalized_blueprint.decks
                 if deck.level > level for region in deck.regions)


def ray_hits_polygon(origin, direction, vertices):
    if point_inside_or_on_polygon(origin, vertices):
        return True
    cross = lambda a, b: a[0] * b[1] - a[1] * b[0]
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        offset = a[0] - origin[0], a[1] - origin[1]
        edge = b[0] - a[0], b[1] - a[1]
        denominator = cross(direction, edge)
        if abs(denominator) <= EPS:
            if abs(cross(offset, direction)) <= EPS:
                if max((p[0]-origin[0])*direction[0] + (p[1]-origin[1])*direction[1] for p in (a,b)) >= -EPS:
                    return True
            continue
        t, u = cross(offset, edge) / denominator, cross(offset, direction) / denominator
        if t >= -EPS and -EPS <= u <= 1 + EPS:
            return True
    return False


def blocked_regions(hull, origin, level, direction):
    if not isinstance(direction, (tuple, list)) or len(direction) != 2 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in direction
    ) or not isfinite(hypot(*direction)) or hypot(*direction) <= EPS:
        raise ContractError("weapon_arc.direction_invalid", "$.launch_direction_local_xy", "发射方向必须是有限的非零二维向量")
    length = hypot(*direction)
    normalized = direction[0]/length, direction[1]/length
    return tuple((deck_id, region.id) for deck_id, region in higher_regions(hull, level)
                 if ray_hits_polygon(origin, normalized, region.vertices_m))


def validate_weapon_launch(hull, weapon, direction):
    blockers = blocked_regions(hull, weapon.anchor_m, weapon.base_deck_level, direction)
    if blockers:
        raise ContractError("weapon_arc.hull_blocked", "$.launch_direction_local_xy",
                            f"武器 {weapon.id} 的水平射线被上层船壳遮挡：" + ", ".join(f"{d}/{r}" for d,r in blockers))


def horizontal_fire_arc(hull, origin, level):
    regions = higher_regions(hull, level)
    # Polygon visibility changes only at vertex bearings. Test each exact angular
    # interval, avoiding a coarse sampled angle grid that can miss narrow blockers.
    angles = {0.0, 360.0}
    for _, region in regions:
        for x, y in region.vertices_m:
            angles.add(degrees(atan2(x-origin[0], y-origin[1])) % 360)
    points = sorted(angles)
    blocked = []
    for start, end in zip(points, points[1:]):
        angle = radians((start + end) / 2)
        if any(ray_hits_polygon(origin, (sin(angle), cos(angle)), r.vertices_m) for _,r in regions):
            if blocked and abs(blocked[-1][1] - start) <= EPS:
                blocked[-1][1] = end
            else:
                blocked.append([start, end])
    allowed, last = [], 0.0
    for start, end in blocked:
        if start > last:
            allowed.append([last, start])
        last = end
    if last < 360:
        allowed.append([last, 360.0])
    return dict(policy=POLICY, status="hull_occlusion_resolved", origin_m=list(origin),
                base_deck_level=level, intervals_deg=allowed, blocked_intervals_deg=blocked,
                boundary_policy="blocked", angle_convention="clockwise_from_bow")
