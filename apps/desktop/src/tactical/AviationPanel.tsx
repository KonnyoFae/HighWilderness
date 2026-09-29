import {useState} from 'react';
import type {Aircraft,AviationOrder,AviationProfile,AviationState} from './aviation';
import {aircraftLocation,payloadNames} from './aviation';
import {AviationEmissionControls,AviationTaskForm,taskNames} from './AviationFlightPanel';

export function AviationPanel({profile,state,names,disabled,onOrder,preparation=false,planned=0}:
  {profile?:AviationProfile;state?:AviationState;names:Record<string,string>;disabled:boolean;onOrder:(o:AviationOrder)=>void;preparation?:boolean;planned?:number}) {
  const [hangar,setHangar]=useState(''),[catapult,setCatapult]=useState('');
  if(!profile||!state)return <p>此存档尚未接入航空后勤，请重新导入舰艇设计。</p>;
  const hangars=profile.facilities.filter(f=>f.kind==='aircraft_hangar'),catapults=profile.facilities.filter(f=>f.kind==='aircraft_catapult');
  const selected=hangars.find(f=>f.module_id===hangar)??hangars[0],launch=catapults.find(f=>f.module_id===catapult)??catapults[0];
  const pilots=state.manifest.personnel.filter(p=>p.module_id===selected?.module_id&&p.housing==='hangar');
  const reserved=new Set(state.jobs.flatMap(j=>j.pilot_ids));
  const aboard=state.manifest.aircraft.filter(a=>state.hangar_assignments[a.id]===selected?.module_id).reduce((n,a)=>n+a.crew.length,0);
  const occupied=state.manifest.aircraft.filter(a=>a.module_id===selected?.module_id&&['ready','preparing'].includes(a.location)).reduce((n,a)=>n+(profile.catalog.aircraft.find(m=>m.id===a.model_id)?.berth_slots??0),0);
  return <section className="aviation-panel" aria-label="舰载航空后勤">
    <p>先整备至待命，再装载弹射器并起飞。战前预装的飞机开战即可弹射；战术航空不扣灵烷。</p>
    {!selected?<p>尚未安装机库，可在货舱存放备用机。</p>:<>
      <label>作业机库 <select aria-label="作业机库" value={selected.module_id} onChange={e=>setHangar(e.target.value)}>{hangars.map(f=><option key={f.module_id} value={f.module_id}>{names[f.module_id]??f.module_id}</option>)}</select></label>
      <p>泊位（含整备预留）{occupied} / {selected.ready_slots} 格 · 工位 {state.jobs.filter(j=>j.module_id===selected.module_id).length} / {selected.workstations}</p>
      <p>飞行员 {pilots.filter(p=>p.health!=='dead').length+aboard} / {selected.pilot_capacity} 人 · 空闲 {pilots.filter(p=>p.health==='fit'&&!reserved.has(p.id)).length} · 预留 {pilots.filter(p=>reserved.has(p.id)).length} · 入机 {aboard} · 受伤 {pilots.filter(p=>p.health==='wounded').length}</p>
      {preparation&&<button disabled={disabled} onClick={()=>onOrder({kind:'pilots',module_id:selected.module_id,quantity:1})}>接收 1 名飞行员</button>}
    </>}
    {launch&&<label>装载弹射器 <select aria-label="装载弹射器" value={launch.module_id} onChange={e=>setCatapult(e.target.value)}>{catapults.map(f=><option key={f.module_id} value={f.module_id}>{names[f.module_id]??f.module_id}</option>)}</select></label>}
    {preparation&&<div className="aviation-actions">{profile.catalog.aircraft.map(m=><button disabled={disabled} key={m.id} onClick={()=>onOrder({kind:'acquire',model_id:m.id})}>接收 {m.name.split(' ')[0]}</button>)}</div>}
    {preparation&&<p>接收从有限测试供给扣除；整备自动补齐所需物资。开战后计划只预装物资，开战后才占工位和泊位。</p>}
    {planned>0&&<p>草稿内有 {planned} 项航空操作。<button disabled={disabled} onClick={()=>onOrder({kind:'clear_plan'})}>清除本次航空操作</button></p>}
    {state.queue.length>0&&<p>等待开工 {state.queue.length} 项<button disabled={disabled} onClick={()=>onOrder({kind:'clear_queue'})}>清除等待计划</button></p>}
    {!state.manifest.aircraft.length&&<p>尚无舰载机。先接收飞机与飞行员，再安排整备。</p>}
    {state.manifest.aircraft.map(a=><AircraftCard key={a.id} a={a} profile={profile} state={state} names={names} hangar={selected?.module_id} catapult={launch?.module_id} disabled={disabled} preparation={preparation} onOrder={onOrder}/>)}
  </section>;
}

function AircraftCard({a,profile,state,names,hangar,catapult,disabled,preparation,onOrder}:{a:Aircraft;profile:AviationProfile;state:AviationState;names:Record<string,string>;hangar?:string;catapult?:string;disabled:boolean;preparation:boolean;onOrder:(o:AviationOrder)=>void}){
  const m=profile.catalog.aircraft.find(m=>m.id===a.model_id)!;
  const [loadout,setLoadout]=useState<Record<string,string>>(()=>Object.keys(a.loadout).length?a.loadout:Object.fromEntries(m.hardpoints.filter(()=>m.required_payload).map(p=>[p.id,m.required_payload!])));
  const job=state.jobs.find(j=>j.aircraft_id===a.id),queued=state.queue.some(q=>'aircraft_id' in q&&q.aircraft_id===a.id);
  const capabilities={radar:(m.radar_range_m??0)>0,jammer:(m.jammer_radius_m??0)>0};
  const order:AviationOrder|undefined=hangar?{kind:a.condition==='damaged'?'repair':'prepare',aircraft_id:a.id,module_id:hangar,...(a.condition==='damaged'?{}:{loadout})} as AviationOrder:undefined;
  return <article className="aviation-aircraft" aria-label={`${m.name} ${a.id.split('.').at(-1)}`}>
    <strong>{m.name} · #{a.id.split('.').at(-1)}</strong>
    <p>{a.condition==='damaged'?'受损机':'完好机'} · {aircraftLocation[a.location]??a.location}{a.module_id?` · ${names[a.module_id]??a.module_id}`:''} · {m.berth_slots} 格 · {m.pilots_required} 名机组{queued?' · 已预排开战后作业':''}</p>
    {job&&<p>剩余 {(job.remaining_steps/60).toFixed(1)} 秒 <button disabled={disabled} onClick={()=>onOrder({kind:'cancel',aircraft_id:a.id})}>取消并立即完工</button></p>}
    {a.location==='catapult'&&!job&&<p>装载已完成。{!preparation&&<button disabled={disabled} onClick={()=>onOrder({kind:'launch',aircraft_ids:[a.id]})}>弹射起飞</button>}</p>}
    {['cargo','repairing','preparing','ready','catapult'].includes(a.location)&&<details><summary>起飞任务{state.departure_tasks?.[a.id]?`：${taskNames[state.departure_tasks[a.id].kind]}`:'：默认前方盘旋'}</summary><AviationTaskForm initial={state.departure_tasks?.[a.id]} disabled={disabled} label="保存起飞任务" onSubmit={task=>onOrder({kind:'departure_task',aircraft_ids:[a.id],task})}/></details>}
    {['cargo','repairing','preparing','ready','catapult'].includes(a.location)&&<AviationEmissionControls label="起飞设备" value={state.departure_emissions?.[a.id]??capabilities} capabilities={capabilities} disabled={disabled} onChange={emissions=>onOrder({kind:'departure_emissions',aircraft_ids:[a.id],emissions})}/>}
    {!job&&!queued&&['cargo','ready'].includes(a.location)&&<>
      {a.condition==='intact'&&<fieldset disabled={disabled}><legend>本次挂载配置</legend>{m.hardpoints.map(p=><label key={p.id}>{p.id} <select aria-label={`${a.id} ${p.id} 挂载`} value={loadout[p.id]??''} onChange={e=>setLoadout(old=>{const next={...old};if(e.target.value)next[p.id]=e.target.value;else delete next[p.id];return next;})}>
        {!m.required_payload&&<option value="">空挂点</option>}{p.compatible_payloads.map(k=><option key={k} value={k}>{payloadNames[k]??k}</option>)}</select></label>)}</fieldset>}
      <div className="aviation-actions"><button disabled={disabled||!order} onClick={()=>order&&onOrder(order)}>{a.condition==='damaged'?'修复后入库':a.location==='ready'?'重新整备与挂载':'整备至待命'}</button>
      {preparation&&a.location==='cargo'&&<button disabled={disabled||!order} onClick={()=>order&&onOrder({kind:'queue',order})}>安排开战后{a.condition==='damaged'?'修复':'整备'}</button>}
      {a.location==='ready'&&catapult&&<button disabled={disabled} onClick={()=>onOrder({kind:'load',aircraft_id:a.id,module_id:catapult})}>{preparation?'战前预装弹射器':'装载弹射器'}</button>}</div>
    </>}
    {!job&&['ready','catapult'].includes(a.location)&&<button disabled={disabled} onClick={()=>onOrder({kind:'store',aircraft_id:a.id})}>卸弹并存回货舱</button>}
    {!!a.cannon_rounds&&<p>已装机炮弹 {a.cannon_rounds} 发</p>}
  </article>;
}
