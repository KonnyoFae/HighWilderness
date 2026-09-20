import {useState} from 'react';
import type {TacticalView} from './model';
export interface NavigationView {
  command_sequence:number;
  last:unknown;
  ships:{ship_id:string;kind:string;status:string;points:number[][];speed_mps:number;heading_deg:number|null;target_id:string|null;distance_m:number}[];
  withdrawals:{flagship_id:string;speed_mps:number;members:string[]}[];
}
export type NavigationIntent={kind:'move'|'hold'|'return'|'attack'|'withdraw'|'cancel_withdraw';arguments:Record<string,unknown>};
export function FleetOrders({view,shipId,directId,disabled,picking,onPick,onCommand,speed,onSpeed,heading,onHeading}:{
  view:TacticalView;shipId:string;directId:string;disabled:boolean;picking:boolean;onPick:()=>void;
  onCommand:(v:NavigationIntent)=>void;speed:number;onSpeed:(v:number)=>void;heading:number|null;onHeading:(v:number|null)=>void}) {
  const [distance,setDistance]=useState(3000);
  const n=view.snapshot.navigation;if(!n)return null;
  const order=n.ships.find(s=>s.ship_id===shipId),retreat=n.withdrawals.find(r=>r.members.includes(shipId));
  const sensor=view.snapshot.gunnery?.observation?.ships.find(s=>s.ship_id===shipId);
  const target=sensor?.contacts.find(c=>c.id===sensor.locked_target_id&&c.kind==='ship'&&c.valid);
  const labels:Record<string,string>={following:'跟随编队',returning:'正在归队',holding:'保持位置',moving:'沿航点移动',attacking:'攻击目标',flagship_unavailable:'旗舰失能，保持位置'};
  return <section className="fleet-orders" aria-label="舰队命令">
    <h4>舰队命令</h4><p role="status">{retreat?`整队撤离机动 · 共同目标速度 ${retreat.speed_mps.toFixed(1)} m/s`:shipId===directId?'旗舰使用独立操纵区':labels[order?.status??'following']}</p>
    {shipId!==directId&&<>
      <div className="fleet-order-buttons">
        <button disabled={disabled||!!retreat} aria-pressed={picking} onClick={onPick}>指定航点</button>
        <button disabled={disabled||!!retreat} onClick={()=>onCommand({kind:'hold',arguments:{}})}>保持位置</button>
        <button disabled={disabled||!!retreat} onClick={()=>onCommand({kind:'return',arguments:{}})}>归队</button>
        <button disabled={disabled||!!retreat||!target} onClick={()=>onCommand({kind:'attack',arguments:{target_id:target!.id,distance_m:distance,speed_mps:speed}})}>攻击火控所选敌舰</button>
      </div>
      {picking&&<p>点击画布设置航点；按住 Shift 追加。拖动或中键平移，Esc 退出。</p>}
      {!!order?.points.length&&<p>剩余航点 {order.kind==='move'?order.points.length:0}</p>}
      <details><summary>航行与攻击参数</summary>
        <label>目标航速 m/s<input type="number" min={1} max={5000} value={speed} onChange={e=>onSpeed(Math.max(1,Math.min(5000,Number(e.target.value)||1)))}/></label>
        <label>期望交战距离 m<input type="number" min={100} max={50000} value={distance} onChange={e=>setDistance(Math.max(100,Math.min(50000,Number(e.target.value)||100)))}/></label>
        <label><input type="checkbox" checked={heading!==null} onChange={e=>onHeading(e.target.checked?0:null)}/>指定航向（0° 向上，正角向左）</label>
        {heading!==null&&<input aria-label="指定航向角度" type="number" min={-180} max={180} value={heading} onChange={e=>onHeading(Math.max(-180,Math.min(180,Number(e.target.value))))}/>}
        <p>换层单独下令。攻击命令仅自动发射已开启自动发射的导弹，近防继续自主防御。</p>
      </details>
    </>}
    <button disabled={disabled} onClick={()=>onCommand({kind:retreat?'cancel_withdraw':'withdraw',arguments:{}})}>{retreat?'取消整队撤离机动':'整队撤离机动'}</button>
    {retreat&&<p>舰队按最慢可机动成员协调航速；推进失能舰留待抢修。距离脱离判定将在下一阶段接入。</p>}
  </section>;
}
