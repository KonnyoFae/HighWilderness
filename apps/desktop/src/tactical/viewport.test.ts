import { describe, expect, it } from "vitest";
import { screen, world } from "../editor/viewport";
import { acceptSnapshot } from "./model";
import type { TacticalSnapshot } from "./model";
import { bodyToWorld, fitScene, gridSpacing, moduleFootprints, pickShip, shipPoints, worldToBody, zoomScene } from "./viewport";
import snapshot from "./testing/snapshot.fixture.json";

const view = acceptSnapshot(null, snapshot as TacticalSnapshot, "fixture.1");
describe("tactical coordinates and camera", () => {
  it("faces +Y at zero and -X at a positive quarter turn; round-trips arbitrary headings", () => {
    const pose = { ...view.snapshot.ships[0], position_m: [10, -20], heading_rad: Math.PI / 2 };
    expect(bodyToWorld({ x: 0, y: 5 }, pose).x).toBeCloseTo(5);
    expect(bodyToWorld({ x: 0, y: 5 }, pose).y).toBeCloseTo(-20);
    for (const heading_rad of [0, Math.PI, -.76, 1.4]) {
      const p = { x: -8, y: 31 }, rotated = { ...pose, heading_rad };
      const actual = worldToBody(bodyToWorld(p, rotated), rotated);
      expect(actual.x).toBeCloseTo(p.x); expect(actual.y).toBeCloseTo(p.y);
    }
    const red = view.snapshot.ships.find(s => s.id === "ship.web.red")!;
    expect(bodyToWorld({ x: 0, y: 10 }, red).y).toBeCloseTo(red.position_m[1] - 10);
  });
  it("converts level/x/y cells to metres without rotating compiled module occupancy twice", () => {
    const module = { ...view.geometry.ships[0].modules[0], rotation_deg: 90, anchor_m: [80, 90],
      internal_cells: [[1, 2, -3]], top_cells: [[2, 1, 4]], body_points: [[0, 12.5, -7.5]] };
    expect(moduleFootprints(module)).toEqual([
      { level: 1, x: 10, y: -15, size: 5, kind: "internal" },
      { level: 2, x: 5, y: 20, size: 5, kind: "top" },
      { level: 0, x: 12.5, y: -7.5, size: 5, kind: "body" },
    ]);
  });
  it("fits actual two-ship hull and external module bounds, including rotated ships", () => {
    for (const [width, height] of [[740, 540], [310, 440]]) {
      const camera = fitScene(view, width, height);
      for (const ship of view.geometry.ships) {
        const pose = view.snapshot.ships.find(s => s.id === ship.id)!;
        for (const p of shipPoints(ship)) {
          const pixel = screen(bodyToWorld(p, pose), camera);
          expect(pixel.x).toBeGreaterThanOrEqual(49); expect(pixel.x).toBeLessThanOrEqual(width - 49);
          expect(pixel.y).toBeGreaterThanOrEqual(49); expect(pixel.y).toBeLessThanOrEqual(height - 49);
        }
      }
      expect(fitScene(view, width, height, view.geometry.ships[0].id).scale).toBeGreaterThan(camera.scale);
    }
  });
  it("selects both real ships and external modules without confusing blank space", () => {
    const camera = fitScene(view, 740, 540);
    for (const pose of view.snapshot.ships) {
      const point = screen({ x: pose.position_m[0], y: pose.position_m[1] }, camera);
      expect(pickShip(view, point, camera)).toBe(pose.id);
    }
    expect(pickShip(view, screen({ x: 1000, y: 1000 }, camera), camera)).toBeNull();
    const ship = view.geometry.ships[0], pose = view.snapshot.ships[0];
    const body = ship.modules.flatMap(moduleFootprints).find(p => p.kind === "body")!;
    expect(pickShip(view, screen(bodyToWorld(body, pose), camera), camera)).toBe(ship.id);
  });
  it("keeps the cursor world point fixed through zoom clamps and never mutates authority", () => {
    const before = JSON.stringify(view), anchor = { x: 173, y: 304 };
    const camera = fitScene(view, 740, 540), p = world(anchor, camera);
    for (const factor of [.0001, .8, 1.3, 10000]) {
      const next = zoomScene(camera, anchor, factor), q = world(anchor, next);
      expect(q.x).toBeCloseTo(p.x); expect(q.y).toBeCloseTo(p.y);
      expect(next.scale).toBeGreaterThanOrEqual(.02); expect(next.scale).toBeLessThanOrEqual(64);
      expect(gridSpacing(next.scale) * next.scale).toBeGreaterThanOrEqual(65);
      pickShip(view, anchor, next);
    }
    expect(JSON.stringify(view)).toBe(before);
  });
});
