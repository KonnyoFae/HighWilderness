"""3c finite deck-local fire regions; geometry is compiled only at entry."""
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from math import ceil, floor, hypot
from pathlib import Path
import json

POLICY = 'gaotian.spatial-fire/same-deck-separated-surfaces/3c-v1'


@lru_cache(maxsize=1)
def policy():
    from . import persistent_ship as ps
    value = json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-spatial-fire.3c.json').read_text(encoding='utf-8'))
    ps.obj(value,'id spread_interval_steps spread_probability_at_unit_intensity child_intensity_ratio armor_damage_points_per_intensity_s incendiary_penetration_ratio','$.spatial_fire')
    ps.need(value['id']==POLICY,'$.spatial_fire.id','Unsupported spatial fire policy')
    ps.integer(value['spread_interval_steps'],'$.spread_interval_steps',1,3600)
    for key in ('spread_probability_at_unit_intensity','child_intensity_ratio','incendiary_penetration_ratio'):
        ps.number(value[key],'$.'+key,0.000001,1)
    ps.number(value['armor_damage_points_per_intensity_s'],'$.armor_damage_points_per_intensity_s',0,1000000)
    return value


def identity(*parts):
    return sha256(json.dumps(parts, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()[:20]


def seed(*parts):
    return int(identity(*parts)[:8], 16)


def random_step(value):
    value = (1664525*value+1013904223) & 0xffffffff
    return value, value/2**32


@dataclass(frozen=True)
class Zone:
    id: str
    level: int
    surface: bool
    center: tuple
    bounds: tuple
    modules: tuple
    armor: tuple
    neighbors: tuple = ()

    @property
    def label(self):
        return f'第 {self.level} 甲板 · {"舰外" if self.surface else "舰内"}（{self.center[0]:.1f}, {self.center[1]:.1f} 米）'


def touching(a, b):
    return all(a[0][i] <= b[1][i]+1e-7 and b[0][i] <= a[1][i]+1e-7 for i in (0, 1))


def compile_zones(hull, modules):
    """5 m installation cells and <=5 m armor segments; no inter-deck links.

    Surface and internal regions are distinct even when their projections overlap.
    Empty hull edges can burn, without requiring a surviving external module.
    """
    from dataclasses import replace
    rows, cells = {}, {}
    for m in modules:
        internal = {(level, x*5., y*5.) for level, x, y in m.internal_cells}
        exposed = {(level, x*5., y*5.) for level, x, y in m.top_cells}
        if not internal and not exposed:
            exposed = set(m.body_spatial_keys)
        for surface, keys in ((False, internal), (True, exposed)):
            for level, x, y in keys:
                cells.setdefault((level, surface, x, y), set()).add(m.id)
    for (level, surface, x, y), ids in sorted(cells.items()):
        key = 'fire.cell.'+identity(level, surface, x, y)
        rows[key] = Zone(key, level, surface, (x, y), ((x-2.5, y-2.5), (x+2.5, y+2.5)), tuple(sorted(ids)), ())
    for deck in hull.normalized_blueprint.decks:
        for region in deck.regions:
            for n, a in enumerate(region.vertices_m):
                b = region.vertices_m[(n+1) % len(region.vertices_m)]
                count = max(1, ceil(hypot(b[0]-a[0], b[1]-a[1])/5))
                edge = (deck.id, deck.level, region.id, n)
                for piece in range(count):
                    p = tuple(a[i]+(b[i]-a[i])*piece/count for i in (0, 1))
                    q = tuple(a[i]+(b[i]-a[i])*(piece+1)/count for i in (0, 1))
                    key = 'fire.armor.'+identity(edge, piece)
                    rows[key] = Zone(key, deck.level, True, tuple((p[i]+q[i])/2 for i in (0, 1)),
                        (tuple(min(p[i], q[i]) for i in (0, 1)), tuple(max(p[i], q[i]) for i in (0, 1))), (), (edge,))
    # Compile adjacency once, not in the fixed-step loop. Rectangular contact is
    # an explicit regional abstraction, including corner-touching cells.
    ordered = tuple(rows.values())
    edge_bindings = {z.id: z.armor for z in ordered}
    bins, buckets = {}, {}
    for z in ordered:
        buckets[z.id] = tuple((z.level,z.surface,x,y)
            for x in range(floor((z.bounds[0][0]-1e-7)/5),floor((z.bounds[1][0]+1e-7)/5)+1)
            for y in range(floor((z.bounds[0][1]-1e-7)/5),floor((z.bounds[1][1]+1e-7)/5)+1))
        for key in buckets[z.id]:
            bins.setdefault(key,set()).add(z.id)
    for a in ordered:
        nearby = {key for bucket in buckets[a.id] for key in bins[bucket]}
        neighbors = tuple(key for key in sorted(nearby) if key!=a.id and touching(a.bounds,rows[key].bounds))
        armor = a.armor or tuple(sorted({edge for k in neighbors for edge in edge_bindings[k]}))
        rows[a.id] = replace(a, neighbors=tuple(sorted(neighbors)), armor=armor)
    return rows


def validate_rows(fires, zones):
    from . import persistent_ship as ps
    for row in fires:
        if 'zone_id' in row:
            zone = zones.get(row['zone_id'])
            ps.need(zone is not None, '$.fires.zone_id', 'Fire region does not belong to this design')
            ps.need(row['module_id'] == (zone.modules[0] if zone.modules else None), '$.fires.module_id', 'Fire region module binding mismatch')


def nearest(zones, level, surface, point, module_id=None):
    choices = [z for z in zones.values() if z.level == level and z.surface == surface and
               (module_id is None or module_id in z.modules)]
    if not choices:
        return None
    return min(choices, key=lambda z:(hypot(z.center[0]-point[0], z.center[1]-point[1]), z.id))
