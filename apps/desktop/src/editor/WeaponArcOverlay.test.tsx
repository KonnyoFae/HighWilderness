import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WeaponArcOverlay } from "./WeaponArcOverlay";
import type { WeaponArc } from "./weaponGroups";
import type { LayoutModule } from "./outfitCanvas";

const weapon: LayoutModule = { id: "lower_gun", base_deck_level: 0, anchor_m: [5, -10], rotation_deg: 0,
  placement_kind: "grid", host_instance_id: null, internal_cells: [[0, 1, -2]], top_cells: [[1, 1, -2]],
  body_spatial_keys: [], clearance_spatial_keys: [], side_slots: [] };
const arc: WeaponArc = { instance_id: weapon.id, origin_m: weapon.anchor_m, base_deck_level: 0,
  status: "hull_occlusion_resolved", intervals_deg: [[30, 330]], blocked_intervals_deg: [[0, 30], [330, 360]] };
const render = (level: number, value = arc, show = true) => renderToStaticMarkup(<svg>
  <WeaponArcOverlay arc={value} module={weapon} level={level} camera={{ x: 100, y: 100, scale: 2 }} show={show} />
</svg>);

describe("下层武器射界显示", () => {
  it.each([0, 1])("在安装层及炮塔露出层 %i 均绘制可射和禁射方向", level => {
    const svg = render(level);
    expect(svg.match(/<path /g)).toHaveLength(3);
    expect(svg).toContain('fill="#70dfa1"');
    expect(svg).toContain('fill="#ff6868"');
    expect(svg).toContain('M 110,120');
  });
  it("武器不可见层和关闭显示时不绘制", () => {
    expect(render(2)).toBe("<svg></svg>");
    expect(render(0, arc, false)).toBe("<svg></svg>");
  });
  it("完全遮挡时保留完整红圈，无绿色可射区", () => {
    const svg = render(0, { ...arc, intervals_deg: [], blocked_intervals_deg: [[0, 360]] });
    expect(svg.match(/<path /g)).toHaveLength(1);
    expect(svg.match(/ A /g)).toHaveLength(2);
    expect(svg).toContain('fill="#ff6868"');
    expect(svg).not.toContain("#70dfa1");
  });
  it("无遮挡下层武器绘制完整绿色圈", () => {
    const svg = render(0, { ...arc, intervals_deg: [[0, 360]], blocked_intervals_deg: [] });
    expect(svg.match(/ A /g)).toHaveLength(2);
    expect(svg).toContain('fill="#70dfa1"');
    expect(svg).not.toContain("#ff6868");
  });
  it("旧版未计算和非法安装不显示虚假的可用射界", () => {
    expect(render(0, { ...arc, status: "requires_higher_deck_hull_raycast" })).toBe("<svg></svg>");
    expect(render(0, { ...arc, status: "placement_invalid", origin_m: null })).toBe("<svg></svg>");
  });
});
