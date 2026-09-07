import type { LayoutModule } from "./outfitCanvas";
import { visibleAtLevel } from "./outfitCanvas";
import type { Camera } from "./viewport";
import { screen } from "./viewport";
import { arcSectorPath } from "./weaponGroups";
import type { WeaponArc } from "./weaponGroups";

export function arcVisibleAtLevel(arc: WeaponArc | undefined, module: LayoutModule | undefined, level: number | undefined) {
  return level !== undefined && !!arc?.origin_m && arc.status !== "placement_invalid"
    && (arc.base_deck_level === level || !!module && visibleAtLevel(module, level));
}

export function WeaponArcOverlay({ arc, module, level, camera, show }: {
  arc?: WeaponArc; module?: LayoutModule; level?: number; camera: Camera; show: boolean;
}) {
  if (!show || !arcVisibleAtLevel(arc, module, level) || !arc?.origin_m) return null;
  const p = screen({ x: arc.origin_m[0], y: arc.origin_m[1] }, camera);
  if (arc.status === "hull_occlusion_resolved") return <g pointerEvents="none" aria-label="武器水平射界">{[
    { intervals: arc.intervals_deg ?? [], color: "#70dfa1", label: "水平可射方向；与禁射区共用的边界禁止开火" },
    { intervals: arc.blocked_intervals_deg ?? [], color: "#ff6868", label: "上层船壳遮挡，禁止开火（含边界）" },
  ].flatMap(({ intervals, color, label }) => intervals.map((interval, i) => <path key={`${color}${i}`}
    d={arcSectorPath(p.x, p.y, 90, interval)} fill={color} fillOpacity={.24} stroke={color} strokeWidth={2}>
    <title>{label}</title>
  </path>))}</g>;
  if (arc.status === "full_circle_no_higher_deck") return <circle cx={p.x} cy={p.y} r={70} fill="none"
    stroke="#ffdb83" strokeWidth={2} strokeDasharray="7 5" pointerEvents="none"><title>360° 船壳参考方向圈，不表示射程</title></circle>;
  return null;
}
