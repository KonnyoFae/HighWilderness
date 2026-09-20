import type { Point } from '../editor/viewport';

export interface MarkerInput {id:string;anchor:Point;above:number;selected:boolean}
export interface MarkerPosition {id:string;point:Point;anchor:Point;offscreen:boolean;bearing:number;shifted:boolean}
// Stable ordering, bounded candidates, and visible leaders preserve identity
// when several ships occupy the same place or share an edge direction.
export function placeShipMarkers(inputs:MarkerInput[], width:number, height:number):MarkerPosition[] {
  const pad=44, gap=40, result:MarkerPosition[]=[];
  const clamp=(n:number,max:number)=>Math.max(pad,Math.min(Math.max(pad,max-pad),n));
  for(const item of [...inputs].sort((a,b)=>Number(b.selected)-Number(a.selected)||a.id.localeCompare(b.id))){
    const {anchor}=item, dx=anchor.x-width/2,dy=anchor.y-height/2;
    const offscreen=anchor.x<pad||anchor.x>width-pad||anchor.y<pad||anchor.y>height-pad;
    const k=Math.min((width/2-pad)/Math.max(.001,Math.abs(dx)),(height/2-pad)/Math.max(.001,Math.abs(dy)));
    const preferred=offscreen?{x:width/2+dx*k,y:height/2+dy*k}:{x:anchor.x,y:item.above};
    preferred.x=clamp(preferred.x,width);preferred.y=clamp(preferred.y,height);
    const candidates=[preferred];
    for(let ring=1;ring<=inputs.length+1;ring++){
      if(offscreen){
        const vertical=preferred.x===pad||preferred.x===width-pad;
        for(const sign of [-1,1])candidates.push(vertical?{x:preferred.x,y:clamp(preferred.y+sign*ring*gap,height)}:{x:clamp(preferred.x+sign*ring*gap,width),y:preferred.y});
      }else for(const [x,y] of [[0,-1],[-1,0],[1,0],[0,1],[-1,-1],[1,-1],[-1,1],[1,1]])candidates.push({x:clamp(preferred.x+x*ring*gap,width),y:clamp(preferred.y+y*ring*gap,height)});
    }
    if(offscreen){
      const edges:Point[]=[];
      for(let x=pad;x<=width-pad;x+=gap)edges.push({x,y:pad},{x,y:height-pad});
      for(let y=pad;y<=height-pad;y+=gap)edges.push({x:pad,y},{x:width-pad,y});
      candidates.push(...edges.sort((a,b)=>Math.hypot(a.x-preferred.x,a.y-preferred.y)-Math.hypot(b.x-preferred.x,b.y-preferred.y)));
    }
    const distance=(p:Point)=>Math.min(Infinity,...result.map(r=>Math.hypot(p.x-r.point.x,p.y-r.point.y)));
    const point=candidates.find(p=>distance(p)>=gap)??candidates.reduce((a,b)=>distance(a)>=distance(b)?a:b);
    result.push({id:item.id,point,anchor,offscreen,bearing:Math.atan2(dy,dx),shifted:Math.hypot(point.x-preferred.x,point.y-preferred.y)>1});
  }
  return result;
}
