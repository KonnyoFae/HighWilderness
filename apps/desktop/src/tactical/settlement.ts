export interface CombatRecord {
  ship_id: string;
  state: {
    instance_id: string; revision: number; hull_integrity_fraction: number;
    service: { status: string; reasons: string[] };
    modules: { module_id: string; durability_points: number }[];
    magazines: { module_id: string; quantity: number }[];
    weapons: { module_id: string; ready_rounds: number; cooldown_steps: number }[];
    cargo: { good_id: string; quantity: number }[];
  };
  armor: { deck_id: string; deck_level: number; region_id: string; edge_index: number; durability: number }[];
}
export interface SettlementShip {
  before: CombatRecord; after: CombatRecord; module_names: Record<string, string>;
  capacity_before: { capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean };
  capacity_after: { capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean };
  changes: { resource: string; reason: string; delta: number }[];
}
export interface SettlementEnvelope {
  saved: boolean; error?: string | null;
  result: { settlement_id: string; reason: string; fixed_step: number; ships: SettlementShip[] };
}
export interface SettlementLibrary {
  results: { settlement_id: string; saved: boolean; reason: string; fixed_step: number }[];
  ships: { instance_id: string; revision: number; hull_integrity: number; service: { status: string }; can_deploy: boolean }[];
}
export const endingLabel: Record<string, string> = { withdrawal: "主动撤离", victory: "本方胜利", defeat: "本方失去作战能力", draw: "双方失去作战能力" };
export const serviceLabel: Record<string, string> = { available: "可出航", disabled: "失去出航能力", destroyed: "已毁坏", withdrawn: "已离场" };
const reasonLabel: Record<string, string> = { load: "装载", unload: "卸载", consume: "使用", reload: "装填", discharge: "射击" };

export function resourceRows(ship: SettlementShip) {
  function totals(record: CombatRecord) {
    const rows: Record<string, number> = { ammunition: record.state.magazines.reduce((n, v) => n+v.quantity, 0) };
    for (const w of record.state.weapons) rows[`ready:${w.module_id}`] = w.ready_rounds;
    for (const c of record.state.cargo) rows[`cargo:${c.good_id}`] = c.quantity;
    return rows;
  }
  const before = totals(ship.before), after = totals(ship.after);
  return [...new Set([...Object.keys(before), ...Object.keys(after), ...ship.changes.map(c => c.resource)])].map(key => ({
    key, name: key === "ammunition" ? "弹药资源" : key.startsWith("ready:") ? `${ship.module_names[key.slice(6)] ?? key.slice(6)}待发弹` : key.slice(6),
    before: before[key] ?? 0, after: after[key] ?? 0,
    detail: ship.changes.filter(c => c.resource === key).map(c => `${reasonLabel[c.reason] ?? c.reason} ${c.delta > 0 ? "+" : ""}${c.delta}`).join("；") || "无变动",
  }));
}
