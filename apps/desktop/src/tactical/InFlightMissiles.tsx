import type {DisplayProjectile} from './model';
import type {ObservationShip} from './FireControlPanel';
import {layerName} from './layers';
const phases:Record<string,string>={boost:'助推',powered:'动力',coast:'滑行'};
const states:Record<string,string>={midcourse:'中段飞行',search:'搜索',acquiring:'锁定确认',tracking:'追踪',lost:'失锁直飞',memory:'记忆跟踪',rescan:'8 字重搜',datalink:'数据链引导'};
const maneuvers:Record<string,string>={boost:'助推直飞',climbing:'上爬追击',diving:'下潜追击',returning:'上爬失败，回落中',leveling:'逐步拉平',level:'平飞'};
function FlightSpeed({projectile:p}:{projectile:DisplayProjectile}) {
  const horizontal=p.missile?.horizontal_speed_mps??Math.hypot(...p.velocity_mps);
  const total=p.missile?.speed_mps??Math.hypot(horizontal,p.missile?.vertical_speed_mps??0);
  return <span aria-label="导弹飞行速度">总速度 {total.toFixed(0)} 米/秒 · 水平速度 {horizontal.toFixed(0)} 米/秒</span>;
}
export function InFlightMissiles({projectiles,observation,names,friendlyIds,disabled,onRetarget}:{projectiles:DisplayProjectile[];observation?:ObservationShip;names:Record<string,string>;friendlyIds:string[];disabled:boolean;onRetarget:(id:number,target:string|number)=>void}) {
  const rows=projectiles.filter(p=>p.missile&&friendlyIds.includes(p.ship_id));
  if(!rows.length)return <p role="status">当前没有友方在途导弹。</p>;
  const linked=observation?.devices.some(d=>d.kind==='datalink'&&!d.reason&&d.durability>0);
  const contacts=observation?.contacts.filter(c=>c.valid&&c.status==='tracked')??[];
  return <section aria-label="在途导弹制导"><h3>友方在途导弹（{rows.length}）</h3>{rows.map(p=><article className="observation-card" key={p.id} data-flight={p.id}>
    <strong>{p.missile!.interceptor?'拦截弹':'导弹'} #{p.id} · {names[p.ship_id]??'友舰'}</strong>
    <p>{phases[p.missile!.phase]} · <span data-seeker-state={p.missile!.seeker_state}>{states[p.missile!.seeker_state]??p.missile!.seeker_state}</span> · 当前命中层 {layerName(p.height_layer??'upper')}</p>
    <p><FlightSpeed projectile={p}/> · 耐久 {p.durability} / {p.maximum_durability} · 剩余寿命 {p.missile!.remaining_s.toFixed(1)} 秒</p>
    {p.missile!.altitude_m!=null&&<p>相对雨层高度 {p.missile!.altitude_m.toFixed(0)} 米 · 垂直速度 {(p.missile!.vertical_speed_mps??0).toFixed(0)} 米/秒（向上为正）</p>}
    {p.missile!.maneuver_state&&<p data-maneuver-state={p.missile!.maneuver_state}>{maneuvers[p.missile!.maneuver_state]??p.missile!.maneuver_state}
      {p.missile!.maneuver_target_layer&&<> · 目标层 {layerName(p.missile!.maneuver_target_layer)}{p.missile!.vertical_remaining_m!=null&&<> · 垂直距离剩余 {p.missile!.vertical_remaining_m.toFixed(0)} 米</>}</>}
      {p.missile!.pitch_deg!=null&&<> · 俯仰 {p.missile!.pitch_deg.toFixed(1)}°</>}
    </p>}
    {p.missile!.maneuver_reason==='climb_failed_for_target'&&<p>本次飞行不再向该目标上爬；仍可同层或向下攻击。</p>}
    {p.missile!.maneuver_reason==='unpowered_climb_failed'&&<p>无动力上爬速度不足，保持当前碰撞层并回落，不刷新寿命。</p>}
    {p.missile!.target_id&&<p>锁定：{names[p.missile!.target_id]??(typeof p.missile!.target_id==='number'?`来袭弹体 #${p.missile!.target_id}`:p.missile!.target_id.startsWith('ew.')?'主动诱饵':'目标')}</p>}
    {p.missile!.interceptor&&<p>单次拦截伤害 {p.missile!.interception_damage} · 判定半径 {p.missile!.interception_radius_m} 米</p>}
    {p.missile!.link_sender&&<p>情报更新：{names[p.missile!.link_sender]??'友舰数据链'}</p>}
    {p.missile!.datalink&&<label>数据链改攻<select aria-label={`导弹 ${p.id} 数据链改攻`} value="" disabled={disabled||!linked}
      onChange={e=>e.target.value&&onRetarget(p.id,contacts.find(c=>String(c.id)===e.target.value)!.id)}><option value="">选择有效观测目标</option>{contacts.filter(c=>p.missile!.interceptor?c.kind!=='ship':c.kind==='ship').map(c=><option key={c.id} value={c.id}>{names[c.id]??`来袭弹体 #${c.id}`} · {layerName(c.height_layer)}</option>)}</select></label>}
  </article>)}<p className="muted">数据链可引导导弹逐层追击其他高度层的有效目标，完成换层后才改变命中所属层。改攻不等于导引头锁定，也不恢复动力、寿命或同一目标的上爬机会。</p></section>;
}
