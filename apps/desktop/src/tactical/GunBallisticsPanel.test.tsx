import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { GunBallisticsPanel } from './GunBallisticsPanel';
import type { GunView } from './model';
import { ammunitionName } from './ammunition';

const gun = {attack_layer:'cloud',ballistics:{caliber_mm:75,speed_mps:900,effective_speed_mps:630,
  lifetime_s:25,reference_range_m:12000,speed_retention:.7,drag:true,cyclic_rpm:120}} as GunView;
describe('gun flight controls',()=>{
  it('displays effective speed with unchanged lifetime and disables nonadjacent layer',()=>{
    const html=renderToStaticMarkup(<GunBallisticsPanel gun={gun} ownLayer="upper" onCommand={()=>{}}/>);
    expect(html).toContain('初速 630 米/秒');expect(html).toContain('寿命 25.0 秒');
    expect(html).toContain('跨层初速保留 70%');expect(html).toMatch(/<option value="rain" disabled="">/);
    expect(html).not.toMatch(/<option value="cloud" disabled/);
    expect(html).toContain('炮弹穿过友舰');
  });
  it('names each supported caliber and ammunition type without a raw resource ID',()=>{
    for(const cal of [30,50,75,120]){
      expect(ammunitionName(`recipe.3a.${cal}mm.ordinary`)).toBe(`${cal} 毫米普通弹`);
      expect(ammunitionName(`projectile.3a.${cal}mm.armor_piercing`)).toBe(`${cal} 毫米穿甲弹`);
      expect(ammunitionName(`recipe.3a.${cal}mm.incendiary`)).toBe(`${cal} 毫米燃烧弹`);
    }
  });
});
