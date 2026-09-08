import type { HullDeck, HullRegion } from "./model";

export type Point = { x: number; y: number };
export type Camera = Point & { scale: number };
export type Selection = { region: string; vertex: number | null };
export const screen = (p: Point, c: Camera): Point => ({ x: c.x + p.x * c.scale, y: c.y - p.y * c.scale });
export const world = (p: Point, c: Camera): Point => ({ x: (p.x - c.x) / c.scale, y: (c.y - p.y) / c.scale });
export const snap = (p: Point): Point => ({ x: Math.round(p.x / 2.5) * 2.5, y: Math.round(p.y / 2.5) * 2.5 });
export function zoom(c: Camera, anchor: Point, factor: number, minScale = 0.5, maxScale = 64): Camera {
  const p = world(anchor, c), scale = Math.max(minScale, Math.min(maxScale, c.scale * factor));
  return { scale, x: anchor.x - p.x * scale, y: anchor.y + p.y * scale };
}
export function fit(regions: HullRegion[], width: number, height: number): Camera {
  const points = regions.flatMap(r => r.vertices_m).filter(p => p.every(Number.isFinite));
  if (!points.length) return { x: width / 2, y: height / 2, scale: 4 };
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const left = Math.min(...xs), right = Math.max(...xs), bottom = Math.min(...ys), top = Math.max(...ys);
  const scale = Math.max(0.5, Math.min(32, (width - 80) / Math.max(10, right - left), (height - 80) / Math.max(10, top - bottom)));
  return { scale, x: width / 2 - (left + right) / 2 * scale, y: height / 2 + (bottom + top) / 2 * scale };
}
export function pick(regions: HullRegion[], p: Point, c: Camera): Selection | null {
  // Reverse drawing order resolves overlap deterministically; vertices win over fills.
  for (const r of [...regions].reverse()) {
    for (let i = 0; i < r.vertices_m.length; i++) {
      const v = screen({ x: r.vertices_m[i][0], y: r.vertices_m[i][1] }, c);
      if (Math.hypot(v.x - p.x, v.y - p.y) <= 8) return { region: r.id, vertex: i };
    }
  }
  const w = world(p, c);
  for (const r of [...regions].reverse()) {
    let inside = false;
    for (let i = 0, j = r.vertices_m.length - 1; i < r.vertices_m.length; j = i++) {
      const [xi, yi] = r.vertices_m[i], [xj, yj] = r.vertices_m[j];
      if ((yi > w.y) !== (yj > w.y) && w.x < (xj - xi) * (w.y - yi) / (yj - yi) + xi) inside = !inside;
    }
    if (inside) return { region: r.id, vertex: null };
  }
  return null;
}

// Installation cells are centred at 5*n; their boundaries are 5*n + 2.5.
export function gridLines(min: number, max: number, scale: number) {
  const step = scale * 2.5 < 3 ? 25 : 2.5;
  const offset = step === 25 ? 2.5 : 0;
  const result: { value: number; boundary: boolean }[] = [];
  for (let value = Math.ceil((min - offset) / step) * step + offset; value <= max; value += step) {
    result.push({ value, boundary: Math.abs(value % 5) === 2.5 });
  }
  return result;
}

export function lowerDeck(decks: HullDeck[], current: HullDeck | undefined): HullDeck | undefined {
  return current ? decks.find(d => d.level === current.level - 1) : undefined;
}
