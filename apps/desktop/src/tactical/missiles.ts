export interface MissileUnit {serial:number;model_id:string;warhead_id:string;source:'raw'|'magazine'}
export interface MissileJob {kind:string;unit:MissileUnit;remaining_work_steps:number;total_work_steps:number}
export interface MissileLauncher {module_id:string;model_id:string;warhead_id:string;auto_fire:boolean;ready:MissileUnit[];job:MissileJob|null;load_remaining:number;unload_remaining:number}
export interface MissileMagazine {module_id:string;model_id:string;warhead_id:string;stock:MissileUnit[];jobs:MissileJob[];assembly_remaining:number}
export interface MissileState {launchers:MissileLauncher[];magazines:MissileMagazine[]}
export interface MissileModel {id:string;name:string;diameter_mm:number;warhead_ids:string[];assembly_steps:number;raw_reload_steps:number;ready_reload_steps:number;cargo_costs:{good_id:string;quantity:number}[];warhead_material_quantity:number}
export interface MissileSpec {module_id:string;compatible_model_ids:string[];capacity_by_model:Record<string,number>;launcher_kind?:string;raw_reload_multiplier?:number;ready_reload_multiplier?:number;warhead_switch_in_battle?:boolean;assembly_parallel?:number;integrated_fire_control?:boolean}
export interface MissilePerformance {interface:'gaotian.missile-performance/5j-v1';seeker:string;boost_s:number;powered_s:number;coast_s:number;lifetime_s:number;speed_cap_mps:number;max_g:number;durability:number;datalink:boolean;lost_behavior:string;warhead_scale:number;range_m:number;seeker_range_m:number[]}
export interface MissileProfile {models:MissileModel[];warheads:{id:string;name:string;good_id:string}[];launchers:MissileSpec[];magazines:MissileSpec[];flight_profiles?:Record<string,MissilePerformance>}
export type MissileOrder={module_id:string;kind:string;model_id?:string;warhead_id?:string;quantity?:number;enabled?:boolean;target_id?:string|number;point_m?:number[];layer?:string|null;projectile_id?:number};
export interface LauncherFireArc {launcher_kind:string;origin_local_m:number[];rotation_rad:number;boundary_policy:'hull_blocked';sectors:{start_deg:number;end_deg:number;kind:'clear'|'hull_blocked'|'out_of_arc'}[]}
export interface MissileLauncherView {module_id:string;target_id:string|number|null;active_target_id?:string|number|null;interceptor?:boolean;integrated_fire_control?:boolean;point_m:number[]|null;attack_layer:string;automatic_layer?:boolean;active_attack_layer?:string|null;angle_rad:number;aim_point_m:number[]|null;fire_requested:boolean;shots:number;status:string;supported:boolean;maximum_range_m:number;fire_arc?:LauncherFireArc}
export interface MissileShipView {ship_id:string;profile:MissileProfile;state:MissileState;module_names:Record<string,string>;blocked:Record<string,string[]>;cargo:{good_id:string;quantity:number}[];over_capacity:boolean;launchers?:MissileLauncherView[]}
export interface MissileView {command_sequence:number;flight_available:boolean;ships:MissileShipView[];supported_model_ids?:string[];pending?:{projectile_id:number;ship_id:string;weapon_id:string;remaining_steps:number;height_layer:string}[]}
export const selectedLauncher=(ship:MissileShipView|undefined,id:string|null|undefined)=>ship?.launchers?.find(l=>l.module_id===id)??ship?.launchers?.[0];
export const jobName=(kind:string)=>({assemble:'组装',load_raw:'原料装填',load_ready:'整装弹装填',unload:'卸弹退库',swap:'更换战斗部',dismantle:'拆解回收'}[kind]??kind);
export function missileTotals(state?:MissileState) {
  const result:Record<string,number>={};
  const add=(mid:string,stage:string,unit:MissileUnit)=>{const key=`missile:${mid}:${stage}:${unit.model_id}:${unit.warhead_id}`;result[key]=(result[key]??0)+1;};
  state?.launchers.forEach(r=>{r.ready.forEach(u=>add(r.module_id,'ready',u));if(r.job)add(r.module_id,r.job.kind,r.job.unit);});
  state?.magazines.forEach(r=>{r.stock.forEach(u=>add(r.module_id,'stock',u));r.jobs.forEach(j=>add(r.module_id,j.kind,j.unit));});
  return result;
}
export function missileResourceName(key:string,names:Record<string,string>) {
  const [,mid,stage,model,head]=key.split(':');
  const parts=model.split('.');
  const type=parts.includes('interceptor')?'小型拦截弹':`${parts.includes('turbojet')?'涡喷':'火箭'}${parts.includes('radar_infrared')?'复合':parts.includes('anti_radiation')?'反辐射':parts.includes('infrared')?'红外':'雷达'}${parts.includes('medium')?'中型':'小型'}弹`;
  return `${names[mid]??mid} · ${type} · ${head==='blast'?'高爆':'燃烧'} · ${stage==='ready'?'待发':stage==='stock'?'库存':jobName(stage)}`;
}
