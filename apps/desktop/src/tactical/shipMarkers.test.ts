import { expect, it } from 'vitest';
import { placeShipMarkers } from './shipMarkers';

it('separates dense fleet markers without moving their anchors or the camera',()=>{
  const input=Array.from({length:18},(_,i)=>({id:String(i),anchor:{x:400,y:300},above:280,selected:i===0}));
  const placed=placeShipMarkers(input,800,540);
  expect(placed.every(p=>p.anchor.x===400&&p.anchor.y===300)).toBe(true);
  for(let i=0;i<placed.length;i++)for(let j=0;j<i;j++)expect(Math.hypot(placed[i].point.x-placed[j].point.x,placed[i].point.y-placed[j].point.y)).toBeGreaterThanOrEqual(40);
  expect(placeShipMarkers([...input].reverse(),800,540)).toEqual(placed);
});
it('keeps 18 coincident offscreen bearings distinct and inside the viewport',()=>{
  const result=placeShipMarkers(Array.from({length:18},(_,i)=>({id:String(i),anchor:{x:20000,y:270},above:250,selected:false})),800,540);
  for(let i=0;i<result.length;i++){
    expect(result[i].offscreen).toBe(true);expect(result[i].bearing).toBe(0);
    expect(result[i].point.x).toBeGreaterThanOrEqual(24);expect(result[i].point.x).toBeLessThanOrEqual(776);
    for(let j=0;j<i;j++)expect(Math.hypot(result[i].point.x-result[j].point.x,result[i].point.y-result[j].point.y)).toBeGreaterThanOrEqual(40);
  }
});
