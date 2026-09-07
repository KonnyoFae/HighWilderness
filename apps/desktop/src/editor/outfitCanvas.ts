import type { HullDeck, ModuleOption, OutfitInstance } from "./model";
import type { Point } from "./viewport";

export type Cell = [number, number, number];
export interface SideSlot { deck_id: string; region_id: string; edge_index: number; slot_index: number; start_m: [number, number]; end_m: [number, number] }
export interface LayoutModule { id: string; base_deck_level: number; anchor_m: [number, number]; rotation_deg: number;
  placement_kind: string; host_instance_id: string | null; internal_cells: Cell[]; top_cells: Cell[];
  body_spatial_keys: Cell[]; clearance_spatial_keys: Cell[]; side_slots: Omit<SideSlot, "start_m" | "end_m">[] }
export interface OutfitLayout { interface: "gaotian.outfit-layout/v1alpha1"; hull: { decks: HullDeck[] };
  decks: { id: string; level: number; internal_cells: number[][]; exposed_top_cells: number[][]; side_mount_slots: SideSlot[] }[];
  modules: LayoutModule[]; errors: { instance_id: string; message: string; path: string }[];
  conflicts: { layer: string; key: (number | string)[]; instance_ids: string[] }[] }
export function visibleAtLevel(v: LayoutModule, level: number) {
  return v.base_deck_level === level || v.internal_cells.some(c=>c[0]===level) || v.top_cells.some(c=>c[0]===level);
}
export function rotationPoint(p: number[], rotation: number): Point {
  const [x, y] = p;
  return rotation === 90 ? { x: y, y: -x } : rotation === 180 ? { x: -x, y: -y } : rotation === 270 ? { x: -y, y: x } : { x, y };
}
// Choose an anchor parity that puts the prototype's cell centres on 5 m centres.
// Both even and odd half-cell anchors are supported; no optional off-grid mode.
export function placementPoint(p: Point, option: ModuleOption, rotation: number): Point {
  const g = option.prototype.installation;
  const offset = rotationPoint(g.internal_footprint_half_cells[0] ?? g.top_footprint_half_cells[0] ?? [0, 0], rotation);
  const axis = (n: number, d: number) => Math.round((n + d * 2.5) / 5) * 5 - d * 2.5;
  return { x: axis(p.x, offset.x), y: axis(p.y, offset.y) };
}
export function footprint(option: ModuleOption, anchor: Point, rotation: number) {
  const g = option.prototype.installation;
  return [...g.internal_footprint_half_cells, ...g.top_footprint_half_cells].map(p => {
    const r = rotationPoint(p, rotation); return { x: anchor.x + r.x * 2.5, y: anchor.y + r.y * 2.5 };
  });
}
export function nearestSlot(slots: SideSlot[], p: Point, tolerance: number): SideSlot | undefined {
  let best: SideSlot | undefined, distance = tolerance;
  for (const s of slots) {
    const [ax, ay] = s.start_m, [bx, by] = s.end_m, dx = bx - ax, dy = by - ay;
    const t = Math.max(0, Math.min(1, ((p.x - ax) * dx + (p.y - ay) * dy) / (dx * dx + dy * dy)));
    const d = Math.hypot(p.x - ax - t * dx, p.y - ay - t * dy);
    if (d < distance) { distance = d; best = s; }
  }
  return best;
}
export function mountKind(option: ModuleOption) {
  const g = option.prototype.installation;
  return g.host_slot ? "hosted" : g.side_mount_length_steps ? "side" : "grid";
}
export function defaultRotation(option?: ModuleOption) {
  if (!option) return 0;
  const g = option.prototype.installation, allowed = g.allowed_rotations_deg;
  if (mountKind(option) !== "side") return allowed[0] ?? 0;
  const body = (g.side_external_footprint_half_cells ?? []) as number[][];
  const clearance = [...(g.side_clearance_half_cells ?? []) as number[][], ...(g.exhaust_clearance_half_cells ?? []) as number[][]];
  // A starting orientation relative to the mounting edge, not a legality verdict.
  // Local +Y points out of the hull; clearance must lie strictly beyond the edge.
  return allowed.find(r => body.every(p => rotationPoint(p, r).y >= 0)
    && clearance.every(p => rotationPoint(p, r).y > 0)) ?? allowed[0] ?? 0;
}
export function sidePreview(slots: SideSlot[], first: SideSlot, option: ModuleOption, rotation: number) {
  const g = option.prototype.installation;
  const selected = Array.from({ length: g.side_mount_length_steps }, (_, i) => slots.find(s =>
    s.deck_id === first.deck_id && s.region_id === first.region_id && s.edge_index === first.edge_index && s.slot_index === first.slot_index + i));
  if (!selected.length || selected.some(s => !s)) return undefined;
  const last = selected[selected.length - 1]!;
  const anchor = { x: (first.start_m[0] + last.end_m[0]) / 2, y: (first.start_m[1] + last.end_m[1]) / 2 };
  const dx = first.end_m[0] - first.start_m[0], dy = first.end_m[1] - first.start_m[1], length = Math.hypot(dx, dy);
  if (!length) return undefined;
  const project = (offset: number[]) => {
    const p = rotationPoint(offset, rotation);
    return { x: anchor.x + 2.5 * (dx * p.x + dy * p.y) / length, y: anchor.y + 2.5 * (dy * p.x - dx * p.y) / length };
  };
  return { anchor, body: ((g.side_external_footprint_half_cells ?? []) as number[][]).map(project),
    clearance: [...(g.side_clearance_half_cells ?? []) as number[][], ...(g.exhaust_clearance_half_cells ?? []) as number[][]].map(project) };
}
export function hostedDescendants(modules: OutfitInstance[], id: string) {
  const ids = new Set([id]);
  let previous = 0;
  while (previous !== ids.size) {
    previous = ids.size;
    for (const m of modules) if (m.placement.kind === "hosted" && ids.has(m.placement.host_instance_id ?? "")) ids.add(m.id);
  }
  return modules.filter(m => m.id !== id && ids.has(m.id));
}
export function gridHint(layout: OutfitLayout, deckId: string, option: ModuleOption, anchor: Point, rotation: number, exclude = "") {
  const base = layout.decks.find(d => d.id === deckId);
  if (!base) return "没有可用甲板";
  const g = option.prototype.installation;
  if (g.deck_rule === "base_only" && base.level !== 0) return "该模块只能放在基底层";
  if (option.prototype.category === "cic" && (base.level !== 0 || anchor.x !== 0 || anchor.y !== 0 || rotation !== 0)) return "CIC 必须位于基底原点，朝向 0°";
  const requests: { level: number; top: boolean; offsets: number[][] }[] = [];
  for (let i = 0; i < Number(g.internal_deck_span ?? 1); i++) if (g.internal_footprint_half_cells.length) requests.push({ level: base.level + i, top: false, offsets: g.internal_footprint_half_cells });
  if (g.top_footprint_half_cells.length) requests.push({ level: base.level + Number(g.top_deck_offset ?? 0), top: true, offsets: g.top_footprint_half_cells });
  for (const request of requests) {
    const deck = layout.decks.find(d => d.level === request.level);
    if (!deck) return "模块跨层范围超出船壳";
    const available = request.top ? deck.exposed_top_cells : deck.internal_cells;
    for (const offset of request.offsets) {
      const p = rotationPoint(offset, rotation), x = (anchor.x + p.x * 2.5) / 5, y = (anchor.y + p.y * 2.5) / 5;
      if (!available.some(c => c[0] === x && c[1] === y)) return request.top ? "顶挂部分没有可用露天格" : "模块超出内部安装格";
      const other = layout.modules.find(m => m.id !== exclude && (request.top ? m.top_cells : m.internal_cells).some(c => c[0] === request.level && c[1] === x && c[2] === y));
      if (other) return `与 ${other.id} 占用重叠`;
    }
  }
  return "";
}
export function compatibleHosts(option: ModuleOption, modules: OutfitInstance[], options: ModuleOption[], exclude = "") {
  const slot = option.prototype.installation.host_slot;
  return modules.filter(m => m.id !== exclude && options.some(o => o.prototype.id === m.prototype.id && o.prototype.version === m.prototype.version
    && (o.prototype.installation.provided_slots as string[] | undefined)?.includes(slot ?? ""))
    && !modules.some(child => child.id !== exclude && child.placement.host_instance_id === m.id
      && options.some(o => o.prototype.id === child.prototype.id && o.prototype.version === child.prototype.version && o.prototype.installation.host_slot === slot)));
}
