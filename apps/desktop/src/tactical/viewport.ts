import { screen, world, zoom } from "../editor/viewport";
import type { Camera, Point } from "../editor/viewport";
import type { TacticalSnapshot, TacticalStatic, TacticalView } from "./model";

export type ShipGeometry = TacticalStatic["ships"][number];
export type ShipPose = TacticalSnapshot["ships"][number];
export type ModuleGeometry = ShipGeometry["modules"][number];
export type Footprint = { level: number; x: number; y: number; size: number; kind: "internal" | "top" | "body" };

// Core body_to_world: local +Y is the bow, positive heading rotates counterclockwise.
// The screen's Y inversion is applied only by screen()/the world container.
export function bodyToWorld(p: Point, ship: ShipPose): Point {
  const c = Math.cos(ship.heading_rad), s = Math.sin(ship.heading_rad);
  return { x: ship.position_m[0] + c * p.x - s * p.y, y: ship.position_m[1] + s * p.x + c * p.y };
}
export function worldToBody(p: Point, ship: ShipPose): Point {
  const x = p.x - ship.position_m[0], y = p.y - ship.position_m[1];
  const c = Math.cos(ship.heading_rad), s = Math.sin(ship.heading_rad);
  return { x: c * x + s * y, y: -s * x + c * y };
}
export function moduleFootprints(module: ModuleGeometry): Footprint[] {
  // These are compiled ship-local positions, already rotated and translated by installation.
  return [
    ...module.internal_cells.map(([level, x, y]) => ({ level, x: x * 5, y: y * 5, size: 5, kind: "internal" as const })),
    ...module.top_cells.map(([level, x, y]) => ({ level, x: x * 5, y: y * 5, size: 5, kind: "top" as const })),
    ...module.body_points.map(([level, x, y]) => ({ level, x, y, size: 5, kind: "body" as const })),
  ];
}
export function shipPoints(ship: ShipGeometry): Point[] {
  return [
    ...ship.decks.flatMap(d => d.regions.flatMap(r => r.vertices_m.map(([x, y]) => ({ x, y })))),
    ...ship.modules.flatMap(m => moduleFootprints(m).flatMap(p => [
      { x: p.x - p.size / 2, y: p.y - p.size / 2 }, { x: p.x + p.size / 2, y: p.y + p.size / 2 },
    ])),
  ];
}
export function fitScene(view: TacticalView, width: number, height: number, shipId?: string): Camera {
  const points = view.geometry.ships.filter(s => !shipId || s.id === shipId).flatMap(s => {
    const pose = view.snapshot.ships.find(p => p.id === s.id);
    return pose ? shipPoints(s).map(p => bodyToWorld(p, pose)) : [];
  });
  if (!points.length) return { x: width / 2, y: height / 2, scale: 1 };
  const left = Math.min(...points.map(p => p.x)), right = Math.max(...points.map(p => p.x));
  const bottom = Math.min(...points.map(p => p.y)), top = Math.max(...points.map(p => p.y));
  const scale = Math.max(.02, Math.min(24, Math.max(1, width - 100) / Math.max(10, right - left), Math.max(1, height - 100) / Math.max(10, top - bottom)));
  return { scale, x: width / 2 - (left + right) * scale / 2, y: height / 2 + (bottom + top) * scale / 2 };
}
export const zoomScene = (c: Camera, anchor: Point, factor: number) => zoom(c, anchor, factor, .02, 64);

function contains(vertices: number[][], p: Point): boolean {
  let inside = false;
  for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
    const [ax, ay] = vertices[j], [bx, by] = vertices[i];
    const cross = (p.x - ax) * (by - ay) - (p.y - ay) * (bx - ax);
    if (Math.abs(cross) < 1e-8 && p.x >= Math.min(ax, bx) && p.x <= Math.max(ax, bx) && p.y >= Math.min(ay, by) && p.y <= Math.max(ay, by)) return true;
    if ((ay > p.y) !== (by > p.y) && p.x < (bx - ax) * (p.y - ay) / (by - ay) + ax) inside = !inside;
  }
  return inside;
}
export function pickShip(view: TacticalView, point: Point, camera: Camera): string | null {
  const p = world(point, camera);
  for (const ship of [...view.geometry.ships].reverse()) {
    const pose = view.snapshot.ships.find(s => s.id === ship.id);
    if (!pose) continue;
    const local = worldToBody(p, pose);
    if (ship.decks.some(d => d.regions.some(r => contains(r.vertices_m, local))) ||
      ship.modules.some(m => moduleFootprints(m).some(f => Math.abs(local.x - f.x) <= f.size / 2 && Math.abs(local.y - f.y) <= f.size / 2))) return ship.id;
    // Small ships remain selectable when zoomed out; the list is a second accessible path.
    const center = screen({ x: pose.position_m[0], y: pose.position_m[1] }, camera);
    if (Math.hypot(center.x - point.x, center.y - point.y) <= 7) return ship.id;
  }
  return null;
}

export function gridSpacing(scale: number): number {
  const base = 10 ** Math.floor(Math.log10(65 / scale));
  return [1, 2, 5, 10].map(n => n * base).find(n => n * scale >= 65)!;
}

export function pickModules(view: TacticalView, point: Point, camera: Camera, shipId?: string, weaponsOnly = false, level?: number) {
  const p = world(point, camera);
  return view.geometry.ships.filter(s => !shipId || s.id === shipId).flatMap(ship => {
    const pose = view.snapshot.ships.find(s => s.id === ship.id);
    if (!pose) return [];
    const local = worldToBody(p, pose);
    return ship.modules.filter(m => !weaponsOnly || m.category === "weapon").filter(m => {
      const cells = moduleFootprints(m).filter(f => level === undefined || f.level === level);
      if (cells.some(f => Math.abs(local.x-f.x) <= f.size/2 && Math.abs(local.y-f.y) <= f.size/2)) return true;
      const anchor = screen(bodyToWorld({ x: m.anchor_m[0], y: m.anchor_m[1] }, pose), camera);
      return (level === undefined || m.deck_level === level) && Math.hypot(anchor.x-point.x, anchor.y-point.y) <= 7;
    }).map(m => ({ shipId: ship.id, moduleId: m.id, name: m.name, level: m.deck_level }));
  }).sort((a, b) => b.level-a.level || a.moduleId.localeCompare(b.moduleId));
}
