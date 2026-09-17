import type { DamageControlView } from './DamageControlPanel';
import type { LiftReserveReading } from '../LiftReserve';
export const SCENARIO_ID = "gtw.sample.web.two_ship.v1";
export interface TacticalRequest {
  backend_instance_id: string;
  method: 'tactical.realtime.deploy_encounter' | 'tactical.reset_test_state' | 'tactical.realtime.height' | 'tactical.realtime.damage_control' | `tactical.preparation.${"maintenance" | "scene_read" | "scene_save" | "scene_encounter" | "supply_replenish" | "library" | "import" | "open" | "read" | "draft" | "preview" | "commit" | "discard"}` | "tactical.create" | "tactical.inspect" | "tactical.close" | "tactical.set_mode" | "tactical.step" | "tactical.advance" | "tactical.pause"
    | "tactical.realtime.create" | "tactical.realtime.read" | "tactical.realtime.resume" | "tactical.realtime.pause" | "tactical.realtime.control" | "tactical.realtime.settlements" | "tactical.realtime.settlement" | "tactical.realtime.save" | "tactical.realtime.deploy" | "tactical.realtime.deploy_prepared" | "tactical.realtime.prepared_entry" | "tactical.realtime.withdraw" | "tactical.realtime.gun" | "tactical.realtime.close";
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
    structural_durability?: { policy_id:string; maximum_points:number };
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
    physical_status: string; command_status: string; lift_reserve?: LiftReserveReading; height_navigation?: HeightNavigation | null;
    descent?: {source_layer:string;next_layer:string|null;progress:number;duration_s:number;paused:boolean;remaining_s:number|null}|null;
    wreck?: {fixed_step:number;height_layer:string;position_m:number[];reason:string}|null;
    modules: { id: string; durability: number }[] }[];
  height_commands?: { command_sequence: number };
  events: TacticalVisualEvent[];
  presentation?: { interface: "gaotian.tactical-presentation/v1alpha1";
    finished_projectiles: FinishedProjectile[]; dropped_projectiles: number };
  gunnery?: GunneryView;
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
export interface GunView {
  point_defense_capable?:boolean; point_defense?:boolean;
  interception_target_id?:number|null; interception_priority?:number|null; interception_needed_rounds?:number;
  incendiary_effect?:'surface'|'internal'|null;
  target_policy?: 'automatic' | 'assigned' | 'hold';
  attack_layer?: string | null; effective_layer?: string;
  ballistics?: { caliber_mm: number; speed_mps: number; effective_speed_mps: number; lifetime_s: number;
    reference_range_m: number; speed_retention: number; drag: boolean; cyclic_rpm: number };
  ship_id: string; module_id: string; mode: "auto" | "manual"; angle_rad: number;
  origin_m: number[]; direction: number[]; aim_point_m: number[] | null;
  target_ship_id: string | null; target_module_id: string | null;
  quality: "normal" | "degraded"; quality_reason: string; lock_sources: string[];
  status: string; shots: number; ready_rounds: number; reload_steps: number; cooldown_steps: number;
  deck_level?: number | null; aimed_deck_levels?: number[]; ammo_resources: number; batch_cost: number; batch_rounds: number;
  selected_recipe_id?: string; loaded_recipe_id?: string | null; loading_recipe_id?: string | null;
  recipe_options?: {id: string; ammo_cost: number; rounds: number; cargo_costs: {good_id: string; quantity: number}[]}[];
  cargo?: {good_id: string; quantity: number; reserved: number}[];
}
export interface GunneryView {
  point_defense?:{hits:number;intercepted:number;recent:{step:number;impact_fraction:number;projectile_id:number;round_id:number;
    source_ship_id:string;weapon_id:string;position_m:number[];height_layer:string;durability_before:number;durability_after:number;intercepted:boolean}[];
    threats:{observer_ship_id:string;projectile_id:number;ship_id:string;remaining_s:number;durability:number;height_layer:string}[]};
  personnel?: { ships: {ship_id:string;fit:number;wounded:number;dead:number;unclassified_wounded:number;
    types:{crew_type:string;fit:number;wounded:number;dead:number}[];
    modules:{module_id:string;staffing_fraction:number;requirements:{crew_type:string;assigned:number;minimum:number;standard:number}[];
      functions:{function_id:string;crew_efficiency:number}[]}[] }[];
    recent:{ship_id:string;module_id:string;step:number;deck_level:number;cause:string;
      casualties:{crew_type:string;wounded:number;dead:number}[]}[] };
  deck_hit_policy?: {id: string; base_weight: number; aim_bonus: number; spanning_module_bonus: 'split' | 'base'} | null;
  groups?: {ship_id: string; group_id: string; name: string; weapon_ids: string[]}[];
  fuel?:{ship_id:string;total_units:number;tanks:{tank_id:string;module_id:string|null;deck_id:string|null;deck_level:number;
    quantity_units:number;durability_points:number;capacity_units:number;maximum_points:number}[]}[];
  damage_control?:DamageControlView;
  fireproof?:{ship_id:string;decks:{deck_id:string;deck_level:number;multiplier:number}[]}[];
  interface: "gaotian.gunnery-view/p2a-v1alpha1"; command_sequence: number;
  weapons: GunView[]; projectiles: DisplayProjectile[];
  policy_id: string; damage_enabled: boolean;
  ending?: { reason: string; step: number; removed_projectiles: number; saved: boolean } | null;
  damage?: { hits: number; expired: number; magazine_detonations?:number; magazine_explosions?:{
    ship_id:string; module_id:string; step:number; deck_level:number; height_layer:string; cause:string;
    ammunition_resources:number; radius_m:number; position_local_m:number[]; position_m:number[];
    module_losses:{module_id:string;damage_points:number}[]; hull_damage_fraction:number;
  }[]; recent: { projectile_id: number; step: number; source_ship_id: string;
    ship_id: string; position_m: number[]; deck_level: number; outcome: string; module_ids: string[]; module_damage: number; projectile_type?: string; height_layer?: string | null;
    deck_selection?: {policy: string; preferred_levels: number[]; probabilities: {deck_level: number; probability: number}[]; sample: number} }[] } | null;
}
export interface HeightNavigation {
  base_duration_s: number | null; duration_s: number | null; lift_loss_fraction: number;
  target_layer: string | null; next_layer: string | null; progress: number;
  remaining_s: number | null; total_remaining_s: number | null; unavailable_reason: string | null;
}
export interface DisplayProjectile {
  durability?:number|null;maximum_durability?:number|null;interception_target_id?:number|null;
  id: number; ship_id: string; position_m: number[]; previous_m: number[]; velocity_mps: number[];
  born_step?: number; origin_m?: number[]; expires_step?: number;
  height_layer?: string | null;
}
export interface FinishedProjectile {
  id: number; ship_id: string; born_step: number; origin_m: number[]; expires_step: number;
  position_m: number[]; velocity_mps: number[]; end_step: number; end_m: number[];
  impact: { ship_id: string; outcome: string } | null;
  height_layer?: string | null;
}
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
