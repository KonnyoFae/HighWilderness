import {missileTotals,missileResourceName} from './missiles';
import type {MissileState} from './missiles';
export interface CombatRecord {
  resources?:{fuel_tanks?:{tank_id:string;module_id:string|null;deck_id:string|null;deck_level:number}[]};
  ship_id: string;
  state: {
    missiles?:MissileState;instance_id: string; revision: number; hull_integrity_fraction: number;
    crew?:{crew_type:string;count:number}[];wounded_aboard?:number;
    personnel?:{policy:string;statuses:{crew_type:string;wounded:number;dead:number;loss_fraction:number;death_fraction:number}[]};
    service: { status: string; reasons: string[] };
    modules: { module_id: string; durability_points: number }[];
    magazines: { module_id: string; quantity: number }[];
    weapons: { module_id: string; ready_rounds: number; cooldown_steps: number; recipe_id?: string | null }[];
    cargo: { good_id: string; quantity: number }[];
    damage_controls?: {module_id:string; quantity_units:number; preparation:{remaining_steps:number}|null}[];
    fires?: {module_id:string|null; intensity_units:number; remaining_steps:number;zone_id?:string;spread_steps?:number;random_state?:number}[];
    fuel_tanks?:{tank_id:string;quantity_units:number;durability_points:number}[];
  };
  armor: { deck_id: string; deck_level: number; region_id: string; edge_index: number; durability: number }[];
}
export interface SettlementShip {
  side_id?: string; ship_name?: string;
  before: CombatRecord; after: CombatRecord; module_names: Record<string, string>;
  capacity_before: { capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean };
  capacity_after: { capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean };
  changes: { resource: string; reason: string; delta: number }[];
}
export interface SettlementEnvelope {
  saved: boolean; error?: string | null;
  result: { settlement_id: string; reason: string; fixed_step: number; ships: SettlementShip[]; player_side_id?: string;
    wrecks?:{instance_id:string;ship_id:string;position_m:number[];height_layer:string;reason:string}[] };
}
export interface SettlementLibrary {
  results: { settlement_id: string; saved: boolean; reason: string; fixed_step: number }[];
  ships: { instance_id: string; revision: number; hull_integrity: number; service: { status: string }; can_deploy: boolean }[];
}
export const endingLabel: Record<string, string> = { withdrawal: "主动撤离", victory: "本方胜利", defeat: "本方失去作战能力", draw: "双方失去作战能力" };
export const serviceLabel: Record<string, string> = { available: "可入战", disabled: "失去作战能力", destroyed: "已毁坏", withdrawn: "已离场" };
export function settlementShipLabel(ship: SettlementShip, playerSide?: string) {
  if (ship.side_id && playerSide) return `${ship.side_id === playerSide ? '我方' : '敌方'} · ${ship.ship_name ?? ship.after.ship_id}`;
  // Old technical scenes have known identities; arbitrary historical IDs do not encode a side.
  return `${ship.after.ship_id === 'ship.web.red' ? '敌方测试舰' : ship.after.ship_id === 'ship.web.blue' ? '我方测试舰' : '舰艇'} · ${ship.ship_name ?? ship.after.ship_id}`;
}
export function missileSettlement(ship: SettlementShip) {
  const count = (state?: MissileState) => ({
    ready: state?.launchers.reduce((n, l) => n + l.ready.length, 0) ?? 0,
    stored: state?.magazines.reduce((n, m) => n + m.stock.length, 0) ?? 0,
    working: (state?.launchers.filter(l => l.job).length ?? 0) + (state?.magazines.reduce((n, m) => n + m.jobs.length, 0) ?? 0),
  });
  return {before: count(ship.before.state.missiles), after: count(ship.after.state.missiles),
    fired: -ship.changes.filter(c => c.reason === 'missile_fired' && c.resource.startsWith('missile:') && c.resource.split(':')[2] === 'ready').reduce((n, c) => n + c.delta, 0)};
}
const reasonLabel: Record<string, string> = { load: "装载", unload: "卸载", consume: "使用", reload: "装填", discharge: "射击",
  missile_fired:'导弹发射消耗', missile_logistics:'导弹组装与装填', damage_control_preparation:'损管准备', damage_control_use:'损管使用', firefighting:'灭火', module_repair:'部件维修', hull_repair:'船壳维修',tank_destroyed:'燃料槽损毁',emergency_lift_repair:'储罐紧急抢修',emergency_lift_refill:'抢修后补油',magazine_detonation:'弹药库殉爆' };

export function fuelTankName(t:{module_id:string|null;deck_level:number},names:Record<string,string>) {
  return t.module_id?`${names[t.module_id]??t.module_id}燃料槽`:`第 ${t.deck_level} 层填充燃料槽`;
}

export function resourceRows(ship: SettlementShip) {
  function totals(record: CombatRecord) {
    const rows: Record<string, number> = { ammunition: record.state.magazines.reduce((n, v) => n+v.quantity, 0) };
    for (const w of record.state.weapons) rows[`ready:${w.module_id}`] = w.ready_rounds;
    for (const c of record.state.cargo) rows[`cargo:${c.good_id}`] = c.quantity;
    for (const d of record.state.damage_controls??[]) rows[`damage_control:${d.module_id}`] = d.quantity_units;
    for (const t of record.state.fuel_tanks??[])rows[`fuel:${t.tank_id}`]=t.quantity_units;
    Object.assign(rows,missileTotals(record.state.missiles));
    return rows;
  }
  const before = totals(ship.before), after = totals(ship.after);
  const tankLabel=(key:string)=>{const t=ship.after.resources?.fuel_tanks?.find(t=>t.tank_id===key.slice(5));return t?fuelTankName(t,ship.module_names)+'燃料':key.slice(5);};
  return [...new Set([...Object.keys(before), ...Object.keys(after), ...ship.changes.map(c => c.resource)])].map(key => ({
    key, name: key.startsWith('missile:') ? missileResourceName(key,ship.module_names) : key.startsWith('fuel:') ? tankLabel(key) : key.startsWith('damage_control:') ? `${ship.module_names[key.slice(15)]??key.slice(15)}损管资源（点）` : key === "ammunition" ? "弹药资源" : key.startsWith("ready:") ? `${ship.module_names[key.slice(6)] ?? key.slice(6)}待发弹` : key === 'cargo:cargo.rocket_parts' ? '火箭零件' : key === 'cargo:cargo.turbojet_parts' ? '涡喷零件' : key === 'cargo:cargo.radar_parts' ? '雷达零件' : key === 'cargo:cargo.infrared_parts' ? '红外零件' : key === 'cargo:cargo.high_explosive' ? '高能炸药' : key === 'cargo:cargo.high_energy_fuel' ? '高能燃料' : key === 'cargo:cargo.special_alloy' ? '特殊合金' : key === 'cargo:cargo.engineering_parts' ? '工程零件' : key.slice(6),
    before: (before[key] ?? 0)/(key.startsWith('damage_control:')?1000:1), after: (after[key] ?? 0)/(key.startsWith('damage_control:')?1000:1),
    detail: ship.changes.filter(c => c.resource === key).map(c => `${reasonLabel[c.reason] ?? c.reason} ${c.delta > 0 ? "+" : ""}${c.delta/(key.startsWith('damage_control:')?1000:1)}`).join("；") || "无变动",
  }));
}
