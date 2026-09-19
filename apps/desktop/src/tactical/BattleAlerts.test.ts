import { describe, expect, it } from 'vitest';
import { battleAlerts } from './BattleAlerts';
import type { TacticalView } from './model';

describe('friendly alerts', () => {
  it('deduplicates shared threats and excludes enemy damage and exited ships', () => {
    const view = { geometry: { ships: [{ id: 'own', name: '本舰', side_id: 'blue' }, { id: 'enemy', side_id: 'red' }, { id: 'gone', side_id: 'blue' }] },
      snapshot: { ships: [{ id: 'own', descent: { paused: false } }, { id: 'enemy', descent: {} }, { id: 'gone', descent: {}, physical_status: 'exited' }],
        gunnery: { damage_control: { fires: [{ ship_id: 'enemy' }] }, point_defense: { threats: [
          { ship_id: 'own', projectile_id: 1 }, { ship_id: 'own', projectile_id: 1 }, { ship_id: 'own', projectile_id: 2 }] } } } } as unknown as TacticalView;
    const alerts = battleAlerts(view, 'own');
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toMatchObject({ shipId: 'own', page: 'damage' });
    expect(alerts[1].text).toContain('2 个撞舰威胁');
  });
});
