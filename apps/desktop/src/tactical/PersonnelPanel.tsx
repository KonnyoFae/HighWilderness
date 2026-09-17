import type {TacticalView} from './model';
import type {CombatRecord} from './settlement';

export const crewName=(kind:string)=>({ordinary:'普通船员',officer:'军官',technical_officer:'技术军官',veteran_damage_control:'损管老兵'}[kind]??kind);
const functionName=(kind:string)=>({'weapon.aim':'瞄准','weapon.fire':'射击','weapon.reload':'装填','engine.throttle':'推进',
  'thruster.throttle':'转向推进','sensor.search':'搜索','sensor.track':'跟踪','fire_control.solution':'火控解算',
  'fire_control.guidance':'制导','damage_control.firefighting':'损管','generator.regulation':'发电',
  'cic.basic_control':'舰艇指挥','lift_tank.lift':'升力','remote_core.command_link':'遥控'}[kind]??kind);

export function PersonnelPanel({view,shipId}:{view:TacticalView;shipId:string}) {
  const people=view.snapshot.gunnery?.personnel?.ships.find(s=>s.ship_id===shipId);
  if(!people)return null;
  const geometry=view.geometry.ships.find(s=>s.id===shipId);
  return <section aria-label="舰上人员" className="personnel-panel">
    <h4>舰上人员</h4><p>可执勤 {people.fit} · 负伤 {people.wounded} · 累计阵亡 {people.dead}</p>
    <p>伤员暂不参与岗位分配；本阶段无战中治疗。</p>
    <details><summary>人员类别与岗位</summary>
      {people.types.map(row=><p key={row.crew_type}>{crewName(row.crew_type)}：可用 {row.fit} · 负伤 {row.wounded} · 阵亡 {row.dead}</p>)}
      {people.unclassified_wounded>0 && <p>旧存档未分类伤员：{people.unclassified_wounded}</p>}
      {people.modules.map(m=><details key={m.module_id} className="personnel-post">
        <summary>{geometry?.modules.find(v=>v.id===m.module_id)?.name??m.module_id} · 配员 {(m.staffing_fraction*100).toFixed(0)}%</summary>
        <p>{m.requirements.map(r=>`${crewName(r.crew_type)} ${r.assigned.toFixed(1)} / 标准 ${r.standard}（最低 ${r.minimum}）`).join('；')}</p>
        <p>{m.functions.map(f=>`${functionName(f.function_id)}人员效能 ${(f.crew_efficiency*100).toFixed(0)}%`).join(' · ')}</p>
      </details>)}
    </details>
  </section>;
}

export function PersonnelLog({view}:{view:TacticalView}) {
  const people=view.snapshot.gunnery?.personnel;
  if(!people)return null;
  return <details aria-label="人员伤亡记录"><summary>人员伤亡记录</summary>
    {people.recent.slice(-8).reverse().map((event,index)=>{
      const ship=view.geometry.ships.find(s=>s.id===event.ship_id);
      return <p key={`${event.step}:${event.ship_id}:${event.module_id}:${index}`}>
        {ship?.name??event.ship_id} · {ship?.modules.find(m=>m.id===event.module_id)?.name??event.module_id} · 第 {event.deck_level} 甲板 ·
        {{projectile:'炮击',fire:'内部火灾',magazine_detonation:'殉爆波及'}[event.cause]??event.cause}：
        {event.casualties.map(row=>`${crewName(row.crew_type)}负伤 ${row.wounded}、阵亡 ${row.dead}`).join('；')}
      </p>;
    })}
    {!people.recent.length && <p>本场暂无人员伤亡。</p>}
  </details>;
}

export function PersonnelSettlement({before,after}:{before:CombatRecord;after:CombatRecord}) {
  if(!after.state.crew)return null;
  const fit=(r:CombatRecord)=>r.state.crew?.reduce((n,v)=>n+v.count,0)??0;
  const dead=(r:CombatRecord)=>r.state.personnel?.statuses.reduce((n,v)=>n+v.dead,0)??0;
  return <section aria-label="人员战后变化"><h4>人员战后变化</h4>
    <p>可执勤 {fit(before)} → {fit(after)} · 负伤 {before.state.wounded_aboard??0} → {after.state.wounded_aboard??0} · 累计阵亡 {dead(before)} → {dead(after)}</p>
    <p>负伤、阵亡及可用人员随战果保存，下场不会自动补员。</p>
  </section>;
}
