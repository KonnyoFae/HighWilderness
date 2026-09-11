import type { TacticalView } from './model';
import { goodName } from './preparation';

export type DamageControlIntent = {module_id:string; kind:'enabled'|'repair_target'; arguments:Record<string,unknown>};
export interface DamageControlView {
  command_sequence:number;
  devices:{ship_id:string;module_id:string;enabled:boolean;status:string;quantity_units:number;capacity_units:number;
    target_module_id:string|null;repair_module_id:string|null;remaining_preparation_steps:number;preparation_steps:number;mode_pending:boolean;
    cargo_costs:{good_id:string;quantity:number;available:number;reserved:number}[]}[];
  fires:{ship_id:string;module_id:string;intensity_units:number;remaining_steps:number}[];
}
const statusName:Record<string,string> = {off:'已关闭',waiting:'等待执行',idle:'待机 · 无需维修',firefighting:'优先灭火中',
  preparing:'准备损管资源',no_engineering_parts:'工程零件不足',repairing_module:'维修部件中',repairing_hull:'修复船壳中',
  target_destroyed:'指定部件已毁，无法重建',destroyed:'设备损毁',host_unavailable:'宿主不可用',power_unavailable:'供电不足',
  crew_unavailable:'损管人员不足',mode_disabled:'设备未启用',control_unavailable:'失去控制权限',battle_finished:'交战已结束'};

export function DamageControlPanel({view,shipId,disabled,uncertain,onCommand}:{view:TacticalView;shipId:string;
  disabled:boolean;uncertain:boolean;onCommand:(intent:DamageControlIntent)=>void}) {
  const fire=view.snapshot.gunnery?.damage_control, geometry=view.geometry.ships.find(s=>s.id===shipId);
  const pose=view.snapshot.ships.find(s=>s.id===shipId);
  const devices=fire?.devices.filter(d=>d.ship_id===shipId)??[];
  const name=(id:string)=>geometry?.modules.find(m=>m.id===id)?.name??id;
  return <fieldset aria-label="损管操作" disabled={disabled||uncertain}>
    <legend>旗舰损管</legend>
    <p>启动后优先灭火，再维修指定部件或自动选择耐久比例最低的部件，最后修复船壳。不会重建已毁部件或恢复装甲。</p>
    {uncertain&&<p role="alert">损管命令结果尚待确认，正在读取设备状态，请勿重复发令。</p>}
    {!devices.length&&<p>本舰没有可用的新配置损管设备。可在战前准备中重新导入带损管设备的栖装设计。</p>}
    {!!fire?.fires.filter(f=>f.ship_id===shipId).length&&<p>当前火情：{fire.fires.filter(f=>f.ship_id===shipId).map(f=>`${name(f.module_id)}（强度 ${(f.intensity_units/1000).toFixed(2)}）`).join('、')}</p>}
    {devices.map((d,i)=><article className="preparation-weapon" key={d.module_id}>
      <h4>{name(d.module_id)} · 损管 {i+1}</h4>
      <p aria-live="off">{statusName[d.status]??d.status}{d.mode_pending?' · 启停等待下一步执行':''} · 资源 {(d.quantity_units/1000).toFixed(3)} / {d.capacity_units/1000} 点</p>
      <div className="editor-row"><button aria-label={`损管 ${i+1} ${d.enabled?'关闭':'启动'}`} onClick={()=>onCommand({module_id:d.module_id,kind:'enabled',arguments:{enabled:!d.enabled}})}>{d.enabled?'关闭损管':'启动损管'}</button>
        <label>维修目标<select aria-label={`损管 ${i+1} 维修目标`} value={d.target_module_id??''} onChange={e=>onCommand({module_id:d.module_id,kind:'repair_target',arguments:{module_id:e.target.value||null}})}>
          <option value="">自动选择受损部件</option>{geometry?.modules.map(m=><option key={m.id} value={m.id}>{m.name} · 第 {m.deck_level} 层 · {m.id}{(pose?.modules.find(v=>v.id===m.id)?.durability??0)<=0?'（已毁）':''}</option>)}
        </select></label></div>
      <p>{d.status==='repairing_module'&&d.repair_module_id?`正在修复：${name(d.repair_module_id)}`:d.target_module_id?'指定部件修复完成或不可修复后转向船壳。':'自动模式依次修复可修部件后转向船壳。'}</p>
      {d.remaining_preparation_steps>0&&<p>准备进度 <progress max={d.preparation_steps} value={d.preparation_steps-d.remaining_preparation_steps}/> 剩余 {(d.remaining_preparation_steps/60).toFixed(1)} 秒</p>}
      <p>再次准备：{d.cargo_costs.map(c=>`${goodName(c.good_id)} ${c.quantity} 份（可用 ${c.available}，已预留 ${c.reserved}）`).join('、')} · {(d.preparation_steps/60).toFixed(1)} 秒</p>
    </article>)}
    <small>暂停时损管与准备均停止推进；关闭损管取消未完成准备。下一场交战需要重新启动并选择目标。</small>
  </fieldset>;
}
