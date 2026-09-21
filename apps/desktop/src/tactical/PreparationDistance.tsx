import type { ScenePacket } from './preparationScene';

export function preparationDistanceText(packet:ScenePacket|null) {
  if(!packet)return '正在读取交战距离…';
  if(packet.scene.distance_mode!=='automatic')return `手动测试距离 ${(packet.scene.distance_m/1000).toLocaleString()} 公里`;
  const contact=packet.contact_start;
  if(contact?.status==='ready'&&contact.distance_m!==null)
    return `自动交战距离 ${(contact.distance_m/1000).toLocaleString(undefined,{maximumFractionDigits:3})} 公里 · 距离上限 ${(contact.threshold_m??0)/1000} 公里`;
  if(contact?.status==='no_contact')return '双方无法形成接触，请调整舰载设备、朝向或编队，也可切换手动测试距离。';
  if(contact?.status==='incomplete')return '请先为双方加入舰艇。';
  if(contact?.status==='invalid_fleet')return '请先修正双方编队资格。';
  if(contact?.status==='unavailable')return contact.message??'当前舰艇状态无法计算自动交战距离。';
  return '正在计算自动交战距离…';
}

export function PreparationDistance({packet,disabled,onMode,onRefresh}: {
  packet:ScenePacket|null;disabled:boolean;onMode:(mode:'automatic'|'manual')=>void;onRefresh:()=>void;
}) {
  return <><label>开战距离模式 <select aria-label="开战距离模式" value={packet?.scene.distance_mode??'manual'} disabled={disabled||!packet}
    onChange={e=>onMode(e.target.value as 'automatic'|'manual')}>
    <option value="automatic">按首次探测自动计算</option><option value="manual">手动测试距离</option>
  </select></label>
    {packet?.scene.distance_mode==='automatic'&&<button disabled={disabled} onClick={onRefresh}>重新计算距离</button>}</>;
}
