import { nextId } from "./interaction";

export interface WeaponGroup {
  id: string;
  name: string;
  prototype: { id: string; version: number };
  weapon_instance_ids: string[];
}
export interface WeaponArc {
  instance_id: string;
  origin_m: [number, number] | null;
  base_deck_level: number | null;
  status: "placement_invalid" | "requires_higher_deck_hull_raycast" | "full_circle_no_higher_deck" | "hull_occlusion_resolved";
  intervals_deg?: [number, number][];
  blocked_intervals_deg?: [number, number][];
}
export interface WeaponControl {
  interface: "gaotian.weapon-control-view/v1alpha1" | "gaotian.weapon-control-view/v2alpha1";
  grouping: "automatic" | "explicit";
  groups: WeaponGroup[];
  arcs: WeaponArc[];
}
export const sameWeapon = (a: WeaponGroup, b: WeaponGroup) => a.prototype.id === b.prototype.id && a.prototype.version === b.prototype.version;

export function splitGroup(groups: WeaponGroup[], id: string, members: string[], name: string): WeaponGroup[] {
  const group = groups.find(g => g.id === id);
  if (!group || !members.length || members.length >= group.weapon_instance_ids.length || new Set(members).size !== members.length || members.some(m => !group.weapon_instance_ids.includes(m))) throw new Error("请选择组内部分武器，原组至少保留一件武器。");
  if (!name.trim() || name.trim().length > 80) throw new Error("武器组名称须为 1–80 个字符。");
  if (groups.length >= 64) throw new Error("最多配置 64 个武器组。");
  return [...groups.map(g => g.id === id ? { ...g, weapon_instance_ids: g.weapon_instance_ids.filter(m => !members.includes(m)) } : g),
    { id: nextId("weapon_group", groups.map(g => g.id)), name: name.trim(), prototype: { ...group.prototype }, weapon_instance_ids: [...members].sort() }];
}

export function mergeGroups(groups: WeaponGroup[], sourceId: string, targetId: string): WeaponGroup[] {
  const source = groups.find(g => g.id === sourceId), target = groups.find(g => g.id === targetId);
  if (!source || !target || sourceId === targetId || !sameWeapon(source, target)) throw new Error("只能合并相同型号、相同版本的不同武器组。");
  return groups.filter(g => g.id !== sourceId).map(g => g.id === targetId ? { ...g, weapon_instance_ids: [...g.weapon_instance_ids, ...source.weapon_instance_ids].sort() } : g);
}

export function arcText(arc?: WeaponArc): string {
  if (!arc || arc.status === "placement_invalid") return "安装位置无效，射界暂不可用";
  if (arc.status === "hull_occlusion_resolved") {
    const blocked = (arc.blocked_intervals_deg ?? []).reduce((total, [a,b]) => total + b-a, 0);
    return blocked === 0 ? "水平射界 360°：上层船壳无遮挡" : `上层船壳禁射 ${blocked.toFixed(1)}°；绿色可射，红色禁射（含边界）`;
  }
  if (arc.status === "full_circle_no_higher_deck") return "没有更高甲板；船壳参考射界为 360°";
  return "存在更高甲板，遮挡射界尚需检查，不能按全向射界使用";
}

export function arcSectorPath(cx: number, cy: number, radius: number, [start, end]: [number, number]): string {
  const point = (angle: number) => `${cx + radius*Math.sin(angle*Math.PI/180)},${cy - radius*Math.cos(angle*Math.PI/180)}`;
  if (end-start >= 360) return `M ${point(start)} A ${radius},${radius} 0 1 1 ${point(start+180)} A ${radius},${radius} 0 1 1 ${point(end)} Z`;
  return `M ${cx},${cy} L ${point(start)} A ${radius},${radius} 0 ${end-start > 180 ? 1 : 0} 1 ${point(end)} Z`;
}
