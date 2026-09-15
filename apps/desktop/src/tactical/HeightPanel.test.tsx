import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { HeightPanel, heightTime } from './HeightPanel';
import type { HeightNavigation } from './model';

const value: HeightNavigation = { base_duration_s: 80, duration_s: 100, lift_loss_fraction: .25,
  target_layer: 'rain', next_layer: 'cloud', progress: .4, remaining_s: 60, total_remaining_s: 160, unavailable_reason: null };
const props = { value, actualLayer: 'upper', friendly: true, disabled: false, uncertain: false, onTarget: () => {} };
describe('ship height controls', () => {
  it('separates actual, next and final layers with damage-adjusted time', () => {
    const html = renderToStaticMarkup(<HeightPanel {...props} />);
    expect(html).toContain('实际高度：<strong>上层</strong>');
    expect(html).toContain('上层 → 云层'); expect(html).toContain('最终目标：雨层');
    expect(html).toContain('升力损失 25.0%'); expect(html).toContain('value="0.4"');
    expect(html).toContain('2 分 40.0 秒');
  });
  it('keeps enemy ships read-only and missing data explicit', () => {
    const enemy=renderToStaticMarkup(<HeightPanel {...props} friendly={false} />);
    expect(enemy).toContain('换层状态'); expect(enemy).not.toContain('<button');
    expect(renderToStaticMarkup(<HeightPanel {...props} value={null} />)).toBe('');
    expect(heightTime(null)).toBe('不可用');
  });
  it('blocks repeated commands while uncertain and explains unavailable lift', () => {
    const html=renderToStaticMarkup(<HeightPanel {...props} uncertain value={{...value,unavailable_reason:'no_lift_surplus'}} />);
    expect(html).toContain('换层状态待确认'); expect(html).toContain('当前没有正升力冗余');
    expect((html.match(/disabled=""/g)??[]).length).toBe(4);
  });
  it('shows rain rescue deadline and neutral freeze without an extra height layer', () => {
    const descent={source_layer:'rain',next_layer:null,progress:.8,duration_s:30,paused:false,remaining_s:6};
    const html=renderToStaticMarkup(<HeightPanel {...props} value={{...value,target_layer:null,next_layer:null}} descent={descent} />);
    expect(html).toContain('雨层下坠 · 即将坠毁');expect(html).toContain('6.0 秒');
    expect(html).toContain('下坠期间仍可移动');expect(html).not.toContain('前往地面');
    const frozen=renderToStaticMarkup(<HeightPanel {...props} descent={{...descent,paused:true,remaining_s:null}} />);
    expect(frozen).toContain('下坠进度冻结');expect(frozen).toContain('value="0.8"');
  });
});
