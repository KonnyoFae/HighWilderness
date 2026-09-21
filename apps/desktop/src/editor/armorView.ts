import type { HullRegion } from './model';
import { screen, world } from './viewport';
import type { Camera, Point, Selection } from './viewport';

type XY = [number, number];
export interface ArmorEdgeView {
  edge_index: number; flare_angle_deg: number; area_m2: number;
  upper_edge_m: number[][]; projection_m: XY[];
}
export interface ArmorRegionView {
  deck_id: string; region_id: string; deck_level: number;
  structure_outline_m: XY[]; outer_outline_m: XY[]; edges: ArmorEdgeView[];
}
export interface ArmorGeometryView { regions: ArmorRegionView[]; has_flare: boolean }

// Compiled edges are normalized. Resolve their original identity by endpoints;
// editor drafts can start at another vertex or have the opposite winding.
export function sourceEdgeIndex(region: HullRegion, edge: ArmorEdgeView) {
  const same = (p: number[], q: number[]) => Math.hypot(p[0]-q[0], p[1]-q[1]) < 1e-7;
  return region.vertices_m.findIndex((a, i) => {
    const b = region.vertices_m[(i+1)%region.vertices_m.length], [c,d] = edge.upper_edge_m;
    return same(a,c) && same(b,d) || same(a,d) && same(b,c);
  });
}
export function armorFitRegions(regions: HullRegion[], geometry?: ArmorGeometryView, deckId?: string) {
  return regions.map(r => ({...r, vertices_m: geometry?.regions.find(g=>g.deck_id===deckId && g.region_id===r.id)?.outer_outline_m ?? r.vertices_m}));
}
function inside(p: Point, vertices: XY[]) {
  let result = false;
  for (let i=0,j=vertices.length-1;i<vertices.length;j=i++) {
    const [x,y]=vertices[i], [u,v]=vertices[j];
    if ((y>p.y)!==(v>p.y) && p.x<(u-x)*(p.y-y)/(v-y)+x) result=!result;
  }
  return result;
}
export function pickArmor(regions: HullRegion[], shapes: ArmorRegionView[], p: Point, camera: Camera): Selection | null {
  let distance=10, best: Selection | null=null;
  for (const r of regions) for (let i=0;i<r.vertices_m.length;i++) {
    const a=screen({x:r.vertices_m[i][0],y:r.vertices_m[i][1]},camera);
    const next=r.vertices_m[(i+1)%r.vertices_m.length], b=screen({x:next[0],y:next[1]},camera);
    const dx=b.x-a.x,dy=b.y-a.y, t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy)));
    const d=Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
    if (d<distance) { distance=d; best={region:r.id,vertex:null,edge:i}; }
  }
  if (best) return best;
  for (const g of shapes) for (const e of g.edges) if (inside(world(p,camera),e.projection_m)) {
    const r=regions.find(r=>r.id===g.region_id), index=r ? sourceEdgeIndex(r,e) : -1;
    if (index>=0) return {region:g.region_id,vertex:null,edge:index};
  }
  return null;
}
