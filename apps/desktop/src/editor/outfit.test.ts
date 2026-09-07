import { describe, expect, it } from "vitest";
import { filterModules, instanceFields, mounts, outfitCommand } from "./outfit";
import type { ModuleOption, OutfitInstance } from "./model";

const option = (category: string, version: number, geometry = {}): ModuleOption => ({
  prototype: { id: `module.${category}`, version, name: category, category, balance_status: "contract_fixture", mass_kg: 20, durability_points: 100,
    installation: { allowed_rotations_deg: [0, 90], internal_footprint_half_cells: [[0, 0]], top_footprint_half_cells: [], side_mount_length_steps: 0, host_slot: null, ...geometry },
    power: {}, crew: [], automation: {}, capability: {} }, sha256: category + version,
  catalog: { id: "catalog", version: 1, key: "catalog-key", kind: "ModulePrototypeCatalog", name: "test", sha256: "digest", usage: "contract_fixture", editable: false, read_only: true },
});
describe("outfit resource and command boundary", () => {
  it("filters exact versions and permits combined internal/top geometry", () => {
    const options = [option("weapon", 1, { top_footprint_half_cells: [[0, 0]] }), option("weapon", 2), option("cargo", 1)];
    expect(mounts(options[0])).toEqual(["内部", "顶挂"]);
    expect(filterModules(options, "weapon", "顶挂", "1")).toEqual([options[0]]);
    expect(filterModules(options, "weapon", "", "2")).toEqual([options[1]]);
    expect(filterModules(options, "", "侧挂", "")).toEqual([]);
  });
  it("converts metre anchors exactly and preserves the selected prototype version", () => {
    const fields = { ...instanceFields(), instance_id: "module.1", x: "-7.5", y: "10", rotation: "90" };
    const value = outfitCommand("place", "grid", fields, option("cargo", 2));
    expect(value).toEqual({ command: "outfit.place_grid", args: { instance_id: "module.1", prototype: { id: "module.cargo", version: 2 }, deck_id: "deck.0", anchor_half_cell: [-3, 4], rotation_deg: 90 } });
  });
  it("rejects off-grid, blank and nonfinite coordinate input", () => {
    for (const x of ["2", "", "Infinity", "NaN", "90071992547409999"]) {
      expect(() => outfitCommand("move", "grid", { ...instanceFields(), instance_id: "a", x })).toThrow();
    }
  });
  it("moves and rotates a grid module in a single command without replacing its prototype", () => {
    const value = outfitCommand("move", "grid", { ...instanceFields(), instance_id: "a", x: "5", rotation: "90" }, option("cargo", 2));
    expect(value.args).toEqual({ instance_id: "a", deck_id: "deck.0", anchor_half_cell: [2, 0], rotation_deg: 90 });
  });
  it("preserves side placement and builds rehost without stray grid fields", () => {
    const instance: OutfitInstance = { id: "side", prototype: { id: "thruster", version: 1 }, placement: { kind: "side", deck_id: "deck.1", region_id: "r", edge_index: 3, start_slot_index: 8, rotation_deg: 180 } };
    expect(outfitCommand("move", "side", instanceFields(instance))).toEqual({ command: "outfit.move_side", args: { instance_id: "side", deck_id: "deck.1", region_id: "r", edge_index: 3, start_slot_index: 8, rotation_deg: 180 } });
    expect(outfitCommand("move", "hosted", { ...instanceFields(), instance_id: "core", host: "cic" })).toEqual({ command: "outfit.rehost", args: { instance_id: "core", host_instance_id: "cic" } });
  });
  it("rejects missing prototype or host and deletes only the selected instance", () => {
    const fields = { ...instanceFields(), instance_id: "core" };
    expect(() => outfitCommand("place", "grid", fields)).toThrow();
    expect(() => outfitCommand("move", "hosted", fields)).toThrow();
    expect(outfitCommand("remove", "hosted", fields)).toEqual({ command: "outfit.remove", args: { instance_id: "core" } });
  });
});
