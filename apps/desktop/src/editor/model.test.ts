import { describe, expect, it } from "vitest";
import { editorReducer, initialEditorModel } from "./model";
import type { SessionSnapshot } from "./model";

const snapshot = (instance = "backend.1", session = "session.1", revision = 1) => ({
  interface: "gaotian.editor-session/v2alpha1", backend_instance_id: instance,
  session_id: session, revision,
}) as SessionSnapshot;

describe("editor response ownership", () => {
  it("accepts an explicit bound outfit session but rejects missing or mismatched bindings", () => {
    const value = { ...snapshot(), interface: "gaotian.editor-session/v3alpha1", resource: { kind: "OutfitPlan", key: "r", id: "outfit", version: 1, name: "test", sha256: "a".repeat(64), editable: true, read_only: false, usage: "prototype_unbalanced" },
      draft: { name: "test", hull_blueprint: { id: "user.hull", version: 2 } },
      hull_binding: { interface: "gaotian.outfit-hull-binding/v1alpha1", hull: { kind: "HullBlueprint", id: "user.hull", version: 2, name: "test hull", decks: [] },
        hull_sha256: "a".repeat(64), catalog_dependencies_sha256: "b".repeat(64) } } as SessionSnapshot;
    const model = editorReducer(initialEditorModel("backend.1"), { type: "begin", ticket: 1 });
    const accept = (v: SessionSnapshot) => editorReducer(model, { type: "snapshot", ticket: 1, value: v }).session;
    expect(accept(value)).toBe(value);
    expect(accept({ ...value, hull_binding: undefined })).toBeNull();
    expect(accept({ ...value, interface: "gaotian.editor-session/v2alpha1" })).toBeNull();
    expect(accept({ ...value, draft: { name: "bad", hull_blueprint: { id: "other", version: 2 } } })).toBeNull();
    expect(accept({ ...value, hull_binding: { ...value.hull_binding!, hull_sha256: "bad" } })).toBeNull();
  });
  it("ignores older request completions and never rolls revision back", () => {
    let model = editorReducer(initialEditorModel("backend.1"), { type: "begin", ticket: 1 });
    model = editorReducer(model, { type: "snapshot", ticket: 1, value: snapshot() });
    model = editorReducer(model, { type: "begin", ticket: 3 });
    expect(editorReducer(model, { type: "snapshot", ticket: 2, value: snapshot() })).toBe(model);
    const after = editorReducer(model, { type: "snapshot", ticket: 3, value: snapshot("backend.1", "session.1", 0) });
    expect(after.session?.revision).toBe(1);
  });
  it("rejects old backend and different-session previews", () => {
    let model = editorReducer(initialEditorModel("backend.1"), { type: "begin", ticket: 1 });
    model = editorReducer(model, { type: "snapshot", ticket: 1, value: snapshot() });
    model = editorReducer(model, { type: "begin", ticket: 2 });
    expect(editorReducer(model, { type: "snapshot", ticket: 2, value: snapshot("backend.1", "session.2") }).session)
      .toBe(model.session);
    model = editorReducer(model, { type: "reset", instance: "backend.2" });
    model = editorReducer(model, { type: "begin", ticket: 3 });
    expect(editorReducer(model, { type: "snapshot", ticket: 3, value: snapshot() }).session).toBeNull();
  });
  it("domain errors leave the session available for refresh", () => {
    let model = editorReducer(initialEditorModel("backend.1"), { type: "begin", ticket: 1 });
    model = editorReducer(model, { type: "snapshot", ticket: 1, value: snapshot() });
    model = editorReducer(model, { type: "begin", ticket: 2 });
    const after = editorReducer(model, { type: "failed", ticket: 2, error: {
      code: "editor.revision_conflict", path: "$.expected_revision", message: "过期修订",
      source: "domain", retryable: false, details: {},
    } });
    expect(after.session).toBe(model.session);
    expect(after.pending).toBeNull();
  });
  it("cancelling a file chooser preserves the draft and cannot cancel a newer request", () => {
    let model = editorReducer(initialEditorModel("backend.1"), { type: "begin", ticket: 1 });
    model = editorReducer(model, { type: "snapshot", ticket: 1, value: snapshot() });
    model = editorReducer(model, { type: "begin", ticket: 2 });
    const cancelled = editorReducer(model, { type: "cancelled", ticket: 2 });
    expect(cancelled.session).toBe(model.session);
    expect(cancelled.pending).toBeNull();
    const newer = editorReducer(model, { type: "begin", ticket: 3 });
    expect(editorReducer(newer, { type: "cancelled", ticket: 2 })).toBe(newer);
  });

});
