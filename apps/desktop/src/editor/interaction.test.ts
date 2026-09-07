import { afterEach, describe, expect, it, vi } from "vitest";
import { closedRegion, deferredCommit, nextId, onGrid } from "./interaction";
const armor = { material: { id: "armor.test", version: 1 }, thickness_m: 0.1 };
afterEach(() => vi.useRealTimers());
describe("local hull edit lifecycle", () => {
  it("rejects incomplete and off-grid drawings before submitting, and detaches edge armor", () => {
    expect(() => closedRegion("r", [{x:0,y:0}], armor)).toThrow();
    expect(() => closedRegion("r", [{x:0,y:0},{x:1,y:0},{x:0,y:5}], armor)).toThrow();
    const result = closedRegion("r", [{x:-7.5,y:-5},{x:7.5,y:-5},{x:0,y:5}], armor);
    result.edge_armor[0].thickness_m = 1;
    expect(result.edge_armor[1].thickness_m).toBe(0.1);
    expect(armor.thickness_m).toBe(0.1);
    expect(result.vertices_m).toHaveLength(3);
  });
  it("cancelling during the settle delay prevents late submission after deck/session exit", () => {
    vi.useFakeTimers(); const pending = deferredCommit(), commit = vi.fn();
    pending.schedule(commit); vi.advanceTimersByTime(149); expect(commit).not.toHaveBeenCalled();
    pending.cancel(); vi.advanceTimersByTime(1000); expect(commit).not.toHaveBeenCalled();
  });
  it("coalesces pending movement into one authoritative edit and never submits twice", () => {
    vi.useFakeTimers(); const pending = deferredCommit(), old = vi.fn(), latest = vi.fn();
    pending.schedule(old); vi.advanceTimersByTime(100); pending.schedule(latest);
    vi.advanceTimersByTime(150); vi.advanceTimersByTime(1000);
    expect(old).not.toHaveBeenCalled(); expect(latest).toHaveBeenCalledTimes(1);
  });
  it("generates unused ids and enforces the same hard coordinate boundary as the backend", () => {
    expect(nextId("deck", ["deck.0", "deck.2"])).toBe("deck.1");
    expect(onGrid(-7.5)).toBe(true); expect(onGrid(2.500000001)).toBe(false);
    expect(onGrid(Infinity)).toBe(false); expect(onGrid(1000002.5)).toBe(false);
  });
});
