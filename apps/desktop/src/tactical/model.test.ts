import { describe, expect, it } from "vitest";
import { acceptSnapshot, SCENARIO_ID } from "./model";
import type { TacticalSnapshot } from "./model";
import { TauriBridgeTransport } from "../bridge/transport";

const snapshot = (): TacticalSnapshot => ({
  interface: "gaotian.tactical-render-snapshot/v1alpha1", backend_instance_id: "backend.1", scene_id: "scene.1",
  authority_interface: "gaotian.tactical-scene-timeline/v7alpha1", paused: true, fixed_step: 0, fixed_step_s: 1/60,
  time_s: 0, static_sha256: "a".repeat(64), ships: [], events: [],
  static: { interface: "gaotian.tactical-render-static/v1alpha1", scenario_id: SCENARIO_ID, resources: {}, ships: [] },
});

describe("tactical presentation", () => {
  it("reuses only matching static geometry", () => {
    const first = acceptSnapshot(null, snapshot(), "backend.1");
    const next = acceptSnapshot(first, {...snapshot(), static: null}, "backend.1");
    expect(next.geometry).toBe(first.geometry);
    expect(acceptSnapshot(first, snapshot(), "backend.1").geometry).toBe(first.geometry);
    expect(() => acceptSnapshot(first, {...snapshot(), static: null, static_sha256: "b".repeat(64)}, "backend.1")).toThrow();
    expect(() => acceptSnapshot(null, {...snapshot(), static: null}, "backend.1")).toThrow();
  });
  it("rejects old backend, released scene and earlier authority steps", () => {
    const first = acceptSnapshot(null, {...snapshot(), fixed_step: 4}, "backend.1");
    expect(() => acceptSnapshot(first, snapshot(), "backend.1")).toThrow();
    expect(() => acceptSnapshot(first, {...snapshot(), fixed_step: 4, scene_id: "scene.old"}, "backend.1")).toThrow();
    expect(() => acceptSnapshot(null, snapshot(), "backend.2")).toThrow();
  });
  it("sends tactical operations through the dedicated host entry", async () => {
    const calls: unknown[] = [];
    const transport = new TauriBridgeTransport({
      invoke: async <T>(command: string, args?: Record<string, unknown>) => { calls.push([command,args]); return snapshot() as T; },
      createChannel: () => null,
    });
    const request = {backend_instance_id: "backend.1", method: "tactical.create" as const,
      params: {scenario_id: SCENARIO_ID}, session_id: null, expected_revision: null};
    await transport.tactical(request);
    expect(calls).toEqual([["bridge_tactical_request",{request}]]);
  });
});
