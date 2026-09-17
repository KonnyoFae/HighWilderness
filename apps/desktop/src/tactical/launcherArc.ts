import type { Camera, Point } from '../editor/viewport';
import { screen } from '../editor/viewport';
import type { TacticalView } from './model';
import type { LauncherFireArc } from './missiles';
import { selectedLauncher } from './missiles';
import { bodyToWorld } from './viewport';

export interface LauncherSelection {shipId:string;moduleId:string|null}
export const LAUNCHER_ARC_RADIUS=106;

// Only project authoritative local sectors into the displayed/interpolated pose.
// Radius is a screen-space direction guide, never a range or targeting rule.
export function launcherArcProjection(view:TacticalView,selection:LauncherSelection|undefined,camera:Camera) {
  if(!selection)return null;
  const ship=view.snapshot.gunnery?.missiles?.ships.find(s=>s.ship_id===selection.shipId);
  const launcher=selectedLauncher(ship,selection.moduleId);
  const pose=view.snapshot.ships.find(s=>s.id===selection.shipId);
  const arc=launcher?.fire_arc;
  if(!arc||!launcher||!pose)return null;
  const origin=screen(bodyToWorld({x:arc.origin_local_m[0],y:arc.origin_local_m[1]},pose),camera);
  const at=(bearing:number,radius:number):Point=>{
    const theta=bearing*Math.PI/180-pose.heading_rad;
    return {x:origin.x+radius*Math.sin(theta),y:origin.y-radius*Math.cos(theta)};
  };
  const sectors=arc.sectors.map(sector=>{
    const count=Math.max(1,Math.ceil((sector.end_deg-sector.start_deg)/3));
    const rim=Array.from({length:count+1},(_,i)=>at(sector.start_deg+(sector.end_deg-sector.start_deg)*i/count,LAUNCHER_ARC_RADIUS));
    return {...sector,points:[origin,...rim].flatMap(p=>[p.x,p.y])};
  });
  return {origin,sectors,vertical:arc.launcher_kind==='vls',moduleId:launcher.module_id,
    direction:at((arc.rotation_rad+launcher.angle_rad)*180/Math.PI,LAUNCHER_ARC_RADIUS+14)};
}

export function launcherArcLabel(arc:LauncherFireArc|undefined) {
  if(!arc)return '射界数据暂不可用';
  if(arc.launcher_kind==='vls')return '垂直发射：360° 全向，不受水平舰体遮挡限制';
  const total=(kind:string)=>arc.sectors.filter(s=>s.kind===kind).reduce((n,s)=>n+s.end_deg-s.start_deg,0);
  return `水平可射 ${total('clear').toFixed(1)}° · 舰体遮挡 ${total('hull_blocked').toFixed(1)}°`+
    (total('out_of_arc')?` · 回转范围外 ${total('out_of_arc').toFixed(1)}°`:'');
}
