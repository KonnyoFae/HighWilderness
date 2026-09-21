import type { DisplayProjectile, GunView, TacticalSnapshot, TacticalView } from './model';
import { bodyToWorld, worldToBody } from './viewport';
import { shellTrail } from './shellMotion';
import { displayMissile } from './missileMotion';

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
type Interception = NonNullable<NonNullable<TacticalSnapshot['gunnery']>['point_defense']>['recent'][number];
function displayShell(p:DisplayProjectile,step:number){
  const trail=shellTrail(p.shell_samples!,step,p.born_step??step);
  return {...p,position_m:trail[trail.length-1],previous_m:trail[0],trail_m:trail};
}

export class PresentationTimeline {
  private frames: TacticalView[] = [];
  private receivedAt = 0;
  private displayedStep = 0;
  private effectFloor = -Infinity;

  clear() { this.frames = []; }

  push(view: TacticalView, now: number) {
    const next = view.snapshot, last = this.frames.at(-1)?.snapshot;
    if (last && next.backend_instance_id === last.backend_instance_id && next.scene_id === last.scene_id &&
        next.fixed_step < last.fixed_step) return;
    const discontinuity = !last || next.backend_instance_id !== last.backend_instance_id || next.scene_id !== last.scene_id ||
      next.static_sha256 !== last.static_sha256 || next.display_reset ||
      (next.fixed_step-last.fixed_step)*next.fixed_step_s > MAX_GAP_S;
    if(discontinuity)this.effectFloor=next.fixed_step;
    // A loss/reacquisition must not bridge two disconnected observations. On
    // source changes only enemy samples are retired; friendly curves remain.
    if(next.observation_source){
      const changed=last?.observation_source!==next.observation_source;
      const ships=new Set(next.ships.map(s=>s.id)),projectiles=new Set(next.gunnery?.projectiles.map(p=>p.id));
      this.frames=this.frames.map(frame=>({...frame,snapshot:{...frame.snapshot,
        ships:frame.snapshot.ships.filter(s=>s.physical_status!=='observed'||!changed&&ships.has(s.id)),
        gunnery:frame.snapshot.gunnery&&{...frame.snapshot.gunnery,
          projectiles:frame.snapshot.gunnery.projectiles.filter(p=>p.ship_id!=='observed.enemy'||!changed&&projectiles.has(p.id))}}}));
    }
    if (discontinuity || !last || next.backend_instance_id !== last.backend_instance_id || next.scene_id !== last.scene_id ||
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
    if (latest.paused || latest.gunnery?.ending) return {...newest,snapshot:{...latest,display_effect_after_step:this.effectFloor,gunnery:latest.gunnery&&{...latest.gunnery,
      projectiles:latest.gunnery.projectiles.map(p=>p.missile_samples?.length?(displayMissile(p,latest.fixed_step)??p):p.shell_samples?.length?displayShell(p,latest.fixed_step):p)}}};
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
        if (a && b) {
          const at=pair(a.position_m,b.position_m,t),span=right.fixed_step-left.fixed_step;
          const before=pair(a.position_m,b.position_m,span?Math.max(0,t-.06/latest.fixed_step_s/span):t);
          const headingA=a.heading_rad??Math.atan2(a.velocity_mps[1],a.velocity_mps[0]);
          const headingB=b.heading_rad??Math.atan2(b.velocity_mps[1],b.velocity_mps[0]);
          projectiles.set(p.id,{...(t>=1?b:a),position_m:at,previous_m:before,trail_m:[before,at],
            velocity_mps:pair(a.velocity_mps,b.velocity_mps,t),heading_rad:angle(headingA,headingB,t),
            altitude_m:a.altitude_m!==undefined&&b.altitude_m!==undefined?mix(a.altitude_m,b.altitude_m,t):a.altitude_m,
            pitch_rad:a.pitch_rad!==undefined&&b.pitch_rad!==undefined?angle(a.pitch_rad,b.pitch_rad,t):a.pitch_rad});
        }else if(b && step>=right.fixed_step)projectiles.set(p.id,b);
        continue;
      }
      if (step < p.born_step || step >= (p.expires_step ?? Infinity)) continue;
      if(p.missile_samples?.length){
        const sample=displayMissile(p,step);if(sample)projectiles.set(p.id,sample);
        continue;
      }
      if(p.shell_samples?.length){
        projectiles.set(p.id,displayShell(p,step));
        continue;
      }
      const at = (s: number) => p.trajectory?.length ? pathPosition(p.trajectory,s) : p.origin_m!.map((v, i) => v+p.velocity_mps[i]*(s-p.born_step!)*latest.fixed_step_s);
      projectiles.set(p.id, { ...p, position_m: at(step), previous_m: at(Math.max(p.born_step, step-.06/latest.fixed_step_s)) });
    }
    const hits = new Map<number, Impact>();
    const interceptions=new Map<string,Interception>();
    for (const frame of this.frames) for (const hit of frame.snapshot.gunnery?.damage?.recent ?? []) hits.set(hit.projectile_id, hit);
    for(const frame of this.frames)for(const event of frame.snapshot.gunnery?.point_defense?.recent??[])
      interceptions.set(`${event.step}/${event.round_id}/${event.projectile_id}`,event);
    for (const p of latest.presentation?.finished_projectiles ?? []) {
      projectiles.delete(p.id);
      if (step >= p.born_step && step < p.end_step && step < p.expires_step) {
        if(p.missile_samples?.length){
          const sample=displayMissile(p,step);if(sample)projectiles.set(p.id,sample);
        }else{
        const trail=p.shell_samples?.length?shellTrail(p.shell_samples,step,p.born_step):undefined;
        const at = (s: number) => p.trajectory?.length ? pathPosition(p.trajectory,s) : s <= p.end_step-1 ?
          p.origin_m.map((v, i) => v+p.velocity_mps[i]*(s-p.born_step)*latest.fixed_step_s) :
          pair(p.position_m, p.end_m, s-(p.end_step-1));
        projectiles.set(p.id, { ...p, position_m:trail?trail[trail.length-1]:at(step), previous_m:trail?trail[0]:at(Math.max(p.born_step, step-.06/latest.fixed_step_s)),
          trail_m:trail });
        }
      }
      if (p.impact && !hits.has(p.id)) hits.set(p.id, { projectile_id: p.id, step: p.end_step,
        source_ship_id: p.ship_id, ship_id: p.impact.ship_id, position_m: p.end_m, outcome: p.impact.outcome,
        height_layer: p.height_layer, deck_level: 0, module_ids: [], module_damage: 0 });
    }
    // The sampled view is disposable and stays inside the canvas/picking path.
    return { geometry: newest.geometry, snapshot: { ...discrete, fixed_step: step, time_s: step*latest.fixed_step_s,display_effect_after_step:this.effectFloor,
      ships, events: discrete.events.filter(e => e.fixed_step <= step),
      gunnery: discrete.gunnery && { ...discrete.gunnery, weapons, projectiles: [...projectiles.values()],
        point_defense:latest.gunnery?.point_defense&&{...latest.gunnery.point_defense,
          recent:[...interceptions.values()].filter(e=>e.step<=step&&step-e.step<=35)},
        missiles:missiles&&{...missiles,ships:missileShips!},
        electronic_warfare:ew&&{...ew,effects:effects!},
        damage: discrete.gunnery.damage && { ...discrete.gunnery.damage,
          recent: [...hits.values()].filter(h => h.step <= step && step-h.step <= 45) } } } };
  }
}
