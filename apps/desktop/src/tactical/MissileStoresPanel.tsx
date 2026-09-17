import type {MissileJob,MissileOrder,MissileProfile,MissileState} from './missiles';
import {jobName} from './missiles';
import {goodName} from './preparation';
import {MissilePerformance} from './MissilePerformance';

export function MissileStoresPanel({profile,state,names,selected,preparation=false,disabled=false,blocked={},onCommand}:{
  profile?:MissileProfile;state?:MissileState;names:Record<string,string>;selected?:string|null;
  preparation?:boolean;disabled?:boolean;blocked?:Record<string,string[]>;onCommand:(order:MissileOrder)=>void;
}) {
  if(!profile||!state||(!state.launchers.length&&!state.magazines.length))return <p>本舰没有导弹发射器或导弹库。可在舾装编辑器中安装新设备后重新导入。</p>;
  const modelName=(id:string)=>profile.models.find(m=>m.id===id)?.name??id;
  const jobs=(rows:MissileJob[])=>rows.map(j=><div className="missile-job" key={j.unit.serial}>
    <span>{jobName(j.kind)} · {j.unit.warhead_id==='blast'?'高爆':'燃烧'} · 剩余 {(j.remaining_work_steps/60).toFixed(1)} 秒（全效时）</span>
    <progress max={j.total_work_steps} value={j.total_work_steps-j.remaining_work_steps}/></div>);
  return <section className="missile-stores" aria-label="导弹后勤">
    <p className="muted">优先使用整装弹，库存不足时转用原料装填。更换战斗部需要先卸弹回收，再重新装填。</p>
    {preparation&&<p>操作按下方计划顺序完成；每座库内并行组装。核对只预览，保存准备时统一完成并扣费。</p>}
    {preparation&&<p className="muted">16 种常规导弹和专用小型拦截弹均可入战。专用拦截发射器默认自动防御；VLS 装载拦截弹后仍默认关闭自动发射。</p>}
    {[...state.launchers,...state.magazines].filter(r=>!selected||r.module_id===selected).map(row=>{
      const launcher='ready' in row,spec=(launcher?profile.launchers:profile.magazines).find(s=>s.module_id===row.module_id)!,model=profile.models.find(m=>m.id===row.model_id)!;
      const store=launcher?row.ready:row.stock,work=launcher?(row.job?[row.job]:[]):row.jobs;
      const cap=spec.capacity_by_model[row.model_id];
      const free=cap-store.length-work.length-(launcher?row.load_remaining:row.assembly_remaining);
      const send=(kind:string,args:Partial<MissileOrder>={})=>onCommand({module_id:row.module_id,kind,...args});
      return <fieldset className="missile-device" key={row.module_id} disabled={disabled}>
        <legend>{names[row.module_id]??row.module_id}</legend>
        <p>{launcher?'待发':'库内整装弹'} <strong>{store.length} / {cap}</strong> · {launcher?`装填中 ${work.length}`:`作业中 ${work.length} · 可同时组装 ${spec.assembly_parallel} 枚`}</p>
        {preparation&&spec.compatible_model_ids.length>1?<label>导弹型号<select aria-label={`${names[row.module_id]}导弹型号`} value={row.model_id} disabled={store.length>0||work.length>0}
          onChange={e=>send('model',{model_id:e.target.value})}>{spec.compatible_model_ids.map(id=><option key={id} value={id}>{modelName(id)}</option>)}</select></label>:<p>{model.name} · 弹径 {model.diameter_mm} 毫米</p>}
        <label>战斗部<select aria-label={`${names[row.module_id]}战斗部`} value={row.warhead_id} disabled={launcher&&!preparation&&!spec.warhead_switch_in_battle}
          onChange={e=>send('warhead',{warhead_id:e.target.value})}>{model.warhead_ids.map(id=><option key={id} value={id}>{profile.warheads.find(h=>h.id===id)?.name}</option>)}</select></label>
        <p className="muted">{launcher?`原料装填 ${(model.raw_reload_steps*(spec.raw_reload_multiplier??1)/60).toFixed(1)} 秒 / 枚 · 整装弹 ${(model.ready_reload_steps*(spec.ready_reload_multiplier??1)/60).toFixed(1)} 秒 / 枚`:`组装 ${(model.assembly_steps/60).toFixed(1)} 秒 / 枚`}</p>
        <MissilePerformance value={profile.flight_profiles?.[row.model_id]}/>
        <details><summary>每枚材料与现有战斗部</summary><p>{model.cargo_costs.map(c=>`${goodName(c.good_id)} ${c.quantity}`).join(' · ')} · {goodName(profile.warheads.find(h=>h.id===row.warhead_id)!.good_id)} {model.warhead_material_quantity}</p>
          {profile.warheads.map(h=><p key={h.id}>{h.name}：{store.filter(u=>u.warhead_id===h.id).length} 枚</p>)}</details>
        {jobs(work)}
        <div className="editor-row">{launcher?<>
          <button onClick={()=>send('load')} disabled={free<=0||row.unload_remaining>0}>装满此发射器</button>
          <button onClick={()=>send('fill_same_class')}>装满本舰全部发射器</button>
          <button onClick={()=>send('unload')} disabled={!store.length&&!work.length}>卸下全部导弹</button>
        </>:<>
          <button onClick={()=>send('assemble',{quantity:Math.min(free,spec.assembly_parallel??5)})} disabled={free<=0}>组装一批（{Math.max(0,Math.min(free,spec.assembly_parallel??5))} 枚）</button>
          <button onClick={()=>send('assemble',{quantity:free})} disabled={free<=0}>组装至满库</button>
          <button onClick={()=>send('fill_same_class')}>补满本舰全部导弹库</button>
          <button onClick={()=>send('dismantle')} disabled={!store.length||work.some(j=>j.kind==='dismantle')}>拆解一枚整装弹</button>
        </>}
          <button onClick={()=>send('cancel')} disabled={!work.length&&!(launcher?row.load_remaining||row.unload_remaining:row.assembly_remaining)}>取消当前作业</button>
        </div>
        {!!(launcher?row.load_remaining||row.unload_remaining:row.assembly_remaining)&&<p>排队：{launcher?`${row.load_remaining} 枚装填、${row.unload_remaining} 枚卸弹`:`${row.assembly_remaining} 枚组装`}{!work.length?' · 等待材料、库容或设备可用':''}</p>}
        {!!blocked[row.module_id]?.length&&<p role="status">{blocked[row.module_id].map(r=>({destroyed:'设备已毁',mode_disabled:'设备未开启',host_unavailable:'宿主不可用',power_unavailable:'供电不足',crew_unavailable:'人手不足'}[r]??'设备不可用')).join('、')}，作业暂停。</p>}
        {launcher&&row.job&&['unload','swap'].includes(row.job.kind)&&row.job.remaining_work_steps<=1&&<p role="status">等待同型号导弹库接收；满库时先拆解已有整装弹腾出位置。</p>}
        {launcher&&<p>自动发射预设：{row.auto_fire?'开启':'关闭'}{spec.integrated_fire_control?' · 内置雷达与本武器专用火控计算':''}</p>}
      </fieldset>;
    })}
  </section>;
}
