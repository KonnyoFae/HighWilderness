import type { HostFailure } from "../bridge/types";

export interface ResourceEntry {
  key: string; kind: string; id: string; version: number; name: string;
  sha256: string; editable: boolean; read_only: boolean; usage: string;
}
export interface MaterialOption { id: string; version: number; name: string; category: "structure" | "base_armor" }
export type HullCommand = (command: string, args: Record<string, unknown>) => Promise<boolean>;
export interface ResourceIndex {
  interface: "gaotian.editor-resource-index/v1alpha1" | "gaotian.editor-resource-index/v2alpha1";
  resources: ResourceEntry[];
  material_options?: MaterialOption[];
  module_options?: ModuleOption[];
}
export interface ModuleOption {
  prototype: { id: string; version: number; name: string; category: string; balance_status: string;
    mass_kg: number; durability_points: number; installation: {
      allowed_rotations_deg: number[]; internal_footprint_half_cells: number[][];
      top_footprint_half_cells: number[][]; side_mount_length_steps: number; host_slot: string | null;
      [key: string]: unknown;
    }; power: Record<string, unknown>; crew: Record<string, unknown>[];
    automation: Record<string, unknown>; capability: Record<string, unknown> };
  sha256: string; catalog: ResourceEntry;
}
export interface OutfitInstance { id: string; prototype: { id: string; version: number }; placement: {
  kind: "grid" | "side" | "hosted"; deck_id?: string; anchor_half_cell?: number[]; rotation_deg?: number;
  region_id?: string; edge_index?: number; start_slot_index?: number; host_instance_id?: string;
} }
export interface EditorPreview {
  valid: boolean;
  diagnostics: { code: string; path: string; message: string; severity: string }[];
  model: Record<string, unknown>;
}
export interface HullRegion { id: string; vertices_m: [number, number][]; edge_armor: { material: { id: string; version: number }; thickness_m: number }[] }
export interface HullDeck { id: string; level: number; is_base: boolean; regions: HullRegion[]; structure_material: { id: string; version: number } }
export interface HullBinding {
  interface: "gaotian.outfit-hull-binding/v1alpha1";
  hull: { kind: "HullBlueprint"; id: string; version: number; name: string; decks: HullDeck[] };
  hull_sha256: string; catalog_dependencies_sha256: string;
}
export interface SessionSnapshot {
  interface: "gaotian.editor-session/v2alpha1" | "gaotian.editor-session/v3alpha1";
  hull_binding?: HullBinding;
  backend_instance_id: string; session_id: string; revision: number;
  resource: ResourceEntry;
  draft: { name: string; decks?: HullDeck[]; modules?: OutfitInstance[]; hull_blueprint?: { id: string; version: number } };
  draft_sha256: string; dirty: boolean; can_undo: boolean; can_redo: boolean;
  preview: EditorPreview; last_valid_preview: EditorPreview | null;
  last_valid_revision: number | null;
  file_label: string | null; can_save_current: boolean; recovered: boolean;
  recovery_available: boolean; recovery_warning: string | null;
}
export interface EditorRequest {
  backend_instance_id: string; method: string; params: Record<string, unknown>;
  session_id: string | null; expected_revision: number | null;
}
export interface EditorModel {
  instance: string;
  pending: number | null;
  session: SessionSnapshot | null;
  error: HostFailure | null;
}
export type EditorAction =
  | { type: "begin"; ticket: number }
  | { type: "snapshot"; ticket: number; value: SessionSnapshot }
  | { type: "failed"; ticket: number; error: HostFailure }
  | { type: "closed"; ticket: number }
  | { type: "cancelled"; ticket: number }
  | { type: "reset"; instance: string };

export function initialEditorModel(instance: string): EditorModel {
  return { instance, pending: null, session: null, error: null };
}

export function editorReducer(model: EditorModel, action: EditorAction): EditorModel {
  if (action.type === "reset") return initialEditorModel(action.instance);
  if (action.type === "begin") return { ...model, pending: action.ticket, error: null };
  if (action.ticket !== model.pending) return model;
  if (action.type === "failed") return { ...model, pending: null, error: action.error };
  if (action.type === "cancelled") return { ...model, pending: null };
  if (action.type === "closed") return { ...model, pending: null, session: null };
  const value = action.value;
  const binding = value.hull_binding;
  const versionValid = value.interface === "gaotian.editor-session/v2alpha1" ? binding === undefined
    : value.interface === "gaotian.editor-session/v3alpha1" && value.resource?.kind === "OutfitPlan"
      && binding?.interface === "gaotian.outfit-hull-binding/v1alpha1" && binding.hull?.kind === "HullBlueprint"
      && Array.isArray(binding.hull.decks) && /^[0-9a-f]{64}$/.test(binding.hull_sha256)
      && /^[0-9a-f]{64}$/.test(binding.catalog_dependencies_sha256)
      && value.draft?.hull_blueprint?.id === binding.hull.id && value.draft.hull_blueprint.version === binding.hull.version;
  if (!versionValid
    || value.backend_instance_id !== model.instance
    || (model.session !== null && (value.session_id !== model.session.session_id
      || value.revision < model.session.revision))) {
    return { ...model, pending: null };
  }
  return { ...model, session: value, pending: null, error: null };
}
