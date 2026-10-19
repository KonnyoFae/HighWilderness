import {useState} from 'react';
import type {FireControlIntent,ObservedContact,ObservationShip} from './FireControlPanel';
import type {TargetDirectory} from './model';
import {targetRows,targetPage,TARGET_CATEGORIES,TARGET_LABELS} from './targetDirectory';
import type {TargetRow} from './targetDirectory';

const layers:Record<string,string>={upper:'上层',cloud:'云层',rain:'雨层'};
interface Props {
  directory?:TargetDirectory;ship?:ObservationShip;names:Record<string,string>;memorySeconds:number;
  disabled:boolean;onCommand:(v:FireControlIntent)=>void;onFocus:(c:ObservedContact)=>void;
}
function Group({label,rows,activeCount,initiallyOpen,ship,disabled,onCommand,onFocus}:Props&{
  label:string;rows:TargetRow[];activeCount:number;initiallyOpen:boolean}) {
  const [open,setOpen]=useState(initiallyOpen),[requested,setPage]=useState(0);
  const page=targetPage(rows,requested);
  return <section className="target-directory-group">
    <button type="button" className="target-group-heading" aria-expanded={open} onClick={()=>setOpen(!open)}>
      <span>{open?'▾':'▸'} {label}</span><small>{activeCount} 活动 · {rows.length} 条记录</small>
    </button>
    {open&&<>
      {!rows.length&&<p className="muted target-empty">此分类暂无符合条件的目标。</p>}
      {page.rows.map(row=>{
        const c=row.contact,locked=c?.valid&&ship?.locked_target_id===row.target_id;
        const title=!c?'尚未发现，无法定位或锁定':!c.valid?'失联：双击查看最后观测位置':
          !row.canLock?'已发现，但缺少舰级火控；双击定位':'为当前舰艇锁定目标；双击定位';
        return <button type="button" key={row.key} data-target-key={row.key} data-target-state={row.state}
          className={`fire-contact${row.state==='tracked'?'':' lost'}`} disabled={!c} aria-pressed={!!locked}
          title={title} onClick={()=>{if(!disabled&&row.canLock&&!locked)onCommand({kind:'lock',target:row.target_id,value:null});}}
          onDoubleClick={()=>{if(c)onFocus(c);}}>
          <span className="target-name">{row.name}</span>
          <span>{!c?'未发现':!c.valid?'失联':locked?(ship?.lock_status==='locked'?'已锁定':'锁定中'):!row.canLock?'无舰级火控':'跟踪中'}</span>
          {c&&<><small>{layers[c.height_layer]??c.height_layer} · {Math.hypot(...c.velocity_mps,c.vertical_speed_mps??0).toFixed(0)} m/s</small>
            <small>{c.age_s.toFixed(1)} 秒前观测</small></>}
        </button>;
      })}
      {page.pages>1&&<nav className="target-pagination" aria-label={`${label}分页`}>
        <button type="button" disabled={page.page===0} onClick={()=>setPage(page.page-1)}>上一页</button>
        <span>{page.page+1} / {page.pages}</span>
        <button type="button" disabled={page.page+1===page.pages} onClick={()=>setPage(page.page+1)}>下一页</button>
      </nav>}
    </>}
  </section>;
}
export function TargetDirectoryPanel(props:Props) {
  const [onlyTracked,setOnlyTracked]=useState(false);
  const rows=targetRows(props.directory,props.ship,props.names,props.memorySeconds);
  const active=rows.filter(r=>r.active),history=rows.filter(r=>!r.active);
  const filter=(r:TargetRow)=>!onlyTracked||r.state==='tracked';
  return <section className="fire-control-targets target-directory" aria-label="目标名册">
    <div className="target-directory-heading"><strong>目标名册</strong>
      <small>{props.directory?`${active.length} 个活动目标`:'当前观测记录'} · 双击定位</small></div>
    <div className="target-directory-filter" role="group" aria-label="目标过滤">
      <button type="button" aria-pressed={!onlyTracked} onClick={()=>setOnlyTracked(false)}>全部</button>
      <button type="button" aria-pressed={onlyTracked} onClick={()=>setOnlyTracked(true)}>有有效观测</button>
    </div>
    {!props.ship&&<p className="muted">请选择己方舰艇查看观测。</p>}
    {!rows.length&&<p className="muted">暂无目标记录。</p>}
    {TARGET_CATEGORIES.map(category=><Group {...props} key={category} label={TARGET_LABELS[category]}
      rows={active.filter(r=>r.category===category&&filter(r))} activeCount={active.filter(r=>r.category===category).length}
      initiallyOpen={category==='ship'||category==='aircraft'}/>)}
    {history.length>0&&<Group {...props} label="历史观测" rows={history.filter(filter)} activeCount={0} initiallyOpen={false}/>}
    {props.ship&&props.ship.locked_target_id!==null&&<button disabled={props.disabled}
      onClick={()=>props.onCommand({kind:'lock',target:null,value:null})}>解除火控锁定</button>}
  </section>;
}
