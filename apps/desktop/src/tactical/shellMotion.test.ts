import {expect,it} from 'vitest';
import {shellPosition,shellTrail} from './shellMotion';
import type {ShellSample} from './shellMotion';
import {PresentationTimeline} from './presentation';
import {ProjectileStreamCache,PROJECTILE_STREAM} from './projectileStream';
import {acceptSnapshot} from './model';
import type {TacticalSnapshot} from './model';
import fixture from './testing/snapshot.fixture.json';

const base=acceptSnapshot(null,fixture as TacticalSnapshot,'fixture.1'),own=base.geometry.ships[0].id;
const start={id:1,ship_id:own,kind:'shell' as const,maximum_durability:null,born_step:0,origin_m:[0,0],expires_step:20};
const samples:ShellSample[]=[[0,0,0,600,0,1],[4,32,0,360,0,0],[8,48,0,120,0,0]];
function frame(step:number,sequence:number,points:ShellSample[],end=false){
  return {...base,snapshot:{...base.snapshot,paused:false,fixed_step:step,time_s:step/60,
    projectile_stream:{interface:PROJECTILE_STREAM,sequence,base_sequence:sequence===1?null:sequence-1,reset:sequence===1,step,
      starts:sequence===1?[start]:[],paths:[],shells:[[1,points]] as [number,ShellSample[]][],dropped_projectiles:0,
      ends:end?[{...start,position_m:[32,0],velocity_mps:[360,0],end_step:step,end_m:[40,0],impact:null}]:[]},
    gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1' as const,policy_id:'test',command_sequence:0,weapons:[],damage_enabled:true,
      projectiles:end?[]:[{id:1,ship_id:own,position_m:points.at(-1)!.slice(1,3),previous_m:[0,0],velocity_mps:points.at(-1)!.slice(3,5)}]}}};
}

it('uses velocity to show deceleration and clamps positions to the committed interval',()=>{
  expect(shellPosition(samples,2)[0]).toBeCloseTo(18);
  expect(shellPosition(samples,6)[0]).toBeCloseTo(42);
  expect(shellPosition(samples,-10)).toEqual([0,0]);expect(shellPosition(samples,999)).toEqual([48,0]);
  const abrupt:ShellSample[]=[[0,0,0,5000,-300,1],[4,1,0,-5000,300,0]];
  for(let step=0;step<4;step+=.01){
    const p=shellPosition(abrupt,step);expect(p[0]).toBeGreaterThanOrEqual(0);expect(p[0]).toBeLessThanOrEqual(1);expect(p[1]).toBe(0);
  }
  const terminal:ShellSample[]=[samples[1],[5,33,0,360,0,1]];
  expect(shellPosition(terminal,4.5)).toEqual([32.5,0]);
});

it('merges only new state anchors, retaining the preceding incoming curve mode',()=>{
  const cache=new ProjectileStreamCache();cache.apply(frame(4,1,samples.slice(0,2)));
  const decoded=cache.apply(frame(8,2,samples.slice(2)));
  expect(decoded.snapshot.gunnery!.projectiles[0].shell_samples).toEqual(samples);
  expect(decoded.snapshot.gunnery!.projectiles[0].trajectory).toBeUndefined();
  const bad=frame(12,3,[[12,60,0,20,0,0]]);bad.snapshot.projectile_stream.shells[0][1][0][1]=NaN;
  expect(()=>cache.apply(bad)).toThrow();expect(cache.cursor()).toBeNull();
});

it('draws short flights only in their true interval and stops at the committed hit',()=>{
  const cache=new ProjectileStreamCache(),clock=new PresentationTimeline();
  clock.push(cache.apply(frame(0,1,[samples[0]])),0);
  clock.push(cache.apply(frame(4,2,[[4,40,0,600,0,1]],true)),4000/60);
  const mid=clock.sample(100+2/60*1000)!.snapshot.gunnery!.projectiles[0];
  expect(mid.position_m[0]).toBeCloseTo(20);expect(mid.position_m[1]).toBe(0);expect(mid.trail_m?.[0]).toEqual([0,0]);
  expect(clock.sample(100+4/60*1000)!.snapshot.gunnery!.projectiles).toEqual([]);
});

it('creates the same world-space trail at 30/60/120 FPS and freezes without extrapolation',()=>{
  const results=[];
  for(const fps of [30,60,120]){
    const cache=new ProjectileStreamCache(),clock=new PresentationTimeline();
    clock.push(cache.apply(frame(0,1,[samples[0]])),0);clock.push(cache.apply(frame(8,2,samples.slice(1))),8000/60);
    for(let now=0;now<200;now+=1000/fps)clock.sample(now);
    const p=clock.sample(200)!.snapshot.gunnery!.projectiles[0];results.push(p);
    expect(p.trail_m).toEqual(shellTrail(samples,6,0));
    expect(clock.sample(10000)!.snapshot.gunnery!.projectiles[0].position_m).toEqual([48,0]);
  }
  expect(results[0]).toEqual(results[1]);expect(results[1]).toEqual(results[2]);
});

it('keeps paused trails fixed and does not accumulate frame history',()=>{
  const cache=new ProjectileStreamCache(),clock=new PresentationTimeline(),view=cache.apply(frame(8,1,samples));
  view.snapshot.paused=true;clock.push(view,0);
  expect(clock.sample(0)).toEqual(clock.sample(100000));
  expect(clock.sample(0)!.snapshot.gunnery!.projectiles[0].trail_m).toHaveLength(5);
  expect(view.snapshot.gunnery!.projectiles[0].trail_m).toBeUndefined();
  clock.clear();expect(clock.sample(100001)).toBeNull();
});
