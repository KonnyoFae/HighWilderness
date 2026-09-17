import { useCallback, useEffect, useRef, useState } from 'react';
import type { BridgeTransport } from '../bridge/transport';
import { normalizeHostFailure } from '../bridge/model';
import type { TacticalRequest } from './model';
import type { PreparationLibrary, PreparedLaunch } from './preparation';
import { goodName } from './preparation';
import { PreparationPanel } from './PreparationPanel';
import { PreparationCanvas } from './PreparationCanvas';
import { addToScene, changeScene, moduleTab, preparationTabs, sideName } from './preparationScene';
import type { FleetSide, PreparationScene, PreparationTab, ScenePacket } from './preparationScene';
import { LiftReserve } from '../LiftReserve';

export function PreparationWorkspace({transport,instance,active,onEnter}: {
  transport:BridgeTransport;instance:string;active:boolean;onEnter:(launch:PreparedLaunch)=>void;
}) {
  const [packet,setPacket]=useState<ScenePacket|null>(null),[library,setLibrary]=useState<PreparationLibrary|null>(null);
  const [side,setSide]=useState<FleetSide>('player'),[shipId,setShipId]=useState(''),[moduleId,setModuleId]=useState<string|null>(null);
  const [tab,setTab]=useState<PreparationTab>('devices'),[source,setSource]=useState('');
  const [busy,setBusy]=useState(false),[editorBusy,setEditorBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [distance,setDistance]=useState('1'),[supply,setSupply]=useState({ammunition_resources:1000000,goods_quantity:100000,fuel_units:10000000});
  const [retry,setRetry]=useState<(()=>Promise<void>)|null>(null);
  const [readyRevision,setReadyRevision]=useState<number|null>(null);
  const pending=useRef(false),alive=useRef(true),sceneRef=useRef<PreparationScene|null>(null);
  const sideRef=useRef(side);sideRef.current=side;
  const importing=useRef<{side:FleetSide;label:string;instance_id:string;source:{kind:string;value:string}}|null>(null);
  const call=<T,>(suffix:string,params:Record<string,unknown>={})=>transport.tactical<T>({backend_instance_id:instance,
    method:`tactical.preparation.${suffix}` as TacticalRequest['method'],params,session_id:null,expected_revision:null});
  async function refresh() {
    const p=await call<ScenePacket>('scene_read');const l=await call<PreparationLibrary>('library');
    if(!alive.current)return;
    sceneRef.current=p.scene;setPacket(p);setLibrary(l);setDistance(String(p.scene.distance_m/1000));
    setShipId(id=>p.scene.sides.some(s=>s.ships.some(m=>m.instance_id===id))?id:p.scene.sides.find(s=>s.id===sideRef.current)?.ships[0]?.instance_id??'');
  }
  async function run(job:()=>Promise<void>) {
    if(pending.current||!active)return;pending.current=true;setBusy(true);setError('');setRetry(null);
    try{await job();}catch(e){if(alive.current){setError(normalizeHostFailure(e).message);setRetry(()=>job);}}
    finally{pending.current=false;if(alive.current)setBusy(false);}
  }
  useEffect(()=>{alive.current=true;void run(refresh);return()=>{alive.current=false;};},[]);
  async function save(next:PreparationScene) {
    const saved=await call<PreparationScene>('scene_save',{scene:next,expected_revision:next.revision-1});
    sceneRef.current=saved;if(alive.current)setPacket(p=>p?{...p,scene:saved}:p);
  }
  function edit(update:(next:PreparationScene)=>void) {
    if(!packet)return;const next=changeScene(packet.scene,update);void run(async()=>{await save(next);setNotice('编队配置已保存。');});
  }
  async function importShip(file:boolean) {
    if(!importing.current){
      const chosenSide=side;let src={kind:'resource',value:source||library?.sources[0]?.key||''};
      let label=library?.sources.find(s=>s.key===src.value)?.name??'目录设计';
      if(file){const grant=await transport.chooseFile({backend_instance_id:instance,method:'open',params:{},session_id:null,expected_revision:null});
        if(!grant)return;src={kind:'file',value:grant.destination_handle};label=grant.label;}
      importing.current={side:chosenSide,label,source:src,instance_id:`instance.preparation.${crypto.randomUUID()}`};
    }
    const task=importing.current;setNotice(`正在加载「${task.label}」并加入${sideName(task.side)}编队…`);
    await call('import',{instance_id:task.instance_id,source:task.source});
    const current=sceneRef.current!;
    const next=addToScene(current,task.side,task.instance_id);
    if(next!==current)await save(next);
    await refresh();if(sideRef.current===task.side){setShipId(task.instance_id);setModuleId(null);}
    importing.current=null;setNotice(`已加入${sideName(task.side)}编队。可在画布拖动排布。`);
  }
  const lock=busy||editorBusy||!!retry||!active;
  const selectedFleet=packet?.scene.sides.find(s=>s.ships.some(m=>m.instance_id===shipId));
  const selectedMember=selectedFleet?.ships.find(m=>m.instance_id===shipId);
  const selectedShape=packet?.geometry.ships.find(s=>s.id===shipId);
  const selectedState=packet?.ships.find(s=>s.instance_id===shipId);
  const selectedModule=selectedShape?.modules.find(m=>m.id===moduleId);
  const ids=packet?.scene.sides.flatMap(s=>s.ships.map(m=>m.instance_id))??[];
  const preparation=packet?.scene.preparation_id;
  useEffect(()=>setReadyRevision(null),[preparation]);
  const canAdd=!lock&&!preparation&&ids.length<(packet?.limits.max_ships??16);
  function select(id:string,module:string|null) {
    if(editorBusy)return;
    const fleet=packet?.scene.sides.find(s=>s.ships.some(m=>m.instance_id===id));if(fleet)setSide(fleet.id);
    setShipId(id);setModuleId(module);
    const m=packet?.geometry.ships.find(s=>s.id===id)?.modules.find(m=>m.id===module);if(m)setTab(moduleTab(m.category));
  }
  const onEditorBusy=useCallback((value:boolean)=>setEditorBusy(value),[]);
  async function leavePreparation() {
    const current=sceneRef.current!;await save(changeScene(current,s=>{s.preparation_id=null;}));await refresh();
  }
  async function enter() {
    let current=sceneRef.current!;
    const launch_id=`encounter.test.${crypto.randomUUID()}`;
    // Clear only the layout's draft link; saved supply/ship receipts remain intact.
    if(current.preparation_id){
      if(readyRevision!==null)await call('discard',{preparation_id:current.preparation_id,revision:readyRevision});
      current=changeScene(current,s=>{s.preparation_id=null;});await save(current);
    }
    const encounter=await call<Record<string,unknown>>('scene_encounter',{revision:current.revision,launch_id});
    onEnter({launch_id,encounter,preparation_id:'test.scene',direct_instance_id:current.sides.find(s=>s.id==='player')!.flagship_instance_id!});
  }
  const ready=!!packet&&packet.scene.sides.every(s=>s.ships.length>0)&&(preparation?readyRevision!==null:packet.ships.every(s=>s.revision>0));
  return <section className="preparation-workspace" aria-label="双方战前准备">
    <header className="preparation-toolbar"><div><small>战术测试 / 编队与补给</small><h2>战前准备</h2></div>
      <div className="formation-tabs" role="tablist" aria-label="编辑阵营">{(['player','enemy'] as const).map(id=><button key={id} role="tab" aria-selected={side===id} onClick={()=>{setSide(id);if(!editorBusy){setShipId(packet?.scene.sides.find(s=>s.id===id)?.ships[0]?.instance_id??'');setModuleId(null);}}}>{sideName(id)}</button>)}</div>
      <label>初始交战距离 <input aria-label="初始交战距离（公里）" type="number" min=".001" max="1000" step=".1" value={distance} disabled={lock} onChange={e=>setDistance(e.target.value)}/> 公里</label>
      <button disabled={lock||!packet} onClick={()=>{
        const n=Number(distance);if(!distance.trim()||!Number.isFinite(n)||n<.001||n>1000){setError('初始交战距离须为 0.001—1000 公里。');return;}
        edit(s=>{s.distance_m=n*1000;});}}>应用距离</button>
      <button className="primary" disabled={lock||!ready} onClick={()=>void run(enter)}>按当前编队进入交战</button>
    </header>
    <div className="preparation-feedback" aria-live="polite">{notice&&<p role="status">{notice}</p>}
      {busy&&!importing.current&&<p role="status">正在处理，请稍候…</p>}
      {error&&<p role="alert">{error} {retry&&<button disabled={busy} onClick={()=>void run(retry)}>重试本次操作</button>}</p>}
      {error&&importing.current&&<button disabled={busy} onClick={()=>{importing.current=null;setRetry(null);setError('');setNotice('已结束本次导入；可重新选择文件。');}}>取消本次导入</button>}
      {retry&&!importing.current&&<button disabled={busy} onClick={()=>void run(refresh)}>重新读取编队</button>}
    </div>
    <div className="preparation-stage">
      <aside className="preparation-float preparation-add" aria-label="加入舰船">
        <h3>加入舰船</h3><p>加入到 <strong>{sideName(side)}</strong> · 共 {ids.length} / {packet?.limits.max_ships??16} 艘</p>
        <button disabled={!canAdd} onClick={()=>void run(()=>importShip(true))}>导入栖装文件</button>
        <label>目录设计<select aria-label="准备目录设计" value={source||library?.sources[0]?.key||''} onChange={e=>setSource(e.target.value)}>
          {library?.sources.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
        <button disabled={!canAdd||!library?.sources.length} onClick={()=>void run(()=>importShip(false))}>从目录加入{sideName(side)}</button>
        {preparation&&<p className="muted">完成保存或放弃当前物资草稿后，可调整舰队成员。</p>}
        <details><summary>已有舰艇</summary>{library?.ships.filter(s=>!ids.includes(s.instance_id)).map(s=><button key={s.instance_id} disabled={!canAdd||s.blocked}
          onClick={()=>void run(async()=>{await save(addToScene(sceneRef.current!,side,s.instance_id));await refresh();select(s.instance_id,null);})}>{s.name}{s.blocked?' · 占用中':''}</button>)}</details>
        <h3>双方舰队</h3>{packet?.scene.sides.map(f=><div key={f.id} className={`preparation-roster ${f.id}`}><strong>{sideName(f.id)}</strong>
          {f.ships.map((m,i)=><button key={m.instance_id} className={shipId===m.instance_id?'selected':''} disabled={editorBusy} onClick={()=>select(m.instance_id,null)}>
            {m.instance_id===f.flagship_instance_id?'★ ':''}{packet.geometry.ships.find(s=>s.id===m.instance_id)?.name??'舰艇'} <small>{i+1}</small></button>)}</div>)}
        <details className="test-supply"><summary>测试供给池</summary><p>仅显式补充；不改变舰内库存。</p>
          {(['ammunition_resources','goods_quantity','fuel_units'] as const).map(k=><label key={k}>{({ammunition_resources:'弹药资源',goods_quantity:'每种货物',fuel_units:'灵烷燃料'})[k]}
            <input type="number" min="0" step="1" value={supply[k]} disabled={lock||!!preparation} onChange={e=>setSupply(s=>({...s,[k]:Number(e.target.value)}))}/></label>)}
          <button disabled={lock||!!preparation} onClick={()=>{const params={...supply,operation_id:`supply.${crypto.randomUUID()}`};void run(async()=>{
            await call('supply_replenish',params);await refresh();setNotice('已补充测试供给池。舰艇库存未改动。');});}}>补充到上述数量</button>
          <p>现有弹药 {packet?.supply.ammunition_resources.toLocaleString()} · 燃料 {packet?.supply.fuel_units?.toLocaleString()}</p>
          {packet?.supply.cargo.map(g=><p key={g.good_id}>{goodName(g.good_id)} {g.quantity.toLocaleString()}</p>)}</details>
      </aside>
      <div className="preparation-views">{packet?.scene.sides.map(f=><PreparationCanvas key={f.id} fleet={f} geometry={packet.geometry}
        selected={shipId} moduleId={moduleId} disabled={lock} onSelect={select} onMove={(id,x,y)=>{
          if(Math.abs(x)>100000||Math.abs(y)>100000){setError('舰队局部位置不能超过 100 公里。');return;}
          edit(s=>{const m=s.sides.flatMap(f=>f.ships).find(m=>m.instance_id===id)!;m.x_m=x;m.y_m=y;});}}/>)}</div>
      <aside className="preparation-float preparation-details" aria-label="战前部件面板">
        <header><small>{selectedFleet?sideName(selectedFleet.id):'选择舰艇'}</small><h3>{selectedShape?.name??'部件与舰内物资'}</h3></header>
        {selectedMember&&selectedFleet&&<details className="formation-properties"><summary>编队位置与旗舰</summary>
          <p>局部坐标 ({selectedMember.x_m}, {selectedMember.y_m}) 米 · 航向 {(selectedMember.heading_rad*180/Math.PI).toFixed(0)}°</p>
          {(['x_m','y_m','heading_rad'] as const).map(k=><label key={`${shipId}:${k}:${selectedMember[k]}`}>{k==='heading_rad'?'航向（度）':k==='x_m'?'横向（米）':'纵向（米）'}
            <input type="number" aria-label={k==='heading_rad'?'舰艇航向（度）':k==='x_m'?'舰艇横向（米）':'舰艇纵向（米）'} disabled={lock} defaultValue={k==='heading_rad'?Math.round(selectedMember[k]*180/Math.PI):selectedMember[k]}
              onBlur={e=>{const n=Number(e.target.value);if(!e.target.value.trim()||!Number.isFinite(n)||Math.abs(n)>(k==='heading_rad'?180:100000)){setError('位置须在 ±100 公里内，航向须在 ±180° 内。');e.target.value=String(k==='heading_rad'?Math.round(selectedMember[k]*180/Math.PI):selectedMember[k]);return;}
                const value=k==='heading_rad'?n*Math.PI/180:n;if(Math.abs(value-selectedMember[k])>1e-9)edit(s=>{s.sides.flatMap(f=>f.ships).find(m=>m.instance_id===shipId)![k]=value;});}}/></label>)}
          <button disabled={lock||selectedFleet.flagship_instance_id===shipId} onClick={()=>edit(s=>{s.sides.find(f=>f.id===selectedFleet.id)!.flagship_instance_id=shipId;})}>设为{sideName(selectedFleet.id)}旗舰</button>
          <button disabled={lock||!!preparation} onClick={()=>void run(async()=>{
            await save(changeScene(sceneRef.current!,s=>{const f=s.sides.find(f=>f.id===selectedFleet.id)!;f.ships=f.ships.filter(m=>m.instance_id!==shipId);if(f.flagship_instance_id===shipId)f.flagship_instance_id=f.ships[0]?.instance_id??null;}));
            setModuleId(null);await refresh();})}>移出编队</button></details>}
        <nav className="preparation-detail-tabs" aria-label="部件分类">{preparationTabs.map(t=><button key={t.id} aria-pressed={tab===t.id} onClick={()=>{setTab(t.id);setModuleId(null);}}>{t.name}</button>)}</nav>
        {selectedModule&&<div className="selected-preparation-module"><strong>{selectedModule.name}</strong><p>甲板 {selectedModule.deck_level} · 耐久 {selectedState?.state.modules.find(m=>m.module_id===moduleId)?.durability_points.toFixed(1)} / {selectedModule.max_durability}</p><button onClick={()=>setModuleId(null)}>查看本舰此页全部部件</button></div>}
        {!preparation&&<>
          {tab==='devices'&&<LiftReserve value={selectedState?.lift_reserve}/>}
          {tab==='missiles'?<p>导弹型号、发射器与导弹库在 5c 阶段接入。</p>:<p>点击画布上的部件查看。物资草稿同时保存双方配置，核对通过后统一扣料。</p>}
          <button disabled={lock||!ids.length} onClick={()=>{const next=changeScene(sceneRef.current!,s=>{s.preparation_id=`preparation.${crypto.randomUUID()}`;});void run(()=>save(next));}}>配置双方舰内物资</button>
          <p className="muted">进入物资准备后，可补满单个部件或本舰同类部件，并消耗工程零件修复未毁部件。</p>
          {!ready&&ids.length>0&&<p>双方各加入至少一艘舰艇，并完成物资准备后可开始测试。</p>}
        </>}
        {preparation&&<PreparationPanel key={preparation} transport={transport} instance={instance} active={active} onBusy={onEditorBusy}
          embedded={{preparationId:preparation,instanceIds:ids,shipId,moduleId,tab,
            onSaved:revision=>{setReadyRevision(revision);setNotice('双方物资已保存，可按当前编队进入交战。');void run(refresh);},
            onLeave:()=>{void run(leavePreparation);}}}/>}
      </aside>
    </div>
    <p className="formation-distance-note">旗舰间距：{((packet?.scene.distance_m??0)/1000).toLocaleString()} 公里。上下视区分别显示舰队局部排布，屏幕间隔不代表实际交战距离。</p>
  </section>;
}
