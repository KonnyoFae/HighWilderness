import {expect,it} from 'vitest';
import {missilePose,displayMissile} from './missileMotion';
import type {MissileSample,MissileState} from './missileMotion';
import {ProjectileStreamCache,PROJECTILE_STREAM} from './projectileStream';
import {PresentationTimeline} from './presentation';
import {acceptSnapshot} from './model';
import type {TacticalSnapshot} from './model';
import fixture from './testing/snapshot.fixture.json';
import {observedBattleView,clipObservationSample} from './observedView';

const base=acceptSnapshot(null,fixture as TacticalSnapshot,'fixture.1'),own=base.geometry.ships[0].id,enemy=base.geometry.ships[1].id;
const identity={model_id:'test',warhead_id:'blast',born_step:0,interceptor:true};
const start={id:1,ship_id:own,kind:'missile' as const,maximum_durability:3,born_step:0,origin_m:[0,0],expires_step:600,missile_identity:identity};
const samples:MissileSample[]=[[0,0,0,600,0,1,10000,0,Math.PI-.1,0],
  [3,30,0,600,0,0,9999,-20,Math.PI+.02,-.02],[4,40,0,600,0,1,9998,-40,Math.PI+.1,-.04],
  [8,80,0,600,0,0,9994,-80,Math.PI+.2,-.1]];
const states:MissileState[]=[{step:0,height_layer:'upper',phase:'powered',seeker_state:'tracking',target_id:'target.a',maneuver_state:'diving',
  maneuver_reason:null,maneuver_target_layer:'cloud',vertical_goal:true},
  {step:4,height_layer:'cloud',phase:'coast',seeker_state:'memory',target_id:'target.b',maneuver_state:'returning',maneuver_reason:'unpowered_climb_failed',maneuver_target_layer:'cloud',vertical_goal:true}];
function frame(step:number,sequence:number,points:MissileSample[],changes:MissileState[],end=false){
  return {...base,snapshot:{...base.snapshot,paused:false,fixed_step:step,time_s:step/60,
    projectile_stream:{interface:PROJECTILE_STREAM,sequence,base_sequence:sequence===1?null:sequence-1,reset:sequence===1,step,
      starts:sequence===1?[start]:[],paths:[],shells:[],missiles:[[1,points,changes]] as [number,MissileSample[],MissileState[]][],dropped_projectiles:0,
      ends:end?[{...start,position_m:[40,0],velocity_mps:[600,0],end_step:step,end_m:[80,0],impact:null}]:[]},
    gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1' as const,policy_id:'test',command_sequence:0,weapons:[],damage_enabled:true,
      projectiles:end?[]:[{id:1,ship_id:own,position_m:points.at(-1)!.slice(1,3),previous_m:[0,0],velocity_mps:[600,0],height_layer:'cloud'}]}}};
}

it('samples 3D pose without early layer, phase or retarget changes',()=>{
  const before=missilePose(samples,states,3.5)!;
  expect(before.height_layer).toBe('upper');expect(before.state.phase).toBe('powered');expect(before.state.target_id).toBe('target.a');
  expect(before.altitude_m).toBeCloseTo(9998.5);expect(before.position_m[0]).toBeCloseTo(35);
  const after=missilePose(samples,states,4)!;expect(after.height_layer).toBe('cloud');expect(after.state.phase).toBe('coast');
  expect(missilePose(samples,states,-1)).toBeNull();expect(missilePose(samples,states,100)!.position_m).toEqual([80,0]);
  expect(missilePose(samples,states,1.5)!.heading_rad).toBeCloseTo(Math.PI-.04);
});

it('keeps zero-speed orientation stable and cannot overshoot or turn towards hidden truth',()=>{
  const nearZero:MissileSample[]=[[0,0,0,0,0,1,5100,0,1.2,.2],[4,0,0,0,0,0,5100,0,1.2,.2]];
  const p={...start,position_m:[9999,9999],previous_m:[0,0],velocity_mps:[0,0],missile_samples:nearZero,missile_states:states};
  for(const step of [0,.5,2,4]){
    const shown=displayMissile(p,step)!;expect(shown.position_m).toEqual([0,0]);expect(shown.heading_rad).toBeCloseTo(1.2);
    expect(shown.missile!.remaining_s).toBeCloseTo((600-step)/60);
  }
});

it('decodes incremental missile states and uses shared presentation time for finished flights',()=>{
  const cache=new ProjectileStreamCache(),clock=new PresentationTimeline();
  clock.push(cache.apply(frame(0,1,[samples[0]],[states[0]])),0);
  clock.push(cache.apply(frame(8,2,samples.slice(1),[states[1]],true)),8000/60);
  const before=clock.sample(100+3.5/60*1000)!.snapshot.gunnery!.projectiles[0];
  expect(before.height_layer).toBe('upper');expect(before.missile!.phase).toBe('powered');expect(before.missile!.target_id).toBe('target.a');
  expect(clock.sample(100+5/60*1000)!.snapshot.gunnery!.projectiles[0].height_layer).toBe('cloud');
  expect(clock.sample(100+8/60*1000)!.snapshot.gunnery!.projectiles).toEqual([]);
  expect(cache.cursor()).toBe(2);
});

it('keeps trails independent of frame cadence and freezes a paused missile exactly',()=>{
  const rows=[];
  for(const fps of [30,60,120]){
    for(let now=0;now<.1;now+=1/fps)missilePose(samples,states,now*60);
    rows.push(missilePose(samples,states,6));
  }
  expect(rows[0]).toEqual(rows[1]);expect(rows[1]).toEqual(rows[2]);
  const cache=new ProjectileStreamCache(),clock=new PresentationTimeline(),v=cache.apply(frame(8,1,samples,states));
  v.snapshot.paused=true;clock.push(v,0);expect(clock.sample(0)).toEqual(clock.sample(99999));
});

it('discards malformed missile batches and never exposes enemy trajectory or guidance',()=>{
  const cache=new ProjectileStreamCache(),wire=frame(8,1,samples,states);wire.snapshot.projectile_stream.starts[0]={...start,ship_id:enemy};
  wire.snapshot.gunnery.projectiles[0].ship_id=enemy;
  const decoded=cache.apply(wire);expect(observedBattleView(decoded,own,own).snapshot.gunnery!.projectiles).toEqual([]);
  decoded.snapshot.gunnery!.observation={command_sequence:0,sample_interval_s:.2,memory_s:5,ships:[{ship_id:own,locked_target_id:null,lock_status:null,devices:[],contacts:[{
    id:1,kind:'missile',position_m:[7,8],velocity_mps:[10,20],height_layer:'upper',altitude_m:8000,vertical_speed_mps:-10,
    valid:true,age_s:0,sources:['radar'],status:'tracked',radar_source_available:true}]}]};
  const filtered=observedBattleView(decoded,own,own),p=filtered.snapshot.gunnery!.projectiles[0];
  expect(p.position_m).toEqual([7,8]);expect(p.altitude_m).toBe(8000);expect(p.missile).toBeUndefined();
  expect(p.missile_samples).toBeUndefined();expect(p.missile_states).toBeUndefined();
  decoded.snapshot.gunnery!.observation.ships[0].contacts[0].valid=false;
  expect(clipObservationSample(filtered,observedBattleView(decoded,own,own)).snapshot.gunnery!.projectiles).toEqual([]);
  const invalid=frame(12,2,[[12,NaN,0,1,0,1,0,0,0,0]],[]);
  expect(()=>cache.apply(invalid)).toThrow();expect(cache.cursor()).toBeNull();
});
