import {expect,it} from 'vitest';
import {acceptSnapshot} from './model';
import type {TacticalSnapshot} from './model';
import fixture from './testing/snapshot.fixture.json';
import {ProjectileStreamCache,PROJECTILE_STREAM} from './projectileStream';
import type {ProjectileStreamPacket} from './projectileStream';
import {PresentationTimeline} from './presentation';
import {observedBattleView,clipObservationSample} from './observedView';

const base=acceptSnapshot(null,fixture as TacticalSnapshot,'fixture.1'), own=base.geometry.ships[0].id,enemy=base.geometry.ships[1].id;
const start={id:1,ship_id:own,kind:'shell' as const,maximum_durability:null,born_step:1,origin_m:[0,0],expires_step:300};
function view(step:number,packet:Partial<ProjectileStreamPacket>={},active=false){
  return {...base,snapshot:{...base.snapshot,paused:false,fixed_step:step,time_s:step/60,
    projectile_stream:{interface:PROJECTILE_STREAM,sequence:1,base_sequence:null,reset:true,step,starts:[],paths:[],ends:[],dropped_projectiles:0,...packet},
    gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1' as const,command_sequence:0,policy_id:'test',damage_enabled:true,weapons:[],
      projectiles:active?[{id:1,ship_id:'',position_m:[250,0],previous_m:[200,0],velocity_mps:[5000,0],height_layer:'upper'}]:[]}}};
}
const end={...start,position_m:[0,0],velocity_mps:[5000,0],end_step:2,end_m:[50,0],impact:{ship_id:enemy,outcome:'module'}};

it('reconstructs a complete 5000 m/s short flight without early impact or a ghost',()=>{
  const cache=new ProjectileStreamCache(),timeline=new PresentationTimeline();
  timeline.push(cache.apply(view(0)),0);
  const wire=view(4,{sequence:2,base_sequence:1,reset:false,starts:[start],paths:[[1,[[1,0,0],[2,50,0]]]],ends:[end]});
  const before=JSON.stringify(wire),decoded=cache.apply(wire);timeline.push(decoded,4/60*1000);
  expect(cache.apply(wire).snapshot.presentation!.finished_projectiles).toHaveLength(1);
  const mid=timeline.sample(125)!.snapshot.gunnery!;
  expect(mid.projectiles[0].position_m).toEqual([25,0]);
  expect(timeline.sample(150)!.snapshot.gunnery!.projectiles).toHaveLength(0);
  expect(decoded.snapshot.presentation!.finished_projectiles[0].end_m).toEqual([50,0]);
  expect(JSON.stringify(wire)).toBe(before);expect(cache.cursor()).toBe(2);
});

it('merges incremental paths and accepts an empty repeated read without replay',()=>{
  const cache=new ProjectileStreamCache();
  const first=view(4,{starts:[start],paths:[[1,[[1,0,0],[4,250,0]]]]},true);
  delete (first.snapshot.gunnery.projectiles[0] as Partial<typeof start>).ship_id;
  expect(cache.apply(first).snapshot.gunnery!.projectiles[0].ship_id).toBe(own);
  const next=view(8,{sequence:2,base_sequence:1,reset:false,paths:[[1,[[4,250,0],[8,500,0]]]]},true);
  const a=cache.apply(next);
  expect(a.snapshot.gunnery!.projectiles[0].trajectory).toEqual([[1,0,0],[4,250,0],[8,500,0]]);
  const repeat=cache.apply(view(8,{sequence:2,base_sequence:2,reset:false},true));
  expect(repeat.snapshot.gunnery!.projectiles[0].trajectory).toEqual(a.snapshot.gunnery!.projectiles[0].trajectory);
});

it('requests recovery after a gap or malformed transaction and discards old scene data',()=>{
  const cache=new ProjectileStreamCache();cache.apply(view(0));
  expect(()=>cache.apply(view(8,{sequence:3,base_sequence:2,reset:false}))).toThrow();expect(cache.cursor()).toBeNull();
  cache.apply(view(8,{sequence:3}));
  expect(()=>cache.apply(view(12,{sequence:4,base_sequence:3,reset:false,paths:[[99,[[12,0,0]]]]}))).toThrow();
  const next=view(0);next.snapshot.scene_id='new.scene';
  expect(cache.apply(next).snapshot.presentation!.finished_projectiles).toEqual([]);
  expect(cache.apply({...base}).snapshot).toBe(base.snapshot);expect(cache.cursor()).toBeNull();
});

it('prunes completed flights and rejects old publications without replaying effects',()=>{
  const cache=new ProjectileStreamCache();cache.apply(view(4,{starts:[start],paths:[[1,[[1,0,0],[2,50,0]]]],ends:[end]}));
  expect(cache.apply(view(123,{sequence:2,base_sequence:1,reset:false})).snapshot.presentation!.finished_projectiles).toEqual([]);
  expect(()=>cache.apply(view(4))).toThrow();
  expect(cache.cursor()).toBe(2);
});

it('bounds a sustained stream of completed flights and releases it on disposal',()=>{
  const cache=new ProjectileStreamCache();
  for(let n=0;n<1000;n++){
    const step=n*8+4,id=n+1,identity={...start,id,born_step:step-3};
    cache.apply(view(step,{sequence:n+1,base_sequence:n? n:null,reset:n===0,
      starts:[identity],paths:[[id,[[step-3,0,0],[step-2,50,0]]]],ends:[{...end,...identity,end_step:step-2}]}));
    expect((cache as unknown as {flights:Map<number,unknown>}).flights.size).toBeLessThanOrEqual(16);
  }
  cache.clear();expect(cache.cursor()).toBeNull();
  expect((cache as unknown as {flights:Map<number,unknown>}).flights.size).toBe(0);
});

it('keeps decoded enemy histories behind the existing observation boundary',()=>{
  const cache=new ProjectileStreamCache(),wire=view(4,{starts:[{...start,ship_id:enemy,maximum_durability:3}],paths:[[1,[[1,0,0],[4,250,0]]]]},true);
  wire.snapshot.projectile_stream.paths=[];
  (wire.snapshot.projectile_stream as ProjectileStreamPacket).shells=[[1,[[1,0,0,5000,0,1],[4,250,0,5000,0,0]]]];
  delete (wire.snapshot.gunnery.projectiles[0] as Partial<typeof start>).ship_id;
  const decoded=cache.apply(wire);expect(observedBattleView(decoded,own,own).snapshot.gunnery!.projectiles).toEqual([]);
  decoded.snapshot.gunnery!.observation={command_sequence:0,sample_interval_s:.2,memory_s:5,ships:[{ship_id:own,locked_target_id:null,lock_status:null,devices:[],contacts:[{id:1,kind:'shell',position_m:[9,8],velocity_mps:[1,0],height_layer:'upper',valid:true,age_s:0,sources:['sensor'],status:'tracked',radar_source_available:true}]}]};
  const shown=observedBattleView(decoded,own,own);expect(shown.snapshot.gunnery!.projectiles[0].position_m).toEqual([9,8]);
  expect(shown.snapshot.gunnery!.projectiles[0].trajectory).toBeUndefined();
  expect(shown.snapshot.gunnery!.projectiles[0].shell_samples).toBeUndefined();
  decoded.snapshot.gunnery!.observation.ships[0].contacts[0].valid=false;
  expect(clipObservationSample(shown,observedBattleView(decoded,own,own)).snapshot.gunnery!.projectiles).toEqual([]);
});
