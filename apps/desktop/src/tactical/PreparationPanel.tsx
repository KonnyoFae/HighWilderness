import { useEffect, useRef, useState } from 'react';
import type { BridgeTransport } from '../bridge/transport';
import { normalizeHostFailure } from '../bridge/model';
import { changeDraft, goodName, preparationError, quantity } from './preparation';
import type { PreparedLaunch, PreparationDraft, PreparationLibrary, PreparationPacket, PreparationPreview, PreparationResult, WeaponChoice } from './preparation';
import type { TacticalRequest } from './model';
import { resourceRows, fuelTankName } from './settlement';
import { ammunitionName } from './ammunition';

export function PreparationPanel({transport,instance,active,onBusy,onEnter}: {
  transport: BridgeTransport; instance: string; active: boolean; onBusy: (value:boolean)=>void;
  onEnter?: (launch:PreparedLaunch)=>void;
}) {
  const [library,setLibrary]=useState<PreparationLibrary|null>(null), [packet,setPacket]=useState<PreparationPacket|null>(null);
  const [draft,setDraft]=useState<PreparationDraft|null>(null), [savedRevision,setSavedRevision]=useState(-1);
  const [selected,setSelected]=useState<string[]>([]), [shipId,setShipId]=useState(''), [source,setSource]=useState('');
  const [preview,setPreview]=useState<PreparationPreview|null>(null), [result,setResult]=useState<PreparationResult|null>(null);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [uncertain,setUncertain]=useState(false);
  const [draftUncertain,setDraftUncertain]=useState(false);
  const [status,setStatus]=useState('');
  const working=useRef(false), mounted=useRef(true), failedSave=useRef(-1);
  const pendingImport=useRef<{instance_id:string;source:{kind:string;value:string}}|null>(null);
  const pendingOpen=useRef<{preparation_id:string;instance_ids:string[]}|null>(null);
  const dirty=!!draft && draft.revision!==savedRevision;
  useEffect(()=>{ mounted.current=true; return ()=>{mounted.current=false;}; },[]);
  useEffect(()=>{onBusy(busy || dirty || uncertain); return ()=>onBusy(false);},[busy,dirty,uncertain,onBusy]);
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
  useEffect(()=>{if(active)void run(refresh);},[active]);
  function accept(next:PreparationPacket) {
    if(!mounted.current)return;
    setPacket(next);setDraft(next.draft);setSavedRevision(next.draft.revision);setPreview(null);setResult(next.receipt);
    setShipId(next.draft.ships[0]?.instance_id??'');setUncertain(false);setDraftUncertain(false);failedSave.current=-1;
    setStatus(next.receipt?'准备已保存。':'准备草稿已恢复，库存尚未扣费。');
  }
  async function saveDraft() {
    if(!draft || !dirty)return;
    try {
      const next=await call<PreparationDraft>('draft',{draft,expected_saved_revision:savedRevision});
      if(mounted.current){setSavedRevision(next.revision);setDraftUncertain(false);setStatus('草稿已保存，尚未扣费。');}
    } catch(e) {setDraftUncertain(true);throw e;}
  }
  useEffect(()=>{
    if(!active || busy || !draft || !dirty || uncertain || result || failedSave.current===draft.revision)return;
    const timer=window.setTimeout(()=>void run(async()=>{try{await saveDraft();}catch(e){failedSave.current=draft.revision;throw e;}}),700);
    return ()=>window.clearTimeout(timer);
  },[active,busy,draft,dirty,uncertain,result]);
  function edit(update:(row:PreparationDraft['ships'][number])=>void) {
    if(!draft || busy || uncertain || draftUncertain || result)return;
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
    setResult(value);setUncertain(false);setStatus('准备已保存，库存与待发弹已更新。');await refresh();
  }
  const sourceShip=packet?.ships.find(s=>s.instance_id===shipId), row=draft?.ships.find(s=>s.instance_id===shipId);
  const savedShip=result?.ships.find(s=>s.after.state.instance_id===shipId);
  const ship=sourceShip&&savedShip?{...sourceShip,state:{...sourceShip.state,...savedShip.after.state,
    weapons:sourceShip.state.weapons.map(w=>({...w,...savedShip.after.state.weapons.find(v=>v.module_id===w.module_id)}))},capacity:savedShip.capacity_after}:sourceShip;
  const supply=result?.supply_after??packet?.supply;
  const locked=busy||uncertain||draftUncertain||!!result||!!packet?.stale_error;
  const output=result??preview?.result;
  return <section className="panel preparation-panel" aria-label="战前准备">
    <h2>战前准备</h2><p>逐舰管理货物和弹药，选择武器预装填与损管设备准备。草稿只记录选择；“保存准备”才会转移物资并完成准备。</p>
    <p className="muted">当前使用有限测试物资库：初始 1000 点弹药资源、每种货物 100 份，燃料账户初始 10000 单位，使用后不会自动补满。新建实例采用标准人员与部件燃料槽的技术装载预设，填充燃料槽初始为空。</p>
    {error&&<p role="alert">{error}</p>}{status&&<p role="status">{status}</p>}
    {uncertain&&<p role="alert">保存结果尚未确认。请重试保存或重新读取；在确认前不能修改或放弃这份准备。</p>}
    {draftUncertain&&<p role="alert">草稿保存尚未确认，请重试“保存准备草稿”；重新读取会放弃本地未确认的修改，以仓库版本为准。</p>}
    {!!library?.interrupted_battles&&<p role="status">此前有 {library.interrupted_battles} 场未结算交战因后台退出而中断，已恢复到入战前保存状态；战前准备费用不会返还。已生成的战后结算仍需保存。</p>}
    {!draft&&<fieldset disabled={busy||!active}>
      <legend>舰船选择</legend>
      <div className="editor-row"><button onClick={()=>void run(()=>importDesign(true))}>导入已保存的栖装文件</button>
        <label>目录设计<select aria-label="准备目录设计" value={source||library?.sources[0]?.key||''} onChange={e=>setSource(e.target.value)}>{library?.sources.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
        <button disabled={!library?.sources.length} onClick={()=>void run(()=>importDesign(false))}>从目录设计添加舰船</button>
        <button onClick={()=>void run(refresh)}>刷新准备列表</button></div>
      {pendingImport.current&&<p>导入尚未确认，再次导入会查询同一请求。<button onClick={()=>{pendingImport.current=null;setError('');}}>放弃本次导入重试</button></p>}
      {!library?.ships.length&&<p>尚无准备舰船，请先导入已保存的栖装设计。</p>}
      <div className="preparation-fleet">{library?.ships.map((s,i)=><label key={s.instance_id}><input type="checkbox" aria-label={`选择准备舰船 ${i+1}`} checked={selected.includes(s.instance_id)} disabled={s.blocked||!!pendingOpen.current}
        onChange={e=>setSelected(ids=>e.target.checked?[...ids,s.instance_id]:ids.filter(id=>id!==s.instance_id))}/>{s.name} · 舰船 {i+1} · 船壳 {(s.hull_integrity*100).toFixed(0)}%{s.blocked?' · 战斗或结算占用中':''}</label>)}</div>
      <button disabled={selected.length===0||selected.length>16} onClick={()=>void run(open)}>准备所选舰船</button>
      {pendingOpen.current&&<button onClick={()=>{pendingOpen.current=null;setStatus('可重新选择舰船；已创建的草稿仍在准备列表中。');void run(refresh);}}>重新选择舰船</button>}
      <h3>准备草稿与已保存记录</h3>
      {library?.drafts.map((d,i)=><p key={d.preparation_id}><button onClick={()=>void run(async()=>accept(await call<PreparationPacket>('read',{preparation_id:d.preparation_id})))}>打开准备 {i+1} · {d.saved?'已保存':'草稿'}</button></p>)}
    </fieldset>}
    {draft&&<>
      <div className="editor-row"><button disabled={busy||(dirty&&!draftUncertain)} onClick={()=>void run(async()=>accept(await call<PreparationPacket>('read',{preparation_id:draft.preparation_id})))}>重新读取准备</button>
        <button disabled={busy||uncertain||!!result||!dirty} onClick={()=>void run(saveDraft)}>保存准备草稿</button>
        <button disabled={busy||uncertain||!!result||!!packet?.stale_error} onClick={()=>void run(async()=>{await saveDraft();setPreview(await call<PreparationPreview>('preview',{preparation_id:draft.preparation_id,revision:draft.revision}));})}>核对资源与预装填</button>
        <button disabled={busy||!!result||(!uncertain&&(!preview?.can_commit||dirty))} onClick={()=>void run(submit)}>{uncertain?'重试保存准备':'保存准备'}</button>
        <button disabled={busy||uncertain||dirty} onClick={()=>{setPacket(null);setDraft(null);setPreview(null);setResult(null);setStatus('');void run(refresh);}}>返回舰船列表</button>
        <button disabled={busy||uncertain} onClick={()=>void run(async()=>{await call('discard',{preparation_id:draft.preparation_id,revision:savedRevision});setDraft(null);setPacket(null);setPreview(null);setResult(null);await refresh();setStatus('准备入口已移除，舰船实际库存保持原样。');})}>{result?'移除此准备入口':'放弃准备草稿'}</button>
      </div>
      {packet?.stale_error&&<p role="alert">{packet.stale_error}。原草稿已保留，可放弃后重新选择舰船准备。</p>}
      {supply&&<p>{result?'本次准备后供给':'可用供给'}：弹药资源 {supply.ammunition_resources} 点{supply.cargo.map(c=>` · ${goodName(c.good_id)} ${c.quantity} 份`)}</p>}
      <label>当前准备舰船<select aria-label="当前准备舰船" value={shipId} onChange={e=>setShipId(e.target.value)}>{packet?.ships.map((s,i)=><option key={s.instance_id} value={s.instance_id}>{s.name} · 舰船 {i+1}</option>)}</select></label>
      {ship&&row&&<fieldset disabled={locked}>
        <legend>{ship.name}</legend>
        <p>船壳 {(ship.state.hull_integrity_fraction*100).toFixed(0)}% · 燃料 {ship.state.fuel_units} · 人员 {ship.state.crew.reduce((n,c)=>n+c.count,0)}。准备不会修复已有战损。</p>
        {ship.resources.ignition_decks&&<section aria-label="本舰防火配置"><h3>防火配置</h3>
          {ship.resources.ignition_decks.map(d=><p key={d.deck_id}>第 {d.deck_level} 层：{d.multiplier<1?`起火概率降低 ${((1-d.multiplier)*100).toFixed(0)}%`:'无额外防火效果'}</p>)}
          <small>仅影响新的点燃尝试；不减轻已有火情。跨层部件按安装基准层归属；零净填充空间无增益。当前比例为测试数值。</small></section>}
        {!!row.fuel_tanks?.length&&<><h3>灵烷燃料</h3><p>当前燃料 {ship.state.fuel_units.toFixed(2)} 单位 · 可用供给 {supply?.fuel_units??0} 单位。发动机在战术中不耗油，仅燃料槽损毁时损失其中燃料。填充槽独立耐久，不提供升力。</p>
          {row.fuel_tanks.map((t,i)=>{const spec=ship.resources.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!,current=ship.state.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!;
            return <label className="preparation-input" key={t.tank_id}>{fuelTankName(spec,ship.module_names)} · 容量 {spec.capacity_units} · 耐久 {current.durability_points.toFixed(1)} / {spec.maximum_points}
              <input aria-label={`燃料槽 ${i+1} 装载目标`} type="number" min="0" max={spec.capacity_units} step="1" value={t.quantity_units} disabled={current.durability_points<=0}
                onChange={e=>edit(r=>{r.fuel_tanks!.find(v=>v.tank_id===t.tank_id)!.quantity_units=quantity(e.target.value);})}/></label>;})}</>}
        <h3>弹药资源</h3><p>数量为预装填前的装载目标；武器装填后会扣除相应资源。</p>
        {row.magazines.map((m,i)=><label className="preparation-input" key={m.module_id}>{ship.module_names[m.module_id]} · 库 {i+1} · 容量 {ship.resources.magazines.find(v=>v.module_id===m.module_id)?.capacity_resources}
          <input aria-label={`弹药库 ${i+1} 装载目标`} type="number" min="0" step="1" value={m.quantity} onChange={e=>edit(r=>{r.magazines.find(v=>v.module_id===m.module_id)!.quantity=quantity(e.target.value);})}/></label>)}
        {!row.magazines.length&&<p>本舰没有弹药库。</p>}
        <h3>货物</h3><p>当前已用 {(ship.capacity.used_volume_cm3/1e6).toFixed(1)} / {(ship.capacity.capacity_cm3/1e6).toFixed(1)} m³{ship.capacity.over_capacity?' · 战损超容，可保留或卸载现有货物':''}</p>
        {ship.resources.goods.map(g=><label className="preparation-input" key={g.id}>{goodName(g.id)} · 每份 {g.unit_volume_cm3/1e6} m³
          <input aria-label={`${goodName(g.id)}装载目标`} type="number" min="0" step="1" value={row.cargo.find(c=>c.good_id===g.id)?.quantity??0} onChange={e=>edit(r=>{const n=quantity(e.target.value),c=r.cargo.find(v=>v.good_id===g.id);if(c)c.quantity=n;else r.cargo.push({good_id:g.id,quantity:n});})}/></label>)}
        <h3>损管设备准备</h3><p>仅资源用尽的设备可以消耗工程零件准备。保留现有余量，战前准备不执行维修；战中启动后，资源耗尽会自动尝试再次准备。</p>
        {row.damage_controls?.map((d,i)=>{const spec=ship.resources.damage_controls?.find(v=>v.module_id===d.module_id), current=ship.state.damage_controls?.find(v=>v.module_id===d.module_id);
          if(!spec||!current)return null;
          const destroyed=(ship.state.modules.find(m=>m.module_id===d.module_id)?.durability_points??0)<=0;
          return <article className="preparation-weapon" key={d.module_id}><h4>{ship.module_names[d.module_id]} · 损管 {i+1}</h4>
            <p>资源 {current.quantity_units/1000} / {spec.capacity_units/1000} 点{destroyed?' · 设备已损毁':''}</p>
            <label><input type="checkbox" aria-label={`损管 ${i+1} 预准备`} checked={d.prepare} disabled={destroyed||current.quantity_units>0||!!current.preparation}
              onChange={e=>edit(r=>{r.damage_controls!.find(v=>v.module_id===d.module_id)!.prepare=e.target.checked;})}/>消耗 {spec.cargo_costs.map(c=>`${goodName(c.good_id)} ${c.quantity} 份`).join('、')}，准备至 {spec.capacity_units/1000} 点</label>
            <p>战中再次准备耗时 {(spec.preparation_steps/60).toFixed(1)} 秒；关闭设备会取消未完成准备并释放零件预留。</p>
          </article>;})}
        {!row.damage_controls?.length&&<p>{ship.resources.damage_controls?'本舰没有损管设备。':'本舰沿用旧资源配置。重新导入栖装设计可建立支持损管的新舰船实例。'}</p>}
        <h3>武器预装填</h3><p>保留现状不会卸下已有弹药。弃置换弹不会返还旧弹成本。</p>
        <div className="editor-row"><button onClick={()=>edit(r=>{for(const w of r.weapons){const recipe=ship.resources.weapons.find(v=>v.module_id===w.module_id)?.recipe_ids.find(id=>ship.enabled_recipe_ids.includes(id));if(recipe)Object.assign(w,{action:'preload',recipe_id:recipe,batches:1});}})}>本舰武器各预装一批</button>
          <button onClick={()=>edit(r=>{for(const w of r.weapons)Object.assign(w,{action:'keep',recipe_id:null,batches:0});})}>本舰武器全部保持现状</button></div>
        {row.weapons.map((w,i)=>{const spec=ship.resources.weapons.find(v=>v.module_id===w.module_id)!,current=ship.state.weapons.find(v=>v.module_id===w.module_id)!;
          const recipe=ship.resources.recipes.find(r=>r.id===(w.recipe_id??spec.recipe_ids[0]));
          return <article className="preparation-weapon" key={w.module_id}><h4>{ship.module_names[w.module_id]} · 武器 {i+1}</h4>
            <p>现有待发 {current.ready_rounds} / {spec.ready_capacity} 发 · {ammunitionName(current.recipe_id)} · 冷却 {(current.cooldown_steps/60).toFixed(1)} 秒</p>
            <div className="editor-row"><label>准备动作<select aria-label={`武器 ${i+1} 准备动作`} value={w.action} onChange={e=>edit(r=>{const c=r.weapons[i];c.action=e.target.value as WeaponChoice['action'];c.recipe_id=c.action==='keep'?null:spec.recipe_ids.find(id=>id==='recipe.x1a.ordinary'&&ship.enabled_recipe_ids.includes(id))??spec.recipe_ids.find(id=>ship.enabled_recipe_ids.includes(id))??null;c.batches=c.action==='keep'?0:1;})}><option value="keep">保留现状</option><option value="preload">补装整批弹药</option><option value="discard_and_preload">弃置现有弹药并重装</option></select></label>
            <label>弹药种类<select aria-label={`武器 ${i+1} 弹药种类`} disabled={w.action==='keep'} value={w.recipe_id??spec.recipe_ids[0]} onChange={e=>edit(r=>{r.weapons[i].recipe_id=e.target.value;})}>{spec.recipe_ids.map(id=><option key={id} value={id} disabled={!ship.enabled_recipe_ids.includes(id)}>{ammunitionName(id)}{!ship.enabled_recipe_ids.includes(id)?'（尚不可用）':''}</option>)}</select></label>
            <label>批次<input aria-label={`武器 ${i+1} 预装填批次`} type="number" min="1" max="10000" step="1" disabled={w.action==='keep'} value={w.batches} onChange={e=>edit(r=>{const n=quantity(e.target.value);if(n<1||n>10000)throw new Error('预装填批次须为 1—10000 的整数。');r.weapons[i].batches=n;})}/></label></div>
            {recipe&&w.action!=='keep'&&<p>预计装入 {recipe.rounds*w.batches} 发，消耗 {recipe.ammo_cost*w.batches} 点弹药资源{recipe.cargo_costs.map(c=>`、${goodName(c.good_id)} ${c.quantity*w.batches} 份`)}{w.action==='discard_and_preload'?`；另弃置已有 ${current.ready_rounds} 发`:''}。</p>}
          </article>;})}
        {!row.weapons.length&&<p>本舰没有可预装填武器。</p>}
        {!!row.weapons.length&&!ship.enabled_recipe_ids.includes('recipe.x1a.special_armor_piercing')&&<p>本舰沿用旧弹药配置。新导入的测试舰可选择穿甲弹，已有舰船的库存与战损继续保留。</p>}
      </fieldset>}
      {preview&&!preview.can_commit&&<div role="alert"><h3>准备尚未通过核对</h3><ul>{preview.issues.map((issue,i)=>{const detail=packet?.ships.find(s=>s.instance_id===issue.instance_id);return <li key={i}>{detail?.name??'共享供给'} · {issue.target==='ammunition'?'弹药资源':detail?.module_names[issue.target]??goodName(issue.target.replace(/^cargo:/,''))}：{preparationError(issue.message)}{issue.missing!==undefined?`，缺少 ${issue.missing}`:''}</li>;})}</ul></div>}
      {output&&<section aria-label="准备结果"><h3>{result?'已保存准备结果':'准备变动预览'}</h3><p>供给弹药：{output.supply_before.ammunition_resources} → {output.supply_after.ammunition_resources}</p>
        {output.ships.map((s,i)=>{const detail=packet?.ships.find(v=>v.instance_id===s.after.state.instance_id);return <article key={s.after.state.instance_id}><h4>{detail?.name??'舰船'} · {i+1}</h4>
          <div className="propulsion-tables"><table><thead><tr><th>物资</th><th>准备前</th><th>准备后</th><th>原因</th></tr></thead><tbody>{resourceRows({...s,module_names:detail?.module_names??{}}).map(r=><tr key={r.key}><td>{goodName(r.name)}</td><td>{r.before}</td><td>{r.after}</td><td>{r.detail.replaceAll('discard','弃置')}</td></tr>)}</tbody></table></div>
          <p>准备后货物占用 {(s.capacity_after.used_volume_cm3/1e6).toFixed(1)} / {(s.capacity_after.capacity_cm3/1e6).toFixed(1)} m³{s.capacity_after.over_capacity?' · 保留战损超容库存':''}</p></article>;})}
        {result&&<><p>准备已保存。以当前所选舰船为旗舰进入交战，其余参战友舰自动交战。战术推进不消耗燃料。</p>
          <button disabled={busy||!active||!shipId||!onEnter||!!packet?.stale_error} onClick={()=>onEnter?.({preparation_id:result.preparation_id,launch_id:`launch.${crypto.randomUUID()}`,direct_instance_id:shipId})}>以所选舰为旗舰进入交战</button></>}
      </section>}
    </>}
  </section>;
}
