"""只读编辑预览：规范船壳减去完整内部格，不进入旧编译结果或资源。"""
from __future__ import annotations

from collections import defaultdict
from math import ceil, floor, fsum, isclose

from 高天荒野舰艇无界面船壳编译器 import DECK_HEIGHT_M, EPS, polygon_area

EDGE_SPACE_INTERFACE = "gaotian.hull-edge-space/v1alpha1"


def _row_runs(cells):
    rows = defaultdict(list)
    for x, y in cells:
        rows[y].append(x)
    result = {}
    for y, xs in rows.items():
        runs = []
        for x in sorted(set(xs)):
            left, right = x * 5 - 2.5, x * 5 + 2.5
            if runs and runs[-1][1] == left:
                runs[-1] = (runs[-1][0], right)
            else:
                runs.append((left, right))
        result[y] = runs
    return result


def build_deck_edge_space(regions: list[dict], internal_cells) -> dict:
    """Partition a legal deck difference into disjoint triangles/trapezoids.

    Horizontal bands end at every polygon vertex and grid row boundary. Within
    each band, polygon sides are linear and occupied cell runs are constant.
    Pair scanline crossings (including concave regions), then subtract full runs.
    Pieces retain their source region; they are not independent storage tanks.
    """
    rows = _row_runs(internal_cells)
    pieces = []

    def emit(region_id, lo, hi, left, right):
        vertices = []
        for point in ((left[0], lo), (right[0], lo), (right[1], hi), (left[1], hi)):
            if not vertices or point != vertices[-1]:
                vertices.append(point)
        if vertices and vertices[0] == vertices[-1]:
            vertices.pop()
        area = polygon_area(vertices) if len(vertices) >= 3 else 0.0
        if area > EPS:
            pieces.append(dict(region_id=region_id, vertices_m=[list(p) for p in vertices], area_m2=area))

    for region in regions:
        vertices = region["vertices_m"]
        ys = [p[1] for p in vertices]
        bottom, top = min(ys), max(ys)
        cuts = sorted(set(ys) | {i * 5 + 2.5 for i in range(
            ceil((bottom - 2.5) / 5), floor((top - 2.5) / 5) + 1)})
        edges = list(zip(vertices, vertices[1:] + vertices[:1]))
        for lo, hi in zip(cuts, cuts[1:]):
            mid = (lo + hi) / 2
            crossings = []
            for a, b in edges:
                if min(a[1], b[1]) < mid < max(a[1], b[1]):
                    def at(y):
                        return a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                    crossings.append((at(mid), at(lo), at(hi)))
            crossings.sort()
            if len(crossings) % 2:
                raise ValueError("合法船壳扫描线交点必须成对")
            for left, right in zip(crossings[::2], crossings[1::2]):
                cursor = left[1:]
                for start, end in rows.get(floor((mid + 2.5) / 5), []):
                    if end <= left[0] + EPS or start >= right[0] - EPS:
                        continue
                    # Full cells must lie inside this entire band, not just its midpoint.
                    if start < max(left[1:]) - EPS or end > min(right[1:]) + EPS:
                        raise ValueError("安装格不在船壳扫描带内")
                    emit(region["id"], lo, hi, cursor, (start, start))
                    cursor = (end, end)
                emit(region["id"], lo, hi, cursor, right[1:])
    area = fsum(p["area_m2"] for p in pieces)
    expected = fsum(polygon_area(r["vertices_m"]) for r in regions) - len(internal_cells) * 25
    if not isclose(area, expected, rel_tol=1e-9, abs_tol=1e-7):
        raise ValueError("船壳边缘面积分区不守恒")
    return dict(interface=EDGE_SPACE_INTERFACE, pieces=pieces, area_m2=area,
                gross_volume_m3=area * DECK_HEIGHT_M)
