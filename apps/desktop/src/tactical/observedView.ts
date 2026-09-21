import type { TacticalView, DisplayProjectile } from './model';
import type { ShipPose } from './viewport';

// Visible physical effects do not grant a track or fire-control eligibility.
// Require explicit metadata so older/unknown flights do not reveal large shells.
function physicalShell(p: {kind?: string; maximum_durability?: number | null}) {
  return p.kind === 'shell' && p.maximum_durability === null;
}

// Presentation boundary only. Backend commands still validate the observer's
// real track and never consume this filtered view as simulation state.
export function observedBattleView(view: TacticalView, observerId: string, directId: string): TacticalView {
  const side = view.geometry.ships.find(s=>s.id===directId)?.side_id;
  const own = new Set(view.geometry.ships.filter(s=>s.side_id===side).map(s=>s.id));
  const g = view.snapshot.gunnery;
  const observation = g?.observation?.ships.find(s=>s.ship_id===observerId);
  const contacts = observation?.contacts.filter(c=>c.valid) ?? [];
  const ships: ShipPose[] = view.snapshot.ships.filter(s=>own.has(s.id));
  for (const c of contacts) if(c.kind==='ship' && typeof c.id==='string' && !own.has(c.id)) ships.push({
    id:c.id, position_m:[...c.position_m], velocity_mps:[...c.velocity_mps], height_layer:c.height_layer,
    heading_rad:c.heading_rad??0, yaw_rate_radps:c.yaw_rate_radps??0, speed_mps:Math.hypot(...c.velocity_mps),
    hull_integrity:0, physical_status:'observed', command_status:'unknown', modules:[],
  });
  const visible = new Set(ships.map(s=>s.id));
  const projectiles: DisplayProjectile[] = g?.projectiles.filter(p=>own.has(p.ship_id)||physicalShell(p)) ?? [];
  const physicalIds = new Set(projectiles.map(p=>p.id));
  for (const c of contacts) if(typeof c.id==='number' && !physicalIds.has(c.id) && (c.kind==='shell'||c.kind==='missile')) projectiles.push({
    id:c.id, kind:c.kind, ship_id:'observed.enemy', position_m:[...c.position_m], previous_m:[...c.position_m],
    velocity_mps:[...c.velocity_mps], height_layer:c.height_layer,
    // Only measured kinematics, never enemy phase, seeker or intended turn.
    heading_rad:Math.atan2(c.velocity_mps[1],c.velocity_mps[0]),
    altitude_m:c.altitude_m??undefined,vertical_speed_mps:c.vertical_speed_mps,
    pitch_rad:Math.atan2(c.vertical_speed_mps??0,Math.hypot(...c.velocity_mps)),
    // The observation system only tracks durable shells. Do not read hidden HP.
    has_durability:c.kind==='shell',
  });
  return {...view, snapshot:{...view.snapshot, ships,observation_source:observerId,
    events:view.snapshot.events.filter(e=>e.ship_ids.every(id=>own.has(id))),
    presentation:view.snapshot.presentation && {...view.snapshot.presentation,
      finished_projectiles:view.snapshot.presentation.finished_projectiles.filter(p=>own.has(p.ship_id)||physicalShell(p)).map(p=>({...p,impact:p.impact&&own.has(p.impact.ship_id)?p.impact:null}))},
    gunnery:g && {...g, projectiles, observed_only:true,
      weapons:g.weapons.filter(w=>own.has(w.ship_id)).map(w=>w.target_ship_id&&!visible.has(w.target_ship_id)?{...w,aim_point_m:null,target_module_id:null}:w),
      groups:g.groups?.filter(r=>own.has(r.ship_id)), fuel:g.fuel?.filter(r=>own.has(r.ship_id)), stores:g.stores?.filter(r=>own.has(r.ship_id)),
      fireproof:g.fireproof?.filter(r=>own.has(r.ship_id)),
      missiles:g.missiles&&{...g.missiles,ships:g.missiles.ships.filter(r=>own.has(r.ship_id)),pending:g.missiles.pending?.filter(r=>own.has(r.ship_id))},
      electronic_warfare:g.electronic_warfare&&{...g.electronic_warfare,devices:g.electronic_warfare.devices.filter(r=>own.has(r.ship_id)),effects:g.electronic_warfare.effects.filter(r=>own.has(r.ship_id)||r.kind==='chaff'||r.kind==='thermal')},
      damage_control:g.damage_control&&{...g.damage_control,devices:g.damage_control.devices.filter(r=>own.has(r.ship_id)),fires:g.damage_control.fires.filter(r=>own.has(r.ship_id))},
      personnel:g.personnel&&{...g.personnel,ships:g.personnel.ships.filter(r=>own.has(r.ship_id)),recent:g.personnel.recent.filter(r=>own.has(r.ship_id))},
      point_defense:g.point_defense&&{...g.point_defense,threats:g.point_defense.threats.filter(r=>r.observer_ship_id===observerId),recent:g.point_defense.recent.filter(r=>own.has(r.source_ship_id))},
      damage:g.damage&&{...g.damage,recent:g.damage.recent.filter(r=>own.has(r.ship_id)),magazine_explosions:g.damage.magazine_explosions?.filter(r=>own.has(r.ship_id))},
    }} };
}

// Discard obsolete enemy poses immediately on lost contact/observer change,
// including poses held in the interpolation buffer. Retain friendly smoothing.
export function clipObservationSample(sample: TacticalView, current: TacticalView): TacticalView {
  const ships = new Set(current.snapshot.ships.map(s=>s.id));
  const projectiles = new Set(current.snapshot.gunnery?.projectiles.map(p=>p.id));
  return {...sample,snapshot:{...sample.snapshot,ships:sample.snapshot.ships.filter(s=>ships.has(s.id)),
    gunnery:sample.snapshot.gunnery&&{...sample.snapshot.gunnery,
      observation:current.snapshot.gunnery?.observation,
      projectiles:sample.snapshot.gunnery.projectiles.filter(p=>p.ship_id!=='observed.enemy'||projectiles.has(p.id))}}};
}
