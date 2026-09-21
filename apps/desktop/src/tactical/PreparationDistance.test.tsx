import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {PreparationDistance,preparationDistanceText} from './PreparationDistance';
import type {ScenePacket} from './preparationScene';

function packet(mode:'manual'|'automatic'|undefined='automatic',status='ready') {
  return {scene:{distance_mode:mode,distance_m:1234},contact_start:{status,distance_m:15678.12,threshold_m:50000}} as ScenePacket;
}
describe('preparation contact distance feedback',()=>{
  it('shows automatic distance and its cap without promising reciprocal contact or weapon range',()=>{
    const p=packet();expect(preparationDistanceText(p)).toContain('15.678');expect(preparationDistanceText(p)).toContain('50 公里');
    const html=renderToStaticMarkup(<PreparationDistance packet={p} disabled={false} onMode={()=>{}} onRefresh={()=>{}}/>);
    expect(html).toContain('value="automatic" selected');expect(html).toContain('重新计算距离');
  });
  it('keeps old scenes manual and does not show the unused saved distance in automatic failure',()=>{
    const old=packet();delete old.scene.distance_mode;
    expect(preparationDistanceText(old)).toContain('1.234');
    const failed=packet('automatic','no_contact');failed.contact_start!.distance_m=null;
    expect(preparationDistanceText(failed)).toContain('双方无法形成接触');
    expect(preparationDistanceText(failed)).not.toContain('1.234');
  });
  it('distinguishes incomplete fleets, unavailable ships and recomputation',()=>{
    expect(preparationDistanceText(packet('automatic','incomplete'))).toContain('双方加入舰艇');
    expect(preparationDistanceText(packet('automatic','invalid_fleet'))).toContain('编队资格');
    expect(preparationDistanceText(packet('automatic','unavailable'))).toContain('当前舰艇状态');
    const p=packet();delete p.contact_start;
    expect(preparationDistanceText(p)).toContain('正在计算');
  });
});
