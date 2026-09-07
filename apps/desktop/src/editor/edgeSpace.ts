import type { EditorPreview } from "./model";

export interface EdgeSpace {
  interface: "gaotian.hull-edge-space/v1alpha1";
  area_m2: number;
  gross_volume_m3: number;
  pieces: { region_id: string; vertices_m: [number, number][]; area_m2: number }[];
}
const object = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x);
const nonnegative = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x) && x >= 0;

// Only the current legal revision is eligible. Old view contracts never imply zero space.
export function currentEdgeSpace(preview: EditorPreview, deckId: string | undefined, localDraft: boolean): EdgeSpace | null {
  if (localDraft || !preview.valid || !deckId || preview.model.view_interface !== "gaotian.hull-editor-view/v2alpha1") return null;
  const decks = preview.model.decks;
  if (!Array.isArray(decks)) return null;
  const deck: unknown = decks.find(d => object(d) && d.id === deckId);
  if (!object(deck)) return null;
  const space = deck.edge_space;
  if (!object(space) || space.interface !== "gaotian.hull-edge-space/v1alpha1"
    || !nonnegative(space.area_m2) || !nonnegative(space.gross_volume_m3) || !Array.isArray(space.pieces)) return null;
  if (!space.pieces.every(p => object(p) && typeof p.region_id === "string" && nonnegative(p.area_m2)
    && Array.isArray(p.vertices_m) && p.vertices_m.length >= 3 && p.vertices_m.every(v => Array.isArray(v)
      && v.length === 2 && v.every(n => typeof n === "number" && Number.isFinite(n))))) return null;
  return space as unknown as EdgeSpace;
}
