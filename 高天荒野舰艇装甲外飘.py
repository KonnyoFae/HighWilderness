"""A1 immutable armor surfaces. Compiled only at design boundaries, never in flight.

The original polygon remains the structure/collision contour. Each edge uses
unit-offset miters along the outward angle bisectors. Unequal offsets are closed
by one triangular corner face owned by the edge with the larger flare angle.
"""
from dataclasses import dataclass
from math import cos, hypot, radians, sqrt, tan

from 高天荒野舰艇数据契约 import ContractError

HULL_ARMOR_SCHEMA = 'gaotian.hull/v4alpha1'
ARMOR_GEOMETRY_INTERFACE = 'gaotian.armor-surfaces/a1-v1'
ANGLES = (0, 30, 45, 60)
EPS = 1e-8


def validate_angle(value, thickness, path):
    if type(value) is not int or value not in ANGLES:
        raise ContractError('hull.armor_flare_angle', path, '装甲外飘只能关闭或选择30°、45°、60°')
    if value and thickness <= 0:
        raise ContractError('hull.armor_flare_zero_thickness', path, '零厚度装甲不能启用外飘')
    return value


def validate_armor_version(blueprint):
    for di, deck in enumerate(blueprint.decks):
        for ri, region in enumerate(deck.regions):
            for ei, armor in enumerate(region.edge_armor):
                path = f'$.decks[{di}].regions[{ri}].edge_armor[{ei}].flare_angle_deg'
                if blueprint.schema == HULL_ARMOR_SCHEMA:
                    validate_angle(armor.flare_angle_deg, armor.thickness_m, path)
                elif armor.flare_angle_deg is not None:
                    raise ContractError('hull.armor_flare_version', path, '外飘角度需要v4船壳版本')


def has_flared_armor(blueprint):
    return any(a.flare_angle_deg for d in blueprint.decks for r in d.regions for a in r.edge_armor)


def upgrade_armor_source(source):
    """Explicit editor migration only; never called by a reader."""
    source['schema'] = HULL_ARMOR_SCHEMA
    for deck in source['decks']:
        deck.setdefault('structure_thickness_m', .1)
        deck.setdefault('filling', dict(id='gtw.filling.none', version=1))
        for region in deck['regions']:
            for armor in region['edge_armor']:
                armor.setdefault('flare_angle_deg', 0)


def _triangle_area(a, b, c):
    u, v = tuple(y-x for x, y in zip(a, b)), tuple(y-x for x, y in zip(a, c))
    cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    return sqrt(sum(x*x for x in cross)) / 2


@dataclass(frozen=True)
class ArmorSurface:
    kind: str
    edge_index: int
    vertex_index: int | None
    vertices_m: tuple

    def area_less(self, axis, value):
        """Exact surface area on one side of a structural cut (3D clipping)."""
        points = []
        p = self.vertices_m
        for a, b in zip(p, p[1:]+p[:1]):
            if (a[axis] <= value) != (b[axis] <= value):
                t = (value-a[axis])/(b[axis]-a[axis])
                points.append(tuple(a[k]+t*(b[k]-a[k]) for k in range(3)))
            if b[axis] <= value:
                points.append(b)
        return sum(_triangle_area(points[0], points[i], points[i+1]) for i in range(1, len(points)-1))

    def load_cut_length(self, axis, value):
        """Cut length projected onto the plate's in-plane load direction.

        For a vertical plate this reduces to the legacy height * edge direction
        component. A face parallel to the cut transfers no axial plate load.
        """
        p = self.vertices_m
        crossings = []
        for a, b in zip(p, p[1:]+p[:1]):
            if (a[axis] <= value < b[axis]) or (b[axis] <= value < a[axis]):
                t = (value-a[axis])/(b[axis]-a[axis])
                crossings.append(tuple(a[k]+t*(b[k]-a[k]) for k in range(3)))
        if len(crossings) < 2:
            return 0.
        a, b, c = p[:3]
        u, v = tuple(b[k]-a[k] for k in range(3)), tuple(c[k]-a[k] for k in range(3))
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        norm2 = sum(x*x for x in n)
        length = max(sqrt(sum((a[k]-b[k])**2 for k in range(3))) for a in crossings for b in crossings)
        return length * sqrt(max(0., 1-n[axis]**2/norm2)) if norm2 > EPS else 0.

    @property
    def area_m2(self):
        p = self.vertices_m
        return sum(_triangle_area(p[0], p[i], p[i+1]) for i in range(1, len(p)-1))

    @property
    def polar_area_moment_m4(self):
        # Exact integral of x²+y² over the actual triangular surface in 3D.
        p = self.vertices_m
        result = 0.
        for i in range(1, len(p)-1):
            a, b, c = p[0], p[i], p[i+1]
            result += _triangle_area(a, b, c) / 6 * sum(
                a[k]**2+b[k]**2+c[k]**2+a[k]*b[k]+b[k]*c[k]+c[k]*a[k] for k in (0, 1))
        return result

    def to_dict(self):
        return dict(kind=self.kind, edge_index=self.edge_index, vertex_index=self.vertex_index,
                    vertices_m=[list(p) for p in self.vertices_m], area_m2=self.area_m2,
                    polar_area_moment_m4=self.polar_area_moment_m4)


@dataclass(frozen=True)
class ArmorEdgeGeometry:
    edge_index: int
    flare_angle_deg: int
    outward_distance_m: float
    tilt_cosine: float
    upper_edge_m: tuple
    lower_edge_m: tuple
    projection_m: tuple
    surfaces: tuple[ArmorSurface, ...]

    @property
    def area_m2(self):
        return sum(s.area_m2 for s in self.surfaces)

    def to_dict(self):
        return dict(edge_index=self.edge_index, flare_angle_deg=self.flare_angle_deg,
                    outward_distance_m=self.outward_distance_m, tilt_cosine=self.tilt_cosine,
                    upper_edge_m=[list(p) for p in self.upper_edge_m], lower_edge_m=[list(p) for p in self.lower_edge_m],
                    projection_m=[list(p) for p in self.projection_m], area_m2=self.area_m2,
                    surfaces=[s.to_dict() for s in self.surfaces])


@dataclass(frozen=True)
class ArmorRegionGeometry:
    deck_id: str
    deck_level: int
    region_id: str
    structure_outline_m: tuple
    outer_outline_m: tuple
    edges: tuple[ArmorEdgeGeometry, ...]

    def to_dict(self):
        return dict(deck_id=self.deck_id, deck_level=self.deck_level, region_id=self.region_id,
                    structure_outline_m=[list(p) for p in self.structure_outline_m],
                    outer_outline_m=[list(p) for p in self.outer_outline_m], edges=[e.to_dict() for e in self.edges])


@dataclass(frozen=True)
class ArmorGeometry:
    regions: tuple[ArmorRegionGeometry, ...]
    bounds_min_m: tuple
    bounds_max_m: tuple
    has_flare: bool

    def to_dict(self):
        return dict(interface=ARMOR_GEOMETRY_INTERFACE, integration_stage='installation_and_mass',
                    has_flare=self.has_flare, bounds_min_m=list(self.bounds_min_m), bounds_max_m=list(self.bounds_max_m),
                    regions=[r.to_dict() for r in self.regions])


def _positive_overlap(first, second):
    from 高天荒野舰艇无界面船壳编译器 import point_inside_polygon, _segments_properly_intersect
    if any(point_inside_polygon(p, second) for p in first) or any(point_inside_polygon(p, first) for p in second):
        return True
    if any(_segments_properly_intersect(a, b, c, d)
           for a, b in zip(first, first[1:]+first[:1]) for c, d in zip(second, second[1:]+second[:1])):
        return True
    # The first polygon is always a convex flare trapezoid. Its centre is an
    # interior witness for coincident boundaries (vertices alone are insufficient).
    centre = tuple(sum(p[k] for p in first)/len(first) for k in (0, 1))
    return point_inside_polygon(centre, second)


def _region_geometry(deck, region, height, path):
    from 高天荒野舰艇无界面船壳编译器 import validate_simple_polygon, polygon_fully_contained, polygon_area
    points, armors = region.vertices_m, region.edge_armor
    count = len(points)
    normals = []
    for a, b in zip(points, points[1:]+points[:1]):
        length = hypot(b[0]-a[0], b[1]-a[1])
        normals.append(((b[1]-a[1])/length, (a[0]-b[0])/length))
    angles = [a.flare_angle_deg for a in armors]
    distances = [height*tan(radians(a)) if a else 0. for a in angles]
    miters = []
    for i in range(count):
        a, b = normals[i-1], normals[i]
        divisor = 1+a[0]*b[0]+a[1]*b[1]
        if divisor <= EPS and (angles[i-1] or angles[i]):
            raise ContractError('hull.armor_flare_corner', f'{path}.vertices_m[{i}]', '该拐角无法生成有限的角平分线外飘，请调整轮廓')
        miters.append(((a[0]+b[0])/divisor, (a[1]+b[1])/divisor) if divisor > EPS else (0., 0.))
    def shifted(i, distance):
        return tuple(points[i][k]+miters[i][k]*distance for k in (0, 1))
    bottom = [(shifted(i, distances[i]), shifted((i+1)%count, distances[i])) for i in range(count)]
    lower_z, upper_z = deck.level*height, (deck.level+1)*height
    surfaces, projections = [[] for _ in points], []
    outline = []
    for i, (a, b) in enumerate(zip(points, points[1:]+points[:1])):
        q, r = bottom[i]
        if (r[0]-q[0])*(b[0]-a[0])+(r[1]-q[1])*(b[1]-a[1]) <= EPS:
            raise ContractError('hull.armor_flare_fold', f'{path}.edge_armor[{i}]', '外飘下缘发生折返或收缩为零，请减小倾角或调整短边')
        polygon = (a, q, r, b) if angles[i] else ()
        if polygon:
            validate_simple_polygon(polygon, f'{path}.edge_armor[{i}]')
            if _positive_overlap(polygon, points):
                raise ContractError('hull.armor_flare_overlap', f'{path}.edge_armor[{i}]', '外飘斜面投影侵入本层结构轮廓')
        projections.append(polygon)
        surfaces[i].append(ArmorSurface('side', i, None, ((a[0],a[1],upper_z), (q[0],q[1],lower_z),
            (r[0],r[1],lower_z), (b[0],b[1],upper_z))))
        before = bottom[i-1][1]
        if not outline or hypot(outline[-1][0]-before[0], outline[-1][1]-before[1]) > EPS:
            outline.append(before)
        if hypot(before[0]-q[0], before[1]-q[1]) > EPS:
            outline.append(q)
            owner = i if angles[i] > angles[i-1] else (i-1)%count
            surfaces[owner].append(ArmorSurface('corner', owner, i,
                ((a[0],a[1],upper_z), (before[0],before[1],lower_z), (q[0],q[1],lower_z))))
    if len(outline)>1 and hypot(outline[0][0]-outline[-1][0], outline[0][1]-outline[-1][1])<=EPS:
        outline.pop()
    outline = tuple(outline)
    try:
        validate_simple_polygon(outline, path)
    except ContractError as error:
        raise ContractError('hull.armor_flare_outline', path, '外飘外轮廓自交、接触或退化，请调整倾角或轮廓') from error
    if not polygon_fully_contained(points, outline):
        raise ContractError('hull.armor_flare_outline', path, '外飘外轮廓未完整包围本层结构')
    for i, polygon in enumerate(projections):
        if not polygon: continue
        for j in range(i):
            if projections[j] and _positive_overlap(polygon, projections[j]):
                raise ContractError('hull.armor_flare_overlap', f'{path}.edge_armor[{i}]', f'外飘投影与边{j}重叠')
    if abs(polygon_area(outline)-polygon_area(points)-sum(polygon_area(p) for p in projections if p)) > 1e-6*max(1.,polygon_area(outline)):
        raise ContractError('hull.armor_flare_outline', path, '外飘投影存在缺口或重复覆盖')
    edges = tuple(ArmorEdgeGeometry(i, angles[i], distances[i], cos(radians(angles[i])),
        ((points[i][0],points[i][1],upper_z),(points[(i+1)%count][0],points[(i+1)%count][1],upper_z)),
        tuple((p[0],p[1],lower_z) for p in bottom[i]), projections[i], tuple(surfaces[i])) for i in range(count))
    return ArmorRegionGeometry(deck.id, deck.level, region.id, points, outline, edges)


def compile_armor_geometry(decks, height):
    """Inputs have already passed normalization, symmetry and structural support."""
    from 高天荒野舰艇无界面船壳编译器 import polygon_fully_contained, polygons_overlap_or_touch
    result = []
    for di, deck in enumerate(decks):
        level = []
        for ri, region in enumerate(deck.regions):
            path = f'$.decks[{di}].regions[{ri}]'
            geometry = _region_geometry(deck, region, height, path)
            # Same-layer regions cannot touch, so each connected envelope must
            # fit a single lower component; a union cannot bridge a real gap.
            if di and not any(polygon_fully_contained(geometry.outer_outline_m, lower.vertices_m) for lower in decks[di-1].regions):
                raise ContractError('hull.armor_flare_unsupported', path, '整个外飘区域必须由相邻下层原船壳轮廓承接')
            for other in level:
                if polygons_overlap_or_touch(geometry.outer_outline_m, other.outer_outline_m):
                    raise ContractError('hull.armor_flare_regions_touch', path, f'外飘外形与同层区域{other.region_id}相交或接触')
            level.append(geometry)
        result.extend(level)
    xyz = [p for r in result for e in r.edges for s in e.surfaces for p in s.vertices_m]
    return ArmorGeometry(tuple(result), tuple(min(p[k] for p in xyz) for k in range(3)),
                         tuple(max(p[k] for p in xyz) for k in range(3)), any(e.flare_angle_deg for r in result for e in r.edges))
