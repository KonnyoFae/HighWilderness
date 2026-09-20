import { expect, it } from 'vitest';
import { acceptSnapshot } from './model';
import type { TacticalSnapshot } from './model';
import fixture from './testing/snapshot.fixture.json';
import { observedBattleView, clipObservationSample } from './observedView';
import { pickShip } from './viewport';
const base=acceptSnapshot(null,fixture as TacticalSnapshot,'fixture.1');
function scene(){
  const v=structuredClone(base), own=v.geometry.ships[0].id, enemy=v.geometry.ships[1].id;
  v.snapshot.gunnery={interface:'gaotian.gunnery-view/p2a-v1alpha1',command_sequence:0,policy_id:'test',damage_enabled:true,weapons:[],projectiles:[],
    observation:{command_sequence:0,sample_interval_s:.2,memory_s:5,ships:[{ship_id:own,locked_target_id:null,lock_status:null,devices:[],contacts:[
      {id:enemy,kind:'ship',position_m:[10,20],velocity_mps:[3,4],heading_rad:.5,yaw_rate_radps:.1,height_layer:'cloud',valid:true,age_s:.2,sources:['sensor'],status:'tracked',radar_source_available:true}]}]}};
  return {v,own,enemy};
}
it('uses sampled enemy pose, never actual enemy state, and fails closed without observation',()=>{
  const {v,own,enemy}=scene();const p=v.snapshot.ships.find(s=>s.id===enemy)!;
  p.position_m=[999999,999999];p.heading_rad=3;p.height_layer='rain';
  const visible=observedBattleView(v,own,own), enemyPose=visible.snapshot.ships.find(s=>s.id===enemy)!;
  expect(enemyPose.position_m).toEqual([10,20]);expect(enemyPose.heading_rad).toBe(.5);expect(enemyPose.height_layer).toBe('cloud');
  expect(enemyPose.modules).toEqual([]);expect(enemyPose.wreck).toBeUndefined();
  expect(observedBattleView(v,'no_observer',own).snapshot.ships.some(s=>s.id===enemy)).toBe(false);
  expect(v.snapshot.ships.find(s=>s.id===enemy)!.position_m).toEqual([999999,999999]);
});
it('loss immediately removes enemy picking and stale interpolation while retaining the last contact',()=>{
  const {v,own,enemy}=scene(), previous=observedBattleView(v,own,own);
  v.snapshot.gunnery!.observation!.ships[0].contacts[0].valid=false;
  const current=observedBattleView(v,own,own), sampled=clipObservationSample(previous,current);
  expect(sampled.snapshot.ships.some(s=>s.id===enemy)).toBe(false);
  expect(pickShip(sampled,{x:10,y:-20},{x:0,y:0,scale:1})).not.toBe(enemy);
  expect(sampled.snapshot.gunnery!.observation!.ships[0].contacts[0].position_m).toEqual([10,20]);
});
it('does not reveal an enemy projectile trajectory or propulsion state',()=>{
  const {v,own,enemy}=scene();v.snapshot.gunnery!.projectiles=[{id:9,kind:'missile',ship_id:enemy,position_m:[900,900],previous_m:[800,800],velocity_mps:[100,0],trajectory:[[0,800,800],[1,900,900]]}];
  expect(observedBattleView(v,own,own).snapshot.gunnery!.projectiles).toEqual([]);
  v.snapshot.gunnery!.observation!.ships[0].contacts.push({...v.snapshot.gunnery!.observation!.ships[0].contacts[0],kind:'missile',id:9});
  const p=observedBattleView(v,own,own).snapshot.gunnery!.projectiles[0];
  expect(p.position_m).toEqual([10,20]);expect(p.trajectory).toBeUndefined();expect(p.missile).toBeUndefined();
});

it('preserves untracked small-shell trails and clouds as physical effects without granting targets',()=>{
  const {v,own,enemy}=scene(), g=v.snapshot.gunnery!;
  g.observation!.ships[0].contacts=[];
  const small={id:1,kind:'shell' as const,ship_id:enemy,position_m:[100,20],previous_m:[90,20],velocity_mps:[1000,0],maximum_durability:null};
  g.projectiles=[small,{...small,id:2,maximum_durability:3},{...small,id:3,kind:'missile'}];
  const terminal={...small,born_step:0,origin_m:[0,20],expires_step:120,end_step:10,end_m:[100,20],impact:{ship_id:enemy,outcome:'hit'}};
  v.snapshot.presentation={interface:'gaotian.tactical-presentation/v1alpha1',dropped_projectiles:0,
    finished_projectiles:[terminal,{...terminal,id:2,maximum_durability:3}]};
  g.electronic_warfare={command_sequence:0,devices:[],effects:['chaff','thermal','decoy'].map(kind=>({id:kind,ship_id:enemy,kind,height_layer:'upper',position_m:[100,20],velocity_mps:[0,0],radius_m:50,remaining_s:60}))};
  const shown=observedBattleView(v,own,own).snapshot;
  expect(shown.gunnery!.projectiles.map(p=>p.id)).toEqual([1]);
  expect(shown.presentation!.finished_projectiles.map(p=>p.id)).toEqual([1]);
  expect(shown.presentation!.finished_projectiles[0].impact).toBeNull();
  expect(shown.gunnery!.electronic_warfare!.effects.map(e=>e.kind)).toEqual(['chaff','thermal']);
  expect(shown.gunnery!.observation!.ships[0].contacts).toEqual([]);
  expect(shown.ships.some(s=>s.id===enemy)).toBe(false);
  // An accidental duplicate track still produces only one physical shell.
  g.observation!.ships[0].contacts.push({id:1,kind:'shell',position_m:[100,20],velocity_mps:[1000,0],height_layer:'upper',valid:true,age_s:0,sources:['sensor'],status:'tracked',radar_source_available:true});
  expect(observedBattleView(v,own,own).snapshot.gunnery!.projectiles).toHaveLength(1);
});
