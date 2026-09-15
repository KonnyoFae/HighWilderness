import { describe, expect, it } from 'vitest';
import { acceptSnapshot } from './model';
import type { TacticalSnapshot } from './model';
import fixture from './testing/snapshot.fixture.json';
import { initialObservationLayer, viewOnLayer } from './layers';
import { pickShip } from './viewport';
import { PresentationTimeline } from './presentation';

const base = acceptSnapshot(null, fixture as TacticalSnapshot, 'fixture.1');
function scene() {
  return { ...base, snapshot: { ...base.snapshot, ships: ['upper', 'cloud', 'rain'].map((height_layer, n) =>
    ({ ...base.snapshot.ships[0], id: `ship.${n}`, height_layer, position_m: [0, 0] })),
    gunnery: { interface: 'gaotian.gunnery-view/p2a-v1alpha1' as const, command_sequence: 0, weapons: [],
      policy_id: 'test', damage_enabled: true, projectiles: ['upper', 'cloud', 'rain'].map((height_layer, id) =>
        ({ id, ship_id: 'ship.0', height_layer, position_m: [0, 0], previous_m: [0, 0], velocity_mps: [5000, 0] })),
      damage: { hits: 1, expired: 0, recent: [{ projectile_id: 9, step: 0, source_ship_id: 'ship.0', ship_id: 'ship.0',
        height_layer: 'cloud', position_m: [0, 0], deck_level: 0, outcome: 'module', module_ids: [], module_damage: 0 }] } } },
    geometry: { ...base.geometry, ships: ['ship.0', 'ship.1', 'ship.2'].map(id => ({ ...base.geometry.ships[0], id })) } };
}
describe('observation layers', () => {
  it('selects the flagship layer on entry and defaults safely for a geometry-only scene', () => {
    expect(initialObservationLayer(scene(), 'ship.1')).toBe('cloud');
    expect(initialObservationLayer({ ...base, snapshot: { ...base.snapshot, ships: [] } })).toBe('upper');
  });
  it('only displays and picks ships on the observed layer at identical world positions', () => {
    const view = scene(), before = JSON.stringify(view);
    for (const [n, layer] of ['upper', 'cloud', 'rain'].entries()) {
      const shown = viewOnLayer(view, layer as 'upper' | 'cloud' | 'rain');
      expect(shown.snapshot.ships.map(s => s.id)).toEqual([`ship.${n}`]);
      expect(pickShip(shown, { x: 0, y: 0 }, { x: 0, y: 0, scale: 1 })).toBe(`ship.${n}`);
    }
    expect(JSON.stringify(view)).toBe(before);
  });
  it('keeps projectiles and impacts on their own layer when source or target has moved', () => {
    const view = scene();
    expect(viewOnLayer(view, 'cloud').snapshot.gunnery!.projectiles.map(p => p.id)).toEqual([1]);
    expect(viewOnLayer(view, 'cloud').snapshot.gunnery!.damage!.recent).toHaveLength(1);
    expect(viewOnLayer(view, 'upper').snapshot.gunnery!.damage!.recent).toHaveLength(0);
    view.snapshot.ships[0].height_layer = 'rain';
    expect(viewOnLayer(view, 'cloud').snapshot.gunnery!.projectiles.map(p => p.id)).toEqual([1]);
    expect(viewOnLayer(view, 'cloud').snapshot.gunnery!.damage!.recent).toHaveLength(1);
  });
  it('switches ship visibility only at the committed layer boundary in the display timeline', () => {
    const a = scene(), b = structuredClone(a), clock = new PresentationTimeline();
    a.snapshot.paused = b.snapshot.paused = false;
    b.snapshot.fixed_step = 4; b.snapshot.time_s = 4/60; b.snapshot.ships[0].height_layer = 'rain';
    clock.push(a, 0); clock.push(b, 4/60*1000);
    const before = clock.sample(150)!;
    expect(viewOnLayer(before, 'upper').snapshot.ships.map(s => s.id)).toContain('ship.0');
    expect(viewOnLayer(before, 'rain').snapshot.ships.map(s => s.id)).not.toContain('ship.0');
    const after = clock.sample(200)!;
    expect(viewOnLayer(after, 'upper').snapshot.ships).toHaveLength(0);
    expect(viewOnLayer(after, 'rain').snapshot.ships.map(s => s.id)).toContain('ship.0');
  });
});
