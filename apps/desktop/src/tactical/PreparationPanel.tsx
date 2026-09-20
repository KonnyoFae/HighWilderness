import { useEffect, useRef, useState } from 'react';
import type { BridgeTransport } from '../bridge/transport';
import { normalizeHostFailure } from '../bridge/model';
import { changeDraft, goodName, preparationError, quantity } from './preparation';
import type { PreparedLaunch, PreparationDraft, PreparationLibrary, PreparationPacket, PreparationPreview, PreparationResult, WeaponChoice } from './preparation';
import type { TacticalRequest } from './model';
import { resourceRows, fuelTankName } from './settlement';
import { ammunitionName } from './ammunition';
import { MissileStoresPanel } from './MissileStoresPanel';
import type { MissileOrder } from './missiles';
import { LiftReserve } from '../LiftReserve';
import type { PreparationTab } from './preparationScene';

export function PreparationPanel({transport,instance,active,onBusy,onEnter,embedded}: {
  transport: BridgeTransport; instance: string; active: boolean; onBusy: (value:boolean)=>void;
  onEnter?: (launch:PreparedLaunch)=>void;
  embedded?: {preparationId:string;instanceIds:string[];shipId:string;moduleId:string|null;tab:PreparationTab;
    onSaved:(revision:number)=>void;onLeave:()=>void};
}) {
  const [library,setLibrary]=useState<PreparationLibrary|null>(null), [packet,setPacket]=useState<PreparationPacket|null>(null);
  const [draft,setDraft]=useState<PreparationDraft|null>(null), [savedRevision,setSavedRevision]=useState(-1);
  const [selected,setSelected]=useState<string[]>([]), [shipId,setShipId]=useState(''), [source,setSource]=useState('');
  const [preview,setPreview]=useState<PreparationPreview|null>(null), [result,setResult]=useState<PreparationResult|null>(null);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [uncertain,setUncertain]=useState(false);
  const [draftUncertain,setDraftUncertain]=useState(false);
  const [actionUncertain,setActionUncertain]=useState(false);
  const pendingAction=useRef<Record<string,unknown>|null>(null);
  const pendingActionRoute=useRef('maintenance');
  const [status,setStatus]=useState('');
  const working=useRef(false), mounted=useRef(true), failedSave=useRef(-1);
  const pendingImport=useRef<{instance_id:string;source:{kind:string;value:string}}|null>(null);
  const pendingOpen=useRef<{preparation_id:string;instance_ids:string[]}|null>(null);
  const dirty=!!draft && draft.revision!==savedRevision;
  useEffect(()=>{ mounted.current=true; return ()=>{mounted.current=false;}; },[]);
  useEffect(()=>{onBusy(busy || dirty || uncertain || actionUncertain); return ()=>onBusy(false);},[busy,dirty,uncertain,actionUncertain,onBusy]);
  function call<T>(suffix:string,params:Record<string,unknown>) {
    return transport.tactical<T>({backend_instance_id:instance,method:`tactical.preparation.${suffix}` as TacticalRequest['method'],params,session_id:null,expected_revision:null});
  }
  async function refresh() { const next=await call<PreparationLibrary>('library',{}); if(mounted.current)setLibrary(next); }
  async function run(work:()=>Promise<void>) {
    if(working.current || !active) return;
    working.current=true; setBusy(true); setError('');
    try {await work();} catch(e) {if(mounted.current)setError(normalizeHostFailure(e).message);}
    finally {working.current=false;if(mounted.current)setBusy(false);}
  }
  useEffect(()=>{if(active&&!embedded)void run(refresh);},[active]);
  useEffect(()=>{
    if(embedded && active)void run(async()=>accept(await call<PreparationPacket>('open',{
      preparation_id:embedded.preparationId,instance_ids:embedded.instanceIds})));
  },[embedded?.preparationId,active]);
  useEffect(()=>{if(embedded)setShipId(embedded.shipId);},[embedded?.shipId,packet]);
  function accept(next:PreparationPacket) {
    if(!mounted.current)return;
    setPacket(next);setDraft(next.draft);setSavedRevision(next.draft.revision);setPreview(null);setResult(next.receipt);
    setShipId(embedded?.shipId??next.draft.ships[0]?.instance_id??'');setUncertain(false);setDraftUncertain(false);failedSave.current=-1;
    pendingAction.current=null;setActionUncertain(false);
    setStatus(next.receipt?'准备已保存。':'准备草稿已恢复，库存尚未扣费。');
    if(next.receipt)embedded?.onSaved(next.draft.revision);
  }
  async function saveDraft() {
    if(!draft || !dirty)return;
    try {
      const next=await call<PreparationDraft>('draft',{draft,expected_saved_revision:savedRevision});
      if(mounted.current){setSavedRevision(next.revision);setDraftUncertain(false);setStatus('草稿已保存，尚未扣费。');}
    } catch(e) {setDraftUncertain(true);throw e;}
  }
  useEffect(()=>{
    if(!active || busy || !draft || !dirty || uncertain || actionUncertain || result || failedSave.current===draft.revision)return;
    const timer=window.setTimeout(()=>void run(async()=>{try{await saveDraft();}catch(e){failedSave.current=draft.revision;throw e;}}),700);
    return ()=>window.clearTimeout(timer);
  },[active,busy,draft,dirty,uncertain,actionUncertain,result]);
  function edit(update:(row:PreparationDraft['ships'][number])=>void) {
    if(!draft || busy || uncertain || draftUncertain || actionUncertain || result)return;
    try {setDraft(changeDraft(draft,shipId,update));setPreview(null);setStatus('草稿有新修改，正在等待保存…');setError('');}
    catch(e){setError(normalizeHostFailure(e).message);}
  }
  async function importDesign(file:boolean) {
    if(!pendingImport.current){
      let selectedSource={kind:'resource',value:source||library?.sources[0]?.key||''};
      if(file){const grant=await transport.chooseFile({backend_instance_id:instance,method:'open',params:{},session_id:null,expected_revision:null});
        if(!grant)return; selectedSource={kind:'file',value:grant.destination_handle};}
      pendingImport.current={instance_id:`instance.preparation.${crypto.randomUUID()}`,source:selectedSource};
    }
    const added=await call<{instance_id:string}>('import',pendingImport.current);
    pendingImport.current=null;setSelected(ids=>[...ids,added.instance_id]);await refresh();setStatus('舰船实例已添加，库存初始为空。');
  }
  async function open() {
    if(!pendingOpen.current)pendingOpen.current={preparation_id:`preparation.${crypto.randomUUID()}`,instance_ids:selected};
    const next=await call<PreparationPacket>('open',pendingOpen.current);
    pendingOpen.current=null;accept(next);await refresh();
  }
  async function submit() {
    if(!draft)return;
    setUncertain(true);
    const value=await call<PreparationResult>('commit',{preparation_id:draft.preparation_id,revision:draft.revision});
    setResult(value);setUncertain(false);setStatus('准备已保存，库存与待发弹已更新。');await refresh();embedded?.onSaved(draft.revision);
  }
  async function maintenance(targetId?:string,kind='fill',scope='single') {
    if(!draft)return;
    if(!pendingAction.current){
      await saveDraft();
      pendingActionRoute.current='maintenance';
      pendingAction.current={operation_id:`maintenance.${crypto.randomUUID()}`,preparation_id:draft.preparation_id,
        revision:draft.revision,instance_id:shipId,target_id:targetId,kind,scope};
    }
    setActionUncertain(true);
    const next=await call<PreparationDraft>(pendingActionRoute.current,pendingAction.current);
    pendingAction.current=null;setActionUncertain(false);setDraft(next);setSavedRevision(next.revision);setPreview(null);
    setStatus('部件操作已加入草稿，尚未扣费。核对通过后保存准备即可生效。');
    setPreview(await call<PreparationPreview>('preview',{preparation_id:next.preparation_id,revision:next.revision}));
  }
  async function missile(order:MissileOrder|{kind:'clear_plan'}) {
    if(!draft)return;
    await saveDraft();
    pendingActionRoute.current='missile';
    pendingAction.current={operation_id:`missile.${crypto.randomUUID()}`,preparation_id:draft.preparation_id,revision:draft.revision,instance_id:shipId,order};
    await maintenance();
  }
  const sourceShip=packet?.ships.find(s=>s.instance_id===shipId), row=draft?.ships.find(s=>s.instance_id===shipId);
  const savedShip=result?.ships.find(s=>s.after.state.instance_id===shipId);
  const ship=sourceShip&&savedShip?{...sourceShip,state:{...sourceShip.state,...savedShip.after.state,
    weapons:sourceShip.state.weapons.map(w=>({...w,...savedShip.after.state.weapons.find(v=>v.module_id===w.module_id)}))},capacity:savedShip.capacity_after}:sourceShip;
  const supply=result?.supply_after??packet?.supply;
  const locked=busy||uncertain||draftUncertain||actionUncertain||!!result||!!packet?.stale_error;
  const output=result??preview?.result;
  const tab=(id:PreparationTab)=>!embedded||embedded.tab===id;
  const matches=(id:string)=>!embedded?.moduleId||embedded.moduleId===id;
  const weaponMatchesTab=(id:string)=>!embedded||embedded.tab===(ship?.resources.weapons.find(w=>w.module_id===id)?.recipe_ids.some(r=>r.startsWith('recipe.ew.'))?'devices':'guns');
  function maintenanceControls(id:string) {
    const target=ship?.maintenance_targets?.find(t=>t.id===id);if(!target)return null;
    const completed=savedShip?.repairs?.some(t=>t.target_id===id),planned=!result&&row?.repairs?.includes(id);
    const current=completed?target.maximum_points:target.current_points;
    const group=target.group&&({weapon:'火炮',countermeasure:'干扰发射器',magazine:'弹药库',damage_control:'损管设备',fuel:'燃料槽'}[target.group]);
    return <section className="preparation-maintenance" aria-label={`${target.name}部件操作`}>
      <p>耐久 {current.toFixed(1)} / {target.maximum_points.toFixed(1)}{current<=0?' · 已摧毁，不能战前维修':''}</p>
      <div className="editor-row">{group&&<><button disabled={current<=0} onClick={()=>void run(()=>maintenance(id))}>补满此部件</button>
        <button onClick={()=>void run(()=>maintenance(id,'fill','same_class'))}>补满本舰全部{group}</button></>}
        {planned?<button onClick={()=>void run(()=>maintenance(id,'cancel_repair'))}>取消此部件维修</button>:
          <button disabled={!target.repair_allowed||current>=target.maximum_points} onClick={()=>void run(()=>maintenance(id,'repair'))}>修复此部件（{completed?0:target.repair_cost} 份工程零件）</button>}</div>
      {planned&&<p>已安排修至满耐久；将从共享供给扣除 {target.repair_cost} 份工程零件。</p>}
    </section>;
  }
  return <section className="panel preparation-panel" aria-label="战前准备">
    {!embedded&&<><h2>战前准备</h2><p>逐舰管理货物和弹药，选择武器预装填与损管设备准备。草稿只记录选择；“保存准备”才会转移物资并完成准备。</p>
    <p className="muted">测试物资使用有限供给池；消耗后不会自动补满。新建实例采用标准人员与部件燃料槽的技术装载预设，填充燃料槽初始为空。</p></>}
    {error&&<p role="alert">{error}</p>}{status&&<p role="status">{status}</p>}
    {uncertain&&<p role="alert">保存结果尚未确认。请重试保存或重新读取；在确认前不能修改或放弃这份准备。</p>}
    {draftUncertain&&<p role="alert">草稿保存尚未确认，请重试“保存准备草稿”；重新读取会放弃本地未确认的修改，以仓库版本为准。</p>}
    {actionUncertain&&<p role="alert">部件操作尚未确认。<button disabled={busy} onClick={()=>void run(()=>maintenance())}>重试部件操作</button>或重新读取准备，确认已保存的草稿。</p>}
    {!!library?.interrupted_battles&&<p role="status">此前有 {library.interrupted_battles} 场未结算交战因后台退出而中断，已恢复到入战前保存状态；战前准备费用不会返还。已生成的战后结算仍需保存。</p>}
    {!draft&&embedded&&<p role="status">正在加载双方物资准备…</p>}
    {!draft&&!embedded&&<fieldset disabled={busy||!active}>
      <legend>舰船选择</legend>
      <div className="editor-row"><button onClick={()=>void run(()=>importDesign(true))}>导入已保存的栖装文件</button>
        <label>目录设计<select aria-label="准备目录设计" value={source||library?.sources[0]?.key||''} onChange={e=>setSource(e.target.value)}>{library?.sources.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
        <button disabled={!library?.sources.length} onClick={()=>void run(()=>importDesign(false))}>从目录设计添加舰船</button>
        <button onClick={()=>void run(refresh)}>刷新准备列表</button></div>
      {pendingImport.current&&<p>导入尚未确认，再次导入会查询同一请求。<button onClick={()=>{pendingImport.current=null;setError('');}}>放弃本次导入重试</button></p>}
      {!library?.ships.length&&<p>尚无准备舰船，请先导入已保存的栖装设计。</p>}
      <div className="preparation-fleet">{library?.ships.map((s,i)=><label key={s.instance_id}><input type="checkbox" aria-label={`选择准备舰船 ${i+1}`} checked={selected.includes(s.instance_id)} disabled={s.blocked||!!pendingOpen.current}
        onChange={e=>setSelected(ids=>e.target.checked?[...ids,s.instance_id]:ids.filter(id=>id!==s.instance_id))}/>{s.name} · 舰船 {i+1} · 船壳 {(s.hull_integrity*100).toFixed(0)}%{s.blocked?' · 战斗或结算占用中':''}</label>)}</div>
      <button disabled={selected.length===0||selected.length>18} onClick={()=>void run(open)}>准备所选舰船</button>
      {pendingOpen.current&&<button onClick={()=>{pendingOpen.current=null;setStatus('可重新选择舰船；已创建的草稿仍在准备列表中。');void run(refresh);}}>重新选择舰船</button>}
      <h3>准备草稿与已保存记录</h3>
      {library?.drafts.map((d,i)=><p key={d.preparation_id}><button onClick={()=>void run(async()=>accept(await call<PreparationPacket>('read',{preparation_id:d.preparation_id})))}>打开准备 {i+1} · {d.saved?'已保存':'草稿'}</button></p>)}
    </fieldset>}
    {draft&&<>
      <div className="editor-row"><button disabled={busy||(dirty&&!draftUncertain)} onClick={()=>void run(async()=>accept(await call<PreparationPacket>('read',{preparation_id:draft.preparation_id})))}>重新读取准备</button>
        <button disabled={busy||uncertain||actionUncertain||!!result||!dirty} onClick={()=>void run(saveDraft)}>保存准备草稿</button>
        <button disabled={busy||uncertain||actionUncertain||!!result||!!packet?.stale_error} onClick={()=>void run(async()=>{await saveDraft();setPreview(await call<PreparationPreview>('preview',{preparation_id:draft.preparation_id,revision:draft.revision}));})}>核对资源与预装填</button>
        <button disabled={busy||actionUncertain||draftUncertain||!!result||(!uncertain&&(!preview?.can_commit||dirty))} onClick={()=>void run(submit)}>{uncertain?'重试保存准备':'保存准备'}</button>
        {!embedded&&<button disabled={busy||uncertain||dirty} onClick={()=>{setPacket(null);setDraft(null);setPreview(null);setResult(null);setStatus('');void run(refresh);}}>返回舰船列表</button>}
        <button disabled={busy||uncertain||actionUncertain} onClick={()=>void run(async()=>{await call('discard',{preparation_id:draft.preparation_id,revision:savedRevision});setDraft(null);setPacket(null);setPreview(null);setResult(null);await refresh();setStatus('准备入口已移除，舰船实际库存保持原样。');embedded?.onLeave();})}>{result?'继续调整编队与物资':'放弃准备草稿'}</button>
      </div>
      {packet?.stale_error&&<p role="alert">{packet.stale_error}。原草稿已保留，可放弃后重新选择舰船准备。</p>}
      {supply&&<details><summary>{result?'本次准备后供给':'可用供给'} · {supply.cargo.length} 类原料</summary><p>{result?'本次准备后供给':'可用供给'}：弹药资源 {supply.ammunition_resources} 点{supply.cargo.map(c=>` · ${goodName(c.good_id)} ${c.quantity} 份`)}</p></details>}
      {!embedded&&<label>当前准备舰船<select aria-label="当前准备舰船" value={shipId} onChange={e=>setShipId(e.target.value)}>{packet?.ships.map((s,i)=><option key={s.instance_id} value={s.instance_id}>{s.name} · 舰船 {i+1}</option>)}</select></label>}
      {ship&&row&&<fieldset disabled={locked}>
        {!embedded&&<legend>{ship.name}</legend>}
        {tab('devices')&&<LiftReserve value={ship.lift_reserve} />}
        {tab('devices')&&<p>船壳 {(ship.state.hull_integrity_fraction*100).toFixed(0)}% · 燃料 {ship.state.fuel_units} · 人员 {ship.state.crew.reduce((n,c)=>n+c.count,0)}。</p>}
        <details className="maintenance-help"><summary>补满与维修规则</summary><p>维修仅恢复未毁部件耐久，不补库存；共享供给每份工程零件修复 {ship.maintenance_policy?.repair_points_per_engineering_part??25} 点耐久，不足一份向上取整。补满先使用舰内余料，再从供给自动装载缺料；按各部件配置分别计算，资源不足则整次准备不保存。</p></details>
        {embedded?.moduleId&&maintenanceControls(embedded.moduleId)}
        {tab('devices')&&!embedded?.moduleId&&ship.maintenance_targets?.filter(t=>!t.group).map(t=><article className="preparation-weapon" key={t.id}><h4>{t.name}</h4>{maintenanceControls(t.id)}</article>)}
        {tab('devices')&&ship.resources.ignition_decks&&<section aria-label="本舰防火配置"><h3>防火配置</h3>
          {ship.resources.ignition_decks.map(d=><p key={d.deck_id}>第 {d.deck_level} 层：{d.multiplier<1?`起火概率降低 ${((1-d.multiplier)*100).toFixed(0)}%`:'无额外防火效果'}</p>)}
          <small>仅影响新的点燃尝试；不减轻已有火情。跨层部件按安装基准层归属；零净填充空间无增益。当前比例为测试数值。</small></section>}
        {tab('cargo')&&<>{!!row.fuel_tanks?.length&&<><h3>灵烷燃料</h3><p>当前燃料 {ship.state.fuel_units.toFixed(2)} 单位 · 可用供给 {supply?.fuel_units??0} 单位。发动机在战术中不耗油，仅燃料槽损毁时损失其中燃料。填充槽独立耐久，不提供升力。</p>
          {row.fuel_tanks.map((t,i)=>{const spec=ship.resources.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!,current=ship.state.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!;
            if(!matches(spec.module_id??t.tank_id))return null;
            return <div className="preparation-weapon" key={t.tank_id}><label className="preparation-input">{fuelTankName(spec,ship.module_names)} · 容量 {spec.capacity_units} · 耐久 {current.durability_points.toFixed(1)} / {spec.maximum_points}
              <input aria-label={`燃料槽 ${i+1} 装载目标`} type="number" min="0" max={spec.capacity_units} step="1" value={t.quantity_units} disabled={current.durability_points<=0}
                onChange={e=>edit(r=>{r.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!.quantity_units=quantity(e.target.value);})}/></label>{!embedded?.moduleId&&maintenanceControls(spec.module_id??t.tank_id)}</div>;})}</>}
        <h3>弹药资源</h3><p>数量为预装填前的装载目标；武器装填后会扣除相应资源。</p>
        {ship.resources.ammunition_resource_liters&&<p>每点弹药资源占 {ship.resources.ammunition_resource_liters} 升。
          {[30,50,75,120].map(caliber=>{const r=ship.resources.recipes.find(r=>r.id===`recipe.3a.${caliber}mm.ordinary`);return r?`${caliber} 毫米每点约 ${(r.rounds/r.ammo_cost).toLocaleString('zh-CN',{maximumFractionDigits:2})} 发。`:null;})}</p>}
        {!!ship.resources.ammunition_resource_liters&&ship.resources.recipes.some(r=>r.id==='recipe.3a.50mm.ordinary'&&(r.rounds!==30||r.ammo_cost!==1))&&<p className="muted">此舰仍采用调整前的火炮装填配方。重新导入舾装文件可应用新的 50／75／120 毫米消耗；现有舰艇库存保持原样。</p>}
        {!ship.resources.ammunition_resource_liters&&<p className="muted">此舰保留旧版弹药容量与配方。重新导入舾装文件可应用更新后的弹药容量；当前库存不会自动换算或补满。</p>}
        {row.magazines.map((m,i)=>matches(m.module_id)&&<div className="preparation-weapon" key={m.module_id}><label className="preparation-input">{ship.module_names[m.module_id]} · 库 {i+1} · 容量 {ship.resources.magazines.find(v=>v.module_id===m.module_id)?.capacity_resources} 点
          {ship.resources.ammunition_resource_liters&&` · ${(ship.resources.magazines.find(v=>v.module_id===m.module_id)?.capacity_resources??0)*ship.resources.ammunition_resource_liters/1000} 立方米`}
          <input aria-label={`弹药库 ${i+1} 装载目标`} type="number" min="0" step="1" value={m.quantity} onChange={e=>edit(r=>{r.magazines.find(v=>v.module_id===m.module_id)!.quantity=quantity(e.target.value);})}/></label>{!embedded?.moduleId&&maintenanceControls(m.module_id)}</div>)}
        {!row.magazines.length&&<p>本舰没有弹药库。</p>}
        <h3>货物</h3><p>当前已用 {(ship.capacity.used_volume_cm3/1e6).toFixed(1)} / {(ship.capacity.capacity_cm3/1e6).toFixed(1)} m³{ship.capacity.over_capacity?' · 战损超容，可保留或卸载现有货物':''}</p>
        {ship.resources.goods.map(g=><label className="preparation-input" key={g.id}>{goodName(g.id)} · 每份 {g.unit_volume_cm3/1e6} m³
          <input aria-label={`${goodName(g.id)}装载目标`} type="number" min="0" step="1" value={row.cargo.find(c=>c.good_id===g.id)?.quantity??0} onChange={e=>edit(r=>{const n=quantity(e.target.value),c=r.cargo.find(v=>v.good_id===g.id);if(c)c.quantity=n;else r.cargo.push({good_id:g.id,quantity:n});})}/></label>)}
        </>}
        {tab('damage')&&<><h3>损管设备准备</h3><p>一键补满保留现有余量，只支付缺少部分的工程零件。整批准备仍限空设备；战中资源耗尽会自动尝试再次准备。</p>
        {row.damage_controls?.map((d,i)=>{const spec=ship.resources.damage_controls?.find(v=>v.module_id===d.module_id), current=ship.state.damage_controls?.find(v=>v.module_id===d.module_id);
          if(!spec||!current||!matches(d.module_id))return null;
          const destroyed=(ship.state.modules.find(m=>m.module_id===d.module_id)?.durability_points??0)<=0;
          return <article className="preparation-weapon" key={d.module_id}><h4>{ship.module_names[d.module_id]} · 损管 {i+1}</h4>
            <p>资源 {current.quantity_units/1000} / {spec.capacity_units/1000} 点{destroyed?' · 设备已损毁':''}</p>
            {!embedded?.moduleId&&maintenanceControls(d.module_id)}
            {d.top_up&&<p>已安排补满资源。<button onClick={()=>edit(r=>{r.damage_controls!.find(v=>v.module_id===d.module_id)!.top_up=false;})}>取消损管补满</button></p>}
            <label><input type="checkbox" aria-label={`损管 ${i+1} 预准备`} checked={d.prepare} disabled={destroyed||current.quantity_units>0||!!current.preparation}
              onChange={e=>edit(r=>{const choice=r.damage_controls!.find(v=>v.module_id===d.module_id)!;choice.prepare=e.target.checked;if('top_up' in choice)choice.top_up=false;})}/>消耗 {spec.cargo_costs.map(c=>`${goodName(c.good_id)} ${c.quantity} 份`).join('、')}，准备至 {spec.capacity_units/1000} 点</label>
            <p>战中再次准备耗时 {(spec.preparation_steps/60).toFixed(1)} 秒；关闭设备会取消未完成准备并释放零件预留。</p>
          </article>;})}
        {!row.damage_controls?.length&&<p>{ship.resources.damage_controls?'本舰没有损管设备。':'本舰沿用旧资源配置。重新导入栖装设计可建立支持损管的新舰船实例。'}</p>}
        </>}
        {(tab('guns')||tab('devices'))&&<><h3>{embedded?.tab==='devices'?'干扰装备预装填':'武器预装填'}</h3><p>保留现状不会卸下已有弹药。弃置换弹不会返还旧弹成本。</p>
        <div className="editor-row"><button onClick={()=>edit(r=>{for(const w of r.weapons.filter(w=>weaponMatchesTab(w.module_id))){const recipe=ship.resources.weapons.find(v=>v.module_id===w.module_id)?.recipe_ids.find(id=>ship.enabled_recipe_ids.includes(id));if(recipe)Object.assign(w,{action:'preload',recipe_id:recipe,batches:1});}})}>{embedded?.tab==='devices'?'本舰干扰装备各预装一批':'本舰武器各预装一批'}</button>
          <button onClick={()=>edit(r=>{for(const w of r.weapons.filter(w=>weaponMatchesTab(w.module_id)))Object.assign(w,{action:'keep',recipe_id:null,batches:0});})}>本页装备全部保持现状</button></div>
        {row.weapons.map((w,i)=>{const spec=ship.resources.weapons.find(v=>v.module_id===w.module_id)!,current=ship.state.weapons.find(v=>v.module_id===w.module_id)!;
          if(!matches(w.module_id)||!weaponMatchesTab(w.module_id))return null;
          const recipe=ship.resources.recipes.find(r=>r.id===(w.recipe_id??spec.recipe_ids[0]));
          const projectile=ship.resources.projectiles?.find(p=>p.id===recipe?.projectile?.id);
          const fillRounds=w.action==='top_up'?spec.ready_capacity-current.ready_rounds:(recipe?.rounds??0)*w.batches;
          const expense=(cost:number)=>w.action==='top_up'?Math.ceil(cost*fillRounds/(recipe?.rounds??1)):cost*w.batches;
          return <article className="preparation-weapon" key={w.module_id}><h4>{ship.module_names[w.module_id]} · 武器 {i+1}</h4>
            <p>现有待发 {current.ready_rounds} / {spec.ready_capacity} 发 · {ammunitionName(current.recipe_id)} · 冷却 {(current.cooldown_steps/60).toFixed(1)} 秒</p>
            {!embedded?.moduleId&&maintenanceControls(w.module_id)}
            {projectile?.ballistics && <p>所选弹初速 {Math.round(projectile.speed_mmps/1000)} 米/秒 · 寿命 {(projectile.ballistics.lifetime_steps/60).toFixed(1)} 秒{recipe?.reload_steps !== undefined && ` · 每批装填 ${(recipe.reload_steps/60).toFixed(1)} 秒`}{spec.cooldown_steps && ` · 连续射速 ${Math.round(3600/spec.cooldown_steps)} 发/分`}</p>}
            <div className="editor-row"><label>准备动作<select aria-label={`武器 ${i+1} 准备动作`} value={w.action} onChange={e=>edit(r=>{const c=r.weapons[i];c.action=e.target.value as WeaponChoice['action'];c.recipe_id=c.action==='keep'?null:spec.recipe_ids.find(id=>(id==='recipe.x1a.ordinary'||id.endsWith('.ordinary'))&&ship.enabled_recipe_ids.includes(id))??spec.recipe_ids.find(id=>ship.enabled_recipe_ids.includes(id))??null;c.batches=c.action==='keep'?0:1;})}><option value="keep">保留现状</option>{w.action==='top_up'&&<option value="top_up">补齐实际缺弹</option>}<option value="preload">补装整批弹药</option><option value="discard_and_preload">弃置现有弹药并重装</option></select></label>
            <label>弹药种类<select aria-label={`武器 ${i+1} 弹药种类`} disabled={w.action==='keep'} value={w.recipe_id??spec.recipe_ids[0]} onChange={e=>edit(r=>{r.weapons[i].recipe_id=e.target.value;})}>{spec.recipe_ids.map(id=><option key={id} value={id} disabled={!ship.enabled_recipe_ids.includes(id)}>{ammunitionName(id)}{!ship.enabled_recipe_ids.includes(id)?'（尚不可用）':''}</option>)}</select></label>
            <label>批次<input aria-label={`武器 ${i+1} 预装填批次`} type="number" min="1" max="10000" step="1" disabled={w.action==='keep'||w.action==='top_up'} value={w.batches} onChange={e=>edit(r=>{const n=quantity(e.target.value);if(n<1||n>10000)throw new Error('预装填批次须为 1—10000 的整数。');r.weapons[i].batches=n;})}/></label></div>
            {recipe&&w.action!=='keep'&&<p>预计装入 {fillRounds} 发，消耗 {expense(recipe.ammo_cost)} 点弹药资源{recipe.cargo_costs.map(c=>`、${goodName(c.good_id)} ${expense(c.quantity)} 份`)}{w.action==='discard_and_preload'?`；另弃置已有 ${current.ready_rounds} 发`:''}。</p>}
          </article>;})}
        {!row.weapons.length&&<p>本舰没有可预装填武器。</p>}
        {!!row.weapons.length&&!ship.enabled_recipe_ids.includes('recipe.x1a.special_armor_piercing')&&<p>本舰沿用旧弹药配置。新导入的测试舰可选择穿甲弹，已有舰船的库存与战损继续保留。</p>}
        </>}
        {tab('missiles')&&<>
          <MissileStoresPanel profile={ship.resources.missiles} state={output?.ships.find(s=>s.after.state.instance_id===shipId)?.after.state.missiles??ship.state.missiles}
            names={ship.module_names} selected={embedded?.moduleId} preparation disabled={locked} onCommand={order=>void run(()=>missile(order))}/>
          {!!row.missile_orders?.length&&<details open><summary>导弹准备计划（{row.missile_orders.length} 项）</summary>
            <ol>{row.missile_orders.map((o,i)=><li key={i}>{ship.module_names[o.module_id]} · {{assemble:'组装',load:'装满发射器',unload:'卸弹',dismantle:'拆解',cancel:'取消作业',warhead:'切换战斗部',model:'选择型号',fill_same_class:'补满同类'}[o.kind]??o.kind}{o.quantity?` ${o.quantity} 枚`:''}</li>)}</ol>
            <button onClick={()=>void run(()=>missile({kind:'clear_plan'}))}>清空本舰导弹准备计划</button></details>}
        </>}
      </fieldset>}
      {preview&&!preview.can_commit&&<div role="alert"><h3>准备尚未通过核对</h3><ul>{preview.issues.map((issue,i)=>{const detail=packet?.ships.find(s=>s.instance_id===issue.instance_id);return <li key={i}>{detail?.name??'共享供给'} · {issue.target==='ammunition'?'弹药资源':detail?.module_names[issue.target]??goodName(issue.target.replace(/^cargo:/,''))}：{preparationError(issue.message)}{issue.missing!==undefined?`，缺少 ${issue.missing}`:''}</li>;})}</ul></div>}
      {output&&<section aria-label="准备结果"><h3>{result?'已保存准备结果':'准备变动预览'}</h3>{output.preparation_elapsed_steps!==undefined&&<p>本次准备耗时 {(output.preparation_elapsed_steps/60).toFixed(1)} 秒；各舰同步准备，以最长用时为准。</p>}<p>供给弹药：{output.supply_before.ammunition_resources} → {output.supply_after.ammunition_resources}</p>
        {output.supply_before.cargo.map(c=>{const after=output.supply_after.cargo.find(g=>g.good_id===c.good_id)?.quantity??0;return after!==c.quantity&&<p key={c.good_id}>供给{goodName(c.good_id)}：{c.quantity} → {after}</p>;})}
        {output.ships.map((s,i)=>{const detail=packet?.ships.find(v=>v.instance_id===s.after.state.instance_id);return <article key={s.after.state.instance_id}><h4>{detail?.name??'舰船'} · {i+1}</h4>
          {s.repairs?.map(r=><p key={r.target_id}>{r.name}：耐久 {r.before.toFixed(1)} → {r.after.toFixed(1)}，消耗 {r.engineering_parts} 份供给工程零件。</p>)}
          <div className="propulsion-tables"><table><thead><tr><th>物资</th><th>准备前</th><th>准备后</th><th>原因</th></tr></thead><tbody>{resourceRows({...s,module_names:detail?.module_names??{}}).map(r=><tr key={r.key}><td>{goodName(r.name)}</td><td>{r.before}</td><td>{r.after}</td><td>{r.detail.replaceAll('discard','弃置')}</td></tr>)}</tbody></table></div>
          <p>准备后货物占用 {(s.capacity_after.used_volume_cm3/1e6).toFixed(1)} / {(s.capacity_after.capacity_cm3/1e6).toFixed(1)} m³{s.capacity_after.over_capacity?' · 保留战损超容库存':''}</p></article>;})}
        {result&&!embedded&&<><p>准备已保存。以当前所选舰船为旗舰进入交战，其余参战友舰自动交战。战术推进不消耗燃料。</p>
          <button disabled={busy||!active||!shipId||!onEnter||!!packet?.stale_error} onClick={()=>onEnter?.({preparation_id:result.preparation_id,launch_id:`launch.${crypto.randomUUID()}`,direct_instance_id:shipId})}>以所选舰为旗舰进入交战</button></>}
      </section>}
    </>}
  </section>;
}
