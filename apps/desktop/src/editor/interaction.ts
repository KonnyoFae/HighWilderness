import type { HullRegion } from "./model";
import type { Point } from "./viewport";
export const onGrid = (value: number) => Number.isFinite(value) && Math.abs(value) <= 1e6 && value % 2.5 === 0;
export function nextId(prefix: string, ids: string[]) {
  let i = 0; while (ids.includes(`${prefix}.${i}`)) i++;
  return `${prefix}.${i}`;
}
export function closedRegion(id: string, points: Point[], armor: HullRegion["edge_armor"][number]): HullRegion {
  if (points.length < 3 || points.length > 4096 || points.some(p => !onGrid(p.x) || !onGrid(p.y))) throw new Error("至少三个端点，坐标必须落在 2.5 米网格上");
  return { id, vertices_m: points.map(p => [p.x, p.y]), edge_armor: points.map(() => structuredClone(armor)) };
}
export function deferredCommit() {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    cancel() { clearTimeout(timer); timer = undefined; },
    schedule(commit: () => void) { clearTimeout(timer); timer = setTimeout(() => { timer = undefined; commit(); }, 150); },
  };
}
