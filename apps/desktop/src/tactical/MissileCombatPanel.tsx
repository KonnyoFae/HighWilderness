import type {MissileOrder,MissileShipView,MissileView} from './missiles';
import {selectedLauncher} from './missiles';
import type {ObservationShip} from './FireControlPanel';
import type {DisplayProjectile} from './model';
import {HEIGHT_LAYERS,layerName} from './layers';
import {MissilePerformance} from './MissilePerformance';

const statuses:Record<string,string>={no_target:'等待目标',ready:'可发射',fired:'已发射',departed:'已出膛，正在转向',
  traversing:'正在对准',hull_blocked:'发射方向被舰体阻挡',out_of_arc:'超出射界',out_of_range:'超出射程',
  target_unavailable:'目标失联',target_other_layer:'目标不在作用层',layer_out_of_reach:'作用层超出相邻范围',
  reloading:'装填或换弹中',cooldown:'发射间隔',no_ammunition:'没有待发弹',model_unavailable:'此型号飞行尚待后续接入',
  projectile_limit:'在途弹药已达上限',destroyed:'发射器已毁',mode_disabled:'设备未开启',power_unavailable:'供电不足',
  crew_unavailable:'人手不足',host_unavailable:'宿主不可用',control_unavailable:'当前无法指挥',battle_finished:'交战已结束'};
Object.assign(statuses,{defense_waiting:'等待外圈大型高速威胁',defense_covered:'目标已有在途拦截火力',defense_inner_circle:'目标已进入近防炮内圈',defense_sensor_unavailable:'缺少有效感知或火控计算'});

export function MissileCombatPanel({ship,observation,missiles,names,selected,onSelect,ownLayer,disabled,picking,onPick,onCommand,targetHint}:{
  ship?:MissileShipView;observation?:ObservationShip;missiles?:MissileView;projectiles:DisplayProjectile[];
  names:Record<string,string>;selected:string|null;onSelect:(id:string)=>void;ownLayer:string;disabled:boolean;
  picking:boolean;onPick:()=>void;onCommand:(v:MissileOrder)=>void;targetHint?:string|number|null;
}) {
  if(!ship?.launchers?.length)return <p>本舰没有导弹发射器。</p>;
  const launcher=selectedLauncher(ship,selected)!;
  const row=ship.state.launchers.find(r=>r.module_id===launcher.module_id)!;
  const model=ship.profile.models.find(m=>m.id===row.model_id)!;
  const send=(kind:string,extra:Partial<MissileOrder>={})=>onCommand({module_id:launcher.module_id,kind,...extra});
  const contacts=observation?.contacts.filter(c=>(launcher.interceptor?c.kind==='missile'||c.kind==='shell':c.kind==='ship')&&c.valid
    &&(c.status!=='no_computer'||c.defense_weapon_ids?.includes(launcher.module_id)))??[];
  const hint=contacts.find(c=>c.id===targetHint);
  const targetName=(id:string|number)=>names[id]??(typeof id==='number'?`来袭弹体 #${id}`:'原目标');
  return <section className="missile-combat" aria-label="导弹作战">
      <label>发射器<select aria-label="作战发射器" value={launcher.module_id} onChange={e=>onSelect(e.target.value)}>
        {ship.launchers.map(l=><option key={l.module_id} value={l.module_id}>{ship.module_names[l.module_id]??l.module_id}</option>)}
      </select></label>
      <p className="muted">画布高亮所选发射器射界；暂停时也可切换查看。可用“聚焦所选舰”放大检查。</p>
    <fieldset disabled={disabled}>
      <legend>发射控制</legend>
      <p><strong>{model.name}</strong> · 待发 {row.ready.length} 枚</p>
      <p role="status">{statuses[launcher.status]??'暂不可用'}{launcher.fire_requested?' · 单发指令等待执行':''} · 已发射 {launcher.shots} 枚</p>
      <p className="muted">当前参考射程 {(launcher.maximum_range_m/1000).toFixed(1)} 公里，机动会缩短实际飞行距离。</p>
      <MissilePerformance value={ship.profile.flight_profiles?.[row.model_id]}/>
      {launcher.interceptor&&<p>自动防御：外圈大型高速撞舰威胁优先，每目标先分配一枚。{launcher.active_target_id!==null&&launcher.active_target_id!==undefined?` 当前处理 ${targetName(launcher.active_target_id)}。`:''}</p>}
      <label>导弹作用层<select aria-label="导弹作用层" value={launcher.attack_layer} onChange={e=>send('attack_layer',{layer:e.target.value})}>
        {HEIGHT_LAYERS.map((layer,i)=><option key={layer} value={layer} disabled={Math.abs(i-HEIGHT_LAYERS.indexOf(ownLayer as typeof layer))>1}>{layerName(layer)}</option>)}
      </select></label>
      <label>发射目标<select aria-label="导弹发射目标" value={launcher.target_id??''} onChange={e=>e.target.value?send('target',{target_id:contacts.find(c=>String(c.id)===e.target.value)!.id}):send('clear')}>
        <option value="">{launcher.point_m?'已指定地点':'未指定（开启自动后搜索）'}</option>
        {launcher.target_id&&!contacts.some(c=>c.id===launcher.target_id)&&<option value={launcher.target_id}>{targetName(launcher.target_id)} · 已失联</option>}
        {contacts.map(c=><option key={c.id} value={c.id}>{targetName(c.id)} · {layerName(c.height_layer)}</option>)}
      </select></label>
      {hint&&hint.id!==launcher.target_id&&<button disabled={!launcher.supported} onClick={()=>send('target',{target_id:hint.id})}>将 {targetName(hint.id)} 分配给此发射器</button>}
      {launcher.point_m&&<p>指定地点：{launcher.point_m.map(x=>x.toFixed(0)).join('，')} 米</p>}
      <div className="editor-row">
        <button aria-pressed={picking} disabled={!launcher.supported} onClick={onPick}>{picking?'取消画布选点':'在画布指定发射地点'}</button>
        <button disabled={!launcher.supported||launcher.fire_requested||(!launcher.target_id&&!launcher.point_m)} onClick={()=>send('fire')}>单发导弹</button>
        <button onClick={()=>send('clear')}>清除目标与待发指令</button>
      </div>
      <label className="missile-auto"><input type="checkbox" checked={row.auto_fire} disabled={!launcher.supported} onChange={e=>send('auto_fire',{enabled:e.target.checked})}/>自动发射</label>
      <p className="muted">{launcher.interceptor?'专用拦截发射器默认开启，VLS 默认关闭。数据链共享火力分配；已发射弹继续追踪。':'普通发射器默认关闭自动发射。'}单发等待对准后执行；已出膛的 VLS 导弹无法撤回。</p>
    </fieldset>
    {!!missiles?.pending?.some(d=>d.ship_id===ship.ship_id)&&<div aria-label="VLS转向队列">{missiles.pending.filter(d=>d.ship_id===ship.ship_id).map(d=><p key={d.projectile_id}>导弹 #{d.projectile_id} 已出膛 · {layerName(d.height_layer)} · {(d.remaining_steps/60).toFixed(1)} 秒后进入飞行</p>)}</div>}
  </section>;
}
