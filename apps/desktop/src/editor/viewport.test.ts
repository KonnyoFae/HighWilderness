import { describe, expect, it } from "vitest";
import { fit, gridLines, lowerDeck, pick, screen, snap, world, zoom } from "./viewport";
import type { HullDeck, HullRegion } from "./model";
const region: HullRegion = { id: "a", vertices_m: [[-10, -10], [10, -10], [10, 10], [-10, 10]], edge_armor: [] };
describe("viewport geometry", () => {
  it("round trips negative and fractional world coordinates at all supported scales", () => {
    for (const scale of [0.5, 4, 64]) for (const p of [{ x: -123.125, y: 64.25 }, { x: 0, y: 0 }]) {
      const result = world(screen(p, { x: 123, y: 256, scale }), { x: 123, y: 256, scale });
      expect(result.x).toBeCloseTo(p.x, 10); expect(result.y).toBeCloseTo(p.y, 10);
    }
  });
  it("zoom preserves the world point beneath the cursor even at the clamp", () => {
    const c = { x: 50, y: 100, scale: 4 }, p = { x: 153, y: 213 };
    for (const factor of [0.001, 1.25, 100]) {
      const changed = zoom(c, p, factor);
      expect(world(p, changed)).toEqual(world(p, c));
      expect(changed.scale).toBeGreaterThanOrEqual(0.5); expect(changed.scale).toBeLessThanOrEqual(64);
    }
  });
  it("snaps to mandatory half-grid multiples deterministically including negative coordinates", () => {
    expect(snap({ x: -8, y: 12 })).toEqual({ x: -7.5, y: 12.5 });
    expect(snap(snap({ x: -8, y: 12 }))).toEqual(snap({ x: -8, y: 12 }));
  });
  it("fits all vertices within the padded viewport", () => {
    const c = fit([region], 600, 440);
    for (const [x, y] of region.vertices_m) {
      const p = screen({ x, y }, c);
      expect(p.x).toBeGreaterThanOrEqual(40); expect(p.x).toBeLessThanOrEqual(560);
      expect(p.y).toBeGreaterThanOrEqual(40); expect(p.y).toBeLessThanOrEqual(400);
    }
  });
  it("picks vertices before fills, uses screen pixel tolerance and stable overlap order", () => {
    const c = { x: 100, y: 100, scale: 4 }, overlap = { ...region, id: "b" };
    expect(pick([region, overlap], { x: 100, y: 100 }, c)).toEqual({ region: "b", vertex: null });
    expect(pick([region], { x: 63, y: 140 }, c)).toEqual({ region: "a", vertex: 0 });
    expect(pick([region], { x: 200, y: 200 }, c)).toBeNull();
    expect(pick([], { x: 0, y: 0 }, c)).toBeNull();
  });
  it("aligns five metre cell boundaries around the origin rather than through its centre", () => {
    expect(gridLines(-8, 8, 4).filter(l => l.boundary).map(l => l.value)).toEqual([-7.5, -2.5, 2.5, 7.5]);
    expect(gridLines(-8, 8, 4).map(l => l.value)).toContain(0);
    expect(snap({ x: -7.5, y: -20 })).toEqual({ x: -7.5, y: -20 });
  });

  it("distinguishes half, one, five and ten cells on both sides of the origin", () => {
    const lines = gridLines(-55,55,4);
    for (const sign of [-1,1]) {
      expect(lines.find(l=>l.value===sign*2.5)?.tier).toBe("half");
      expect(lines.find(l=>l.value===sign*5)?.tier).toBe("one");
      expect(lines.find(l=>l.value===sign*25)?.tier).toBe("five");
      expect(lines.find(l=>l.value===sign*50)?.tier).toBe("ten");
    }
    expect(new Set(lines.filter(l=>l.value>0).map(l=>l.color)).size).toBe(4);
  });

  it("keeps distant grid references aligned with world coordinates including zero", () => {
    const lines=gridLines(-101,101,.5);
    expect(lines.map(l=>l.value)).toEqual([-100,-75,-50,-25,0,25,50,75,100]);
    expect(lines.find(l=>l.value===0)?.tier).toBe("axis");
  });

  it("uses the immediately lower level regardless of array order, without substituting across a gap", () => {
    const base = { id: "base", level: 0, regions: [region] } as HullDeck;
    const middle = { id: "middle", level: 1, regions: [] } as unknown as HullDeck;
    const upper = { id: "upper", level: 2, regions: [] } as unknown as HullDeck;
    const decks = [upper, base, middle];
    expect(lowerDeck(decks, upper)).toBe(middle);
    expect(lowerDeck(decks, middle)).toBe(base);
    expect(lowerDeck(decks, base)).toBeUndefined();
    expect(lowerDeck([base, upper], upper)).toBeUndefined();
    expect(lowerDeck([], undefined)).toBeUndefined();
  });

});
