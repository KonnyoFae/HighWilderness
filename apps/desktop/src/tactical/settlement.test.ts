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
  it('keeps incendiary cargo consumption separate from tank fuel',()=>{
    const s=ship();s.before.state.cargo=[{good_id:'cargo.high_energy_fuel',quantity:2}];
    s.after.state.cargo=[{good_id:'cargo.high_energy_fuel',quantity:1}];
    s.changes.push({resource:'cargo:cargo.high_energy_fuel',reason:'reload',delta:-1});
    expect(resourceRows(s).find(r=>r.key==='cargo:cargo.high_energy_fuel')).toMatchObject({name:'高能燃料',before:2,after:1,detail:'装填 -1'});
  });
  it('shows fuel destruction separately from damage-control resource scaling',()=>{
    const s=ship();s.after.resources={fuel_tanks:[{tank_id:'tank.filling.deck.0',module_id:null,deck_id:'deck.0',deck_level:0}]};
    s.before.state.fuel_tanks=[{tank_id:'tank.filling.deck.0',quantity_units:120,durability_points:100}];
    s.after.state.fuel_tanks=[{tank_id:'tank.filling.deck.0',quantity_units:0,durability_points:0}];
    s.changes.push({resource:'fuel:tank.filling.deck.0',reason:'tank_destroyed',delta:-120});
    expect(resourceRows(s).find(r=>r.key.startsWith('fuel:'))).toMatchObject({name:'第 0 层填充燃料槽燃料',before:120,after:0,detail:'燃料槽损毁 -120'});
  });
  it('converts damage-control milli-units consistently without scaling cargo',()=>{
    const s=ship();s.module_names.dc='损管设备';
    s.before.state.damage_controls=[{module_id:'dc',quantity_units:100000,preparation:null}];
    s.after.state.damage_controls=[{module_id:'dc',quantity_units:99875,preparation:null}];
    s.changes.push({resource:'damage_control:dc',reason:'module_repair',delta:-125},
      {resource:'cargo:cargo.engineering_parts',reason:'damage_control_preparation',delta:-2});
    const rows=resourceRows(s);
    expect(rows.find(r=>r.key==='damage_control:dc')).toMatchObject({name:'损管设备损管资源（点）',before:100,after:99.875,detail:'部件维修 -0.125'});
    expect(rows.find(r=>r.key==='cargo:cargo.engineering_parts')).toMatchObject({name:'工程零件',detail:'损管准备 -2'});
  });
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
