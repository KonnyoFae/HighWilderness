import { describe, expect, it } from 'vitest';
import { acceptSnapshot } from './model';
import type { DisplayProjectile, FinishedProjectile, GunView, TacticalSnapshot } from './model';
import fixture from './testing/snapshot.fixture.json';
import { PresentationTimeline } from './presentation';
import { bodyToWorld, pickShip } from './viewport';

const base = acceptSnapshot(null, fixture as TacticalSnapshot, 'fixture.1');
const id = base.snapshot.ships[0].id;
const gun: GunView = { ship_id: id, module_id: 'weapon.test', mode: 'auto', angle_rad: 0,
  origin_m: [0, 10], direction: [0, 1], aim_point_m: [0, 200], target_ship_id: 'target', target_module_id: null,
  quality: 'normal', quality_reason: '', lock_sources: [], status: 'ready', shots: 0, ready_rounds: 1,
  reload_steps: 0, cooldown_steps: 0, ammo_resources: 1, batch_cost: 1, batch_rounds: 1 };
function frame(step: number, extra: Partial<TacticalSnapshot> = {}) {
  return { geometry: base.geometry, snapshot: { ...base.snapshot, fixed_step: step, time_s: step/60, paused: false,
    ships: [{ ...base.snapshot.ships[0], position_m: [step*10, 0], velocity_mps: [600, 0], heading_rad: 0 }],
    gunnery: { interface: 'gaotian.gunnery-view/p2a-v1alpha1' as const, command_sequence: 0, weapons: [gun], projectiles: [],
      policy_id: 'test', damage_enabled: true, damage: { hits: 0, expired: 0, recent: [] } }, ...extra } };
}
const trail: FinishedProjectile = { id: 1, ship_id: id, born_step: 1, origin_m: [10, 0], velocity_mps: [5000, 0],
  position_m: [10, 0], end_step: 2, end_m: [60, 0], expires_step: 20, impact: { ship_id: 'target', outcome: 'module' } };

describe('shared tactical presentation time', () => {
  it('draws intermediate positions at frame cadence without changing authority', () => {
    const clock = new PresentationTimeline(), a = frame(0), b = frame(4), original = JSON.stringify([a, b]);
    clock.push(a, 0); clock.push(b, 4/60*1000);
    const samples = [110, 120, 130, 140, 150, 160].map(now => clock.sample(now)!.snapshot);
    expect(new Set(samples.map(s => s.ships[0].position_m[0])).size).toBe(6);
    for (const s of samples) expect(s.ships[0].position_m[0]).toBeCloseTo(s.fixed_step*10);
    expect(JSON.stringify([a, b])).toBe(original);
  });

  it('turns across ±pi by the short arc and keeps the turret anchored to the sampled hull', () => {
    const clock = new PresentationTimeline(), a = frame(0), b = frame(4);
    a.snapshot.ships[0].heading_rad = Math.PI-.1; b.snapshot.ships[0].heading_rad = -Math.PI+.1;
    for (const f of [a, b]) {
      const ship = f.snapshot.ships[0], at = bodyToWorld({ x: 0, y: 10 }, ship);
      f.snapshot.gunnery = { ...f.snapshot.gunnery!, weapons: [{ ...gun, origin_m: [at.x, at.y],
        direction: [-Math.sin(ship.heading_rad), Math.cos(ship.heading_rad)] }] };
    }
    clock.push(a, 0); clock.push(b, 4/60*1000);
    const s = clock.sample(100+2/60*1000)!.snapshot, ship = s.ships[0], barrel = s.gunnery!.weapons[0];
    expect(ship.heading_rad).toBeCloseTo(Math.PI);
    const origin = bodyToWorld({ x: 0, y: 10 }, ship);
    expect(barrel.origin_m[0]).toBeCloseTo(origin.x); expect(barrel.origin_m[1]).toBeCloseTo(origin.y);
    expect(barrel.direction[1]).toBeCloseTo(-1);
  });

  it('shows a 5000 m/s shot born and hit between publications, with no early impact or ghost round', () => {
    const clock = new PresentationTimeline(); clock.push(frame(0), 0);
    clock.push(frame(4, { presentation: { interface: 'gaotian.tactical-presentation/v1alpha1',
      finished_projectiles: [trail], dropped_projectiles: 0 } }), 4/60*1000);
    const sample = (step: number) => clock.sample(100+step/60*1000)!.snapshot.gunnery!;
    expect(sample(.5).projectiles).toHaveLength(0);
    const inFlight = sample(1.5);
    expect(inFlight.projectiles[0].position_m[0]).toBeCloseTo(35);
    expect(inFlight.projectiles[0].previous_m[0]).toBe(10);
    expect(inFlight.damage!.recent).toHaveLength(0);
    expect(sample(2).projectiles).toHaveLength(0); expect(sample(2).damage!.recent).toHaveLength(1);
    expect(sample(3).damage!.recent).toHaveLength(1);
  });

  it('retains 2000/5000 m/s launch speed, birth and TTL instead of stretching flight time', () => {
    for (const speed of [2000, 5000]) {
      const clock = new PresentationTimeline(), b = frame(4);
      const p: DisplayProjectile = { id: 1, ship_id: id, born_step: 1, origin_m: [0, 0], expires_step: 3,
        velocity_mps: [speed, 0], position_m: [speed/20, 0], previous_m: [0, 0] };
      b.snapshot.gunnery!.projectiles = [p]; clock.push(frame(0), 0); clock.push(b, 4/60*1000);
      expect(clock.sample(100+.5/60*1000)!.snapshot.gunnery!.projectiles).toHaveLength(0);
      expect(clock.sample(100+2/60*1000)!.snapshot.gunnery!.projectiles[0].position_m[0]).toBeCloseTo(speed/60);
      expect(clock.sample(150)!.snapshot.gunnery!.projectiles).toHaveLength(0);
    }
  });

  it('never runs backwards on duplicates, late replies or jitter, and freezes on exhausted snapshots', () => {
    const clock = new PresentationTimeline(); clock.push(frame(0), 0); clock.push(frame(4), 67);
    const a = clock.sample(140)!.snapshot.fixed_step;
    clock.push(frame(4), 142); expect(clock.sample(150)!.snapshot.fixed_step).toBeGreaterThan(a);
    clock.push(frame(8), 200); const b = clock.sample(201)!.snapshot.fixed_step;
    expect(b).toBeGreaterThanOrEqual(a); clock.push(frame(2), 210);
    expect(clock.sample(250)!.snapshot.fixed_step).toBeGreaterThanOrEqual(b);
    expect(clock.sample(10000)!.snapshot.fixed_step).toBe(8);
    expect(clock.sample(11000)!.snapshot.ships[0].position_m[0]).toBe(80);
  });

  it('pauses exactly, resumes without replaying paused wall time, and resets scene/backend/static identity', () => {
    const clock = new PresentationTimeline(); clock.push(frame(0), 0); clock.push(frame(4), 67);
    clock.push(frame(5, { paused: true }), 90);
    expect(clock.sample(10000)!.snapshot.fixed_step).toBe(5);
    clock.push(frame(5), 10000); expect(clock.sample(10050)!.snapshot.fixed_step).toBe(5);
    clock.push(frame(9), 10067); expect(clock.sample(10120)!.snapshot.fixed_step).toBeGreaterThan(5);
    for (const extra of [{ scene_id: 'scene.new' }, { backend_instance_id: 'backend.new' }, { static_sha256: 'geometry.new' }]) {
      clock.push(frame(0, extra), 10200); expect(clock.sample(10210)!.snapshot.fixed_step).toBe(0);
    }
    clock.clear(); expect(clock.sample(10220)).toBeNull();
  });

  it('resynchronizes after a long delivery gap instead of animating an old battle backlog', () => {
    const clock = new PresentationTimeline(); clock.push(frame(0), 0); clock.push(frame(60), 1000);
    expect(clock.sample(1000)!.snapshot.fixed_step).toBe(60);
  });

  it('uses displayed positions for picking and does not expose future hull damage', () => {
    const clock = new PresentationTimeline(), a = frame(0), b = frame(4);
    b.snapshot.ships[0] = { ...b.snapshot.ships[0], position_m: [4000, 0], hull_integrity: .2 };
    clock.push(a, 0); clock.push(b, 4/60*1000);
    const shown = clock.sample(100+2/60*1000)!;
    expect(shown.snapshot.ships[0].hull_integrity).toBe(a.snapshot.ships[0].hull_integrity);
    expect(pickShip(shown, { x: 2000, y: 0 }, { x: 0, y: 0, scale: 1 })).toBe(id);
    expect(pickShip(b, { x: 2000, y: 0 }, { x: 0, y: 0, scale: 1 })).toBeNull();
  });
});
