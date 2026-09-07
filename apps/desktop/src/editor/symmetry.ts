import type { HullRegion } from "./model";
import type { Point } from "./viewport";
import { onGrid } from "./interaction";

type Vertex = HullRegion["vertices_m"][number];
type Armor = HullRegion["edge_armor"][number];
export type SourceSide = "left" | "right";
const equal = (a: Vertex, b: Vertex) => a[0] === b[0] && a[1] === b[1];
const reflected = ([x, y]: Vertex): Vertex => [-x, y];
const armorKey = (a: Armor) => JSON.stringify([a.material.id, a.material.version, a.thickness_m]);

// Authoring geometry only. The sidecar still validates and compiles the submitted region.
function joinHalf(id: string, vertices: Vertex[], armor: Armor[]): HullRegion {
  if (vertices.length < 3 || vertices[0][0] !== 0 || vertices.at(-1)![0] !== 0 || equal(vertices[0], vertices.at(-1)!)) {
    throw new Error("从中线 X=0 起笔，沿一侧绘制，再回到中线的另一点结束。");
  }
  const sign = Math.sign(vertices[1][0]);
  if (!sign || vertices.slice(1, -1).some(([x]) => Math.sign(x) !== sign)) {
    throw new Error("请沿同一侧绘制；只有起点和终点可以位于中线。");
  }
  const result = [...vertices, ...vertices.slice(1, -1).reverse().map(reflected)];
  if (result.length > 4096 || result.some(p => p.some(v => !onGrid(v)))) {
    throw new Error("镜像后的端点须在 2.5 米网格内，且总数不超过 4096。");
  }
  return { id, vertices_m: result.map(p => [...p]), edge_armor: [...armor, ...[...armor].reverse()].map(a => structuredClone(a)) };
}

export function symmetricDrawing(id: string, points: Point[], armor: Armor): HullRegion {
  return joinHalf(id, points.map(p => [p.x, p.y]), points.slice(1).map(() => armor));
}

export function drawingPoint(points: Point[], p: Point, symmetric: boolean): Point {
  if (!onGrid(p.x) || !onGrid(p.y)) throw new Error("端点必须落在 2.5 米网格内。");
  if (!symmetric) return p;
  if (!points.length) return { x: 0, y: p.y };
  if (points.length > 1 && points.at(-1)!.x === 0) throw new Error("单侧轮廓已回到中线，请点击生成对称船壳，或撤回上一点。");
  const sign = Math.sign(points.find(q => q.x !== 0)?.x ?? p.x);
  if (p.x !== 0 && Math.sign(p.x) !== sign) throw new Error("请继续沿同一侧绘制，另一侧会自动镜像。");
  if (points.length === 1 && p.x === 0) throw new Error("请先在中线的一侧添加轮廓点。");
  return p;
}

export function symmetrizeRegion(region: HullRegion, side: SourceSide): HullRegion {
  const sign = side === "left" ? -1 : 1;
  type Segment = { a: Vertex; b: Vertex; armor: Armor };
  const chains: Segment[][] = [];
  const axis = (a: Vertex, b: Vertex): Vertex => {
    const y = a[1] + (b[1] - a[1]) * (-a[0]) / (b[0] - a[0]);
    if (!onGrid(y)) throw new Error("轮廓与中线的交点未落在 2.5 米网格上，请先调整跨中线的边。");
    return [0, y];
  };
  region.vertices_m.forEach((a, i) => {
    const b = region.vertices_m[(i + 1) % region.vertices_m.length];
    if (a[0] * sign <= 0 && b[0] * sign <= 0) return;
    const start = a[0] * sign >= 0 ? a : axis(a, b);
    const end = b[0] * sign >= 0 ? b : axis(a, b);
    if (equal(start, end)) return;
    const previous = chains.at(-1);
    const segment = { a: start, b: end, armor: region.edge_armor[i] };
    if (previous && equal(previous.at(-1)!.b, start)) previous.push(segment);
    else chains.push([segment]);
  });
  if (chains.length > 1 && equal(chains.at(-1)!.at(-1)!.b, chains[0][0].a)) {
    const last = chains.pop()!;
    chains[0] = [...last, ...chains[0]];
  }
  if (chains.length !== 1) throw new Error("所选侧须为一段连接中线的连续轮廓；分离或多次穿越中线的区域请分别编辑。");
  const chain = chains[0];
  return joinHalf(region.id, [chain[0].a, ...chain.map(e => e.b)], chain.map(e => e.armor));
}

// Ignore winding, starting vertex and collinear splits with identical armor.
export function sameBoundary(a: HullRegion, b: HullRegion): boolean {
  function key(region: HullRegion) {
    const r = structuredClone(region);
    let changed = true;
    while (changed && r.vertices_m.length > 3) {
      changed = false;
      for (let i = 0; i < r.vertices_m.length; i++) {
        const prev = (i + r.vertices_m.length - 1) % r.vertices_m.length;
        const p = r.vertices_m[prev], q = r.vertices_m[i], t = r.vertices_m[(i + 1) % r.vertices_m.length];
        if ((q[0]-p[0])*(t[1]-q[1]) === (q[1]-p[1])*(t[0]-q[0]) &&
            (q[0]-p[0])*(t[0]-q[0])+(q[1]-p[1])*(t[1]-q[1]) > 0 && armorKey(r.edge_armor[prev]) === armorKey(r.edge_armor[i])) {
          r.vertices_m.splice(i, 1); r.edge_armor.splice(i, 1); changed = true; break;
        }
      }
    }
    return r.vertices_m.map((p, i) => JSON.stringify([...[p, r.vertices_m[(i+1)%r.vertices_m.length]].sort((u,v)=>u[0]-v[0] || u[1]-v[1]), armorKey(r.edge_armor[i])])).sort().join("|");
  }
  return key(a) === key(b);
}
