import {useState} from 'react';
export interface ElectronicWarfareView {
  command_sequence:number;
  devices:{ship_id:string;module_id:string;name:string;kind:string;ready:number;reload_remaining_s:number;radius_m:number;lifetime_s:number;shots:number;status:string;detected:boolean;requested:boolean}[];
  effects:{id:string;ship_id:string;kind:string;height_layer:string;position_m:number[];velocity_mps:number[];radius_m:number;remaining_s:number}[];
}
export type CountermeasureIntent={module_id:string;bearing_deg:number};
const status:Record<string,string>={ready:'可投放',deployed:'已投放',no_detection:'等待发现敌方导弹',no_materials:'装填材料不足',no_ammunition:'等待装填',reloading:'装填中',cooldown:'等待再次投放',destroyed:'已损毁',mode_disabled:'设备未开启',power_unavailable:'供电不足',crew_unavailable:'无法工作',control_unavailable:'舰艇无法接受指令',effect_limit:'场内干扰区域已达上限',battle_finished:'交战结束'};
export function CountermeasurePanel({view,shipId,disabled,onDeploy}:{view?:ElectronicWarfareView;shipId:string|null;disabled:boolean;onDeploy:(v:CountermeasureIntent)=>void}) {
  const [bearing,setBearing]=useState(90);
  const devices=view?.devices.filter(d=>d.ship_id===shipId)??[];
  if(!devices.length)return null;
  return <section className="countermeasure-panel" aria-label="电子对抗">
    <h3>电子对抗</h3><p className="muted">发现敌方导弹后可手动投放。烟云留在本层原地，也会遮挡友方探测；用机动离开区域。</p>
    {devices.some(d=>d.kind==='decoy')&&<label>主动诱饵方向<select aria-label="主动诱饵方向" value={bearing} onChange={e=>setBearing(Number(e.target.value))}>
      {[[0,'舰艏'],[90,'右舷'],[-90,'左舷'],[180,'舰艉']].map(([v,name])=><option key={v} value={v}>{name}</option>)}
      {![0,90,-90,180].includes(bearing)&&<option value={bearing}>自定义 {bearing}°</option>}
    </select><input aria-label="主动诱饵偏转角" type="range" min="-180" max="180" step="1" value={bearing} onChange={e=>setBearing(Number(e.target.value))}/>{bearing}°</label>}
    {devices.map(d=><article key={d.module_id} className="observation-card" data-countermeasure={d.module_id}>
      <strong>{d.name}</strong><p>{status[d.status]??d.status} · 待发 {d.ready} · 已投放 {d.shots} 次</p>
      <p>{d.kind==='decoy'?'同时模拟雷达与红外信号':`区域半径 ${d.radius_m} 米`} · 持续 {d.lifetime_s} 秒{d.reload_remaining_s>0&&` · 装填剩余 ${d.reload_remaining_s.toFixed(1)} 秒`}</p>
      <button disabled={disabled||!d.detected||!d.ready||d.requested||!['ready','no_detection','deployed'].includes(d.status)}
        onClick={()=>onDeploy({module_id:d.module_id,bearing_deg:d.kind==='decoy'?bearing:0})}>{d.requested?'等待投放':`投放${d.name.replace('发射器','')}`}</button>
    </article>)}
  </section>;
}
