import {useEffect,useRef,useState} from 'react';
import type {BridgeTransport} from '../bridge/transport';
import {normalizeHostFailure} from '../bridge/model';
import type {TacticalRequest} from './model';

type Entry={id:string;kind:'aircraft'|'pilot';name:string;claimable:boolean;volume_m3:number|null;origin:null|{height_layer:string;position_m:number[]};asset:{condition?:string;health?:string;modifiers?:Record<string,number>}};
type Pool={pool_id:string;scene_id:string;revision:number;entries:Entry[]};
type Packet={scene_revision:number;can_claim:boolean;legacy_count:number;pools:Pool[];receivers:{instance_id:string;revision:number;name:string}[]};
type Claim={request_id:string;pool_id:string;pool_revision:number;scene_revision:number;receiver_instance_id:string;receiver_revision:number;entry_ids:string[]};
type Receipt={aircraft:number;pilots:number;capacity_after:{used_volume_cm3:number;capacity_cm3:number};supply_inputs:{temporary_cargo:number;wounded:number}[]};

export function SalvagePanel({transport,instance,disabled,onBusy,onChanged}:{transport:BridgeTransport;instance:string;disabled:boolean;onBusy:(v:boolean)=>void;onChanged:()=>Promise<void>}){
  const [packet,setPacket]=useState<Packet|null>(null),[poolId,setPoolId]=useState(''),[receiver,setReceiver]=useState(''),[selected,setSelected]=useState<string[]>([]);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[retry,setRetry]=useState<(()=>Promise<void>)|null>(null);
  const [preview,setPreview]=useState<{request:Claim;receipt:Receipt}|null>(null),[open,setOpen]=useState(false);const pending=useRef(false),alive=useRef(true);
  const call=<T,>(method:string,params:Record<string,unknown>={})=>transport.tactical<T>({backend_instance_id:instance,method:`tactical.preparation.${method}` as TacticalRequest['method'],params,session_id:null,expected_revision:null});
  async function refresh(){const p=await call<Packet>('salvage_read');if(alive.current){setPacket(p);setPreview(null);setSelected([]);}}
  async function run(job:()=>Promise<void>){
    if(pending.current)return;pending.current=true;setBusy(true);onBusy(true);setError('');setRetry(null);
    try{await job();}catch(e){if(alive.current){setError(normalizeHostFailure(e).message);setRetry(()=>job);}}
    finally{pending.current=false;if(alive.current){setBusy(false);onBusy(false);}}
  }
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{if(open&&!disabled&&!packet&&!error&&!pending.current)void run(refresh);},[open,disabled,packet,error]);
  const pool=packet?.pools.find(p=>p.pool_id===poolId)??packet?.pools[0],ship=packet?.receivers.find(r=>r.instance_id===receiver)??packet?.receivers[0];
  const keys=selected.filter(k=>pool?.entries.some(e=>e.id===k&&e.claimable));
  const locked=disabled||busy||!!retry;
  return <details className="salvage-panel" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>航空打捞池</summary>
    <p>保存战果后，未接收的飞机和幸存飞行员独立留存。此测试入口只接收本方资源；战略派船、耗时和搜救另行接入。</p>
    <button disabled={disabled||busy} onClick={()=>void run(refresh)}>刷新航空打捞池</button>
    {error&&<p role="alert">{error}</p>}{retry&&<button disabled={busy} onClick={()=>void run(retry)}>重试打捞操作</button>}
    {notice&&<p role="status">{notice}</p>}
    {packet&&!packet.can_claim&&<p>请先退出舰内物资草稿，再领取打捞资源。</p>}
    {!!packet?.legacy_count&&<p>旧舰存档中还有 {packet.legacy_count} 项待打捞资源。<button disabled={locked||!packet.can_claim} onClick={()=>{const request_id=`salvage.migrate.${crypto.randomUUID()}`;void run(async()=>{const r=await call<{migrated:number}>('salvage_migrate',{request_id});await refresh();await onChanged();setNotice(`已将 ${r.migrated} 项旧记录移交到独立打捞池。`);});}}>归集旧档待打捞资源</button></p>}
    {packet&&!packet.pools.length&&<p>暂无已保存的航空打捞资源。</p>}
    {pool&&<>
      <label>战后打捞记录<select aria-label="战后打捞记录" disabled={locked} value={pool.pool_id} onChange={e=>{setPoolId(e.target.value);setSelected([]);setPreview(null);}}>{packet!.pools.map((p,i)=><option key={p.pool_id} value={p.pool_id}>战后记录 {i+1} · 剩余 {p.entries.length} 项</option>)}</select></label>
      {!pool.entries.length&&<p>本记录的资源已全部接收。</p>}
      {pool.entries.map(e=><label key={e.id} style={{display:'block'}}><input type="checkbox" disabled={locked||!e.claimable} checked={keys.includes(e.id)} onChange={v=>{setSelected(v.target.checked?[...keys,e.id]:keys.filter(k=>k!==e.id));setPreview(null);}}/>
        {e.name} #{e.id.split('.').at(-1)} · {e.kind==='aircraft'?`${e.asset.condition==='damaged'?'受损机':'完好机'} · ${e.volume_m3} m³`:e.asset.health==='wounded'?'受伤':'健康'}
        {!!Object.keys(e.asset.modifiers??{}).length&&' · 保留特殊飞行员能力'}{!e.claimable&&' · 敌方，暂不可领取'}
        {e.origin?` · ${e.origin.height_layer==='upper'?'上层':e.origin.height_layer==='cloud'?'云层':'雨层'} (${e.origin.position_m.map(n=>n.toFixed(0)).join(', ')})`:' · 旧记录未保存位置'}
      </label>)}
      <label>接收舰<select aria-label="打捞接收舰" disabled={locked} value={ship?.instance_id??''} onChange={e=>{setReceiver(e.target.value);setPreview(null);}}>{packet!.receivers.map(r=><option key={r.instance_id} value={r.instance_id}>{r.name} · {r.instance_id}</option>)}</select></label>
      {!ship&&<p>请在本方编队加入一艘可接收航空货物的空闲舰艇。</p>}
      <button disabled={locked||!packet?.can_claim||!keys.length||!ship} onClick={()=>{
        const request:Claim={request_id:`salvage.claim.${crypto.randomUUID()}`,pool_id:pool.pool_id,pool_revision:pool.revision,scene_revision:packet!.scene_revision,receiver_instance_id:ship!.instance_id,receiver_revision:ship!.revision,entry_ids:keys};
        void run(async()=>{const receipt=await call<Receipt>('salvage_preview',request);setPreview({request,receipt});});
      }}>核对接收容量</button>
      {preview&&<><p>接收 {preview.receipt.aircraft} 架飞机、{preview.receipt.pilots} 名飞行员；含退弹后货舱 {(preview.receipt.capacity_after.used_volume_cm3/1e6).toFixed(1)} / {(preview.receipt.capacity_after.capacity_cm3/1e6).toFixed(1)} m³。临时住货舱 {preview.receipt.supply_inputs.reduce((n,s)=>n+s.temporary_cargo,0)} 人。受损机仍需修复，伤员保持受伤。</p>
        <button disabled={locked||!packet?.can_claim} onClick={()=>{const request=preview.request;void run(async()=>{const r=await call<Receipt>('salvage_claim',request);await refresh();await onChanged();setNotice(`已接收 ${r.aircraft} 架飞机、${r.pilots} 名飞行员并保存。`);});}}>接收所选资源</button></>}
    </>}
  </details>;
}
