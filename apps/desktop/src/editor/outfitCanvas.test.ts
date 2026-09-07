import { describe, expect, it } from "vitest";
import { compatibleHosts, defaultRotation, footprint, gridHint, hostedDescendants, mountKind, nearestSlot, placementPoint, sidePreview, visibleAtLevel } from "./outfitCanvas";
import type { OutfitLayout, SideSlot } from "./outfitCanvas";
import type { ModuleOption, OutfitInstance } from "./model";
const option = (geometry = {}): ModuleOption => ({
  prototype: { id: "module", version: 1, name: "test", category: "cargo", balance_status: "contract_fixture", mass_kg: 1, durability_points: 1,
    installation: { allowed_rotations_deg: [0,90,180,270], internal_footprint_half_cells: [[0,0]], top_footprint_half_cells: [], side_mount_length_steps: 0, host_slot: null, ...geometry },
    power: {}, crew: [], automation: {}, capability: {} }, sha256: "test", catalog: {} as ModuleOption["catalog"] });
const layout = (): OutfitLayout => ({ interface: "gaotian.outfit-layout/v1alpha1", hull: {decks: []},
  decks: [{ id: "custom.base", level: 0, internal_cells: [[0,0],[0,1]], exposed_top_cells: [[0,1]], side_mount_slots: [] }], modules: [], errors: [], conflicts: [] });
describe("outfit canvas geometry", () => {
  it("defaults a side thruster to outward exhaust, with matching port/starboard previews", () => {
    const o=option({internal_footprint_half_cells:[],side_mount_length_steps:1,side_external_footprint_half_cells:[[0,0]],exhaust_clearance_half_cells:[[0,-2]]});
    expect(defaultRotation(o)).toBe(180);
    const port:SideSlot={deck_id:"d",region_id:"r",edge_index:3,slot_index:1,start_m:[-10,5],end_m:[-10,0]};
    const starboard:SideSlot={...port,edge_index:1,start_m:[10,0],end_m:[10,5]};
    expect(sidePreview([port],port,o,180)?.clearance).toEqual([{x:-15,y:2.5}]);
    expect(sidePreview([starboard],starboard,o,180)?.clearance).toEqual([{x:15,y:2.5}]);
    expect(sidePreview([port],port,o,0)?.clearance).toEqual([{x:-5,y:2.5}]);
    expect(defaultRotation(option())).toBe(0);
    expect(defaultRotation(option({internal_footprint_half_cells:[],side_mount_length_steps:1,side_external_footprint_half_cells:[[0,1]]}))).toBe(0);
  });
  it("side preview follows the centre of all required slots and rejects a missing segment", () => {
    const o=option({internal_footprint_half_cells:[],side_mount_length_steps:2,side_external_footprint_half_cells:[[0,0]]});
    const first:SideSlot={deck_id:"d",region_id:"r",edge_index:3,slot_index:2,start_m:[-10,10],end_m:[-10,5]};
    const second={...first,slot_index:3,start_m:[-10,5] as [number,number],end_m:[-10,0] as [number,number]};
    expect(sidePreview([first],first,o,180)).toBeUndefined();
    expect(sidePreview([first,second],first,o,180)?.anchor).toEqual({x:-10,y:5});
  });
  it("discloses all hosted descendants without removing independent modules", () => {
    const make=(id:string,host?:string):OutfitInstance=>({id,prototype:{id:"p",version:1},placement:host?{kind:"hosted",host_instance_id:host}:{kind:"grid"}});
    expect(hostedDescendants([make("nested","core"),make("core","cic"),make("cic"),make("cargo")],"cic").map(m=>m.id)).toEqual(["nested","core"]);
  });
  it("snaps even footprints onto full cell centres", () => {
    expect(placementPoint({x:2.4,y:-6.2},option(),0)).toEqual({x:0,y:-5});
  });
  it("keeps odd half-cell anchors and rotated footprints on full centres", () => {
    const o=option({internal_footprint_half_cells:[[1,0],[3,0]]});
    for(const r of [0,90,180,270]) {
      const p=placementPoint({x:-7.2,y:6.4},o,r);
      expect(p.x%2.5).toBeCloseTo(0); expect(p.y%2.5).toBeCloseTo(0);
      for(const c of footprint(o,p,r)) { expect(c.x%5).toBeCloseTo(0); expect(c.y%5).toBeCloseTo(0); }
    }
  });
  it("picks real segments only within tolerance and preserves exact side identity", () => {
    const s:SideSlot={deck_id:"custom.base",region_id:"r",edge_index:7,slot_index:8,start_m:[0,0],end_m:[5,0]};
    expect(nearestSlot([s],{x:4,y:.1},.5)).toBe(s);
    expect(nearestSlot([s],{x:8,y:0},.5)).toBeUndefined();
  });
  it("hints internal bounds, exposed top and missing span independently", () => {
    const l=layout();
    expect(gridHint(l,"custom.base",option(),{x:0,y:0},0)).toBe("");
    expect(gridHint(l,"custom.base",option(),{x:5,y:0},0)).toContain("内部安装格");
    expect(gridHint(l,"custom.base",option({internal_deck_span:2}),{x:0,y:0},0)).toContain("跨层");
    expect(gridHint(l,"custom.base",option({top_footprint_half_cells:[[0,0]]}),{x:0,y:0},0)).toContain("露天格");
  });
  it("ignores the moved instance when showing overlap hints", () => {
    const l=layout(); l.modules=[{id:"a",base_deck_level:0,anchor_m:[0,0],rotation_deg:0,placement_kind:"grid",host_instance_id:null,internal_cells:[[0,0,0]],top_cells:[],body_spatial_keys:[],clearance_spatial_keys:[],side_slots:[]}];
    expect(gridHint(l,"custom.base",option(),{x:0,y:0},0)).toContain("a");
    expect(gridHint(l,"custom.base",option(),{x:0,y:0},0,"a")).toBe("");
  });
  it("filters hosted slots by exact prototype version and occupied slot", () => {
    const host=option({provided_slots:["control"]}), child=option({host_slot:"control"}); child.prototype.id="child";
    const m:OutfitInstance={id:"host",prototype:{id:"module",version:1},placement:{kind:"grid"}};
    const c:OutfitInstance={id:"child",prototype:{id:"child",version:1},placement:{kind:"hosted",host_instance_id:"host"}};
    expect(compatibleHosts(child,[m],[host,child])).toEqual([m]);
    expect(compatibleHosts(child,[m,c],[host,child])).toEqual([]);
    expect(compatibleHosts(child,[m,c],[host,child],"child")).toEqual([m]);
    expect(compatibleHosts(child,[{...m,prototype:{id:"module",version:2}}],[host,child])).toEqual([]);
  });
  it("keeps placement modes distinct", () => {
    expect(mountKind(option())).toBe("grid");
    expect(mountKind(option({side_mount_length_steps:1}))).toBe("side");
    expect(mountKind(option({host_slot:"control"}))).toBe("hosted");
  });
  it("keeps cross-deck and raised top modules selectable on occupied decks", () => {
    const m:OutfitLayout["modules"][number]={id:"span",base_deck_level:0,anchor_m:[0,0],rotation_deg:0,placement_kind:"grid",host_instance_id:null,internal_cells:[[0,0,0],[1,0,0]],top_cells:[[2,0,0]],body_spatial_keys:[],clearance_spatial_keys:[],side_slots:[]};
    expect([0,1,2,3].map(level=>visibleAtLevel(m,level))).toEqual([true,true,true,false]);
  });
});
