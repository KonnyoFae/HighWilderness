import { describe, expect, it } from "vitest";
import type { HullRegion } from "./model";
import { drawingPoint, sameBoundary, symmetricDrawing, symmetrizeRegion } from "./symmetry";
const armor = { material: {id: "armor", version: 1}, thickness_m: 0.1 };
function region(vertices_m: [number,number][]): HullRegion { return {id:"r",vertices_m,edge_armor:vertices_m.map(()=>structuredClone(armor))}; }
const square = region([[-12.5,-12.5],[12.5,-12.5],[12.5,12.5],[-12.5,12.5]]);
describe("single-side hull authoring", () => {
  it("builds one closed boundary with no central seam and independent mirrored armor", () => {
    const r = symmetricDrawing("r", [{x:0,y:-12.5},{x:12.5,y:-12.5},{x:12.5,y:12.5},{x:0,y:12.5}], armor);
    expect(r.vertices_m).toEqual([[0,-12.5],[12.5,-12.5],[12.5,12.5],[0,12.5],[-12.5,12.5],[-12.5,-12.5]]);
    expect(r.edge_armor).toHaveLength(6);
    expect(sameBoundary(square,r)).toBe(true);
    r.edge_armor[0].thickness_m=2; expect(r.edge_armor[5].thickness_m).toBe(0.1);
  });
  it("anchors the first point and rejects changing sides, off-grid and premature closure", () => {
    expect(drawingPoint([], {x:12.5,y:10},true)).toEqual({x:0,y:10});
    expect(drawingPoint([], {x:12.5,y:10},false)).toEqual({x:12.5,y:10});
    expect(()=>drawingPoint([{x:0,y:10},{x:5,y:0}],{x:-5,y:-10},true)).toThrow(/同一侧/);
    expect(()=>drawingPoint([],{x:1,y:10},true)).toThrow(/网格/);
    expect(()=>symmetricDrawing("r",[{x:0,y:10},{x:5,y:0},{x:5,y:-10}],armor)).toThrow(/中线/);
    expect(()=>symmetricDrawing("r",[{x:0,y:10},{x:5,y:0},{x:0,y:10}],armor)).toThrow();
  });
  it("recognizes a complete symmetric hull without adding collinear points or copies", () => {
    for(const side of ["left","right"] as const) expect(sameBoundary(square,symmetrizeRegion(square,side))).toBe(true);
  });
  it("replaces the opposite side of an asymmetric hull and reverses armor edge order", () => {
    const r=region([[-10,-12.5],[12.5,-12.5],[12.5,12.5],[-10,12.5]]);
    r.edge_armor.forEach((a,i)=>a.thickness_m=i+1);
    const result=symmetrizeRegion(r,"right");
    expect(result.id).toBe("r");
    expect(result.vertices_m).toEqual([[0,-12.5],[12.5,-12.5],[12.5,12.5],[0,12.5],[-12.5,12.5],[-12.5,-12.5]]);
    expect(result.edge_armor.map(a=>a.thickness_m)).toEqual([1,2,3,3,2,1]);
    expect(sameBoundary(r,result)).toBe(false);
    expect(sameBoundary(result,symmetrizeRegion(result,"right"))).toBe(true);
    expect(r.vertices_m[0][0]).toBe(-10);
  });
  it("handles wrap-around, reverse winding and half hulls with a closing seam", () => {
    const half=region([[0,12.5],[12.5,12.5],[12.5,-12.5],[0,-12.5]]);
    expect(sameBoundary(square,symmetrizeRegion(half,"right"))).toBe(true);
    const rotated=region([[12.5,12.5],[-12.5,12.5],[-12.5,-12.5],[12.5,-12.5]]);
    expect(sameBoundary(square,symmetrizeRegion(rotated,"left"))).toBe(true);
  });
  it("does not guess or round when the axis intersection is off-grid or the side is disconnected", () => {
    expect(()=>symmetrizeRegion(region([[-5,0],[10,2.5],[10,10],[-5,10]]),"right")).toThrow(/交点/);
    expect(()=>symmetrizeRegion(region([[5,0],[10,0],[10,10],[5,10]]),"right")).toThrow(/中线/);
    expect(()=>symmetrizeRegion(region([[-5,-10],[5,-10],[5,-5],[-2.5,-5],[-2.5,5],[5,5],[5,10],[-5,10]]),"right")).toThrow(/连续轮廓/);
  });
});
