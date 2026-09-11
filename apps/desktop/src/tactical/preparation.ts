import type { CombatRecord, SettlementShip } from './settlement';
export type WeaponChoice = { module_id: string; action: 'keep' | 'preload' | 'discard_and_preload'; recipe_id: string | null; batches: number };
export type PreparedLaunch = { preparation_id:string; launch_id:string; direct_instance_id:string };
export interface PreparationDraft {
  preparation_id: string; revision: number; supply_id: string;
  ships: { instance_id: string; revision: number; magazines: { module_id: string; quantity: number }[];
    cargo: { good_id: string; quantity: number }[]; weapons: WeaponChoice[];
    damage_controls?: {module_id:string; prepare:boolean}[];fuel_tanks?:{tank_id:string;quantity_units:number}[] }[];
}
export interface Supply { ammunition_resources: number;fuel_units?:number; cargo: { good_id: string; quantity: number }[] }
export interface PreparationResult { preparation_id: string; ships: SettlementShip[]; supply_before: Supply; supply_after: Supply }
export interface PreparationShip {
  instance_id: string; name: string; module_names: Record<string,string>;
  state: Omit<CombatRecord['state'], 'weapons'> & { fuel_units: number; crew: { crew_type: string; count: number }[];
    weapons: (CombatRecord['state']['weapons'][number] & { recipe_id: string | null })[] };
  resources: { ignition_decks?:{deck_id:string;deck_level:number;multiplier:number}[]; fuel_tanks?:{tank_id:string;module_id:string|null;deck_id:string|null;deck_level:number;capacity_units:number;maximum_points:number}[];
    damage_controls?: {module_id:string; capacity_units:number; preparation_steps:number; cargo_costs:{good_id:string;quantity:number}[]}[];
    goods: { id: string; unit_volume_cm3: number }[]; magazines: { module_id: string; capacity_resources: number }[];
    weapons: { module_id: string; ready_capacity: number; recipe_ids: string[] }[];
    recipes: { id: string; ammo_cost: number; rounds: number; cargo_costs: { good_id: string; quantity: number }[] }[] };
  enabled_recipe_ids: string[]; capacity: { capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean };
}
export interface PreparationPacket { draft: PreparationDraft; ships: PreparationShip[]; supply: Supply | null; receipt: PreparationResult | null; stale_error: string | null }
export interface PreparationLibrary { interrupted_battles?:number; ships: { instance_id: string; name: string; revision: number; hull_integrity: number; blocked: boolean }[];
  drafts: { preparation_id: string; revision: number; saved: boolean }[]; sources: { key: string; name: string }[] }
export interface PreparationPreview { can_commit: boolean; issues: { instance_id: string | null; target: string; message: string; missing?: number }[]; result: PreparationResult | null }
export const goodName = (id: string) => ({'cargo.high_energy_fuel':'高能燃料','cargo.special_alloy':'特殊合金','cargo.engineering_parts':'工程零件',fuel:'灵烷燃料'}[id] ?? id);
const preparationErrors: Record<string,string> = {
  'Only an empty idle device can prepare':'仅资源用尽且未在准备中的设备可以预准备',
  'Damage-control device or host destroyed':'损管设备或宿主已损毁，不能准备',
  'Insufficient engineering parts':'工程零件不足',
  'Weapon or host destroyed':'武器或其宿主已损毁，不能预装填',
  'Weapon is reloading':'武器已有未完成装填',
  'Incompatible recipe':'该武器不能使用所选弹种',
  'Empty weapon before changing ammunition':'已有其他弹种，请明确选择弃置现有弹药后重装',
  'Whole batch does not fit':'剩余待发容量不足以装下所选整批弹药',
  'Insufficient available ammunition':'舰内可用弹药资源不足',
  'Insufficient special materials':'预装填所需的特殊货物不足',
  'Cargo capacity exceeded':'装载后超出有效货舱容积',
  'Insufficient unreserved cargo':'未被预留的货物不足',
  'Magazine unavailable':'弹药库或其宿主已损毁',
  'Ammunition is reserved or insufficient':'可用弹药资源不足或已被预留',
};
export const preparationError = (message:string) => preparationErrors[message]??message;
export function quantity(text: string): number {
  const value=Number(text);
  if (!text.trim() || !Number.isSafeInteger(value) || value<0) throw new Error('数量需要填写非负整数。');
  return value;
}
export function changeDraft(draft: PreparationDraft, shipId: string, edit: (ship: PreparationDraft['ships'][number]) => void) {
  const next=structuredClone(draft), ship=next.ships.find(s=>s.instance_id===shipId);
  if (!ship) throw new Error('找不到所选舰船。');
  edit(ship); next.revision++; return next;
}
