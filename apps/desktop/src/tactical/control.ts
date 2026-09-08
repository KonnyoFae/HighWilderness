import type { TacticalControlInput, TacticalSnapshot } from "./model";

export const NOTCHES = { stop: "停车", dead_slow: "微速 · 2%", quarter: "四分之一 · 25%", half: "半速 · 50%", three_quarter: "四分之三 · 75%", full: "全速 · 100%" };
export const CHANNELS: Record<string, string> = { "translation.forward": "前进", "translation.reverse": "倒车", "translation.left": "左移", "translation.right": "右移", "yaw.counterclockwise": "左转", "yaw.clockwise": "右转" };
export type Notch = keyof typeof NOTCHES;
export type HelmDraft = { notch: Notch; direction: "forward" | "reverse"; brake: boolean };
export type StepTicket = { input: TacticalControlInput; sha256: string; stepCount?: number };

export function makeStepInput(snapshot: TacticalSnapshot, draft: HelmDraft, turn: -1 | 0 | 1 = 0, turnPercent = 25): TacticalControlInput {
  const state = snapshot.control_state;
  if (!snapshot.paused || state?.interface !== "gaotian.tactical-paused-control-state/v1alpha1" || !state.available || state.direct_ship_id !== "ship.web.blue")
    throw new Error(state?.unavailable_reason ?? "当前场景没有可用的单步直控入口。");
  if (!(draft.notch in NOTCHES) || !["forward", "reverse"].includes(draft.direction) || ![-1, 0, 1].includes(turn) ||
    !Number.isInteger(turnPercent) || !(turnPercent === 2 || turnPercent >= 0 && turnPercent <= 100 && turnPercent % 5 === 0)) throw new Error("不支持的车钟或转向档位。");
  if (!Number.isSafeInteger(state.last_input_seq) || !Number.isSafeInteger(snapshot.fixed_step) || state.last_input_seq < 0 || snapshot.fixed_step < 0 || !Number.isSafeInteger(state.last_input_seq + 1))
    throw new Error("场景序号无效，请重新建立场景。");
  const control: TacticalControlInput["arguments"]["control"] = {
    interface: "gaotian.tactical-propulsion-control/v2alpha1",
    automatic_brake_policy: "gaotian.propulsion-control/translation-only-quarter-brake/v2",
    main_engine_quantization_policy: "gaotian.propulsion-control/nearest-telegraph-ties-up/v1",
    maneuver_quantization_policy: "gaotian.propulsion-control/nearest-stage-ties-up/v1",
    channel_commands: Object.keys(CHANNELS).map(channel => ({ command_channel: channel,
      commanded_notch: channel.startsWith("yaw.") ? null : !draft.brake && channel === `translation.${draft.direction}` ? draft.notch : "stop",
      target_output_percent: channel.startsWith("yaw.") ? !draft.brake && channel === (turn === -1 ? "yaw.counterclockwise" : turn === 1 ? "yaw.clockwise" : "") ? turnPercent : 0 : null,
    })),
    automatic_brake: draft.brake, overg_requested: false, source_migration_id: null,
  };
  return { interface: "gaotian.tactical-input/v1alpha1", scene_id: snapshot.scene_id, input_seq: state.last_input_seq + 1,
    target_step: snapshot.fixed_step, command: "control", arguments: { ship_id: state.direct_ship_id, control } };
}

// Matches Python canonical_json for this ASCII-keyed, integer-only input contract.
function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => [k, ordered(v)]));
  return value;
}
export async function stepTicket(input: TacticalControlInput): Promise<StepTicket> {
  const bytes = new TextEncoder().encode(JSON.stringify(ordered(input), null, 2) + "\n");
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return { input, sha256: Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, "0")).join("") };
}
export function reconcileStep(ticket: StepTicket, snapshot: TacticalSnapshot): "executed" | "not_executed" | "unknown" {
  const { input, sha256 } = ticket, state = snapshot.control_state;
  if (!state || snapshot.scene_id !== input.scene_id || state.interface !== "gaotian.tactical-paused-control-state/v1alpha1") return "unknown";
  if (state.last_input_seq === input.input_seq && state.last_input_sha256 === sha256 && state.last_executed_step === input.target_step && snapshot.fixed_step === input.target_step + 1) return "executed";
  if (snapshot.fixed_step === input.target_step && state.last_input_seq < input.input_seq) return "not_executed";
  return "unknown";
}

export function reconcileAdvance(ticket: StepTicket, snapshot: TacticalSnapshot): "accepted" | "not_executed" | "unknown" {
  const a = snapshot.advance_state;
  if (snapshot.scene_id !== ticket.input.scene_id) return "unknown";
  if (a?.interface === "gaotian.tactical-bounded-advance/v1alpha1" && a.input_seq === ticket.input.input_seq &&
    a.input_sha256 === ticket.sha256 && a.start_step === ticket.input.target_step && a.step_count === ticket.stepCount &&
    Number.isSafeInteger(a.executed_steps) && a.executed_steps >= 0 && a.executed_steps <= a.step_count &&
    snapshot.fixed_step === a.start_step + a.executed_steps &&
    (a.status === "running" && !snapshot.paused && a.executed_steps < a.step_count ||
      a.status === "completed" && snapshot.paused && a.executed_steps === a.step_count ||
      ["stopped", "failed"].includes(a.status) && snapshot.paused)) return "accepted";
  if (a && (a.status === "running" || a.input_seq >= ticket.input.input_seq)) return "unknown";
  return reconcileStep(ticket, snapshot) === "not_executed" ? "not_executed" : "unknown";
}
