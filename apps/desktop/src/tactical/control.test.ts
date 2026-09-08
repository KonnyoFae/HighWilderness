import { describe, expect, it } from "vitest";
import type { TacticalControlInput, TacticalSnapshot } from "./model";
import { makeStepInput, reconcileAdvance, reconcileStep, stepTicket } from "./control";
import snapshot from "./testing/snapshot.fixture.json";
import steps from "./testing/step.fixture.json";
const initial = snapshot as TacticalSnapshot;
const draft = { notch: "full" as const, direction: "forward" as const, brake: false };

describe("paused control inputs and receipts", () => {
  it("recovers bounded advancement including partial stops without confusing duration or progress", async () => {
    const ticket = { ...await stepTicket(makeStepInput(initial, draft)), stepCount: 300 };
    const advancing: TacticalSnapshot = { ...initial, paused: false, fixed_step: 12, advance_state: {
      interface: "gaotian.tactical-bounded-advance/v1alpha1", status: "running", input_seq: ticket.input.input_seq,
      input_sha256: ticket.sha256, start_step: 0, step_count: 300, executed_steps: 12, error: null } };
    expect(reconcileAdvance(ticket, advancing)).toBe("accepted");
    expect(reconcileAdvance(ticket, { ...advancing, paused: true, advance_state: { ...advancing.advance_state!, status: "stopped" } })).toBe("accepted");
    expect(reconcileAdvance(ticket, { ...advancing, paused: true, fixed_step: 300, advance_state: { ...advancing.advance_state!, status: "completed", executed_steps: 300 } })).toBe("accepted");
    for (const wrong of [{ ...advancing, scene_id: "scene.old" }, { ...advancing, fixed_step: 11 },
      { ...advancing, advance_state: { ...advancing.advance_state!, step_count: 600 } },
      { ...advancing, advance_state: { ...advancing.advance_state!, input_sha256: "0".repeat(64) } },
      { ...advancing, paused: true, advance_state: { ...advancing.advance_state!, status: "completed" as const } }]) {
      expect(reconcileAdvance(ticket, wrong)).toBe("unknown");
    }
    expect(reconcileAdvance(ticket, initial)).toBe("not_executed");
  });
  it("matches independently recorded Python inputs and canonical hashes", async () => {
    const controls = [makeStepInput(initial, draft), makeStepInput(steps[0].result as TacticalSnapshot, draft, -1),
      makeStepInput(steps[1].result as TacticalSnapshot, { ...draft, brake: true }), makeStepInput(steps[2].result as TacticalSnapshot, draft)];
    for (let i = 0; i < controls.length; i++) {
      expect(controls[i]).toEqual(steps[i].input);
      const ticket = await stepTicket(controls[i]);
      expect(ticket.sha256).toBe(steps[i].sha256);
      expect(reconcileStep(ticket, steps[i].result as TacticalSnapshot)).toBe("executed");
    }
  });
  it("turns for one explicit step only and keeps braking free of opposing/yaw requests", () => {
    const turn = makeStepInput(initial, draft, 1, 50);
    expect(turn.arguments.control.channel_commands[5].target_output_percent).toBe(50);
    expect(makeStepInput(initial, draft).arguments.control.channel_commands.slice(4).every(c => c.target_output_percent === 0)).toBe(true);
    const brake = makeStepInput(initial, { ...draft, brake: true }, 1, 100).arguments.control;
    expect(brake.automatic_brake).toBe(true);
    expect(brake.channel_commands.every(c => c.commanded_notch === "stop" || c.target_output_percent === 0)).toBe(true);
    const reverse = makeStepInput(initial, { ...draft, direction: "reverse" });
    expect(reverse.arguments.control.channel_commands[0].commanded_notch).toBe("stop");
    expect(reverse.arguments.control.channel_commands[1].commanded_notch).toBe("full");
  });
  it("requires a paused available flagship and safe sequence", () => {
    for (const s of [ { ...initial, paused: false }, { ...initial, control_state: undefined },
      { ...initial, control_state: { ...initial.control_state!, available: false } },
      { ...initial, control_state: { ...initial.control_state!, direct_ship_id: "ship.web.red" } },
      { ...initial, control_state: { ...initial.control_state!, last_input_seq: Number.MAX_SAFE_INTEGER } } ]) {
      expect(() => makeStepInput(s, draft)).toThrow();
    }
    expect(() => makeStepInput(initial, draft, 1, 13)).toThrow();
  });
  it("reconciles a lost reply without resending, rejects mismatching and later outcomes", async () => {
    const ticket = await stepTicket(steps[0].input as TacticalControlInput);
    expect(reconcileStep(ticket, initial)).toBe("not_executed");
    expect(reconcileStep(ticket, steps[0].result as TacticalSnapshot)).toBe("executed");
    expect(reconcileStep(ticket, steps[1].result as TacticalSnapshot)).toBe("unknown");
    expect(reconcileStep(ticket, { ...steps[0].result, scene_id: "scene.old" } as TacticalSnapshot)).toBe("unknown");
    expect(reconcileStep({ ...ticket, sha256: "0".repeat(64) }, steps[0].result as TacticalSnapshot)).toBe("unknown");
  });
});
