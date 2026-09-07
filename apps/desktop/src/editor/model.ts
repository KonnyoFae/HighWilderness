import type { HostFailure } from "../bridge/types";

export interface ResourceEntry {
  key: string; kind: string; id: string; version: number; name: string;
  sha256: string; editable: boolean; read_only: boolean; usage: string;
}
export interface MaterialOption { id: string; version: number; name: string; category: "structure" | "base_armor" }
export type HullCommand = (command: string, args: Record<string, unknown>) => Promise<boolean>;
export interface ResourceIndex {
  interface: "gaotian.editor-resource-index/v1alpha1";
  resources: ResourceEntry[];
  material_options?: MaterialOption[];
}
export interface EditorPreview {
  valid: boolean;
  diagnostics: { code: string; path: string; message: string; severity: string }[];
  model: Record<string, unknown>;
}
export interface HullRegion { id: string; vertices_m: [number, number][]; edge_armor: { material: { id: string; version: number }; thickness_m: number }[] }
export interface HullDeck { id: string; level: number; is_base: boolean; regions: HullRegion[]; structure_material: { id: string; version: number } }
export interface SessionSnapshot {
  interface: "gaotian.editor-session/v2alpha1";
  backend_instance_id: string; session_id: string; revision: number;
  resource: ResourceEntry;
  draft: { name: string; decks: HullDeck[] };
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
  if (value.interface !== "gaotian.editor-session/v2alpha1"
    || value.backend_instance_id !== model.instance
    || (model.session !== null && (value.session_id !== model.session.session_id
      || value.revision < model.session.revision))) {
    return { ...model, pending: null };
  }
  return { ...model, session: value, pending: null, error: null };
}
