import {expect,it} from 'vitest';
import {PresentationTimeline} from './presentation';
import {acceptSnapshot} from './model';
import type {TacticalSnapshot,DisplayProjectile} from './model';
import fixture from './testing/snapshot.fixture.json';

const base=acceptSnapshot(null,fixture as TacticalSnapshot,'fixture.1');
const own=base.geometry.ships[0].id;
function frame(step:number,visible=true,source='observer.a'){
  const projectiles:DisplayProjectile[]=[{id:1,ship_id:own,kind:'shell',born_step:0,expires_step:1000,
    origin_m:[0,0],position_m:[step*10,0],previous_m:[step*10-10,0],velocity_mps:[600,0],
    shell_samples:[[0,0,0,600,0,1],[step,step*10,0,600,0,0]]}];
  if(visible)projectiles.push({id:2,ship_id:'observed.enemy',kind:'missile',position_m:[step*20,10],previous_m:[step*20,10],velocity_mps:[1200,0]});
  return {...base,snapshot:{...base.snapshot,fixed_step:step,time_s:step/60,paused:false,observation_source:source,
    gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1' as const,command_sequence:0,policy_id:'test',damage_enabled:true,weapons:[],projectiles}}};
}

it('breaks lost/reacquired enemy segments, even when the gap is shorter than playback delay',()=>{
  const clock=new PresentationTimeline();clock.push(frame(0),0);clock.push(frame(4),4000/60);
  clock.push(frame(5,false),5000/60);clock.push(frame(8),8000/60);
  const before=clock.sample(100+6/60*1000)!.snapshot.gunnery!.projectiles;
  expect(before.map(p=>p.id)).toEqual([1]);expect(before[0].position_m[0]).toBeCloseTo(60);
  expect(clock.sample(100+8/60*1000)!.snapshot.gunnery!.projectiles.find(p=>p.id===2)!.position_m).toEqual([160,10]);
});

it('changes observation source without replacing friendly smoothing or reusing old enemy samples',()=>{
  const clock=new PresentationTimeline();clock.push(frame(0),0);clock.push(frame(4),4000/60);clock.push(frame(8,true,'observer.b'),8000/60);
  const mid=clock.sample(200)!.snapshot.gunnery!.projectiles;
  expect(mid.map(p=>p.id)).toEqual([1]);expect(mid[0].position_m[0]).toBeCloseTo(60);
});

it('resynchronizes an over-budget recovery without replaying past effects or flights',()=>{
  const clock=new PresentationTimeline();clock.push(frame(0),0);
  clock.push({...frame(8),snapshot:{...frame(8).snapshot,display_reset:true}},8000/60);
  const shown=clock.sample(8000/60)!.snapshot;
  expect(shown.fixed_step).toBe(8);expect(shown.display_effect_after_step).toBe(8);
  // Hiding/reopening clears the playback backlog and establishes a new floor.
  clock.clear();clock.push(frame(12),5000);
  expect(clock.sample(5000)!.snapshot.display_effect_after_step).toBe(12);
  expect(clock.sample(99999)!.snapshot.fixed_step).toBe(12);
});

it('aligns interception effects with a short flight termination and deduplicates repeated events',()=>{
  const clock=new PresentationTimeline(),a=frame(0,false),b=frame(4,false);
  const event={step:2,impact_fraction:.5,projectile_id:9,round_id:8,source_ship_id:own,weapon_id:'gun',position_m:[20,0],height_layer:'upper',durability_before:1,durability_after:0,intercepted:true};
  const defense={hits:1,intercepted:1,threats:[],recent:[event,event]};
  a.snapshot.gunnery={...a.snapshot.gunnery,point_defense:{...defense,recent:[]}} as typeof a.snapshot.gunnery;
  b.snapshot.gunnery={...b.snapshot.gunnery,point_defense:defense} as typeof b.snapshot.gunnery;
  clock.push(a,0);clock.push(b,4000/60);
  expect(clock.sample(100+1.5/60*1000)!.snapshot.gunnery!.point_defense!.recent).toEqual([]);
  expect(clock.sample(100+2/60*1000)!.snapshot.gunnery!.point_defense!.recent).toEqual([event]);
});

it('stays bounded over a long sequence, then clears for a new backend/scene with reused IDs',()=>{
  const clock=new PresentationTimeline();
  for(let n=0;n<2000;n++)clock.push(frame(n*4,false),n*4000/60);
  expect((clock as unknown as {frames:unknown[]}).frames.length).toBeLessThanOrEqual(17);
  const next=frame(0,false);next.snapshot.backend_instance_id='backend.new';next.snapshot.scene_id='scene.new';
  clock.push(next,200000);expect(clock.sample(200001)!.snapshot.fixed_step).toBe(0);
  expect(clock.sample(200001)!.snapshot.gunnery!.projectiles[0].position_m).toEqual([0,0]);
});
