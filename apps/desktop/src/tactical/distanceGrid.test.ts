import { describe, expect, it } from 'vitest';
import { distanceGrid, gridOpacity, scaleReference } from './distanceGrid';
import { zoomScene } from './viewport';

describe('fixed tactical distance references', () => {
  it('uses 50/500/5000 metre tiers on both axes without duplicate coincident lines', () => {
    const lines = distanceGrid({ x: 600, y: 600, scale: .1 }, 1200, 1200);
    expect(new Set(lines.map(l => `${l.axis}:${l.world_m}`)).size).toBe(lines.length);
    for (const axis of ['x', 'y']) {
      expect(lines.find(l => l.axis === axis && l.world_m === 50)?.spacing).toBe(50);
      expect(lines.find(l => l.axis === axis && l.world_m === -500)?.spacing).toBe(500);
      expect(lines.find(l => l.axis === axis && l.world_m === -5000)?.spacing).toBe(5000);
      expect(lines.find(l => l.axis === axis && l.world_m === 0)?.spacing).toBe(5000);
    }
  });
  it('anchors negative and positive references to world space under pan and cursor-centred zoom', () => {
    const camera = { x: 700, y: 650, scale: .2 };
    for (const c of [camera, { ...camera, x: 731, y: 628 }, zoomScene(camera, { x: 437, y: 216 }, 1.4)]) {
      for (const line of distanceGrid(c, 1200, 1100)) {
        expect(line.world_m % line.spacing).toBeCloseTo(0);
        expect(line.pixel).toBeCloseTo(line.axis === 'x' ? c.x+line.world_m*c.scale : c.y-line.world_m*c.scale);
      }
    }
  });
  it('fades fine lines in a distant view without redefining the cell or creating dense loops', () => {
    expect(gridOpacity(50, .02)).toBe(0);
    expect(gridOpacity(50, 1)).toBeGreaterThan(0);
    const lines = distanceGrid({ x: 200, y: 200, scale: .02 }, 1920, 1080);
    expect(lines.every(line => line.world_m % 50 === 0)).toBe(true);
    expect(lines.some(line => line.spacing === 5000)).toBe(true);
    expect(lines.length).toBeLessThan(350);
    for (const scale of [.02, .1, 1, 24, 64]) {
      expect(scaleReference(scale)*scale).toBeGreaterThanOrEqual(65);
      expect(scaleReference(scale)*scale).toBeLessThanOrEqual(160);
    }
  });
});
