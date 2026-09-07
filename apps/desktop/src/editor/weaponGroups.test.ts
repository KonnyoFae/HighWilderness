import { describe, expect, it } from "vitest";
import { arcText, arcSectorPath, mergeGroups, splitGroup } from "./weaponGroups";
import type { WeaponGroup } from "./weaponGroups";

const base: WeaponGroup[] = [{id:"weapon_group.1",name:"主炮",prototype:{id:"gun",version:1},weapon_instance_ids:["a","b","c"]}];
describe("武器组配置", () => {
  it("按舰艏顺时针绘制禁射扇区和全圆", () => {
    const quarter = arcSectorPath(0,0,90,[0,90]);
    expect(quarter).toContain("M 0,0 L 0,-90 A 90,90 0 0 1 90,");
    expect(arcSectorPath(0,0,90,[0,360]).match(/ A /g)).toHaveLength(2);
    expect(arcText({instance_id:"a",origin_m:[0,0],base_deck_level:0,status:"hull_occlusion_resolved",blocked_intervals_deg:[[0,30],[330,360]]})).toContain("禁射 60.0°");
  });
  it("拆组保持完整互斥分区，且不改原值", () => {
    const result = splitGroup(base,base[0].id,["c","a"],"  前组  ");
    expect(result[0].weapon_instance_ids).toEqual(["b"]);
    expect(result[1].weapon_instance_ids).toEqual(["a","c"]);
    expect(result[1].name).toBe("前组");
    expect(result[1].id).not.toBe(base[0].id);
    expect(base[0].weapon_instance_ids).toEqual(["a","b","c"]);
  });
  it("拒绝空组、整组、重复和外部成员", () => {
    for (const members of [[],["a","b","c"],["a","a"],["outside"]]) expect(()=>splitGroup(base,base[0].id,members,"新组")).toThrow();
  });
  it("名称和组数有边界", () => {
    for (const name of [" ","x".repeat(81)]) expect(()=>splitGroup(base,base[0].id,["a"],name)).toThrow();
    expect(()=>splitGroup(Array.from({length:64},(_,i)=>({...base[0],id:String(i)})),"0",["a"],"新组")).toThrow();
  });
  it("同型号组可以合并，保留目标组身份和全部成员", () => {
    const split = splitGroup(base,base[0].id,["a"],"前组");
    expect(mergeGroups(split,split[1].id,split[0].id)).toEqual(base);
  });
  it("不同型号或版本不可合并", () => {
    for (const prototype of [{id:"other",version:1},{id:"gun",version:2}]) {
      expect(()=>mergeGroups([...base,{...base[0],id:"other",prototype}],base[0].id,"other")).toThrow();
    }
    expect(()=>mergeGroups(base,base[0].id,base[0].id)).toThrow();
  });
  it("未知遮挡和非法安装不会宣称全向可用", () => {
    expect(arcText({instance_id:"a",origin_m:[0,0],base_deck_level:0,status:"requires_higher_deck_hull_raycast"})).toContain("尚需检查");
    expect(arcText()).toContain("暂不可用");
  });
});
