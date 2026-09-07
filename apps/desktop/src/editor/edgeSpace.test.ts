import { describe, expect, it } from "vitest";
import { currentEdgeSpace } from "./edgeSpace";
import type { EditorPreview } from "./model";

function preview(): EditorPreview {
  return { valid: true, diagnostics: [], model: { view_interface: "gaotian.hull-editor-view/v2alpha1", decks: [
    { id: "base", edge_space: { interface: "gaotian.hull-edge-space/v1alpha1", area_m2: 0, gross_volume_m3: 0, pieces: [] } },
    { id: "upper", edge_space: { interface: "gaotian.hull-edge-space/v1alpha1", area_m2: 12.5, gross_volume_m3: 62.5,
      pieces: [{ region_id: "a", area_m2: 12.5, vertices_m: [[0, 0], [5, 0], [0, 5]] }] } },
  ] } };
}
describe("authoritative edge space visibility", () => {
  it("selects the requested deck and preserves a legitimate zero result", () => {
    expect(currentEdgeSpace(preview(), "base", false)?.area_m2).toBe(0);
    expect(currentEdgeSpace(preview(), "upper", false)?.gross_volume_m3).toBe(62.5);
    expect(currentEdgeSpace(preview(), "missing", false)).toBeNull();
  });
  it("hides old geometry during drawing, dragging and illegal revisions", () => {
    expect(currentEdgeSpace(preview(), "upper", true)).toBeNull();
    expect(currentEdgeSpace({ ...preview(), valid: false }, "upper", false)).toBeNull();
  });
  it("does not interpret missing or unknown contracts as empty space", () => {
    for (const version of [undefined, "gaotian.hull-editor-view/v1alpha1", "future"]) {
      const p = preview(); p.model.view_interface = version;
      expect(currentEdgeSpace(p, "base", false)).toBeNull();
    }
    const p = preview(); p.model.decks = [{ id: "base", edge_space: { interface: "future", area_m2: 0, gross_volume_m3: 0, pieces: [] } }];
    expect(currentEdgeSpace(p, "base", false)).toBeNull();
  });
  it("rejects malformed geometry and nonfinite or negative quantities", () => {
    for (const value of [NaN, Infinity, -1]) {
      const p = preview(); (p.model.decks as {edge_space: {area_m2: number}}[])[0].edge_space.area_m2 = value;
      expect(currentEdgeSpace(p, "base", false)).toBeNull();
    }
    const p = preview(); (p.model.decks as {edge_space: {pieces: unknown[]}}[])[1].edge_space.pieces = [null];
    expect(currentEdgeSpace(p, "upper", false)).toBeNull();
  });
});
