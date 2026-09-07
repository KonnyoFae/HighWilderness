import type { ModuleOption, OutfitInstance } from "./model";

export const categories: Record<string, string> = { cic: "CIC", main_engine: "主发动机", maneuver_thruster: "转向发动机", generator: "发电", damage_control: "损管", crew_quarters: "人员舱", lift_fuel_tank: "灵烷贮槽", cargo_hold: "货仓", ammunition_magazine: "弹药库", weapon: "武器", fire_control: "火控", sensor: "探测", remote_core: "遥控核心" };
export function mounts(option: ModuleOption): string[] {
  const g = option.prototype.installation;
  return [g.internal_footprint_half_cells.length ? "内部" : "", g.top_footprint_half_cells.length ? "顶挂" : "",
    g.side_mount_length_steps ? "侧挂" : "", g.host_slot ? "嵌入" : ""].filter(Boolean);
}
export function filterModules(options: ModuleOption[], category: string, mount: string, version: string) {
  return options.filter(o => (!category || o.prototype.category === category) && (!mount || mounts(o).includes(mount))
    && (!version || String(o.prototype.version) === version));
}
export interface OutfitFields { instance_id: string; deck_id: string; x: string; y: string; rotation: string;
  region_id: string; edge: string; slot: string; host: string }
export function instanceFields(instance?: OutfitInstance): OutfitFields {
  const p = instance?.placement;
  return { instance_id: instance?.id ?? "", deck_id: p?.deck_id ?? "deck.0", x: String((p?.anchor_half_cell?.[0] ?? 0) * 2.5),
    y: String((p?.anchor_half_cell?.[1] ?? 0) * 2.5), rotation: String(p?.rotation_deg ?? 0),
    region_id: p?.region_id ?? "deck.0.region.0", edge: String(p?.edge_index ?? 0), slot: String(p?.start_slot_index ?? 0), host: p?.host_instance_id ?? "" };
}
export function outfitCommand(action: "place" | "move" | "rotate" | "remove", kind: "grid" | "side" | "hosted", fields: OutfitFields, option?: ModuleOption) {
  const args: Record<string, unknown> = { instance_id: fields.instance_id.trim() };
  if (!args.instance_id) throw new Error("请填写实例名称。");
  const integer = (value: string, scale = 1) => {
    const n = Number(value) / scale;
    if (!value.trim() || !Number.isSafeInteger(n)) throw new Error(scale === 2.5 ? "坐标必须落在 2.5 m 网格点上。" : "请填写有效整数。");
    return n;
  };
  if (action === "remove") return { command: "outfit.remove", args };
  if (action === "rotate") return { command: "outfit.rotate_grid", args: { ...args, rotation_deg: integer(fields.rotation) } };
  if (action === "place") {
    if (!option) throw new Error("请选择模块原型。");
    args.prototype = { id: option.prototype.id, version: option.prototype.version };
  }
  if (kind === "hosted") {
    if (!fields.host) throw new Error("请选择宿主模块。");
    args.host_instance_id = fields.host;
  } else {
    if (!fields.deck_id.trim()) throw new Error("请填写甲板名称。");
    args.deck_id = fields.deck_id.trim();
    if (kind === "grid") args.anchor_half_cell = [integer(fields.x, 2.5), integer(fields.y, 2.5)];
    else Object.assign(args, { region_id: fields.region_id.trim(), edge_index: integer(fields.edge), start_slot_index: integer(fields.slot) });
    args.rotation_deg = integer(fields.rotation);
  }
  return { command: action === "move" && kind === "hosted" ? "outfit.rehost" : `outfit.${action}_${kind}`, args };
}
