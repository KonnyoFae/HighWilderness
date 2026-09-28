import {useState} from 'react';
import {payloadNames,type AviationOrder,type AviationTask,type AviationView} from './aviation';

const layers={upper:'上层',cloud:'云层',rain:'雨层'};
export const taskNames={observe:'定点盘旋',air_patrol:'对空巡逻',sea_patrol:'反舰巡逻'};
const statusNames:Record<string,string>={patrolling:'前往巡逻点 / 盘旋',pursuing:'追击已知目标',returning:'返航',recovering:'回收卸弹',waiting_recovery:'等待可用拦阻索'};

export function AviationTaskForm({initial,disabled,label,onSubmit}:{initial?:AviationTask;disabled:boolean;label:string;onSubmit:(t:AviationTask)=>void}){
  const [task,setTask]=useState<AviationTask>(initial??{kind:'observe',layer:'upper',point_m:[0,1500]});
  const valid=task.point_m.every(n=>Number.isFinite(n)&&Math.abs(n)<=1000000);
  return <fieldset disabled={disabled} className="aviation-task-form"><legend>{label}</legend>
    <label>任务 <select aria-label={`${label}任务`} value={task.kind} onChange={e=>setTask({...task,kind:e.target.value as AviationTask['kind']})}>{Object.entries(taskNames).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>高度层 <select aria-label={`${label}高度层`} value={task.layer} onChange={e=>setTask({...task,layer:e.target.value as AviationTask['layer']})}>{Object.entries(layers).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    {(['X','Y'] as const).map((axis,i)=><label key={axis}>巡逻点 {axis}（米）<input aria-label={`${label}${axis}`} type="number" min={-1000000} max={1000000} value={Number.isNaN(task.point_m[i])?'':task.point_m[i]} onChange={e=>{const point=[...task.point_m] as [number,number];point[i]=e.target.valueAsNumber;setTask({...task,point_m:point});}}/></label>)}
    <button disabled={!valid} onClick={()=>onSubmit(task)}>{label}</button>
  </fieldset>;
}

export function AviationFlightPanel({view,disabled,onOrder}:{view?:AviationView;disabled:boolean;onOrder:(o:AviationOrder)=>void}){
  const [selected,setSelected]=useState<string[]>([]);
  if(!view)return null;
  const flights=view.flights??[],keys=selected.filter(k=>flights.some(f=>f.id===k));
  const groups=[...new Set(flights.map(f=>f.group_id))];
  return <section aria-label="空中航空编队" className="aviation-flight-panel">
    <h3>空中编队 · {flights.length} 架</h3>
    <p>{view.command_available?'舰队指挥塔可用':'舰队指挥塔全部失效，既有任务与自动返航继续'}。飞机自动攻击任务层内的已知目标；挂载用途由整备决定，受损或挂载耗尽后自动返航。</p>
    {groups.map(g=><button key={g} onClick={()=>setSelected(flights.filter(f=>f.group_id===g).map(f=>f.id))}>选择编队 {g.split('.').at(-1)}（{flights.filter(f=>f.group_id===g).length} 架）</button>)}
    {flights.map(f=><article key={f.id} className="aviation-aircraft"><label><input type="checkbox" checked={keys.includes(f.id)} onChange={e=>setSelected(e.target.checked?[...keys,f.id]:keys.filter(k=>k!==f.id))}/>{f.model_id.split('.').at(-1)?.toUpperCase()} #{f.id.split('.').at(-1)} · 编队 {f.group_id.split('.').at(-1)}</label>
      <p>{statusNames[f.status]??f.status} · {layers[f.height_layer]}{f.layer_goal!==f.height_layer?` → ${layers[f.layer_goal]}`:''} · 耐久 {f.hp.toFixed(0)}</p>
      <p>{taskNames[f.task.kind]} · {layers[f.task.layer]}（{f.task.point_m.map(n=>n.toFixed(0)).join(', ')}）</p>
      <p>机炮余弹 {f.cannon_rounds??0} · 挂载 {Object.values(f.loadout??{}).map(k=>payloadNames[k]??k).join('、')||'无'} · 已发射 {f.shots??0} 发</p>
      <p>当前位置（{f.position_m.map(n=>n.toFixed(0)).join(', ')}）· 航速 {Math.hypot(...f.velocity_mps).toFixed(0)} 米/秒 · 本机观测 {f.contacts.length} 个目标{f.target_id?` · 正在追击 ${f.target_id}`:''}</p>
      {f.receiver_ship_id&&<p>接收舰：{f.receiver_ship_id}</p>}
    </article>)}
    {!!view.recent?.length&&<p aria-label="航空交战记录">{view.recent.slice(-4).map((e,i)=><span key={i}>{e.kind==='hit'||e.kind==='destroyed'?`飞机 #${e.aircraft_id.split('.').at(-1)} ${e.kind==='destroyed'?'被击落':'中弹'}`:`飞机 #${e.aircraft_id.split('.').at(-1)} 发射 ${payloadNames[e.weapon??'']??(e.weapon?.startsWith('cannon.')?'机炮':e.weapon)}`}；</span>)}</p>}
    {!!flights.length&&<><p>选中 {keys.length} 架。选择部分飞机后下达任务，会将它们拆为新编队；单机返航不影响其他成员。</p>
      <AviationTaskForm disabled={disabled||!view.command_available||!keys.length} label="向选中飞机下达任务" onSubmit={task=>onOrder({kind:'task',aircraft_ids:keys,task})}/>
      <button disabled={disabled||!view.command_available||!keys.length} onClick={()=>onOrder({kind:'return',aircraft_ids:keys})}>选中飞机返航</button></>}
  </section>;
}
