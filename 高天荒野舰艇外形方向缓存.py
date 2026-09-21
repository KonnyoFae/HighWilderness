"""A4 design-time directional caches from the shared armor surface mesh.

Four-point vertical quadrature approximates occlusion changes, not a flight-time
mesh query. Horizontal visibility uses a depth envelope shared by aero and RCS.
The old cache builders remain authoritative for hulls without active flare.
"""
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, cos, hypot, pi, radians, sin, sqrt

from 高天荒野舰艇气动缓存 import AerodynamicGeometryCache, AerodynamicDirectionSample, _line_intervals, _merge_intervals
from 高天荒野舰艇RCS缓存 import HullRCSCache, RCSDirectionSample, PROTOTYPE_RCS_PARAMETERS

MODEL = 'gaotian.hull-shape/a4-v1'
EPS = 1e-9
# Gauss-Legendre nodes and weights on [0,1], independent of face tessellation.
QUADRATURE = ((.06943184420297371,.17392742256872693),(.33000947820757187,.32607257743127307),
              (.6699905217924281,.32607257743127307),(.9305681557970262,.17392742256872693))


def shape_effect_view(hull,coating=1.,external=0.):
    aero=hull.aerodynamic_cache;radar=hull.hull_rcs_cache
    return dict(model=aero.model,forward_projected_area_m2=aero.directions[0].projected_area_m2,
        lateral_projected_area_m2=aero.directions[90].projected_area_m2,wet_surface_area_m2=aero.wet_surface_area_m2,
        forward_rcs_m2=radar.directions[0].total_m2*coating+external,
        lateral_rcs_m2=radar.directions[90].total_m2*coating+external,external_rcs_m2=external)


def dot(a,b): return sum(x*y for x,y in zip(a,b))
def area(points): return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(points,points[1:]+points[:1])))/2


@dataclass(frozen=True)
class SectionEdge:
    start: tuple
    end: tuple
    normal: tuple
    facet: tuple


def normal(surface):
    a,b,c=surface.vertices_m[:3]
    u=tuple(y-x for x,y in zip(a,b));v=tuple(y-x for x,y in zip(a,c))
    n=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
    length=sqrt(dot(n,n))
    return tuple(x/length for x in n)


def section(region,z):
    edges=[]
    for e in region.edges:
        for surface in e.surfaces:
            n=normal(surface);points=[];vertices=surface.vertices_m
            for a,b in zip(vertices,vertices[1:]+vertices[:1]):
                if (a[2]<=z<b[2]) or (b[2]<=z<a[2]):
                    f=(z-a[2])/(b[2]-a[2]);points.append(tuple(a[k]+f*(b[k]-a[k]) for k in (0,1)))
            if len(points)!=2:continue
            a,b=points
            # Counterclockwise section, so right-hand XY normal points out.
            if (b[1]-a[1])*n[0]-(b[0]-a[0])*n[1]<0:a,b=b,a
            # Coplanar pieces share a coherent-area budget. Splitting a design
            # edge or triangulating the display cannot manufacture stealth.
            facet=(region.deck_id,region.region_id,*(round(x,9) for x in n),round(dot(n,vertices[0]),8))
            edges.append(SectionEdge(a,b,n,facet))
    return tuple(edges)


def outline(region,f):
    points=[]
    for i,e in enumerate(region.edges):
        a=e.upper_edge_m[0]
        for lower in (region.edges[i-1].lower_edge_m[1],e.lower_edge_m[0]):
            p=tuple(lower[k]+f*(a[k]-lower[k]) for k in (0,1))
            if not points or hypot(*(x-y for x,y in zip(p,points[-1])))>EPS:points.append(p)
    return tuple(points)


def visible(edges,sight,screen):
    candidates=[];events=set()
    for edge in edges:
        a,b=dot(edge.start,screen),dot(edge.end,screen)
        if abs(a-b)<=EPS:continue
        candidates.append((edge,a,b,dot(edge.start,sight),dot(edge.end,sight)))
        events.update((a,b))
    front={};rear={}
    ordered=sorted(events)
    for lo,hi in zip(ordered,ordered[1:]):
        if hi-lo<=EPS:continue
        mid=(hi+lo)/2
        hits=[(d0+(mid-a)/(b-a)*(d1-d0),edge) for edge,a,b,d0,d1 in candidates if min(a,b)<mid<max(a,b)]
        if not hits:continue
        near=max(hits,key=lambda x:x[0])[1];far=min(hits,key=lambda x:x[0])[1]
        front[near]=front.get(near,0.)+hi-lo;rear[far]=rear.get(far,0.)+hi-lo
    return front,rear


@lru_cache(maxsize=64)
def build_shape_caches(geometry,deck_height_m=5.,parameters=PROTOTYPE_RCS_PARAMETERS):
    """No material, HP, cargo or sensor state enters the immutable shape cache."""
    bands=[]
    levels=sorted({r.deck_level for r in geometry.regions})
    for level in levels:
        regions=tuple(r for r in geometry.regions if r.deck_level==level)
        for f,w in QUADRATURE:
            regions_edges=tuple(section(r,(level+f)*deck_height_m) for r in regions)
            bands.append((w*deck_height_m,tuple(e for edges in regions_edges for e in edges),
                          tuple(outline(r,f) for r in regions),regions_edges))
    all_points=tuple(p for r in geometry.regions for e in r.edges for p in (*e.upper_edge_m,*e.lower_edge_m))
    side_area=sum(s.area_m2 for r in geometry.regions for e in r.edges for s in e.surfaces)
    base=tuple(r for r in geometry.regions if r.deck_level==levels[0])
    # Upper flares cover lower exposed top surface; the base's underside grows.
    horizontal=sum(area(r.structure_outline_m)+area(r.outer_outline_m) for r in base)
    horizontal-=sum(area(r.outer_outline_m)-area(r.structure_outline_m) for r in geometry.regions if r.deck_level!=levels[0])
    aero=[];radar=[]
    for bearing in range(360):
        theta=radians(bearing);sight=(sin(theta),cos(theta));screen=(cos(theta),-sin(theta))
        lo=min(dot(p,sight) for p in all_points);hi=max(dot(p,sight) for p in all_points)
        projected=front_blunt=rear_blunt=corner=0.;facets={}
        for weight,edges,polygons,regions_edges in bands:
            front,rear=visible(edges,sight,screen)
            for e,width in front.items():
                mu=max(0.,dot(e.normal,sight));projection=width*weight
                projected+=projection;front_blunt+=projection*mu*mu
                if mu>EPS:
                    old=facets.get(e.facet,(0.,mu));facets[e.facet]=(old[0]+projection/mu,mu)
            for e,width in rear.items():rear_blunt+=width*weight*max(0.,-dot(e.normal,sight))**2
            for region_edges in regions_edges:
                by_start={(round(e.start[0],8),round(e.start[1],8)):e for e in region_edges}
                for e in region_edges:
                    following=by_start.get((round(e.end[0],8),round(e.end[1],8)))
                    if following is None or e not in front or following not in front:continue
                    a=tuple(y-x for x,y in zip(e.start,e.end));b=tuple(y-x for x,y in zip(following.start,following.end))
                    al=hypot(*a);bl=hypot(*b);turn=(a[0]*b[1]-a[1]*b[0])/(al*bl)
                    if turn>=-EPS:continue
                    mu=max(0.,dot(e.normal,sight));nu=max(0.,dot(following.normal,sight))
                    corner+=parameters.corner_scale*weight*min(al,bl,parameters.corner_length_cap_m)*min(1.,-turn)*min(1.,4*mu*nu)
        length=hi-lo;count=max(1,ceil(length/5.-1e-10));step=length/count;sections=[0.]
        # Coincident cuts through a corner must choose the same half-open side
        # under mirror/rotation; remove only sub-nanometre trig roundoff.
        wave_bands=tuple((weight,tuple(tuple((round(dot(p,sight),9),round(dot(p,screen),9)) for p in poly)
            for poly in polygons)) for weight,_,polygons,_ in bands)
        for i in range(count):
            x=round(lo+(i+.5)*step,9)
            sections.append(sum(weight*sum(b-a for a,b in _merge_intervals([
                interval for poly in polygons for interval in _line_intervals(poly,(1.,0.),(0.,1.),x)])) for weight,polygons in wave_bands))
        sections.append(0.)
        wave=sum((b-a)**2/step for a,b in zip(sections,sections[1:]))/length
        aero.append(AerodynamicDirectionSample(bearing,projected,front_blunt,rear_blunt,length,wave))
        ideal=4*pi/parameters.reference_wavelength_m**2
        specular=sum(parameters.specular_scale*ideal*a*a/(1+a/parameters.coherent_area_m2)*mu**parameters.specular_exponent for a,mu in facets.values())
        diffuse=sum(parameters.diffuse_scale*a*mu for a,mu in facets.values())
        radar.append(RCSDirectionSample(bearing,specular,diffuse,corner))
    values=[r.total_m2 for r in radar];minimum=min(range(360),key=values.__getitem__);maximum=max(range(360),key=values.__getitem__)
    return (AerodynamicGeometryCache(MODEL,1.,deck_height_m,side_area+horizontal,tuple(aero)),
            HullRCSCache(MODEL,1.,'LEVEL',1.,parameters,tuple(radar),values[minimum],minimum,sum(values)/360,values[maximum],maximum))
