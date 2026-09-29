export type AviationTask = {kind:'observe'|'air_patrol'|'sea_patrol';layer:'upper'|'cloud'|'rain';point_m:[number,number]};
export type AviationEmissions = {radar:boolean;jammer:boolean};
export type AviationOrder = {kind:'departure_emissions'|'emissions';aircraft_ids:string[];emissions:AviationEmissions}|{kind:'departure_task'|'task';aircraft_ids:string[];task:AviationTask}|{kind:'launch'|'return';aircraft_ids:string[]}|{kind:'acquire';model_id:string}|{kind:'pilots';module_id:string;quantity:number}|
  {kind:'repair'|'load';aircraft_id:string;module_id:string}|{kind:'prepare';aircraft_id:string;module_id:string;loadout:Record<string,string>}|
  {kind:'store'|'cancel';aircraft_id:string}|{kind:'queue';order:AviationOrder}|{kind:'clear_queue'|'clear_plan'};
export type AviationPilot = {id:string;health:'fit'|'wounded'|'dead';modifiers:Record<string,number>};
export type Aircraft = {id:string;model_id:string;location:string;module_id:string|null;condition:string;loadout:Record<string,string>;cannon_rounds:number;crew:AviationPilot[]};
export type AviationProfile = {catalog:{aircraft:{id:string;name:string;berth_slots:number;pilots_required:number;cannon_rounds:number;required_payload:string|null;radar_range_m?:number;jammer_radius_m?:number;hardpoints:{id:string;compatible_payloads:string[]}[]}[]};
  facilities:{module_id:string;kind:string;ready_slots?:number;workstations?:number;pilot_capacity?:number;prepare_steps?:number;repair_steps?:number;loading_steps?:number}[]};
export type AviationState = {manifest:{aircraft:Aircraft[];personnel:(AviationPilot&{housing:string;module_id:string|null})[]};
  jobs:{aircraft_id:string;module_id:string;kind:string;remaining_steps:number;pilot_ids:string[]}[];queue:AviationOrder[];hangar_assignments:Record<string,string>;departure_tasks?:Record<string,AviationTask>;departure_emissions?:Record<string,AviationEmissions>};
export type AviationFlight = {emissions?:AviationEmissions;emission_capabilities?:AviationEmissions;jammer_radius_m?:number;id:string;model_id:string;home_ship_id:string;group_id:string;task:AviationTask;position_m:[number,number];velocity_mps:[number,number];heading_rad:number;height_layer:AviationTask['layer'];layer_goal:AviationTask['layer'];layer_progress:number;status:string;hp:number;shots?:number;loadout?:Record<string,string>;cannon_rounds?:number;target_id:string|number|null;receiver_ship_id:string|null;contacts:{id:string|number;kind:string;position_m:[number,number];height_layer:string;channels:string[]}[]};
export type AviationView = {jamming_areas?:{id:string;source_id:string;position_m:[number,number];height_layer:string;radius_m:number}[];recent?:{kind:string;aircraft_id:string;weapon?:string}[];command_sequence:number;command_available:boolean;flights:AviationFlight[];contacts:{id:string;position_m:[number,number];height_layer:string;heading_rad:number}[];ships:{ship_id:string;profile:AviationProfile;state:AviationState;module_names:Record<string,string>}[]};
export const payloadNames:Record<string,string>={self_defense:'超小型自卫弹',small_interceptor:'小型拦截弹',small_missile:'小型反舰弹',large_missile:'大型反舰弹',small_bomb:'小型炸弹',large_bomb:'大型炸弹',small_guided_bomb:'小型制导炸弹',large_guided_bomb:'大型制导炸弹'};
export const aircraftLocation:Record<string,string>={cargo:'货舱',repairing:'修复中',preparing:'整备中',ready:'待命泊位',catapult:'弹射器',airborne:'在空',returning:'返航中',waiting_recovery:'等待回收',salvage:'待打捞',destroyed:'损失'};
export function aviationResourceName(id:string):string|null {
  if(id==='aviation:airframes')return '飞机（含待打捞记录）';
  if(id==='aviation:pilots')return '飞行员（含伤亡记录）';
  if(id==='aviation:cannon')return '机载机炮弹';
  if(id.startsWith('aviation:payload:'))return `机载${payloadNames[id.slice(17)]??id.slice(17)}`;
  if(id.startsWith('supply.aviation.'))return id.endsWith('.pilot')?'待接收飞行员':`${id.split('.').at(-1)!.toUpperCase()} 整机`;
  if(id.startsWith('cargo.aviation.')){const k=id.slice(15);return payloadNames[k]??`${k} 机炮弹`;}
  return null;
}
export function aviationTotals(state?:AviationState):Record<string,number>{
  if(!state)return {};
  const planes=state.manifest.aircraft;
  const result:Record<string,number>={'aviation:airframes':planes.length,'aviation:pilots':state.manifest.personnel.length+planes.reduce((n,a)=>n+a.crew.length,0),'aviation:cannon':0};
  for(const a of planes){result['aviation:cannon']+=a.cannon_rounds;for(const k of Object.values(a.loadout))result[`aviation:payload:${k}`]=(result[`aviation:payload:${k}`]??0)+1;}
  return result;
}
