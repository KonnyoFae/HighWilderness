import type {DisplayProjectile,FinishedProjectile} from './model';
import {shellPosition,shellTrail} from './shellMotion';
import type {ShellSample} from './shellMotion';

// XY constrained curve, then altitude/vertical speed and yaw/pitch in radians.
export type MissileSample=[number,number,number,number,number,0|1,number,number,number,number];
export type MissileIdentity={model_id:string;warhead_id:string;interceptor:boolean;born_step:number};
export type MissileState={step:number;height_layer:string;phase:string;seeker_state:string;target_id:string|number|null;
  maneuver_state:string;maneuver_reason:string|null;maneuver_target_layer:string|null;vertical_goal:boolean};
const windows=new WeakMap<MissileSample[],{xy:ShellSample[];z:ShellSample[]}>();
const mix=(a:number,b:number,t:number)=>a+(b-a)*t;
const angle=(a:number,b:number,t:number)=>a+Math.atan2(Math.sin(b-a),Math.cos(b-a))*t;
function window(samples:MissileSample[]){
  let cached=windows.get(samples);
  if(!cached){
    cached={xy:samples.map(p=>p.slice(0,6) as ShellSample),z:samples.map(p=>[p[0],p[6],0,p[7],0,p[5]])};
    windows.set(samples,cached);
  }
  return cached;
}

export function missilePose(samples:MissileSample[],states:MissileState[],requested:number){
  if(!samples.length || requested<samples[0][0])return null;
  const step=Math.min(requested,samples[samples.length-1][0]);
  const index=Math.max(0,samples.findIndex(p=>p[0]>=step)),b=samples[index],a=samples[Math.max(0,index-1)];
  const t=b[0]===a[0]?1:(step-a[0])/(b[0]-a[0]);
  let state:MissileState|undefined;
  for(const candidate of states){if(candidate.step>step)break;state=candidate;}
  if(!state)return null;
  const cached=window(samples),trail=shellTrail(cached.xy,step,samples[0][0]);
  const altitude=shellPosition(cached.z,step)[0],vz=mix(a[7],b[7],t);
  return {position_m:trail[trail.length-1],previous_m:trail[0],trail_m:trail,
    velocity_mps:[mix(a[3],b[3],t),mix(a[4],b[4],t)],
    heading_rad:angle(a[8],b[8],t),pitch_rad:angle(a[9],b[9],t),altitude_m:altitude,vertical_speed_mps:vz,
    height_layer:state.height_layer,state};
}

export function displayMissile(p:DisplayProjectile|FinishedProjectile,step:number):DisplayProjectile|null{
  if(!p.missile_samples || !p.missile_states || !p.missile_identity)return null;
  const pose=missilePose(p.missile_samples,p.missile_states,step);if(!pose)return null;
  const {state,...motion}=pose,identity=p.missile_identity;
  const target=state.maneuver_target_layer;
  const targetAltitude=target==='upper'?10000:target==='cloud'?5000:0;
  return {...p,...motion,missile:{model_id:identity.model_id,warhead_id:identity.warhead_id,interceptor:identity.interceptor,
    phase:state.phase,seeker_state:state.seeker_state,target_id:state.target_id,
    age_s:Math.max(0,(step-identity.born_step)/60),remaining_s:Math.max(0,((p.expires_step??step)-step)/60),
    altitude_m:pose.altitude_m,vertical_speed_mps:pose.vertical_speed_mps,pitch_deg:pose.pitch_rad*180/Math.PI,
    horizontal_speed_mps:Math.hypot(...pose.velocity_mps),speed_mps:Math.hypot(...pose.velocity_mps,pose.vertical_speed_mps),
    maneuver_state:state.maneuver_state,maneuver_reason:state.maneuver_reason,maneuver_target_layer:target,
    vertical_remaining_m:state.vertical_goal && target?Math.abs(targetAltitude-pose.altitude_m):null}};
}
