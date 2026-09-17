import type { DisplayProjectile, GunView, TacticalSnapshot, TacticalView } from './model';
import { bodyToWorld, worldToBody } from './viewport';

// This clock only samples committed views. It never advances the simulation,
// extrapolates beyond the newest snapshot, or supplies command target steps.
export const PRESENTATION_DELAY_MS = 100;
const MAX_GAP_S = .5;
const mix = (a: number, b: number, t: number) => a + (b-a)*t;
const pair = (a: number[], b: number[], t: number) => [mix(a[0], b[0], t), mix(a[1], b[1], t)];
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const angle = (a: number, b: number, t: number) => a+wrap(b-a)*t;
function pathPosition(path:number[][],step:number) {
  if(step<=path[0][0])return path[0].slice(1);
  const i=path.findIndex(p=>p[0]>=step);
  if(i<0)return path[path.length-1].slice(1);
  const a=path[i-1],b=path[i];
  return pair(a.slice(1),b.slice(1),(step-a[0])/(b[0]-a[0]));
}
type Impact = NonNullable<NonNullable<TacticalSnapshot['gunnery']>['damage']>['recent'][number];

export class PresentationTimeline {
  private frames: TacticalView[] = [];
  private receivedAt = 0;
  private displayedStep = 0;

  clear() { this.frames = []; }

  push(view: TacticalView, now: number) {
    const next = view.snapshot, last = this.frames.at(-1)?.snapshot;
    if (last && next.backend_instance_id === last.backend_instance_id && next.scene_id === last.scene_id &&
        next.fixed_step < last.fixed_step) return;
    if (!last || next.backend_instance_id !== last.backend_instance_id || next.scene_id !== last.scene_id ||
        next.static_sha256 !== last.static_sha256 || next.paused !== last.paused || next.paused ||
        (next.fixed_step-last.fixed_step)*next.fixed_step_s > MAX_GAP_S) {
      this.frames = [view]; this.receivedAt = now; this.displayedStep = next.fixed_step;
      return;
    }
    // Polling the same publication must not restart its clock or replay events.
    if (next.fixed_step === last.fixed_step) { this.frames[this.frames.length-1] = view; return; }
    this.frames.push(view); this.receivedAt = now;
    while (this.frames.length > 2 && (this.frames.length > 32 ||
        next.time_s-this.frames[1].snapshot.time_s > 1)) this.frames.shift();
  }

  sample(now: number): TacticalView | null {
    const newest = this.frames.at(-1);
    if (!newest) return null;
    const latest = newest.snapshot;
    if (latest.paused) return newest;
    const desired = latest.fixed_step + (now-this.receivedAt-PRESENTATION_DELAY_MS)/(1000*latest.fixed_step_s);
    const step = this.displayedStep = Math.min(latest.fixed_step, Math.max(this.displayedStep, desired));
    const rightIndex = this.frames.findIndex(v => v.snapshot.fixed_step >= step);
    const right = this.frames[rightIndex < 0 ? this.frames.length-1 : rightIndex].snapshot;
    const left = this.frames[Math.max(0, rightIndex-1)].snapshot;
    const t = right.fixed_step === left.fixed_step ? 1 : (step-left.fixed_step)/(right.fixed_step-left.fixed_step);
    const discrete = t >= 1 ? right : left;
    const rightShips = new Map(right.ships.map(s => [s.id, s]));
    const ships = discrete.ships.map(p => {
      const a = left.ships.find(s => s.id === p.id), b = rightShips.get(p.id);
      if (!a || !b || a.height_layer !== b.height_layer) return p;
      return { ...p, position_m: pair(a.position_m, b.position_m, t), heading_rad: angle(a.heading_rad, b.heading_rad, t),
        velocity_mps: pair(a.velocity_mps, b.velocity_mps, t), speed_mps: mix(a.speed_mps, b.speed_mps, t),
        yaw_rate_radps: mix(a.yaw_rate_radps, b.yaw_rate_radps, t) };
    });
    const weapons = (discrete.gunnery?.weapons ?? []).map(gun => {
      const match = (g: GunView) => g.ship_id === gun.ship_id && g.module_id === gun.module_id;
      const a = left.gunnery?.weapons.find(match), b = right.gunnery?.weapons.find(match);
      const ship = ships.find(s => s.id === gun.ship_id);
      const oldShip = left.ships.find(s => s.id === gun.ship_id), newShip = rightShips.get(gun.ship_id);
      if (!a || !b || !ship || !oldShip || !newShip) return gun;
      const local = worldToBody({ x: a.origin_m[0], y: a.origin_m[1] }, oldShip);
      const origin = bodyToWorld(local, ship);
      const direction = ship.heading_rad + angle(Math.atan2(a.direction[1], a.direction[0])-oldShip.heading_rad,
        Math.atan2(b.direction[1], b.direction[0])-newShip.heading_rad, t);
      const sameAim = a.mode === b.mode && a.target_ship_id === b.target_ship_id && a.target_module_id === b.target_module_id;
      return { ...gun, origin_m: [origin.x, origin.y], angle_rad: angle(a.angle_rad, b.angle_rad, t),
        direction: [Math.cos(direction), Math.sin(direction)],
        aim_point_m: sameAim && a.aim_point_m && b.aim_point_m ? pair(a.aim_point_m, b.aim_point_m, t) : gun.aim_point_m };
    });
    const missiles=discrete.gunnery?.missiles;
    const ew=discrete.gunnery?.electronic_warfare;
    const effects=ew?.effects.map(e=>{
      const a=left.gunnery?.electronic_warfare?.effects.find(v=>v.id===e.id),b=right.gunnery?.electronic_warfare?.effects.find(v=>v.id===e.id);
      return a&&b?{...e,position_m:pair(a.position_m,b.position_m,t)}:e;
    });
    const missileShips=missiles?.ships.map(s=>({...s,launchers:s.launchers?.map(launcher=>{
      const find=(frame:TacticalSnapshot)=>frame.gunnery?.missiles?.ships.find(v=>v.ship_id===s.ship_id)?.launchers?.find(l=>l.module_id===launcher.module_id);
      const a=find(left),b=find(right);
      return a&&b?{...launcher,angle_rad:angle(a.angle_rad,b.angle_rad,t)}:launcher;
    })}));
    const projectiles = new Map<number, DisplayProjectile>();
    for (const p of latest.gunnery?.projectiles ?? []) {
      if (p.born_step === undefined || !p.origin_m) {
        // Older, paused-only fixtures lack launch metadata; interpolate only
        // when both endpoints contain that identity.
        const a = left.gunnery?.projectiles.find(q => q.id === p.id), b = right.gunnery?.projectiles.find(q => q.id === p.id);
        if (a && b) projectiles.set(p.id, { ...a, position_m: pair(a.position_m, b.position_m, t) });
        continue;
      }
      if (step < p.born_step || step >= (p.expires_step ?? Infinity)) continue;
      const at = (s: number) => p.trajectory?.length ? pathPosition(p.trajectory,s) : p.origin_m!.map((v, i) => v+p.velocity_mps[i]*(s-p.born_step!)*latest.fixed_step_s);
      projectiles.set(p.id, { ...p, position_m: at(step), previous_m: at(Math.max(p.born_step, step-.06/latest.fixed_step_s)) });
    }
    const hits = new Map<number, Impact>();
    for (const frame of this.frames) for (const hit of frame.snapshot.gunnery?.damage?.recent ?? []) hits.set(hit.projectile_id, hit);
    for (const p of latest.presentation?.finished_projectiles ?? []) {
      projectiles.delete(p.id);
      if (step >= p.born_step && step < p.end_step && step < p.expires_step) {
        const at = (s: number) => p.trajectory?.length ? pathPosition(p.trajectory,s) : s <= p.end_step-1 ?
          p.origin_m.map((v, i) => v+p.velocity_mps[i]*(s-p.born_step)*latest.fixed_step_s) :
          pair(p.position_m, p.end_m, s-(p.end_step-1));
        projectiles.set(p.id, { ...p, position_m: at(step), previous_m: at(Math.max(p.born_step, step-.06/latest.fixed_step_s)) });
      }
      if (p.impact && !hits.has(p.id)) hits.set(p.id, { projectile_id: p.id, step: p.end_step,
        source_ship_id: p.ship_id, ship_id: p.impact.ship_id, position_m: p.end_m, outcome: p.impact.outcome,
        height_layer: p.height_layer, deck_level: 0, module_ids: [], module_damage: 0 });
    }
    // The sampled view is disposable and stays inside the canvas/picking path.
    return { geometry: newest.geometry, snapshot: { ...discrete, fixed_step: step, time_s: step*latest.fixed_step_s,
      ships, events: discrete.events.filter(e => e.fixed_step <= step),
      gunnery: discrete.gunnery && { ...discrete.gunnery, weapons, projectiles: [...projectiles.values()],
        missiles:missiles&&{...missiles,ships:missileShips!},
        electronic_warfare:ew&&{...ew,effects:effects!},
        damage: discrete.gunnery.damage && { ...discrete.gunnery.damage,
          recent: [...hits.values()].filter(h => h.step <= step && step-h.step <= 45) } } } };
  }
}
