import {describe,it,expect} from 'vitest';
import {armorFitRegions,pickArmor,sourceEdgeIndex} from './armorView';
import type {ArmorRegionView} from './armorView';
import type {HullRegion} from './model';

const region: HullRegion={id:'r',vertices_m:[[-10,-10],[10,-10],[10,10],[-10,10]],edge_armor:[]};
const shape: ArmorRegionView={deck_id:'d',deck_level:0,region_id:'r',structure_outline_m:region.vertices_m,
  outer_outline_m:[[-15,-15],[15,-15],[15,15],[-15,15]],
  edges:[{edge_index:1,flare_angle_deg:45,area_m2:100,upper_edge_m:[[10,-10,5],[10,10,5]],projection_m:[[10,-10],[15,-15],[15,15],[10,10]]}]};
describe('armor authoring selection',()=>{
  it('selects the whole edge or flare rather than a vertex, at any zoom',()=>{
    for(const scale of [1,4,16]){
      const c={x:100,y:100,scale};
      expect(pickArmor([region],[shape],{x:100+10*scale,y:100},c)).toEqual({region:'r',vertex:null,edge:1});
      expect(pickArmor([region],[shape],{x:100+14*scale,y:100},c)).toEqual({region:'r',vertex:null,edge:1});
      expect(pickArmor([region],[shape],{x:100+40*scale,y:100},c)).toBeNull();
    }
  });
  it('maps canonical edges to reversed draft order and fits the full silhouette',()=>{
    const reversed={...region,vertices_m:[...region.vertices_m].reverse()};
    expect(sourceEdgeIndex(reversed,shape.edges[0])).toBe(1);
    const shifted={...region,vertices_m:[...region.vertices_m.slice(2),...region.vertices_m.slice(0,2)]};
    expect(sourceEdgeIndex(shifted,shape.edges[0])).toBe(3);
    expect(armorFitRegions([region],{regions:[shape],has_flare:true},'d')[0].vertices_m).toEqual(shape.outer_outline_m);
    expect(armorFitRegions([region],{regions:[shape],has_flare:true},'other')[0].vertices_m).toEqual(region.vertices_m);
  });
});
