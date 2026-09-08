export const SCENARIO_ID = "gtw.sample.web.two_ship.v1";
export interface TacticalRequest {
  backend_instance_id: string;
  method: "tactical.create" | "tactical.inspect" | "tactical.close" | "tactical.set_mode" | "tactical.step" | "tactical.advance" | "tactical.pause";
  params: Record<string, unknown>;
  session_id: null;
  expected_revision: null;
}
export interface TacticalStatic {
  interface: "gaotian.tactical-render-static/v1alpha1";
  scenario_id: string;
  resources: Record<string, unknown>;
  ships: { id: string; name: string; side_id: string; fleet_id: string; derived_snapshot_sha256: string;
    decks: { id: string; level: number; regions: { id: string; vertices_m: number[][] }[] }[];
    modules: { id: string; name: string; category: string; anchor_m: number[]; rotation_deg: number; deck_level: number;
      internal_cells: number[][]; top_cells: number[][]; body_points: number[][]; max_durability: number }[] }[];
}
export interface TacticalVisualEvent {
  interface: "gaotian.tactical-visual-event/v1alpha1";
  scene_id: string; event_id: string; fixed_step: number; type: string; ship_ids: string[];
  payload: Record<string, unknown>;
}
export interface TacticalControlInput {
  interface: "gaotian.tactical-input/v1alpha1";
  scene_id: string; input_seq: number; target_step: number; command: "control";
  arguments: { ship_id: string; control: {
    interface: "gaotian.tactical-propulsion-control/v2alpha1";
    automatic_brake_policy: "gaotian.propulsion-control/translation-only-quarter-brake/v2";
    main_engine_quantization_policy: "gaotian.propulsion-control/nearest-telegraph-ties-up/v1";
    maneuver_quantization_policy: "gaotian.propulsion-control/nearest-stage-ties-up/v1";
    channel_commands: { command_channel: string; commanded_notch: string | null; target_output_percent: number | null }[];
    automatic_brake: boolean; overg_requested: boolean; source_migration_id: string | null;
  } };
}
export interface TacticalSnapshot {
  interface: "gaotian.tactical-render-snapshot/v1alpha1";
  backend_instance_id: string; scene_id: string; authority_interface: string;
  paused: boolean; fixed_step: number; fixed_step_s: number; time_s: number;
  static_sha256: string; static: TacticalStatic | null;
  ships: { id: string; position_m: number[]; heading_rad: number; velocity_mps: number[];
    speed_mps: number; yaw_rate_radps: number; height_layer: string; hull_integrity: number;
    physical_status: string; command_status: string; modules: { id: string; durability: number }[] }[];
  events: TacticalVisualEvent[];
  control_state?: PausedControlState;
  advance_state?: { interface: "gaotian.tactical-bounded-advance/v1alpha1";
    status: "running" | "completed" | "stopped" | "failed"; input_seq: number; input_sha256: string;
    start_step: number; step_count: number; executed_steps: number; error: string | null } | null;
}
export interface PausedControlState {
  interface: "gaotian.tactical-paused-control-state/v1alpha1";
  adapter_interface: string; direct_ship_id: string; available: boolean; unavailable_reason: string | null;
  last_input_seq: number; last_input_sha256: string | null; last_executed_step: number | null;
  requested_control: TacticalControlInput["arguments"]["control"] | null;
  command_state_sha256: string; tuning_sha256: string; fuel_units: number;
  fuel_policy?: string;
  channels: { channel: string; requested_percent: number; safety_ceiling_percent: number; safety_reasons: string[] }[];
  engines: { id: string; actual_percent: number; target_percent: number; phase: string }[];
  delivery_status: string | null; missing_channels: string[]; last_arbitration: Record<string, unknown> | null;
}
export interface TacticalView { snapshot: TacticalSnapshot; geometry: TacticalStatic }
export type WorkspaceMode = "editor" | "tactical";
export interface ModeResult { mode: WorkspaceMode; paused: boolean; scene_id: string | null }

// Discard a previous backend/scene or older step before it can replace the view.
export function acceptSnapshot(current: TacticalView | null, next: TacticalSnapshot, instance: string): TacticalView {
  if (next.interface !== "gaotian.tactical-render-snapshot/v1alpha1" || next.backend_instance_id !== instance)
    throw new Error("场景来自旧后台或不支持的表现版本，请重新建立场景。");
  if (current && (next.scene_id !== current.snapshot.scene_id || next.fixed_step < current.snapshot.fixed_step))
    throw new Error("忽略已释放场景或过期步号的结果。");
  if (next.static && next.static.interface !== "gaotian.tactical-render-static/v1alpha1")
    throw new Error("不支持的舰艇几何版本。");
  const geometry = current?.snapshot.static_sha256 === next.static_sha256 ? current.geometry : next.static;
  if (!geometry || geometry.interface !== "gaotian.tactical-render-static/v1alpha1")
    throw new Error("缺少匹配的舰艇几何，请重新读取完整场景。");
  return { snapshot: next, geometry };
}
