import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { LiftReserve } from './LiftReserve';

describe('lift reserve display', () => {
  const value = { dry_mass_kg: 1000, available_force_n: 0, margin_n: 0, reserve_fraction: 0 };
  it('distinguishes zero from even a very small deficit', () => {
    expect(renderToStaticMarkup(<LiftReserve value={value} />)).toContain('恰好悬浮');
    const deficit = renderToStaticMarkup(<LiftReserve value={{ ...value, reserve_fraction: -.00001 }} />);
    expect(deficit).toContain('升力不足');
    expect(deficit).toContain('−＜0.1%');
    expect(deficit).not.toContain('恰好悬浮');
  });
  it('keeps negative values and has an explicit missing-data state', () => {
    expect(renderToStaticMarkup(<LiftReserve value={{ ...value, reserve_fraction: -.2 }} />)).toContain('−20.0%');
    expect(renderToStaticMarkup(<LiftReserve />)).toContain('待计算');
  });
});
