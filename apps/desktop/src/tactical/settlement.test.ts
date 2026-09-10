import { describe, expect, it } from "vitest";
import { resourceRows } from "./settlement";
import type { CombatRecord, SettlementShip } from "./settlement";

function record(ammo: number): CombatRecord {
  return { ship_id: "blue", armor: [], state: { instance_id: "ship.1", revision: 1, hull_integrity_fraction: 1,
    service: { status: "available", reasons: [] }, modules: [], magazines: [{ module_id: "magazine", quantity: ammo }],
    weapons: [{ module_id: "gun", ready_rounds: 1, cooldown_steps: 0 }], cargo: [] } };
}
function ship(): SettlementShip {
  return { before: record(80), after: record(65), module_names: { gun: "主炮" },
    capacity_before: { capacity_cm3: 125000000, used_volume_cm3: 0, over_capacity: false },
    capacity_after: { capacity_cm3: 125000000, used_volume_cm3: 0, over_capacity: false },
    changes: [{ resource: "ammunition", reason: "reload", delta: -15 },
      { resource: "ready:gun", reason: "discharge", delta: -3 }, { resource: "ready:gun", reason: "reload", delta: 3 }] };
}
describe("settlement resource display", () => {
  it("shows firing and reload consumption even when ready rounds end unchanged", () => {
    const rows = resourceRows(ship());
    expect(rows[0]).toMatchObject({ name: "弹药资源", before: 80, after: 65, detail: "装填 -15" });
    expect(rows[1]).toMatchObject({ name: "主炮待发弹", before: 1, after: 1, detail: "射击 -3；装填 +3" });
  });
  it("keeps both completely consumed cargo and unchanged goods visible", () => {
    const s = ship(); s.before.state.cargo = [{ good_id: "fuse", quantity: 10 }, { good_id: "alloy", quantity: 20 }];
    s.after.state.cargo = [{ good_id: "alloy", quantity: 20 }];
    s.changes.push({ resource: "cargo:fuse", reason: "consume", delta: -10 });
    const rows = resourceRows(s);
    expect(rows.find(r => r.key === "cargo:fuse")).toMatchObject({ before: 10, after: 0, detail: "使用 -10" });
    expect(rows.find(r => r.key === "cargo:alloy")).toMatchObject({ before: 20, after: 20, detail: "无变动" });
  });
  it("does not erase stock when the containing module is destroyed", () => {
    const s = ship(); s.after.state.modules = [{ module_id: "magazine", durability_points: 0 }];
    expect(resourceRows(s)[0].after).toBe(65);
  });
});
